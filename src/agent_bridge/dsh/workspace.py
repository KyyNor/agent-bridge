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
from agent_bridge.core.domain import AccessDenied, ValidationError

logger = logging.getLogger(__name__)

DSH_CAPABILITY_HEADER = "X-Agent-Bridge-DSH-Capability"
WORKSPACE_PROXY_PREFIX = "/agent-workspace"
# 小组共享工作台的独立代理前缀：与个人前缀区分路由目标（同一浏览器可同时
# 打开个人与共享工作台，前缀是代理判定 scope 的唯一依据）。
WORKSPACE_PROXY_SHARED_PREFIX = "/agent-workspace-shared"
WORKSPACE_PROXY_PREFIXES = (WORKSPACE_PROXY_PREFIX, WORKSPACE_PROXY_SHARED_PREFIX)
DEFAULT_CAPABILITY_TTL_SECONDS = 24 * 60 * 60

# 工作空间范围：personal 每个业务用户独立 DSH_HOME；shared 同一 Linux 用户
# 下全部业务用户共享同一个 DSH Web Runtime（runtime 身份 = linux_user）。
WORKSPACE_SCOPE_PERSONAL = "personal"
WORKSPACE_SCOPE_SHARED = "shared"
WORKSPACE_SCOPES = (WORKSPACE_SCOPE_PERSONAL, WORKSPACE_SCOPE_SHARED)
DEFAULT_WORKSPACE_SCOPE = WORKSPACE_SCOPE_PERSONAL


def normalize_workspace_scope(value: object) -> str:
    """校验并归一化工作空间范围；非法值明确报错。"""
    scope = str(value or "").strip() or DEFAULT_WORKSPACE_SCOPE
    if scope not in WORKSPACE_SCOPES:
        raise ValidationError(f"工作空间范围不合法：{value!r}（只支持 personal/shared）")
    return scope

# 注入的 MCP 插件行与覆盖文件名。
MCP_OVERLAY_FILENAME = "agent-bridge-mcp.patch.yml"
MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client"
MCP_ENTRY_ID = "agent-bridge-mcp"
MCP_SERVER_NAME = "agent-bridge"


@dataclass(frozen=True)
class DshWorkspaceCapability:
    """绑定业务用户、能力平面与数据归属组的访问能力。

    ``expires_at_monotonic`` 为 ``None`` 表示不设独立过期，生命周期完全由
    registry 记账槽位决定（共享 Runtime 的稳定 capability 用此模式，随
    runtime 停止/回收显式撤销）；个人 Workspace 仍是 24 小时 TTL。
    """

    token: str
    user_id: str
    profile_key: str
    owner_group_key: str
    expires_at_monotonic: float | None


@dataclass(frozen=True)
class WorkspaceSelection:
    """一次进入工作台时选定的能力平面；``profile_key=None`` 表示不注入任何 MCP。"""

    profile_key: str | None
    capability: DshWorkspaceCapability | None = None


class DshWorkspaceCapabilityRegistry:
    """只保存当前进程的有效 capability。

    默认每个业务用户同时至多一个（个人 Workspace 语义，重签发即替换）；
    共享 Runtime 的稳定 capability 通过独立 ``key`` 签发（``_by_user`` 按
    key 记账），不占用任何业务用户的唯一槽位，也不会被成员的个人签发/
    撤销波及。
    """

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
        ttl_seconds: int | None = DEFAULT_CAPABILITY_TTL_SECONDS,
        key: str | None = None,
    ) -> DshWorkspaceCapability:
        """签发 capability；``key`` 为记账槽位（默认 ``user_id``）。

        ``capability.user_id`` 始终是调用方传入的审计身份（共享 Runtime 传
        Linux 用户）；``key`` 只决定替换/撤销的槽位——共享 Runtime 使用
        ``shared-runtime:<linux-user>`` 槽位，成员的个人签发不会触达它。
        ``ttl_seconds=None`` 表示不设独立过期（绑定 runtime 生命周期）。
        """
        if not user_id or not profile_key or not owner_group_key:
            raise AccessDenied("DSH Workspace capability 缺少用户、能力平面或归属组")
        token = secrets.token_urlsafe(32)
        capability = DshWorkspaceCapability(
            token=token,
            user_id=user_id,
            profile_key=profile_key,
            owner_group_key=owner_group_key,
            expires_at_monotonic=(
                time.monotonic() + max(1, ttl_seconds) if ttl_seconds is not None else None
            ),
        )
        registry_key = key or user_id
        with self._lock:
            self.revoke_by_key(registry_key)
            self._items[token] = capability
            self._by_user[registry_key] = token
        return capability

    def require(
        self,
        token: str,
        *,
        profile_key: str | None = None,
    ) -> DshWorkspaceCapability:
        with self._lock:
            capability = self._items.get(token)
            if (
                capability is not None
                and capability.expires_at_monotonic is not None
                and capability.expires_at_monotonic <= time.monotonic()
            ):
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
                for key, bound in list(self._by_user.items()):
                    if bound == token:
                        self._by_user.pop(key, None)

    def revoke_for_user(self, user_id: str) -> None:
        """撤销业务用户的个人 capability；不影响共享 Runtime 的槽位。"""
        self.revoke_by_key(user_id)

    def revoke_by_key(self, key: str) -> None:
        """撤销指定记账槽位上的 capability（个人 = user_id，共享 = 专用 key）。"""
        with self._lock:
            token = self._by_user.pop(key, None)
            if token is not None:
                self._items.pop(token, None)

    def revoke_all(self) -> None:
        """清空全部 capability（服务停止回收全部 runtime 时调用）。"""
        with self._lock:
            self._items.clear()
            self._by_user.clear()

    def _drop(self, capability: DshWorkspaceCapability) -> None:
        self._items.pop(capability.token, None)
        # 槽位 key 不一定是 user_id（共享 Runtime 用专用 key），按 token 反查。
        for key, bound in list(self._by_user.items()):
            if bound == capability.token:
                self._by_user.pop(key, None)


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
