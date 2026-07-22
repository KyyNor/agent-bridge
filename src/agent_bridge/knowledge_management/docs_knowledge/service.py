"""文档知识域的后端协调服务。

该服务负责后端注册、远端知识库对齐与可选 Agent 能力的编排；
具体 HTTP/API 实现仍完全位于各 adapter 内。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from agent_bridge.core.config import BackendConfig
from agent_bridge.core.domain import (
    AgentManagementBackend,
    BackendAdapter,
    ManagedResourcesBackend,
    NotFound,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    ValidationError,
    require_admin_user,
)
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry

logger = logging.getLogger(__name__)

SUPPORTED_BACKEND_TYPES = {"mock", "ragflow", "weknora", "pageindex"}


class DocsKnowledgeService:
    """文档知识后端的稳定应用边界。"""

    def __init__(
        self,
        *,
        store: Any,
        admins: set[str],
        registry_provider: Callable[[], BackendRegistry | None],
    ) -> None:
        self.store = store
        self.admins = admins
        self._registry_provider = registry_provider

    @property
    def registry(self) -> BackendRegistry | None:
        return self._registry_provider()

    def get_adapter(self, slug: str) -> BackendAdapter:
        registry = self.registry
        adapter = registry.get(slug) if registry else None
        if adapter is None:
            raise NotFound(f"backend '{slug}' is not configured or unavailable")
        return adapter

    def supports_folders(self, adapter: BackendAdapter) -> bool:
        return bool(adapter.capabilities().supports_folders)

    def provision_kb(self, kb: dict[str, Any]) -> None:
        """为已配置后端建立远端 KB；失败时保留可观测的未完成 target。"""
        registry = self.registry
        if not registry:
            return
        for backend_slug in registry.list_slugs():
            self.store.ensure_backend_target(
                kb["id"], slug=backend_slug, backend_type=backend_slug
            )
            adapter = self.get_adapter(backend_slug)
            try:
                backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
            except Exception as exc:
                logger.warning(
                    "远端知识库创建失败 kb=%s backend=%s 原因=%s",
                    kb["slug"],
                    backend_slug,
                    exc,
                    exc_info=True,
                )
                continue
            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)

    def delete_remote_kbs(self, kb: dict[str, Any]) -> None:
        registry = self.registry
        if not registry:
            return
        for target in self.store.list_backend_targets(kb["id"]):
            backend_kb_id = target.get("backend_kb_id")
            if not backend_kb_id:
                continue
            try:
                self.get_adapter(target["slug"]).delete_kb(backend_kb_id)
            except NotFound as exc:
                logger.warning(
                    "删除知识库时后端不可用 kb=%s backend=%s 原因=%s",
                    kb["slug"],
                    target["slug"],
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "删除知识库时清理远端失败，已继续本地删除 kb=%s backend=%s 原因=%s",
                    kb["slug"],
                    target["slug"],
                    exc,
                    exc_info=True,
                )

    def ensure_managed_resources(self) -> None:
        registry = self.registry
        if not registry:
            return
        for slug in registry.list_slugs():
            adapter = self.get_adapter(slug)
            capabilities = adapter.capabilities()
            if not capabilities.supports_managed_resources:
                continue
            if not isinstance(adapter, ManagedResourcesBackend):
                logger.error(
                    "后端能力声明与实现不一致 backend=%s capability=managed_resources",
                    slug,
                )
                continue
            try:
                result = adapter.ensure_managed_resources()
                logger.info("后端托管资源检查完成 backend=%s result=%s", slug, result)
            except Exception as exc:
                logger.warning(
                    "后端托管资源自愈失败 backend=%s 原因=%s",
                    slug,
                    exc,
                    exc_info=True,
                )

    def _agent_adapter(self, slug: str) -> AgentManagementBackend | None:
        adapter = self.get_adapter(slug)
        if not adapter.capabilities().supports_agents:
            return None
        if not isinstance(adapter, AgentManagementBackend):
            raise RuntimeError(
                f"backend '{slug}' declares agent support but does not implement the protocol"
            )
        return adapter

    def list_backend_agents(self, actor: str, slug: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        adapter = self._agent_adapter(slug)
        if adapter is None:
            return []
        try:
            agents = adapter.list_agents()
        except Exception as exc:
            logger.warning(
                "列出后端 Agent 失败 backend=%s 原因=%s",
                slug,
                exc,
                exc_info=True,
            )
            return []
        return [self._normalize_agent(item) for item in agents if isinstance(item, dict) and item.get("id")]

    def list_backend_agent_types(self, actor: str, slug: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        adapter = self._agent_adapter(slug)
        if adapter is None:
            return []
        try:
            presets = adapter.get_type_presets()
        except Exception as exc:
            logger.warning(
                "列出后端 Agent 类型失败 backend=%s 原因=%s",
                slug,
                exc,
                exc_info=True,
            )
            return []
        return [
            self._normalize_agent_preset(item)
            for item in presets
            if isinstance(item, dict) and item.get("id")
        ]

    def create_backend_agent(
        self, actor: str, slug: str, name: str, preset_id: str
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        adapter = self._agent_adapter(slug)
        if adapter is None:
            raise ValidationError(f"backend '{slug}' does not support agents")
        presets = adapter.get_type_presets()
        preset = next(
            (item for item in presets if isinstance(item, dict) and item.get("id") == preset_id),
            None,
        )
        if preset is None:
            raise NotFound(f"agent type preset '{preset_id}' not found in backend '{slug}'")
        created = adapter.create_agent(name, preset)
        if isinstance(created, dict):
            return self._normalize_agent(created)
        return {
            "agent_id": None,
            "name": name,
            "agent_type": preset_id,
            "is_builtin": False,
        }

    @staticmethod
    def _normalize_agent(agent: dict[str, Any]) -> dict[str, Any]:
        config = agent.get("config") or {}
        return {
            "agent_id": agent.get("id"),
            "name": agent.get("name") or agent.get("id") or "",
            "agent_type": config.get("agent_type") or config.get("system_prompt_id"),
            "is_builtin": bool(agent.get("is_builtin", False)),
        }

    @staticmethod
    def _normalize_agent_preset(preset: dict[str, Any]) -> dict[str, Any]:
        i18n = preset.get("i18n") or {}
        zh_cn = i18n.get("zh-CN") if isinstance(i18n, dict) else {}
        description = zh_cn.get("description") if isinstance(zh_cn, dict) else None
        return {
            "preset_id": preset.get("id"),
            "description": description or preset.get("id") or "",
            "config": preset.get("config") or {},
        }

    def align_backends(self) -> None:
        registry = self.registry
        if not registry:
            return
        configured_slugs = set(registry.list_slugs())
        for kb in self.store.list_kbs():
            existing_targets = self.store.list_backend_targets(kb["id"])
            for target in existing_targets:
                if (
                    target["slug"] not in configured_slugs
                    and target["status"] == "active"
                ):
                    self.store.set_backend_target_status(kb["id"], target["slug"], "inactive")

            for backend_slug in configured_slugs:
                adapter = self.get_adapter(backend_slug)
                target = next(
                    (item for item in existing_targets if item["slug"] == backend_slug),
                    None,
                )
                if target is None:
                    self.store.ensure_backend_target(
                        kb["id"], slug=backend_slug, backend_type=backend_slug
                    )
                    self._repair_backend_kb(kb, backend_slug, adapter)
                else:
                    if not target.get("backend_kb_id"):
                        self._repair_backend_kb(kb, backend_slug, adapter)
                    if target["status"] == "inactive":
                        self.store.set_backend_target_status(kb["id"], backend_slug, "active")
                self._backfill_missing_backend_jobs(kb, backend_slug)

    def _repair_backend_kb(
        self, kb: dict[str, Any], backend_slug: str, adapter: BackendAdapter
    ) -> None:
        try:
            backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
        except Exception as exc:
            logger.warning(
                "后端知识库对齐失败 kb=%s backend=%s 原因=%s",
                kb["slug"],
                backend_slug,
                exc,
                exc_info=True,
            )

    def _backfill_missing_backend_jobs(self, kb: dict[str, Any], backend_slug: str) -> None:
        runnable_statuses = {
            SyncJobStatus.pending.value,
            SyncJobStatus.running.value,
            SyncJobStatus.failed.value,
        }
        runnable_doc_ids = {
            job["doc_id"]
            for job in self.store.list_all_jobs(backend_slug=backend_slug)
            if job["kb_id"] == kb["id"]
            and job["operation"] == Operation.create.value
            and job["status"] in runnable_statuses
        }
        for doc_id in self.store.list_synced_doc_ids(kb["id"]):
            sync_state = self.store.get_sync_state(doc_id, kb["id"], backend_slug)
            if sync_state and sync_state["status"] == SyncStateStatus.synced.value:
                continue
            if doc_id in runnable_doc_ids:
                continue
            versions = self.store.list_versions(doc_id)
            version_id = versions[-1]["id"] if versions else None
            self.store.create_sync_job(
                doc_id,
                kb["id"],
                Operation.create,
                version_id,
                backend_slug=backend_slug,
            )

    def list_backends(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        rows = self.store.list_backends()
        registry = self.registry
        for row in rows:
            row["api_key_set"] = bool(row.get("api_key"))
            row.pop("api_key", None)
            row["runtime_status"] = (
                "active"
                if registry and row["slug"] in registry.backends
                else "inactive"
            )
        return rows

    def add_backend(
        self,
        actor: str,
        slug: str,
        backend_type: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 120,
        embedding_model_id: str | None = None,
        summary_model_id: str | None = None,
        rerank_model_id: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if backend_type not in SUPPORTED_BACKEND_TYPES:
            raise ValidationError(f"unsupported backend type: {backend_type}")
        row = self.store.upsert_backend(
            slug=slug,
            backend_type=backend_type,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            embedding_model_id=embedding_model_id,
            summary_model_id=summary_model_id,
            rerank_model_id=rerank_model_id,
        )
        config = BackendConfig(
            slug=slug,
            backend_type=backend_type,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            embedding_model_id=embedding_model_id,
            summary_model_id=summary_model_id,
            rerank_model_id=rerank_model_id,
        )
        registry = self.registry
        if registry:
            registry.add_backend(config)
            self.align_backends()
        return self._public_backend_row(row, api_key=api_key, active=registry is not None)

    def update_backend(
        self,
        actor: str,
        slug: str,
        backend_type: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
        embedding_model_id: str | None = None,
        summary_model_id: str | None = None,
        rerank_model_id: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        existing = self.store.get_backend(slug)
        if existing is None:
            raise NotFound(f"backend '{slug}' not found")
        resolved_type = backend_type or existing["backend_type"]
        if resolved_type not in SUPPORTED_BACKEND_TYPES:
            raise ValidationError(f"unsupported backend type: {resolved_type}")
        resolved_key = existing.get("api_key") if api_key is None else (api_key or None)
        row = self.store.upsert_backend(
            slug=slug,
            backend_type=resolved_type,
            base_url=base_url if base_url is not None else existing.get("base_url"),
            api_key=resolved_key,
            timeout=timeout if timeout is not None else existing.get("timeout", 120),
            embedding_model_id=(
                embedding_model_id
                if embedding_model_id is not None
                else existing.get("embedding_model_id")
            ),
            summary_model_id=(
                summary_model_id
                if summary_model_id is not None
                else existing.get("summary_model_id")
            ),
            rerank_model_id=(
                rerank_model_id
                if rerank_model_id is not None
                else existing.get("rerank_model_id")
            ),
        )
        config = BackendConfig(
            slug=slug,
            backend_type=resolved_type,
            base_url=row.get("base_url"),
            api_key=resolved_key,
            timeout=row.get("timeout", 120),
            embedding_model_id=row.get("embedding_model_id"),
            summary_model_id=row.get("summary_model_id"),
            rerank_model_id=row.get("rerank_model_id"),
        )
        registry = self.registry
        if registry:
            registry.update_backend(config)
            self.align_backends()
        return self._public_backend_row(
            row, api_key=resolved_key, active=registry is not None
        )

    def remove_backend(self, actor: str, slug: str) -> dict[str, str]:
        require_admin_user(actor, self.admins)
        if self.store.get_backend(slug) is None:
            raise NotFound(f"backend '{slug}' not found")
        self.store.delete_backend(slug)
        registry = self.registry
        if registry:
            registry.remove_backend(slug)
        for kb in self.store.list_kbs():
            self.store.set_backend_target_status(kb["id"], slug, "inactive")
        return {"slug": slug, "status": "removed"}

    @staticmethod
    def _public_backend_row(
        row: dict[str, Any], *, api_key: str | None, active: bool
    ) -> dict[str, Any]:
        row["api_key_set"] = bool(api_key)
        row.pop("api_key", None)
        row["runtime_status"] = "active" if active else "inactive"
        return row
