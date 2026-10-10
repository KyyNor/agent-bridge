"""DSH Workspace 的能力平面接入：DSH ``--patch`` 覆盖文件。

能力平面（Agent Bridge Profile）是工作台/session 级选择：进入工作台时由业务
用户选定（也可以不选，此时不注入任何 MCP）。capability 的签发与记账已抽取到
:mod:`agent_bridge.agent_workspace.capability`（个人 24h TTL、共享 runtime 级
稳定 capability、槽位隔离）；本模块只保留 DSH 特有的 ``--patch`` 覆盖文档
构造与文件读写。

覆盖文件写在业务用户的 ``DSH_HOME``（``.config/dsh/<business-user>/``）内，
只对该 Linux 用户可读，且只在注入期间存在；不写入 ``AGENT_BRIDGE_ROOT/data``。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

from agent_bridge.agent_workspace.capability import (
    DEFAULT_CAPABILITY_TTL_SECONDS,
    WorkspaceCapability as DshWorkspaceCapability,
    WorkspaceCapabilityRegistry as DshWorkspaceCapabilityRegistry,
    WorkspaceSelection,
    shared_runtime_capability_key,
)
from agent_bridge.agent_workspace.scopes import (
    WORKSPACE_SCOPE_PERSONAL,
    WORKSPACE_SCOPE_SHARED,
    WORKSPACE_SCOPES,
    DEFAULT_WORKSPACE_SCOPE,
    normalize_workspace_scope,
)
from agent_bridge.core.defaults import DEFAULT_CLAUDE_CODE_MCP_TOOL_TIMEOUT_MS

logger = logging.getLogger(__name__)

DSH_CAPABILITY_HEADER = "X-Agent-Bridge-DSH-Capability"
WORKSPACE_PROXY_PREFIX = "/agent-workspace"
# 小组共享工作台的独立代理前缀：与个人前缀区分路由目标（同一浏览器可同时
# 打开个人与共享工作台，前缀是代理判定 scope 的唯一依据）。
WORKSPACE_PROXY_SHARED_PREFIX = "/agent-workspace-shared"
WORKSPACE_PROXY_PREFIXES = (WORKSPACE_PROXY_PREFIX, WORKSPACE_PROXY_SHARED_PREFIX)

# 注入的 MCP 插件行与覆盖文件名。
MCP_OVERLAY_FILENAME = "agent-bridge-mcp.patch.yml"
MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client"
MCP_ENTRY_ID = "agent-bridge-mcp"
MCP_SERVER_NAME = "agent-bridge"

__all__ = [
    "DEFAULT_CAPABILITY_TTL_SECONDS",
    "DEFAULT_WORKSPACE_SCOPE",
    "DSH_CAPABILITY_HEADER",
    "DshWorkspaceCapability",
    "DshWorkspaceCapabilityRegistry",
    "MCP_CLIENT_PLUGIN",
    "MCP_ENTRY_ID",
    "MCP_OVERLAY_FILENAME",
    "MCP_SERVER_NAME",
    "WORKSPACE_PROXY_PREFIX",
    "WORKSPACE_PROXY_PREFIXES",
    "WORKSPACE_PROXY_SHARED_PREFIX",
    "WORKSPACE_SCOPES",
    "WORKSPACE_SCOPE_PERSONAL",
    "WORKSPACE_SCOPE_SHARED",
    "WorkspaceSelection",
    "build_mcp_overlay",
    "ensure_owner",
    "mcp_overlay_path",
    "normalize_workspace_scope",
    "remove_mcp_overlay",
    "shared_runtime_capability_key",
    "write_mcp_overlay",
]


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
