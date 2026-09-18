"""DSH Workspace 的能力平面接入：短期 capability 与 MCP 注入配置。

能力平面（Agent Bridge Profile）是 Workspace/session 级选择：进入工作台时由
业务用户选定（也可以不选，此时不注入任何 MCP）。服务端签发绑定
(user, profile, group) 的短期 capability，并生成一份 DSH 的 ``--patch`` 覆盖
文件，把 ``dsh-mcp-client`` 插件实例（streamable-http，指向 Agent Bridge
``/mcp``）插入组合树。DSH 作为 MCP client 携带 capability 请求 ``/mcp``，
服务端按既有能力平面做权限控制。

覆盖文件写在业务用户的 ``DSH_HOME``（``.config/dsh/<business-user>/``）内，
只对该 Linux 用户可读，且只在注入期间存在；不写入 ``AGENT_BRIDGE_ROOT/data``。
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_bridge.core.defaults import DEFAULT_CLAUDE_CODE_MCP_TOOL_TIMEOUT_MS
from agent_bridge.core.domain import AccessDenied

logger = logging.getLogger(__name__)

DSH_CAPABILITY_HEADER = "X-Agent-Bridge-DSH-Capability"
WORKSPACE_PROXY_PREFIX = "/agent-workspace"
DEFAULT_CAPABILITY_TTL_SECONDS = 24 * 60 * 60

# 注入的 MCP 插件行与覆盖文件名。
MCP_OVERLAY_FILENAME = "agent-bridge-mcp.patch.yml"
MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client"
MCP_ENTRY_ID = "agent-bridge-mcp"
MCP_SERVER_NAME = "agent-bridge"


@dataclass(frozen=True)
class DshWorkspaceCapability:
    """绑定业务用户、能力平面与数据归属组的短期访问能力。"""

    token: str
    user_id: str
    profile_key: str
    owner_group_key: str
    expires_at_monotonic: float


@dataclass(frozen=True)
class WorkspaceSelection:
    """一次进入工作台时选定的能力平面；``profile_key=None`` 表示不注入任何 MCP。"""

    profile_key: str | None
    capability: DshWorkspaceCapability | None = None


class DshWorkspaceCapabilityRegistry:
    """只保存当前进程的有效 capability；每个用户同时至多一个。"""

    def __init__(self) -> None:
        self._items: dict[str, DshWorkspaceCapability] = {}
        self._by_user: dict[str, str] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        *,
        user_id: str,
        profile_key: str,
        owner_group_key: str,
        ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS,
    ) -> DshWorkspaceCapability:
        if not user_id or not profile_key or not owner_group_key:
            raise AccessDenied("DSH Workspace capability 缺少用户、能力平面或归属组")
        token = secrets.token_urlsafe(32)
        capability = DshWorkspaceCapability(
            token=token,
            user_id=user_id,
            profile_key=profile_key,
            owner_group_key=owner_group_key,
            expires_at_monotonic=time.monotonic() + max(1, ttl_seconds),
        )
        with self._lock:
            self.revoke_for_user(user_id)
            self._items[token] = capability
            self._by_user[user_id] = token
        return capability

    def require(
        self,
        token: str,
        *,
        profile_key: str | None = None,
    ) -> DshWorkspaceCapability:
        with self._lock:
            capability = self._items.get(token)
            if capability is not None and capability.expires_at_monotonic <= time.monotonic():
                self._drop(capability)
                capability = None
        if capability is None:
            raise AccessDenied("DSH Workspace capability 无效或已过期")
        if profile_key is not None and capability.profile_key != profile_key:
            raise AccessDenied("DSH Workspace capability 与请求的能力平面不匹配")
        return capability

    def revoke(self, token: str) -> None:
        with self._lock:
            capability = self._items.pop(token, None)
            if capability is not None:
                self._by_user.pop(capability.user_id, None)

    def revoke_for_user(self, user_id: str) -> None:
        with self._lock:
            token = self._by_user.pop(user_id, None)
            if token is not None:
                self._items.pop(token, None)

    def _drop(self, capability: DshWorkspaceCapability) -> None:
        self._items.pop(capability.token, None)
        if self._by_user.get(capability.user_id) == capability.token:
            self._by_user.pop(capability.user_id, None)


def mcp_overlay_path(dsh_home: Path) -> Path:
    return Path(dsh_home) / MCP_OVERLAY_FILENAME


def build_mcp_overlay(*, mcp_url: str, profile_key: str, capability_token: str) -> list[dict[str, Any]]:
    """构造 DSH 的 ``--patch`` 覆盖文档：插入一个 streamable-http MCP 实例。

    DSH 的 MCP 服务器是 loader 条目（``dsh-mcp-client`` 插件实例），不是
    ``mcpServers`` JSON 文件，因此以 patch 覆盖层注入组合树。
    """
    return [
        {
            "insert": [
                {
                    "id": MCP_ENTRY_ID,
                    "name": MCP_CLIENT_PLUGIN,
                    "config": {
                        "transport": "streamable-http",
                        "serverName": MCP_SERVER_NAME,
                        "url": mcp_url,
                        "headers": {
                            "X-Agent-Bridge-MetaMCP-Profile": profile_key,
                            DSH_CAPABILITY_HEADER: capability_token,
                        },
                        "toolCallTimeoutMs": DEFAULT_CLAUDE_CODE_MCP_TOOL_TIMEOUT_MS,
                        "failOnStartupError": False,
                    },
                }
            ]
        }
    ]


def write_mcp_overlay(path: Path, overlay: list[dict[str, Any]]) -> Path:
    """写入覆盖文件；DSH 以目标 Linux 用户读取，权限收紧到 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(overlay, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("DSH Workspace MCP 覆盖文件 chmod 失败 path=%s 原因=%s", path, exc)
    return path


def remove_mcp_overlay(path: Path) -> None:
    """移除覆盖文件（退出注入时调用，避免残留旧 capability）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("DSH Workspace MCP 覆盖文件删除失败 path=%s 原因=%s", path, exc)


def ensure_owner(path: Path, *, uid: int, gid: int) -> None:
    """root 下把覆盖文件归属目标 Linux 用户（非 root 或同用户时跳过）。"""
    if os.geteuid() != 0 or uid == os.geteuid():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        logger.warning("DSH Workspace MCP 覆盖文件 chown 失败 path=%s uid=%s 原因=%s", path, uid, exc)
