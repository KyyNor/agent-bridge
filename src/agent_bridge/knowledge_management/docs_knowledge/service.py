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
    ValidationError,
    require_admin_user,
)
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry

from agent_bridge.knowledge_management.docs_knowledge.sync_policy import (
    SyncPolicy,
    default_retrieval_backend,
    resolve_sync_policy,
    target_sync_config,
)

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

    def save_sync_policies(
        self,
        kb: dict[str, Any],
        sync_on_upload: bool | None,
        legacy_policies: dict[str, bool],
        policies: dict[str, SyncPolicy],
    ) -> None:
        targets = self.store.list_backend_targets(kb["id"])
        target_slugs = {target["slug"] for target in targets}
        unknown = (legacy_policies.keys() | policies.keys()) - target_slugs
        if unknown:
            raise ValidationError(f"未知知识后端: {', '.join(sorted(unknown))}")
        if sync_on_upload is None and not legacy_policies and not policies:
            raise ValidationError("至少提供一个同步策略")

        retrieval_backend = default_retrieval_backend(
            kb, [target["slug"] for target in targets if target["status"] == "active"]
        )
        effective = {}
        for target in targets:
            slug = target["slug"]
            if slug in policies:
                policy = policies[slug]
                self.store.update_backend_target_config(
                    kb["id"], slug, {"sync_policy": policy, "sync_on_upload": policy == "immediate"}
                )
            elif (enabled := legacy_policies.get(slug, sync_on_upload)) is not None:
                policy = "immediate" if enabled else "scheduled"
                config = {"sync_on_upload": enabled}
                if "sync_policy" in target_sync_config(target):
                    config["sync_policy"] = policy
                self.store.update_backend_target_config(kb["id"], slug, config)
            else:
                policy = resolve_sync_policy(kb, target, retrieval_backend)
            effective[slug] = policy

        # 旧布尔摘要变化时，固定存量后端的继承值，避免修改一个后端影响其他后端。
        immediate = "immediate" in effective.values() if targets else bool(sync_on_upload)
        if not kb.get("sync_follows_default") and immediate != bool(kb.get("sync_on_upload")):
            for target in targets:
                slug = target["slug"]
                if slug not in policies and slug not in legacy_policies and sync_on_upload is None:
                    if "sync_on_upload" not in target_sync_config(target):
                        self.store.update_backend_target_config(
                            kb["id"], slug, {"sync_on_upload": effective[slug] == "immediate"}
                        )
        self.store.update_kb_sync_policy(
            kb["id"], immediate, sync_follows_default=None if targets else False
        )

    def align_backends(
        self, kb_id: int | None = None, backend: str | None = None
    ) -> list[tuple[int, str]]:
        """对齐本轮允许同步的目标，并返回供队列领取使用的范围。"""
        registry = self.registry
        configured_slugs = sorted(registry.list_slugs()) if registry else []
        if not configured_slugs:
            return []
        if backend is not None and backend not in configured_slugs:
            raise NotFound(f"知识后端 {backend!r} 未配置或不可用")

        selected_slugs = [backend] if backend is not None else configured_slugs
        configured_set = set(configured_slugs)
        knowledge_bases = self.store.list_kbs() if kb_id is None else [self.store.get_kb_by_id(kb_id)]
        eligible = []
        for kb in knowledge_bases:
            existing_targets = {target["slug"]: target for target in self.store.list_backend_targets(kb["id"])}
            if backend is None:
                for slug in existing_targets.keys() - configured_set:
                    if existing_targets[slug]["status"] == "active":
                        self.store.set_backend_target_status(kb["id"], slug, "inactive")

            retrieval_backend = default_retrieval_backend(kb, configured_slugs)
            for slug in selected_slugs:
                target = existing_targets.get(slug)
                if target is None:
                    config = self.store.get_backend(slug)
                    self.store.ensure_backend_target(
                        kb["id"], slug=slug, backend_type=str((config or {}).get("backend_type") or slug)
                    )
                    target = {"slug": slug, "status": "active"}
                if target["status"] == "inactive":
                    self.store.set_backend_target_status(kb["id"], slug, "active")
                if resolve_sync_policy(kb, target, retrieval_backend) == "paused":
                    continue
                if not target.get("backend_kb_id"):
                    self._repair_backend_kb(kb, slug, self.get_adapter(slug))
                self.store.backfill_backend_sync_jobs(kb["id"], slug)
                eligible.append((kb["id"], slug))
        return eligible

    def _repair_backend_kb(
        self, kb: dict[str, Any], backend_slug: str, adapter: BackendAdapter
    ) -> None:
        try:
            backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
        except Exception as exc:
            self.store.mark_backend_target_error(kb["id"], backend_slug, str(exc))
            logger.warning(
                "后端知识库对齐失败，将在下次同步重试 kb=%s backend=%s 原因=%s",
                kb["slug"],
                backend_slug,
                exc,
                exc_info=True,
            )

    def list_backends(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        rows = self.store.list_backends()
        registry = self.registry
        for row in rows:
            row["edit_token"] = attach_edit_token(
                {},
                self._backend_edit_snapshot(row),
            )["edit_token"]
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
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        current = self.store.get_backend(slug)
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._backend_edit_snapshot(current) if current else None,
            resource_type="knowledge_backend",
            resource_key=slug,
            actor=actor,
        )
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
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        existing = self.store.get_backend(slug)
        if existing is None:
            raise NotFound(f"backend '{slug}' not found")
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._backend_edit_snapshot(existing),
            resource_type="knowledge_backend",
            resource_key=slug,
            actor=actor,
        )
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
        row = attach_edit_token(row, DocsKnowledgeService._backend_edit_snapshot(row))
        row["api_key_set"] = bool(api_key)
        row.pop("api_key", None)
        row["runtime_status"] = "active" if active else "inactive"
        return row

    @staticmethod
    def _backend_edit_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "slug",
                "backend_type",
                "base_url",
                "api_key",
                "timeout",
                "embedding_model_id",
                "summary_model_id",
                "rerank_model_id",
            )
        }
