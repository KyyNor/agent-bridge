"""Pi Agent 工作台 adapter（社区实现 agegr/pi-web，npm ``@agegr/pi-web``）。

pi 官方没有 Web 服务模式，这里用 pi-web 提供浏览器界面。集成只依赖其公开
启动契约（CLI 参数 + 环境变量），不 fork、不打补丁；版本由包内
:mod:`~agent_bridge.agent_workspace.pi_versions` 的清单钉死，可整体替换。

已核实的上游契约（@agegr/pi-web 0.12.2，2026-10 实测）：

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

import asyncio
import base64
import hashlib
import logging
import os
import shlex
import threading
import time
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
# 换取到的会话 Cookie 在代理侧的复用时长。pi-web 的 Cookie 有效期约 30 天且
# HMAC 绑定 PI_WEB_PASSWORD；密码每次启动重新生成并参与缓存键，进程重启后旧
# 缓存自然失效，这里的 TTL 只是兜底。
PI_SESSION_CACHE_TTL_SECONDS = 12 * 60 * 60

PI_PERSONAL_PROXY_PREFIX = "/agent-workspace-pi"
PI_SHARED_PROXY_PREFIX = "/agent-workspace-pi-shared"

# pi-web 的会话 Cookie 名（30 天，HMAC 绑定 PI_WEB_PASSWORD）。
PI_SESSION_COOKIE = "pi_web_session"
# pi-web 以 scope "/" 注册的 service worker 及其附属产物：必须由代理拦截，
# 否则会接管整个源站（含本平台 dashboard 与 DSH 工作台）。
PI_BLOCKED_ASSETS = ("/sw.js", "/service-worker.js", "/workbox.js")


def _merge_pi_session_cookie(cookie_header: str | None, *, pair: str) -> str | None:
    """用刚换取的 pi 会话 Cookie 替换同名旧值，保留其他 pi Cookie。"""
    name = pair.partition("=")[0].strip()
    kept = [
        segment.strip()
        for segment in (cookie_header or "").split(";")
        if segment.strip() and segment.split("=", 1)[0].strip() != name
    ]
    kept.append(pair)
    return "; ".join(kept)


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
        # (上游 target, 密码摘要) → (会话 Cookie 值, 换取时刻)。代理对每个请求都
        # 需要上游会话；逐请求 POST /api/web-auth 会给每个静态资源、API、SSE
        # 请求叠加一次阻塞登录，并让 pi-web 不断新建会话。
        self._session_cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._session_cache_lock = threading.Lock()
        # 按 target 串行化换取：首屏并发请求只触发一次换取，且不同实例互不阻塞。
        self._exchange_locks: dict[str, threading.Lock] = {}

    def supports_scope(self, scope: str) -> bool:
        # 小组共享与 DSH 同款：一个 Linux 用户一个目录、一个进程，成员经代理
        # 进入同一实例。pi-web 本身按单人工具设计，但组内共享与 DSH 共享是
        # 同一信任模型（内网可信 VM、同组可见）。
        return True

    def config_dir(self, identity: LinuxIdentity, runtime_owner: str) -> Path:
        """``<linux home>/.config/agent-bridge/pi/<runtime-owner>/``。

        刻意**不**放在 pi 自己的默认 agent 目录（``~/.pi/agent``）下面：该目录
        同时存放 pi 的 ``sessions/``、``extensions/``、``skills/``、``auth.json``
        等真实条目，若隔离键恰好叫 ``sessions`` 之类就会指向 pi 自己的会话目录，
        被改权限并写入托管配置；用户在终端跑 ``pi`` 时也会在自己的 agent 目录里
        看到 Agent Bridge 的状态。
        """
        return ensure_user_config_dir(
            identity, ".config", "agent-bridge", "pi", owner_name=runtime_owner
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
            context_window=int(model_binding.get("context_window") or 0),
            max_tokens=int(model_binding.get("max_tokens") or 0),
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

    def needs_proxy_auth(self) -> bool:
        """Pi 的页面请求对每个请求都需要会话 Cookie，必须逐请求鉴权。"""
        return True

    def blocked_paths(self) -> tuple[str, ...]:
        """拦截 pi-web 的根作用域 service worker 资源。

        pi-web 注册 ``scope: "/"`` 的 ``/sw.js``，其 ``activate`` 里
        ``clients.claim()`` 会立刻接管整个源站——包括本平台的 dashboard 与 DSH
        工作台。同源只允许一个根作用域 SW，必须硬拦；注册失败对 pi-web 只是其
        前端已 catch 的一条 console error。
        """
        return PI_BLOCKED_ASSETS

    def filter_request_cookies(
        self, cookie_header: str | None, *, authority: str
    ) -> str | None:
        """只保留 pi 自己的会话 Cookie（``pi_`` 前缀）。

        平台会话（``agent_bridge_admin``）、统计等无关 Cookie 不透传给上游；
        pi-web 的会话 Cookie 必须保持 ``Path=/`` 覆盖整站（其资源与 SSE 都用
        根绝对路径请求）。
        """
        kept = [
            segment.strip()
            for segment in (cookie_header or "").split(";")
            if segment.strip()
            and segment.partition("=")[0].strip().startswith("pi_")
        ]
        return "; ".join(kept) if kept else None

    async def resolve_upstream_auth(
        self,
        service,
        *,
        user_id: str,
        target: str,
        workspace_scope: str,
        browser_cookie: str | None,
    ) -> str | None:
        """用状态文件里的密码换取 ``pi_web_session`` Cookie。

        pi-web 的 HTTP Basic 只覆盖 ``/api/**``，页面请求一律 307 到
        ``/login``，因此**不能**靠代理注入凭据完成鉴权；且其失败限流是单一
        全局计数器、成功也不重置，一次错误尝试会把整实例（共享时是全组）
        锁死最长 60 秒。正确做法是在服务端换取 Cookie 再转发，且换取请求
        不携带 ``Authorization``。
        """
        runtime = _pi_runtime(service, self.slug)
        try:
            password = await asyncio.to_thread(
                runtime.workspace_password, user_id, scope=workspace_scope
            )
        except Exception:
            logger.warning(
                "Pi 工作台读取会话密码失败 user=%s scope=%s",
                user_id,
                workspace_scope,
                exc_info=True,
            )
            return None
        if not password:
            return None
        value = await asyncio.to_thread(self._session_cookie_value, target, password)
        if value is None:
            # 换取失败时不伪造凭据：调用方回落到浏览器自带 Cookie。
            return None
        return _merge_pi_session_cookie(
            browser_cookie, pair=f"{PI_SESSION_COOKIE}={value}"
        )

    def _session_cookie_value(self, target: str, password: str) -> str | None:
        """取（必要时换取）该实例的会话 Cookie 值；命中缓存则不访问上游。"""
        key = (target, hashlib.sha256(password.encode("utf-8")).hexdigest())
        cached = self._cached_session(key)
        if cached is not None:
            return cached
        with self._session_cache_lock:
            exchange_lock = self._exchange_locks.setdefault(target, threading.Lock())
        with exchange_lock:
            # 等锁期间可能已有并发请求完成换取。
            cached = self._cached_session(key)
            if cached is not None:
                return cached
            value = exchange_session_cookie(target, password)
            if value is None:
                return None
            with self._session_cache_lock:
                # 同一 target 换了密码（实例重启复用端口）时，旧条目一并清掉。
                for stale in [item for item in self._session_cache if item[0] == target]:
                    self._session_cache.pop(stale, None)
                self._session_cache[key] = (value, time.monotonic())
            return value

    def _cached_session(self, key: tuple[str, str]) -> str | None:
        with self._session_cache_lock:
            entry = self._session_cache.get(key)
            if entry is None:
                return None
            value, obtained_at = entry
            if time.monotonic() - obtained_at >= PI_SESSION_CACHE_TTL_SECONDS:
                self._session_cache.pop(key, None)
                return None
            return value

    def probe_auth(self, state: dict[str, Any]) -> dict[str, str]:
        """就绪探测带上 Basic Auth。

        pi-web 启用 ``PI_WEB_PASSWORD`` 后 ``/api/home`` 需要认证：实测不带凭据
        恒返回 401，只探测端口会让启动流程一直等不到就绪直至超时。这里用状态
        文件里的会话密码构造 Basic 头（用户名固定为 ``pi``）。

        该请求不会触碰 ``/api/web-auth``，因此不计入 pi-web 的登录失败限流。
        """
        password = str(state.get("auth_secret") or "")
        if not password:
            return {}
        token = base64.b64encode(f"pi:{password}".encode("utf-8")).decode("ascii")
        return {"authorization": f"Basic {token}"}

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


def _pi_runtime(service, slug: str):
    """从代理传入的装配根取 Pi runtime；直接传入 runtime 时原样使用。"""
    resolver = getattr(service, "workspace_runtime", None)
    if callable(resolver):
        return resolver(slug)
    return service


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