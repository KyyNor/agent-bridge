"""Pi 工作台的运行时服务与配置入口。

配置复用 DSH 的分层结构（组级 ``linux_user`` / ``default_model`` /
``api_key`` + 全局公共 Base URL 与可用模型），两种 Agent 共享同一份组级 Linux
用户映射，避免重复配置。本模块只提供 Pi 特有的两点：

- 运行配置里的 ``web_command``（pi-web 启动命令模板）与依赖版本清单；
- 组级默认模型/公共模型接入对 Pi 的落盘形式（见
  :mod:`agent_bridge.agent_workspace.pi_injection`）。

共享工作台的 capability 记账、槽位隔离、recover 语义全部由通用 runtime 承担，
与 DSH 完全一致。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter
from agent_bridge.agent_workspace.launcher import PopenDshLauncher
from agent_bridge.agent_workspace.pi_versions import (
    DEFAULT_PI_WEB_COMMAND,
    normalize_pi_web_command,
)

__all__ = ["PiRuntimeService", "normalize_pi_web_command"]
from agent_bridge.agent_workspace.service import WorkspaceRuntimeService
from agent_bridge.core.domain import ValidationError

logger = logging.getLogger(__name__)


class PiRuntimeService(WorkspaceRuntimeService):
    """Pi Agent 工作台 Runtime：通用 runtime + Pi adapter。

    Pi 的首访鉴权不使用启动 token（pi-web 无 token 机制），代理改用状态文件里
    的会话密码换取 ``pi_web_session`` Cookie，见
    :func:`~agent_bridge.agent_workspace.adapters.pi.exchange_session_cookie`。
    """

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
        adapter = PiWorkspaceAdapter(
            configs=configs,
            launcher=launcher,
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
            state_dir_name="pi-runtimes",
        )

    def workspace_password(
        self, user_id: str, *, scope: str = "personal"
    ) -> str | None:
        """返回目标 runtime 的 pi-web 会话密码（代理据此换取 Cookie）。

        密码每次启动随机生成，只保存在 0600 的 runtime 状态文件中；未运行时
        返回 None。
        """
        from agent_bridge.agent_workspace.scopes import normalize_workspace_scope

        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            password = str(state.get("auth_secret") or "")
            return password or None

    def web_command(self) -> str:
        """当前生效的启动命令模板（管理端展示用）。"""
        return normalize_pi_web_command(
            str(self._configs.runtime_config_for_runtime().get("pi_web_command") or "")
        )