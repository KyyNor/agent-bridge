"""DSH 工作台 adapter：把 DSH 特有的启动与注入翻译成通用 runtime 的启动计划。

通用 runtime（:mod:`agent_bridge.agent_workspace.service`）负责进程、端口、
状态文件与回收；本模块只保留 DSH 真正的差异：

- 启动命令模板（``{port}`` / ``{patch}`` 占位符）；
- 插件首装（必须先于 web 进程，装在目标 Linux 用户身份下）；
- 模型接入（``--patch`` 覆盖层，不写 ``settings.yaml``）；
- 能力平面注入（``--patch`` 覆盖文件）；
- 首访鉴权入口（``dsh web: …?token=…`` 横幅捕获与轮换刷新）。

DSH 的配置目录、共享语义、空闲回收与 capability 记账全部由通用 runtime 承担，
本 PR 不改变 DSH 的任何外部行为。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_bridge.agent_workspace import process_tree
from agent_bridge.agent_workspace.adapter import WorkspaceLaunchPlan
from agent_bridge.agent_workspace.capability import WorkspaceSelection
from agent_bridge.agent_workspace.launcher import (
    LinuxIdentity,
    ensure_user_config_dir,
)
from agent_bridge.agent_workspace.cookies import dsh_session_cookie_name
from agent_bridge.agent_workspace.scopes import WORKSPACE_SCOPE_SHARED
from agent_bridge.core.domain import ValidationError
from agent_bridge.dsh import injection
from agent_bridge.dsh import plugins
from agent_bridge.dsh.agent_runtime import dsh_binary_from_command
from agent_bridge.dsh.workspace import (
    WORKSPACE_PROXY_PREFIX,
    WORKSPACE_PROXY_SHARED_PREFIX,
    build_mcp_overlay,
    ensure_owner as ensure_overlay_owner,
    mcp_overlay_path as workspace_overlay_path,
    remove_mcp_overlay,
    write_mcp_overlay,
)

logger = logging.getLogger(__name__)

# DSH Web 默认绑定回环：工作站台只能经站内代理访问。
DSH_HOST = "127.0.0.1"
DSH_PORT_BASE = 48400
DSH_MAX_INSTANCES = 50
DSH_STARTUP_TIMEOUT_SECONDS = 60.0
DSH_STOP_GRACE_SECONDS = 5.0

# 单个插件安装（含 npm/pnpm 下载）的最长等待；超时按失败处理、下次启动重试。
PLUGIN_INSTALL_TIMEOUT_SECONDS = 240.0
# 一次 runtime 启动内插件安装的总预算。
PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS = 600.0

# DSH 启动横幅：``dsh web: http://127.0.0.1:<port>/?token=<launch-token>``（可能附带 LAN 地址）
_DSH_WEB_URL_RE = re.compile(r"dsh web:\s*(?P<url>https?://[^\s()]+)")


def _service_module():
    """解析 ``agent_bridge.dsh.service`` 模块（延迟导入，避免循环依赖）。"""
    from importlib import import_module

    return import_module("agent_bridge.dsh.service")


class DshWorkspaceAdapter:
    """DSH Web 的 workspace adapter。"""

    plugin_profile = plugins.PLUGIN_PROFILE
    slug = "dsh"
    display_name = "DeepSeek Harness"
    personal_proxy_prefix = WORKSPACE_PROXY_PREFIX
    shared_proxy_prefix = WORKSPACE_PROXY_SHARED_PREFIX
    mark_env = process_tree.RUNTIME_MARK_ENV
    port_base = DSH_PORT_BASE
    port_pool_size = DSH_MAX_INSTANCES
    startup_timeout_seconds = DSH_STARTUP_TIMEOUT_SECONDS
    stop_grace_seconds = DSH_STOP_GRACE_SECONDS
    #: 就绪探测路径。通用 runtime 的 ``_probe_port`` 直接读该属性，
    #: 缺失会在探测里抛 AttributeError 并被吞成 False，导致永远等不到就绪。
    probe_path = "/"

    def __init__(
        self,
        *,
        configs,
        launcher=None,
        passwd_lookup=None,
        mcp_url: str,
    ) -> None:
        self._configs = configs
        self._launcher = launcher
        self._passwd_lookup = passwd_lookup
        self.mcp_url = mcp_url

    def supports_scope(self, scope: str) -> bool:
        return True

    def config_dir(self, identity: LinuxIdentity, runtime_owner: str) -> Path:
        """``<linux home>/.config/dsh/<business-user>/``（共享时 owner 即 Linux 用户）。"""
        return ensure_user_config_dir(
            identity, ".config", "dsh", owner_name=runtime_owner
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
        runtime_config = self._configs.runtime_config_for_runtime()
        overlay_path = self._write_workspace_overlay(
            config_dir, identity, runtime_owner, workspace
        )
        env = self._build_env(
            identity,
            config_dir,
            model_binding,
            mark=process_tree.runtime_mark_value(scope, runtime_key),
        )
        # 插件必须先于 web 进程安装完毕：运行中的 DSH 不会热加载 profile 变更，
        # 后装插件只会在下次重启后出现（首访竞态）。
        self._install_plugins(
            runtime_owner,
            identity=identity,
            config_dir=config_dir,
            dsh_binary=dsh_binary_from_command(str(runtime_config.get("web_command") or "")),
            specs=plugins.read_plugin_list(),
        )
        # 模型覆盖必须在插件安装之后读取生效配置：首装会初始化 web profile。
        model_overlay_path = self._write_model_overlay(
            config_dir,
            identity,
            model_binding,
            env=env,
            dsh_binary=dsh_binary_from_command(str(runtime_config.get("web_command") or "")),
        )
        command = build_dsh_command(
            str(runtime_config.get("web_command") or ""),
            port,
            patch_paths=[
                path for path in (model_overlay_path, overlay_path) if path is not None
            ],
        )
        return WorkspaceLaunchPlan(
            command=command,
            env=env,
            log_path=log_path,
            startup_timeout_seconds=DSH_STARTUP_TIMEOUT_SECONDS,
            probe_path="/",
            display_name=self.display_name,
        )

    def probe_auth(self, state: dict[str, Any]) -> dict[str, str]:
        """DSH 不启用密码，探测不带凭据。

        DSH 的鉴权是启动 token 换会话 Cookie，探测路径无需也不应携带凭据。
        """
        return {}

    def blocked_paths(self) -> tuple[str, ...]:
        """DSH 不需要拦截任何上游路径。"""
        return ()

    def needs_proxy_auth(self) -> bool:
        """DSH 走根导航的 token 换取流程，不需要逐请求鉴权。"""
        return False

    def filter_request_cookies(
        self, cookie_header: str | None, *, authority: str
    ) -> str | None:
        """收敛发往 DSH 的 Cookie：只保留 DSH 自己需要的部分。

        浏览器会把 Agent Bridge 域下的全部 Cookie 送来（``agent_bridge_admin``、
        PostHog 等），它们与 DSH 无关，不透传给上游。保留的范围：

        - 当前 authority 的 ``dsh-auth-<hash>`` 会话 Cookie——其他端口的旧会话
          Cookie 名称不同，对本目标永远无效；
        - 其余 ``dsh-`` 前缀的应用 Cookie。
        """
        session = dsh_session_cookie_name(authority)
        kept: list[str] = []
        for segment in (cookie_header or "").split(";"):
            segment = segment.strip()
            if not segment:
                continue
            cookie_name = segment.partition("=")[0].strip()
            if not cookie_name.startswith("dsh-"):
                continue
            if cookie_name.startswith("dsh-auth-") and cookie_name != session:
                continue
            kept.append(segment)
        return "; ".join(kept) if kept else None

    def on_stopped(self, state: dict[str, Any], *, scope: str, runtime_key: str) -> None:
        # 共享 runtime 停止后移除能力平面覆盖文件，避免下次按需启动带着死 token。
        if scope == WORKSPACE_SCOPE_SHARED:
            config_dir = str(state.get("config_dir") or "")
            if config_dir:
                remove_mcp_overlay(workspace_overlay_path(Path(config_dir)))

    # -- 插件首装 --

    def _plugin_env(self, identity: LinuxIdentity, config_dir: Path) -> dict[str, str]:
        """插件安装进程环境：与 web 进程同源，但不携带 API Key。"""
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["DSH_HOME"] = str(config_dir)
        return env

    def _run_dsh_plugin_command(
        self,
        runner,
        *,
        user_id: str,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        identity: LinuxIdentity,
    ) -> tuple[int, str] | None:
        """执行一条一次性 dsh 插件命令；进程异常时返回 None（已记日志）。"""
        try:
            return runner(
                command=command,
                env=env,
                cwd=cwd,
                identity=identity,
                timeout_seconds=PLUGIN_INSTALL_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error(
                "DSH 插件命令异常 user=%s command=%s 原因=%s", user_id, command[1:], exc
            )
            return None

    def _install_plugins(
        self,
        user_id: str,
        *,
        identity: LinuxIdentity,
        config_dir: Path,
        dsh_binary: str,
        specs: list[str],
    ) -> None:
        """在 web 进程启动前按名单补装插件（幂等、可断点续装、指纹跳过）。"""
        if not specs:
            return
        runner = getattr(self._launcher, "run_once", None)
        if runner is None:
            logger.warning(
                "DSH 插件安装跳过：当前 launcher 不支持一次性命令 user=%s", user_id
            )
            return
        # 预算上限可由 ``agent_bridge.dsh.service`` 模块级常量覆盖（既有用例据此
        # 模拟预算耗尽）；默认取本模块常量。
        total_budget = getattr(
            _service_module(), "PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS", None
        )
        if total_budget is None:
            total_budget = PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS
        fingerprint = plugins.compute_plugin_fingerprint(specs)
        state_path = plugins.plugin_state_path(config_dir)
        plugin_state = plugins.read_plugin_state(config_dir)
        if plugin_state is not None and plugin_state.get("fingerprint") == fingerprint:
            logger.info(
                "DSH 插件依赖未变化，跳过安装 user=%s config_dir=%s fingerprint=%s",
                user_id,
                config_dir,
                fingerprint[:19],
            )
            return
        logger.info(
            "DSH 插件依赖发生变化，开始安装 user=%s config_dir=%s count=%d",
            user_id,
            config_dir,
            len(specs),
        )
        env = self._plugin_env(identity, config_dir)
        cwd = identity.home if identity.home.is_dir() else config_dir
        profile_dir = plugins.profile_dir_for(config_dir, self.plugin_profile)
        # profile 模板（package.json + pnpm-workspace.yaml）由 dsh 在首次插件
        # 命令时生成；先落初始化，才能在其上写构建声明。
        if not (profile_dir / "package.json").exists():
            init = self._run_dsh_plugin_command(
                runner,
                user_id=user_id,
                command=plugins.build_profile_install_command(dsh_binary, profile=self.plugin_profile),
                env=env,
                cwd=cwd,
                identity=identity,
            )
            if init is None or init[0] != 0:
                logger.warning(
                    "DSH profile 初始化失败 user=%s profile=%s exit=%s（继续尝试插件安装）",
                    user_id,
                    profile_dir,
                    init[0] if init is not None else "-",
                )
        plugins.ensure_build_blocks(profile_dir)
        installed = plugins.read_installed_specs(config_dir)
        succeeded: list[str] = list(installed)
        marker_path = plugins.plugin_marker_path(config_dir)
        attempted = 0
        budget_deadline = time.monotonic() + total_budget
        for spec in specs:
            if spec in installed:
                continue
            if time.monotonic() >= budget_deadline:
                logger.warning(
                    "DSH 插件安装超出总预算 %.0fs，剩余条目下次启动重试 user=%s 剩余=%d",
                    total_budget,
                    user_id,
                    sum(1 for item in specs if item not in succeeded),
                )
                break
            attempted += 1
            started = time.monotonic()
            result = self._run_dsh_plugin_command(
                runner,
                user_id=user_id,
                command=plugins.build_install_command(dsh_binary, spec, profile=self.plugin_profile),
                env=env,
                cwd=cwd,
                identity=identity,
            )
            if result is None:
                continue
            exit_code, output = result
            elapsed = time.monotonic() - started
            if exit_code != 0:
                logger.warning(
                    "DSH 插件安装失败 user=%s plugin=%s exit=%s 耗时=%.1fs 输出尾部=%s（下次启动重试）",
                    user_id, spec, exit_code, elapsed, output[-500:] or "(空)",
                )
                continue
            succeeded.append(spec)
            plugins.write_installed_specs(config_dir, succeeded)
            try:
                marker_path.chmod(0o600)
            except OSError:
                logger.warning("DSH 插件 marker chmod 失败 path=%s", marker_path)
            plugins.ensure_marker_owner(marker_path, uid=identity.uid, gid=identity.gid)
            logger.info(
                "DSH 插件安装完成 user=%s plugin=%s 耗时=%.1fs", user_id, spec, elapsed
            )
        if set(specs) <= set(succeeded):
            # 只有全部所需插件成功安装才写入成功指纹；失败/超时留待下次重试。
            plugins.write_plugin_state(config_dir, fingerprint=fingerprint, specs=specs)
            try:
                state_path.chmod(0o600)
            except OSError:
                logger.warning("DSH 插件状态 chmod 失败 path=%s", state_path)
            plugins.ensure_marker_owner(state_path, uid=identity.uid, gid=identity.gid)
            if attempted:
                logger.info(
                    "DSH 插件名单已全部就位 user=%s count=%d fingerprint=%s",
                    user_id,
                    len(specs),
                    fingerprint[:19],
                )

    # -- 注入 --

    def _write_workspace_overlay(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        runtime_owner: str,
        workspace: WorkspaceSelection | None,
    ) -> Path | None:
        """按能力平面写入/清除 DSH ``--patch`` 覆盖文件，返回需注入的路径。"""
        overlay_path = workspace_overlay_path(config_dir)
        if workspace is None:
            # 未指定选择：沿用已存在的注入（例如仅调用 /runtime/ensure 保活）。
            return overlay_path if overlay_path.exists() else None
        capability = workspace.capability
        if not workspace.profile_key or capability is None:
            if overlay_path.exists():
                remove_mcp_overlay(overlay_path)
                logger.info("DSH Workspace 已清除能力平面注入 user=%s", runtime_owner)
            return None
        write_mcp_overlay(
            overlay_path,
            build_mcp_overlay(
                mcp_url=self.mcp_url,
                profile_key=workspace.profile_key,
                capability_token=capability.token,
            ),
        )
        ensure_overlay_owner(overlay_path, uid=identity.uid, gid=identity.gid)
        logger.info(
            "DSH Workspace MCP 覆盖文件已写入 user=%s profile=%s path=%s",
            runtime_owner,
            workspace.profile_key,
            overlay_path,
        )
        return overlay_path

    def _write_model_overlay(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        binding: dict[str, Any],
        *,
        env: dict[str, str],
        dsh_binary: str,
    ) -> Path | None:
        """把公共接入（Base URL / 模型）与组级默认模型写成 web profile 的 ``--patch`` 覆盖文件。"""
        path = injection.model_overlay_path(config_dir)
        entries = injection.resolve_model_overlay(
            dsh_home=config_dir,
            dsh_binary=dsh_binary,
            profile=self.plugin_profile,
            base_url=str(binding.get("base_url") or ""),
            models=[str(item) for item in binding.get("available_models") or []],
            default_model=str(binding.get("default_model") or ""),
            context_window=int(binding.get("context_window") or 0),
            max_tokens=int(binding.get("max_tokens") or 0),
            env=env,
            cwd=identity.home if identity.home.is_dir() else config_dir,
            identity=identity,
        )
        if entries is None:
            injection.remove_overlay(path)
            if not binding.get("base_url"):
                logger.warning(
                    "DSH 模型接入未配置 Base URL（全局与公共模型配置均为空）config_dir=%s，DSH 将使用自身默认供应商",
                    config_dir,
                )
            else:
                logger.warning(
                    "DSH 模型接入已解析 Base URL（source=%s）但全局可用模型列表为空，"
                    "跳过供应商注入，组级 API Key 不会生效 config_dir=%s，请在 DSH 运行配置中补充可用模型",
                    str(binding.get("base_url_source") or "-"),
                    config_dir,
                )
            return None
        injection.write_overlay(path, entries)
        injection.ensure_owner(path, uid=identity.uid, gid=identity.gid)
        logger.info(
            "DSH 模型覆盖文件已写入 path=%s provider=%s models=%d default_model=%s",
            path,
            injection.MANAGED_PROVIDER_KEY,
            len(binding.get("available_models") or []),
            binding.get("default_model") or "-",
        )
        return path

    def _build_env(
        self,
        identity: LinuxIdentity,
        config_dir: Path,
        binding: dict[str, Any],
        *,
        mark: str,
    ) -> dict[str, str]:
        """构造 DSH 进程环境：模型配置走 ``--patch`` 覆盖文件，密钥走环境变量。"""
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["DSH_HOME"] = str(config_dir)
        env[process_tree.RUNTIME_MARK_ENV] = mark
        env.update(injection.managed_api_key_env_value(str(binding.get("api_key") or "")))
        return env


def build_dsh_command(template: str, port: int, *, patch_paths: list[Path] | None = None) -> list[str]:
    """渲染启动命令；``{patch}`` 展开为按序叠加的 ``--patch <路径>``（无覆盖文件时为空）。"""
    patch_arg = " ".join(f'--patch {shlex.quote(str(path))}' for path in patch_paths or [])
    try:
        rendered = template.format(port=port, patch=patch_arg)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValidationError(
            f"DSH 启动命令模板不合法：{template!r}（仅支持 {{port}} 与 {{patch}} 占位符）"
        ) from exc
    command = shlex.split(rendered)
    if not command:
        raise ValidationError(f"DSH 启动命令模板不合法：{template!r}")
    return command


def capture_dsh_auth_entry(log_path: Path) -> dict[str, str] | None:
    """从 DSH 运行日志解析 ``dsh web: http://…/?token=…`` 鉴权入口。

    DSH 会在 token 轮换（Connection 重载）时重印横幅，因此以日志中最后一条
    横幅为当前有效入口。
    """
    if not log_path or not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    captured: dict[str, str] | None = None
    for match in _DSH_WEB_URL_RE.finditer(text):
        parsed = urlparse(match.group("url"))
        if not parsed.query:
            continue
        captured = {
            "auth_path": parsed.path or "/",
            "auth_query": parsed.query,
        }
    return captured