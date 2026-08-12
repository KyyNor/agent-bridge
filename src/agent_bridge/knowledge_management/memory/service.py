from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.core.slug import make_slug
from agent_bridge.knowledge_management.memory.claude_mem.worker import ClaudeMemWorkerService
from agent_bridge.knowledge_management.memory.hooks import MemoryHookService
from agent_bridge.knowledge_management.memory.models import ACTIVE_MEMORY_STATUSES
from agent_bridge.storage.sqlite import SQLiteStore


logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        *,
        paths,
        store: SQLiteStore,
        admins: set[str],
        worker_service: Any | None = None,
        governance_service: Any | None = None,
        access: AccessControlService | None = None,
    ) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.access = access
        self.worker_service = worker_service or ClaudeMemWorkerService(paths=paths)
        self.hooks = MemoryHookService(
            memory_service=self,
            worker_service=self.worker_service,
            governance_service=governance_service,
        )

    def create_block(self, actor: str, block_key: str, name: str, description: str) -> dict[str, Any]:
        scope = (
            self.access.new_resource_scope(
                actor=actor,
                visibility="group",
                resource_type=ScopedResourceType.memory_block,
            )
            if self.access is not None
            else None
        )
        if self.access is None:
            require_admin_user(actor, self.admins)
        normalized_key = make_slug(block_key)
        if not normalized_key or normalized_key != block_key:
            raise ValidationError("memory block key must be a lowercase slug")
        if self.store.memory.get_memory_block(block_key) is not None:
            raise ValidationError("memory block already exists")
        return self.store.memory.create_memory_block(
            block_key=block_key,
            name=name,
            description=description,
            data_dir=str(self._default_data_dir(block_key)),
            created_by=actor,
            owner_group_key=scope.owner_group_key if scope is not None else "",
        )

    def list_blocks(self, actor: str) -> list[dict[str, Any]]:
        blocks = self.store.memory.list_memory_blocks()
        if self.access is None:
            require_admin_user(actor, self.admins)
            return blocks
        return [
            block for block in blocks
            if self.access.can_read(actor=actor, scope=ResourceScope.from_record(block))
        ]

    def get_block(self, actor: str, block_key: str) -> dict[str, Any]:
        return self._require_block_read(actor, block_key)

    def set_block_status(self, actor: str, block_key: str, status: str) -> dict[str, Any]:
        self._require_block_write(actor, block_key)
        if status not in ACTIVE_MEMORY_STATUSES:
            raise ValidationError("invalid memory block status")
        if self.store.memory.get_memory_block(block_key) is None:
            raise NotFound("memory block not found")
        return self.store.memory.set_memory_block_status(block_key, status)

    def delete_block(self, actor: str, block_key: str) -> dict[str, Any]:
        """硬删除一个记忆区块，并清理其副作用数据。

        清理顺序：停止 claude-mem worker 进程（容错）→ 删除记忆数据目录 → 删除
        区块行。``profile_memory_bindings.block_key`` 是外键 ON DELETE SET NULL，
        删除后绑定行保留但 block_key 置空（``resolve_profile_block`` 会安全返回
        not_configured）。
        """
        block = self._require_block_write(actor, block_key)

        # 停止可能正在运行的 claude-mem worker 进程（容错，失败不阻断删除）
        try:
            self.worker_service.stop_dashboard(block)
        except Exception:
            logger.warning("删除记忆区块 %s 时停止 worker 失败，已忽略", block_key, exc_info=True)

        # 删除记忆数据目录（含全部观察数据，容错，目录可能不存在）
        shutil.rmtree(self._default_data_dir(block_key), ignore_errors=True)

        self.store.memory.delete_memory_block(block_key)
        logger.info("已删除记忆区块 %s", block_key)
        return {"block_key": block_key, "deleted": True}

    def get_profile_binding(self, actor: str, profile_key: str) -> dict[str, Any]:
        self._require_profile_access(actor, profile_key, write=False)
        binding = self.store.memory.get_profile_memory_binding(profile_key)
        payload = binding or {"profile_key": profile_key, "block_key": None, "enabled": 1}
        return attach_edit_token(payload, self._profile_binding_snapshot(payload))

    def set_profile_binding(
        self,
        actor: str,
        profile_key: str,
        block_key: str | None,
        *,
        enabled: bool,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        profile = self._require_profile_access(actor, profile_key, write=True)
        current = self.store.memory.get_profile_memory_binding(profile_key) or {
            "profile_key": profile_key,
            "block_key": None,
            "enabled": 1,
        }
        require_edit_token(
            expected_edit_token,
            self._profile_binding_snapshot(current),
            resource_type="能力平面记忆绑定",
            resource_key=profile_key,
            actor=actor,
        )
        if block_key:
            block = self.store.memory.get_memory_block(block_key)
            if block is None:
                raise NotFound("memory block not found")
            self._require_block_read(actor, block_key)
            if str(block.get("owner_group_key") or "") != str(profile.get("owner_group_key") or ""):
                raise ValidationError("能力平面只能绑定同一数据组的记忆块")
            if block.get("status") != "active":
                raise ValidationError("memory block is not active")
        saved = self.store.memory.set_profile_memory_binding(profile_key, block_key, enabled=enabled)
        return attach_edit_token(saved, self._profile_binding_snapshot(saved))

    @staticmethod
    def _profile_binding_snapshot(binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "block_key": binding.get("block_key"),
            "enabled": bool(binding.get("enabled")),
        }

    def resolve_profile_block(self, actor: str, profile_key: str | None) -> dict[str, Any]:
        """解析 profile 当前绑定的记忆块：任一环节缺失/未启用/未激活即返回 not_configured。"""
        if not profile_key:
            return {"status": "not_configured", "block": None}
        profile = self.store.get_project_profile(profile_key)
        if profile is None or profile.get("status") != "active":
            logger.debug("profile=%s 未激活或不存在，记忆块未绑定", profile_key)
            return {"status": "not_configured", "block": None}
        if self.access is not None:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(profile))
        binding = self.store.memory.get_profile_memory_binding(profile_key)
        if not binding or not binding.get("enabled") or not binding.get("block_key"):
            logger.debug("profile=%s 未绑定记忆块或绑定已禁用", profile_key)
            return {"status": "not_configured", "block": None}
        block = self.store.memory.get_memory_block(str(binding["block_key"]))
        if block is None or block.get("status") != "active":
            logger.warning("profile=%s 绑定的记忆块 block=%s 不存在或未激活", profile_key, binding.get("block_key"))
            return {"status": "not_configured", "block": None}
        if self.access is not None:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(block))
        return {"status": "ok", "block": block}

    def search(
        self,
        *,
        actor: str,
        profile_key: str | None,
        query: str,
        limit: int = 10,
        block_key: str | None = None,
    ) -> dict[str, Any]:
        """记忆检索入口：先解析运行时记忆块，再委托 worker。未绑定则直接返回 not_configured。"""
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            logger.info("memory 检索跳过 actor=%s status=%s", actor, resolved["status"])
            return {"status": resolved["status"], "block_key": None, "items": []}
        return self.worker_service.search(resolved["block"], query=query, limit=limit)

    def timeline(
        self,
        *,
        actor: str,
        profile_key: str | None,
        limit: int = 20,
        cursor: str | None = None,
        block_key: str | None = None,
    ) -> dict[str, Any]:
        """记忆时间线入口：分页读取观察序列，未绑定则直接返回 not_configured。"""
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            logger.info("memory 时间线跳过 actor=%s status=%s", actor, resolved["status"])
            return {"status": resolved["status"], "block_key": None, "items": [], "next_cursor": None}
        return self.worker_service.timeline(resolved["block"], limit=limit, cursor=cursor)

    def get_observation(
        self,
        *,
        actor: str,
        profile_key: str | None,
        observation_id: str,
        block_key: str | None = None,
    ) -> dict[str, Any]:
        """单条观察读取入口：按 observation_id 取详情，未绑定则直接返回 not_configured。"""
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            logger.info("memory 读取观察跳过 actor=%s status=%s", actor, resolved["status"])
            return {"status": resolved["status"], "block_key": None, "item": None}
        return self.worker_service.get_observation(resolved["block"], observation_id)

    def block_health(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.get_block(actor, block_key)
        health = self.worker_service.health(block)
        self.store.memory.update_memory_block_health(block_key, health)
        return {"block_key": block_key, **health}

    def dashboard_status(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.get_block(actor, block_key)
        return self._external_dashboard_payload(block_key, self.worker_service.dashboard_status(block))

    def start_dashboard(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.get_block(actor, block_key)
        return self._external_dashboard_payload(block_key, self.worker_service.start_dashboard(block))

    def stop_dashboard(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.get_block(actor, block_key)
        return self.worker_service.stop_dashboard(block)

    def touch_dashboard(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.get_block(actor, block_key)
        self.worker_service.touch_dashboard(block)
        return {"ok": True}

    def dashboard_proxy_target(self, block_key: str) -> str | None:
        block = self.store.memory.get_memory_block(block_key)
        if block is None or block.get("status") != "active":
            return None
        status = self.worker_service.dashboard_status(block)
        if not status.get("running"):
            return None
        url = status.get("url")
        return str(url) if url else None

    def _resolve_runtime_block(self, actor: str, profile_key: str | None, block_key: str | None) -> dict[str, Any]:
        if block_key:
            block = self._require_block_read(actor, block_key)
            if block is None or block.get("status") != "active":
                return {"status": "not_configured", "block": None}
            return {"status": "ok", "block": block}
        return self.resolve_profile_block(actor, profile_key)

    def _require_profile(self, profile_key: str) -> dict[str, Any]:
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return profile

    def _require_profile_access(
        self, actor: str, profile_key: str, *, write: bool
    ) -> dict[str, Any]:
        profile = self._require_profile(profile_key)
        if self.access is None:
            require_admin_user(actor, self.admins)
        elif write:
            self.access.require_write(actor=actor, scope=ResourceScope.from_record(profile))
        else:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(profile))
        return profile

    def _require_block_read(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.store.memory.get_memory_block(block_key)
        if block is None:
            raise NotFound("memory block not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(block))
        return block

    def _require_block_write(self, actor: str, block_key: str) -> dict[str, Any]:
        block = self.store.memory.get_memory_block(block_key)
        if block is None:
            raise NotFound("memory block not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_write(actor=actor, scope=ResourceScope.from_record(block))
        return block

    def _default_data_dir(self, block_key: str) -> Path:
        return self.paths.data_dir / "claude-mem" / "blocks" / block_key

    def _external_dashboard_payload(self, block_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "url" not in payload:
            return payload
        if not payload.get("url"):
            return {**payload, "url": None}
        return {**payload, "url": f"/memory-dashboard/{block_key}/"}
