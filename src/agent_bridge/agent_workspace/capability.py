"""Workspace 能力平面接入：短期 capability 签发与记账。

能力平面（Agent Bridge Profile）是工作台/session 级选择：进入工作台时由业务
用户选定（也可以不选，此时不注入任何 MCP）。服务端签发绑定
``(user, profile, group)`` 的短期 capability，交给各 Agent adapter 注入自己的
MCP 配置（DSH 走 ``--patch`` 覆盖文件，Pi 走 ``mcp.json`` 的 ``${ENV}`` 展开）。

两种 capability 语义：

- **个人**：按业务用户唯一签发，24 小时 TTL。重签发即替换旧 token。
- **共享 runtime**：按 ``shared-runtime:<linux-user>`` 槽位签发，**不设独立
  过期**，生命周期完全绑定 runtime（停止/回收/重启时显式撤销并重签）。

个人槽位与共享槽位互相隔离：成员的个人签发/撤销不会让共享 runtime 的
capability 失效，反之亦然。

capability 只存在于 Agent Bridge 进程内存与后端进程环境，**不落盘**。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from agent_bridge.agent_workspace.directory import WorkspaceDirectory
from agent_bridge.core.domain import AccessDenied

logger = logging.getLogger(__name__)

DEFAULT_CAPABILITY_TTL_SECONDS = 24 * 60 * 60

# 共享 Runtime 的 capability 记账槽位前缀：与任何业务用户的个人槽位
# （key = user_id）隔离。
SHARED_RUNTIME_CAPABILITY_PREFIX = "shared-runtime:"


def shared_runtime_capability_key(linux_user: str) -> str:
    """共享 runtime 的记账槽位键。"""
    return f"{SHARED_RUNTIME_CAPABILITY_PREFIX}{linux_user}"


@dataclass(frozen=True)
class WorkspaceCapability:
    """绑定业务用户、能力平面与数据归属组的访问能力。

    ``expires_at_monotonic`` 为 ``None`` 表示不设独立过期，生命周期完全由
    registry 记账槽位决定（共享 runtime 的稳定 capability 用此模式，随
    runtime 停止/回收显式撤销）；个人工作台仍是 24 小时 TTL。
    """

    token: str
    user_id: str
    profile_key: str
    owner_group_key: str
    expires_at_monotonic: float | None


@dataclass(frozen=True)
class WorkspaceSelection:
    """一次进入工作台时选定的能力平面与可选启动目录；``profile_key=None`` 表示不注入任何 MCP。"""

    profile_key: str | None
    capability: WorkspaceCapability | None = None
    directory: WorkspaceDirectory | None = None


class WorkspaceCapabilityRegistry:
    """只保存当前进程的有效 capability。

    默认每个业务用户同时至多一个（个人工作台语义，重签发即替换）；共享
    runtime 的稳定 capability 通过独立 ``key`` 签发（``_by_user`` 按 key 记账），
    不占用任何业务用户的唯一槽位，也不会被成员的个人签发/撤销波及。
    """

    def __init__(self) -> None:
        self._items: dict[str, WorkspaceCapability] = {}
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
    ) -> WorkspaceCapability:
        """签发 capability；``key`` 为记账槽位（默认 ``user_id``）。

        ``capability.user_id`` 始终是调用方传入的审计身份（共享 runtime 传
        Linux 用户）；``key`` 只决定替换/撤销的槽位——共享 runtime 使用
        ``shared-runtime:<linux-user>`` 槽位，成员的个人签发不会触达它。
        ``ttl_seconds=None`` 表示不设独立过期（绑定 runtime 生命周期）。
        """
        if not user_id or not profile_key or not owner_group_key:
            raise AccessDenied("Workspace capability 缺少用户、能力平面或归属组")
        token = secrets.token_urlsafe(32)
        capability = WorkspaceCapability(
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
        self, token: str, *, profile_key: str | None = None
    ) -> WorkspaceCapability:
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
            raise AccessDenied("Workspace capability 无效或已过期")
        if profile_key is not None and capability.profile_key != profile_key:
            raise AccessDenied("Workspace capability 与请求的能力平面不匹配")
        return capability

    def current_for_user(self, user_id: str) -> WorkspaceCapability | None:
        """返回仍有效的个人 capability；过期时同步清理记账槽位。"""
        with self._lock:
            token = self._by_user.get(user_id)
            if token is None:
                return None
            capability = self._items.get(token)
            if capability is None:
                self._by_user.pop(user_id, None)
                return None
            if (
                capability.expires_at_monotonic is not None
                and capability.expires_at_monotonic <= time.monotonic()
            ):
                self._drop(capability)
                return None
            return capability

    def revoke(self, token: str) -> None:
        with self._lock:
            capability = self._items.pop(token, None)
            if capability is not None:
                for key, bound in list(self._by_user.items()):
                    if bound == token:
                        self._by_user.pop(key, None)

    def revoke_for_user(self, user_id: str) -> None:
        """撤销业务用户的个人 capability；不影响共享 runtime 的槽位。"""
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

    def _drop(self, capability: WorkspaceCapability) -> None:
        self._items.pop(capability.token, None)
        # 槽位 key 不一定是 user_id（共享 runtime 用专用 key），按 token 反查。
        for key, bound in list(self._by_user.items()):
            if bound == capability.token:
                self._by_user.pop(key, None)