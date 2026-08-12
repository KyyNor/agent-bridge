"""工作流运行时的短期、不可伪造访问能力。"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from agent_bridge.core.domain import AccessDenied


WORKFLOW_CAPABILITY_HEADER = "X-Agent-Bridge-Workflow-Capability"
_DEFAULT_TTL_SECONDS = 26 * 60 * 60


@dataclass(frozen=True)
class WorkflowRuntimeCapability:
    token: str
    actor: str
    initiated_by: str
    workflow_key: str
    run_id: str
    profile_key: str
    owner_group_key: str
    expires_at_monotonic: float


class WorkflowRuntimeCapabilityRegistry:
    """只保存当前进程的活动运行；服务重启时运行本身会被回收。"""

    def __init__(self) -> None:
        self._items: dict[str, WorkflowRuntimeCapability] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        *,
        initiated_by: str,
        workflow_key: str,
        run_id: str,
        profile_key: str,
        owner_group_key: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> WorkflowRuntimeCapability:
        if not owner_group_key:
            raise AccessDenied("工作流运行缺少数据归属组")
        token = secrets.token_urlsafe(32)
        capability = WorkflowRuntimeCapability(
            token=token,
            actor=f"workflow-runtime:{secrets.token_hex(16)}",
            initiated_by=initiated_by,
            workflow_key=workflow_key,
            run_id=run_id,
            profile_key=profile_key,
            owner_group_key=owner_group_key,
            expires_at_monotonic=time.monotonic() + max(1, ttl_seconds),
        )
        with self._lock:
            self._items[token] = capability
        return capability

    def require(
        self,
        token: str,
        *,
        workflow_key: str,
        run_id: str,
        profile_key: str | None,
    ) -> WorkflowRuntimeCapability:
        with self._lock:
            capability = self._items.get(token)
            if capability is not None and capability.expires_at_monotonic <= time.monotonic():
                self._items.pop(token, None)
                capability = None
        if capability is None:
            raise AccessDenied("工作流运行 capability 无效或已过期")
        if (
            capability.workflow_key != workflow_key
            or capability.run_id != run_id
            or (profile_key is not None and capability.profile_key != profile_key)
        ):
            raise AccessDenied("工作流运行 capability 与请求上下文不匹配")
        return capability

    def revoke(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)
