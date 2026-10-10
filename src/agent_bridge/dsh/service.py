"""DSH Web Runtime 生命周期管理（薄壳）。

通用进程托管已抽取到 :mod:`agent_bridge.agent_workspace.service`，DSH 特有的
插件首装、模型 ``--patch`` 覆盖、能力平面覆盖文件与首访鉴权入口捕获在
:mod:`agent_bridge.agent_workspace.adapters.dsh`。本模块只做两件事：

1. 组装 :class:`~agent_bridge.agent_workspace.service.WorkspaceRuntimeService`
   与 DSH adapter，并保留本文件历史对外符号（``DshRuntimeService``、端口/超时
   常量、``shared_runtime_capability_key``）以兼容既有调用方与测试；
2. 承担 DSH 专属的「首访 token→Cookie 换取入口」状态维护——它依赖 DSH 运行
   日志中的 ``dsh web: …?token=…`` 横幅，不属于通用 runtime。

DSH 的外部行为（路径、个人/共享语义、能力平面、插件、空闲回收、recover）
在本 PR 中保持不变。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.agent_workspace.adapters.dsh import (
    DSH_HOST,
    DSH_MAX_INSTANCES,
    DSH_PORT_BASE,
    DSH_STARTUP_TIMEOUT_SECONDS,
    DSH_STOP_GRACE_SECONDS,
    PLUGIN_INSTALL_TIMEOUT_SECONDS,
    PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS,
    DshWorkspaceAdapter,
    build_dsh_command,
    capture_dsh_auth_entry,
)
from agent_bridge.agent_workspace.capability import (
    DEFAULT_CAPABILITY_TTL_SECONDS,
    WorkspaceCapability,
    WorkspaceCapabilityRegistry,
    WorkspaceSelection,
    shared_runtime_capability_key,
)
from agent_bridge.agent_workspace.launcher import (
    LinuxIdentity,
    PopenDshLauncher,
    resolve_linux_identity,
)
from agent_bridge.agent_workspace.scopes import (
    WORKSPACE_SCOPE_PERSONAL,
    WORKSPACE_SCOPE_SHARED,
    normalize_workspace_scope,
)
from agent_bridge.agent_workspace.service import REAP_INTERVAL_SECONDS, WorkspaceRuntimeService

logger = logging.getLogger(__name__)

STATE_DIR_NAME = "dsh-runtimes"
LOG_DIR_NAME = "dsh-runtimes"

__all__ = [
    "DSH_HOST",
    "DSH_MAX_INSTANCES",
    "DSH_PORT_BASE",
    "DSH_STARTUP_TIMEOUT_SECONDS",
    "DSH_STOP_GRACE_SECONDS",
    "DEFAULT_CAPABILITY_TTL_SECONDS",
    "LOG_DIR_NAME",
    "PLUGIN_INSTALL_TIMEOUT_SECONDS",
    "PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS",
    "REAP_INTERVAL_SECONDS",
    "STATE_DIR_NAME",
    "DshRuntimeService",
    "DshWorkspaceCapabilityRegistry",
    "DshWorkspaceCapability",
    "WorkspaceSelection",
    "LinuxIdentity",
    "PopenDshLauncher",
    "resolve_linux_identity",
    "build_dsh_command",
    "capture_dsh_auth_entry",
    "normalize_workspace_scope",
    "shared_runtime_capability_key",
]


class DshRuntimeService(WorkspaceRuntimeService):
    """DSH Web Runtime 服务：通用 runtime + DSH adapter + 首访鉴权入口维护。"""

    def __init__(
        self,
        *,
        paths,
        configs,
        access,
        admins: set[str],
        launcher=None,
        passwd_lookup=None,
        mcp_url: str | None = None,
    ) -> None:
        resolved_mcp_url = mcp_url or DEFAULT_MCP_URL
        adapter = DshWorkspaceAdapter(
            configs=configs,
            launcher=launcher,
            passwd_lookup=passwd_lookup,
            mcp_url=resolved_mcp_url,
        )
        super().__init__(
            adapter=adapter,
            paths=paths,
            configs=configs,
            access=access,
            admins=admins,
            launcher=launcher or PopenDshLauncher(),
            passwd_lookup=passwd_lookup,
            mcp_url=resolved_mcp_url,
            state_dir_name=STATE_DIR_NAME,
        )
        # DSH 的进程枚举走 ``agent_bridge.dsh.process_tree`` 兼容层：既有测试与
        # 调用方在该模块上 monkeypatch，转发保证替换对实际回收路径生效。
        from agent_bridge.dsh import process_tree as dsh_process_tree

        self._process_tree = dsh_process_tree

    # -- DSH 专属：首访 token→Cookie 换取入口 --

    def workspace_auth(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> tuple[str, str] | None:
        """返回 DSH 启动时打印的鉴权入口路径与查询串，例如 ``("/", "token=…")``。

        DSH Web 只接受带启动 token 的首次导航：``GET /?token=…`` 校验后写入
        会话 Cookie 并 303 回 ``/``，后续请求凭 Cookie 通过。代理层用该入口
        完成首次导航，浏览器无需感知 token。非 DSH 实现或日志缺失时返回 None。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            auth_path = str(state.get("auth_path") or "")
            auth_query = str(state.get("auth_query") or "")
            if auth_path and auth_query:
                return auth_path, auth_query
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            state.update(captured)
            self._write_state(scope, runtime_key, state)
            return str(captured["auth_path"]), str(captured["auth_query"])

    def refresh_workspace_auth(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> tuple[str, str] | None:
        """重扫运行日志中的最新 ``dsh web:`` 横幅，更新已轮换的鉴权入口。

        DSH 的启动 token 绑定进程内 owner，Connection 重载会静默轮换 token 并
        重印横幅；启动时捕获的入口可能已过期（表现为交换 401）。本方法返回
        按最新横幅刷新后的入口；横幅与已存入口一致时只回读、不落盘。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            new_path = str(captured["auth_path"])
            new_query = str(captured["auth_query"])
            old_path = str(state.get("auth_path") or "")
            old_query = str(state.get("auth_query") or "")
            if new_path == old_path and new_query == old_query:
                return old_path, old_query
            state.update(captured)
            self._write_state(scope, runtime_key, state)
            logger.info(
                "DSH 启动 token 已轮换，按最新横幅刷新鉴权入口 user=%s scope=%s",
                normalized_user,
                scope,
            )
            return new_path, new_query

    @staticmethod
    def _capture_auth_entry(state: dict[str, Any]) -> dict[str, str] | None:
        """从运行日志解析当前有效的 ``dsh web: …?token=…`` 鉴权入口。"""
        log_path = Path(str(state.get("log_path") or ""))
        return capture_dsh_auth_entry(log_path)

    # -- 启动后捕获鉴权入口 --

    def _start_runtime(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        state = super()._start_runtime(*args, **kwargs)
        captured = self._capture_auth_entry(state)
        if captured is None:
            logger.warning(
                "DSH Runtime 未在启动日志中找到鉴权入口 runtime=%s log=%s"
                "（工作台将无法自动完成首次鉴权）",
                state.get("user_id"),
                state.get("log_path"),
            )
        else:
            scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
            runtime_key = str(state.get("user_id") or "")
            state.update(captured)
            self._write_state(scope, runtime_key, state)
        return state

    # -- 兼容旧测试/调用方使用的辅助方法 --

    def _build_command(
        self, template: str, port: int, *, patch_paths: list[Path] | None = None
    ) -> list[str]:
        return build_dsh_command(template, port, patch_paths=patch_paths)


    def _write_workspace_overlay(self, *args: Any, **kwargs: Any) -> Any:
        return self.adapter._write_workspace_overlay(*args, **kwargs)

    def _write_model_overlay(self, *args: Any, **kwargs: Any) -> Any:
        return self.adapter._write_model_overlay(*args, **kwargs)

    def _build_env(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self.adapter._build_env(*args, **kwargs)

    def _install_plugins(self, *args: Any, **kwargs: Any) -> None:
        # 插件安装走服务当前的 launcher：既有测试通过替换 ``service.dsh._launcher``
        # 注入 fake launcher，adapter 必须读到同一实例。
        self.adapter._launcher = self._launcher
        return self.adapter._install_plugins(*args, **kwargs)

    def _plugin_env(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self.adapter._plugin_env(*args, **kwargs)

    def _run_dsh_plugin_command(self, *args: Any, **kwargs: Any) -> Any:
        return self.adapter._run_dsh_plugin_command(*args, **kwargs)

    def _ensure_config_dir(self, identity: LinuxIdentity, user_id: str) -> Path:
        return self.adapter.config_dir(identity, user_id)

    def stop_idle_expired(self) -> list[str]:
        return super().stop_idle_expired()

    def _wait_until_ready(
        self,
        port: int,
        *,
        process,
        timeout: float | None = None,
        state: dict[str, Any] | None = None,
    ) -> bool:
        return super()._wait_until_ready(
            port,
            process=process,
            timeout=timeout if timeout is not None else DSH_STARTUP_TIMEOUT_SECONDS,
            state=state,
        )
