"""Pi Agent 工作台 adapter（社区实现 agegr/pi-web，npm ``@agegr/pi-web``）。

pi 官方没有 Web 服务模式，这里用 pi-web 提供浏览器界面。集成只依赖其公开
启动契约（CLI 参数 + 环境变量），不 fork、不打补丁；版本由包内
:mod:`~agent_bridge.agent_workspace.pi_versions` 的清单钉死，可整体替换。

已核实的上游契约（@agegr/pi-web 0.11.0，2026-10 实测）：

- 启动：``pi-web --no-open --port <p> --hostname 127.0.0.1``，Node ≥ 22.19；
- 鉴权：``PI_WEB_PASSWORD`` 非空即启用。**HTTP Basic 只覆盖 ``/api/**``，
  页面请求一律 307 到 ``/login``**，因此不能用代理注入 header 的方式鉴权；
  正确做法与 DSH 首访一致——代理在服务端 ``POST /api/web-auth`` 换取
  ``pi_web_session`` Cookie 再转发（见 ``exchange_session_cookie``）。
  另外 pi-web 的失败限流是**单一全局计数器**，漏发/发错密码会把整实例锁死
  最长 60 秒，因此绝不能依赖逐请求注入凭据。
- 配置目录：``PI_CODING_AGENT_DIR`` 由底层 ``pi-coding-agent`` 消费，可整体
  重定向到隔离目录；
- 无 WebSocket，聊天流式全部走 SSE；
- 不支持 basePath，只能走「剥前缀 + 根路径认领」（与 DSH 相同形态）。

已知风险（代理层必须处理）：pi-web 注册 ``scope: "/"`` 的 service worker，
``clients.claim()`` 会接管整个源站，必须硬拦 ``/sw.js``。
"""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.agent_workspace import pi_injection, pi_versions
from agent_bridge.agent_workspace.pi_versions import normalize_pi_web_command
from agent_bridge.agent_workspace import process_tree
from agent_bridge.agent_workspace.adapter import WorkspaceLaunchPlan
from agent_bridge.agent_workspace.capability import WorkspaceSelection
from agent_bridge.agent_workspace.launcher import (
    LinuxIdentity,
    ensure_user_config_dir,
)
from agent_bridge.core.domain import ValidationError

logger = logging.getLogger(__name__)

PI_HOST = "127.0.0.1"
PI_PORT_BASE = 48500
PI_MAX_INSTANCES = 50
PI_STARTUP_TIMEOUT_SECONDS = 90.0
PI_STOP_GRACE_SECONDS = 5.0
# 代理→上游首访换取 Cookie 的超时。
PI_AUTH_EXCHANGE_TIMEOUT_SECONDS = 15.0

PI_PERSONAL_PROXY_PREFIX = "/agent-workspace-pi"
PI_SHARED_PROXY_PREFIX = "/agent-workspace-pi-shared"

# pi-web 的会话 Cookie 名（30 天，HMAC 绑定 PI_WEB_PASSWORD）。
PI_SESSION_COOKIE = "pi_web_session"
# 代理换取 Cookie 的上游端点。
PI_WEB_AUTH_PATH = "/api/web-auth"


class PiWorkspaceAdapter:
    """pi-web 的 workspace adapter。"""

    slug = "pi"
    display_name = "Pi Agent"
    personal_proxy_prefix = PI_PERSONAL_PROXY_PREFIX
    shared_proxy_prefix = PI_SHARED_PROXY_PREFIX
    mark_env = process_tree.PI_RUNTIME_MARK_ENV
    port_base = PI_PORT_BASE
    port_pool_size = PI_MAX_INSTANCES
    startup_timeout_seconds = PI_STARTUP_TIMEOUT_SECONDS
    stop_grace_seconds = PI_STOP_GRACE_SECONDS
    # 未启用密码时 pi-web 的首页直接可达；启用密码时首页 307 到 /login，
    # 两者都是 <500，故以 /api/home 作为就绪探测更明确。
    probe_path = "/api/home"

    def __init__(self, *, configs, launcher=None, mcp_url: str) -> None:
        self._configs = configs
        self._launcher = launcher
        self.mcp_url = mcp_url

    def supports_scope(self, scope: str) -> bool:
        # 小组共享与 DSH 同款：一个 Linux 用户一个目录、一个进程，成员经代理
        # 进入同一实例。pi-web 本身按单人工具设计，但组内共享与 DSH 共享是
        # 同一信任模型（内网可信 VM、同组可见）。
        return True

    def config_dir(self, identity: LinuxIdentity, runtime_owner: str) -> Path:
        """``<linux home>/.pi/agent/<business-user>/``（共享时 owner 即 Linux 用户）。"""
        return ensure_user_config_dir(
            identity, ".pi", "agent", owner_name=runtime_owner
        )

    def build_launch_plan(
        self,
        *,
        identity: LinuxIdentity,
        runtime_owner: str,
        scope: str,
        runtime_key: str,
        port: int,
        workspace: WorkspaceSelection | None,
        model_binding: dict[str, Any],
        log_path: Path,
    ) -> WorkspaceLaunchPlan:
        config_dir = self.config_dir(identity, runtime_owner)
        base_url = str(model_binding.get("base_url") or "")
        models = [str(item) for item in model_binding.get("available_models") or []]
        default_model = str(model_binding.get("default_model") or "")

        pi_injection.write_models(
            config_dir,
            uid=identity.uid,
            gid=identity.gid,
            base_url=base_url,
            models=models,
        )
        pi_injection.write_settings(
            config_dir,
            uid=identity.uid,
            gid=identity.gid,
            default_model=default_model,
        )
        token = workspace.capability.token if (
            workspace is not None and workspace.capability is not None
        ) else None
        profile_key = workspace.profile_key if workspace is not None else None
        pi_injection.write_mcp_servers(
            config_dir,
            uid=identity.uid,
            gid=identity.gid,
            mcp_url=self.mcp_url,
            profile_key=profile_key,
            capability_token=token,
        )

        runtime_config = self._configs.runtime_config_for_runtime()
        command = self._build_command(
            normalize_pi_web_command(str(runtime_config.get("pi_web_command") or "")),
            port,
        )
        env, password = self._build_env(
            identity,
            config_dir,
            model_binding,
            workspace=workspace,
            mark=process_tree.runtime_mark_value(scope, runtime_key),
        )
        logger.info(
            "Pi 工作台启动准备完成 runtime=%s config_dir=%s models=%d default_model=%s profile=%s",
            runtime_key,
            config_dir,
            len(models),
            default_model or "-",
            profile_key or "-",
        )
        return WorkspaceLaunchPlan(
            command=command,
            env=env,
            log_path=log_path,
            startup_timeout_seconds=PI_STARTUP_TIMEOUT_SECONDS,
            probe_path=self.probe_path,
            display_name=self.display_name,
            auth_secret=password,
        )

    def on_stopped(self, state: dict[str, Any], *, scope: str, runtime_key: str) -> None:
        # 停止后移除托管 MCP 条目，避免下次按需启动带着死 token 启动。
        config_dir = str(state.get("config_dir") or "")
        if not config_dir:
            return
        path = Path(config_dir)
        if not path.is_dir():
            return
        try:
            pi_injection.remove_managed_mcp_server(
                path,
                uid=int(state.get("uid") or os.geteuid()),
                gid=int(state.get("gid") or os.getegid()),
            )
        except Exception:
            logger.warning(
                "Pi 停止后清理 MCP 条目失败 config_dir=%s", config_dir, exc_info=True
            )

    # -- 启动命令与环境 --

    @staticmethod
    def _build_command(template: str, port: int) -> list[str]:
        """渲染启动命令；只支持 ``{port}`` 占位符。"""
        try:
            rendered = template.format(port=port)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationError(
                f"Pi 启动命令模板不合法：{template!r}（仅支持 {{port}} 占位符）"
            ) from exc
        command = shlex.split(rendered)
        if not command:
            raise ValidationError(f"Pi 启动命令模板不合法：{template!r}")
        return command

    @staticmethod
    def _build_env(
        identity: LinuxIdentity,
        config_dir: Path,
        binding: dict[str, Any],
        *,
        workspace: WorkspaceSelection | None,
        mark: str,
    ) -> tuple[dict[str, str], str]:
        """构造 pi-web 进程环境，并返回本次启动的会话密码。

        ``PI_WEB_PASSWORD`` 每次启动随机生成、只存于状态文件（0600），代理用
        它换取会话 Cookie；API Key 与 capability 只走环境变量、不落盘。
        """
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["PI_CODING_AGENT_DIR"] = str(config_dir)
        env[process_tree.PI_RUNTIME_MARK_ENV] = mark
        # 关闭版本检查与浏览器自启（工作台经站内代理访问）。
        env["PI_WEB_SKIP_VERSION_CHECK"] = "1"
        env["PI_WEB_NO_OPEN"] = "1"
        # pi-web 自身的会话空闲回收与我们的进程级空闲回收并存。
        env["PI_WEB_IDLE_TIMEOUT_MS"] = str(pi_versions.SESSION_IDLE_TIMEOUT_MS)
        password = generate_password()
        env["PI_WEB_PASSWORD"] = password
        api_key = str(binding.get("api_key") or "")
        if api_key:
            env[pi_injection.PI_API_KEY_ENV] = api_key
        else:
            env.pop(pi_injection.PI_API_KEY_ENV, None)
        token = workspace.capability.token if (
            workspace is not None and workspace.capability is not None
        ) else None
        if token:
            env[pi_injection.PI_CAPABILITY_ENV] = token
        else:
            env.pop(pi_injection.PI_CAPABILITY_ENV, None)
        return env, password


def generate_password() -> str:
    """生成本次启动使用的 pi-web 密码（仅存于状态文件，0600）。"""
    import secrets

    return secrets.token_urlsafe(32)


def exchange_session_cookie(
    base_url: str, password: str, *, timeout_seconds: float = PI_AUTH_EXCHANGE_TIMEOUT_SECONDS
) -> str | None:
    """在服务端用密码换取 pi-web 会话 Cookie 值。

    Basic Auth 只覆盖 ``/api/**``，页面请求会被 307 到 ``/login``，因此代理
    必须持有一份会话 Cookie 才能转发浏览器请求。本函数按 pi-web 的
    ``POST /api/web-auth`` 契约换取，返回 ``pi_web_session`` 的值；失败返回
    None，调用方应如实透传上游响应（如实回 401）。

    换取请求不携带任何浏览器 Cookie，也不注入 Basic Auth——pi-web 的失败
    限流是全局单一计数器，任何一次错误尝试都会把整实例锁死。
    """
    if not password:
        return None
    try:
        response = httpx.post(
            base_url.rstrip("/") + PI_WEB_AUTH_PATH,
            json={"password": password},
            timeout=timeout_seconds,
        )
    except Exception as exc:
        logger.warning("Pi 会话 Cookie 换取失败（连接异常）base_url=%s 原因=%s", base_url, exc)
        return None
    if response.status_code >= 400:
        logger.warning(
            "Pi 会话 Cookie 换取失败 base_url=%s status=%s",
            base_url,
            response.status_code,
        )
        return None
    for raw in response.headers.get_list("set-cookie"):
        first = raw.split(";", 1)[0].strip()
        name, _, value = first.partition("=")
        if name == PI_SESSION_COOKIE and value:
            return value
    logger.warning("Pi 会话 Cookie 换取未返回 %s base_url=%s", PI_SESSION_COOKIE, base_url)
    return None