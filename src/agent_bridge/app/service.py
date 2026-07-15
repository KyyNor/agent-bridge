"""Application services for Agent Bridge."""

from __future__ import annotations

import logging
import mimetypes
from tempfile import TemporaryDirectory
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any, Callable

from agent_bridge.agent_runtime.service import AgentService
from agent_bridge.app.document_paths import (
    join_backend_path,
    normalize_relative_document_path,
    split_document_path,
)
from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
from agent_bridge.knowledge_management.docs_knowledge.uploads import extract_zip_documents
from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.knowledge_management.code_knowledge.scheduler import CodeGraphScheduler
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.knowledge_management.code_knowledge.understand_scheduler import UnderstandingScheduler
from agent_bridge.knowledge_management.docs_knowledge.doc_sync_scheduler import DocSyncScheduler
from agent_bridge.core.config import AgentBridgePaths, BackendConfig, ensure_directories, migrate_toml_backends_to_db
from agent_bridge.capability_hub.models import ProfileResourceType
from agent_bridge.core.domain import (
    AccessDenied,
    AskResult,
    NotFound,
    Operation,
    RetrievalResult,
    RetrievalStrategy,
    SyncJobStatus,
    SyncStateStatus,
    ValidationError,
    require_admin_user,
)
from agent_bridge.knowledge_management.docs_knowledge.backends.mock import MockBackend
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry, create_registry_from_db
from agent_bridge.knowledge_management.docs_knowledge.backends.weknora import WeknoraBackend
from agent_bridge.knowledge_management.memory.service import MemoryService
from agent_bridge.core.slug import make_slug, unique_slug
from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.scripts.service import ScriptService
from agent_bridge.system_config.skills.service import SkillService
from agent_bridge.system_config.plugin_update_scheduler import PluginUpdateScheduler
from agent_bridge.automation.workflows.scheduler import WorkflowScheduler
from agent_bridge.automation.workflows.service import WorkflowService


ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".markdown", ".csv", ".json",
}
UPLOAD_EXTENSIONS = ALLOWED_EXTENSIONS | {".zip"}
SUPPORTED_BACKEND_TYPES = {"mock", "ragflow", "weknora", "pageindex"}
_UNSET = object()


class AgentBridgeService:
    def __init__(
        self,
        paths: AgentBridgePaths,
        store: SQLiteStore,
        archive: ArchiveStorage,
        mock_backend: MockBackend,
        admins: set[str],
    ) -> None:
        self.paths = paths
        self.store = store
        self.archive = archive
        self.mock_backend = mock_backend
        self.admins = admins
        self.registry: BackendRegistry | None = None
        self.governance = CapabilityGovernanceService(store=store, admins=admins)
        self.capabilities = CapabilityService(store=store, admins=admins, governance=self.governance)
        self.agents = AgentService(paths=paths, store=store, admins=admins, governance=self.governance)
        self.codegraph = CodeGraphService(paths=paths, store=store, admins=admins, agent_service=self.agents)
        self.codegraph_scheduler = CodeGraphScheduler(service=self.codegraph, store=store, admins=admins)
        self.understand_scheduler = UnderstandingScheduler(service=self.codegraph, store=store, admins=admins)
        self.doc_sync_scheduler = DocSyncScheduler(service=self, store=store, admins=admins)
        self.workflows = WorkflowService(store=store, admins=admins)
        self.skills = SkillService(store=store, admins=admins)
        # The workflow service generates HTML reports for summary runs, which
        # requires driving an agent run and reading the design skill. Wire
        # those collaborators now that both services exist.
        self.workflows.agent_service = self.agents
        self.workflows.skills = self.skills
        self.scripts = ScriptService(paths=paths, store=store, admins=admins)
        self.memory = MemoryService(paths=paths, store=store, admins=admins, governance_service=self.governance)
        self.plugin_update_scheduler = PluginUpdateScheduler(service=self, store=store, admins=admins)
        self.workflow_scheduler = WorkflowScheduler(
            service=self.workflows,
            store=store,
            admins=admins,
            agent_service=self.agents,
            base_run_dir=paths.run_dir / "workflow-runs",
        )
        from agent_bridge.capability_hub.sources.builtin.codegraph import CodeGraphBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.memory import MemoryBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.platform import PlatformBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.wiki import WikiBuiltinProvider

        self.capabilities.register_builtin_provider(PlatformBuiltinProvider(self))
        self.capabilities.register_builtin_provider(WikiBuiltinProvider(self))
        self.capabilities.register_builtin_provider(CodeGraphBuiltinProvider(self.codegraph, self.governance))
        self.capabilities.register_builtin_provider(MemoryBuiltinProvider(self))

    @classmethod
    def create(cls, paths: AgentBridgePaths, admins: set[str]) -> "AgentBridgeService":
        """工厂入口：装配全部子服务，恢复中断的 CodeGraph 同步，迁移并重建后端 registry。"""
        logger.info("AgentBridgeService 开始装配 root=%s admins=%s", paths.root, sorted(admins))
        service = cls(
            paths=paths,
            store=SQLiteStore(paths.db_path),
            archive=ArchiveStorage(paths.archive_dir),
            mock_backend=MockBackend(paths.mock_backend_dir),
            admins=admins,
        )
        service.store.init_schema()
        recovered = service.codegraph.recover_interrupted_sync_runs()
        if recovered:
            logger.warning("Recovered %s stale CodeGraph sync run(s) left running by a prior process", recovered)
        migrate_toml_backends_to_db(paths, service.store)
        service.registry = create_registry_from_db(paths, service.store)
        logger.info(
            "AgentBridgeService 装配完成 子服务=governance/capabilities/agents/codegraph/"
            "memory/workflows/skills/scripts 后端数=%d",
            len(service.registry.list_slugs()) if service.registry else 0,
        )
        return service

    def init_system(self) -> None:
        ensure_directories(self.paths)
        self.store.init_schema()

    def ensure_weknora_agents(self) -> None:
        if not self.registry:
            return
        for slug in self.registry.list_slugs():
            adapter = self.registry.get(slug)
            if isinstance(adapter, WeknoraBackend):
                try:
                    adapter.ensure_hybrid_agent()
                except Exception:
                    logger.warning("确保后端 '%s' 混合智能体失败", slug, exc_info=True)

    def ensure_managed_plugins(self) -> dict[str, Any]:
        """按 sync_config 拉取/更新 understand-anything 与 claude-mem 两个托管插件仓库。"""
        config = self.store.get_sync_config()
        results: dict[str, Any] = {}
        ua_git_url = str(config.get("ua_git_url") or "").strip()
        if ua_git_url:
            logger.info("understand-anything 插件开始拉取 url=%s", ua_git_url)
            try:
                results["understand-anything"] = self.codegraph.ensure_understand_plugin(ua_git_url)
                logger.info("understand-anything 插件拉取完成 result=%s", results["understand-anything"])
            except Exception:
                logger.error("understand-anything 插件拉取失败 url=%s", ua_git_url, exc_info=True)
                raise
        claude_mem_git_url = str(config.get("claude_mem_git_url") or "").strip()
        if claude_mem_git_url:
            logger.info("claude-mem 插件开始拉取 url=%s", claude_mem_git_url)
            try:
                results["claude-mem"] = self.memory.worker_service.ensure_plugin(claude_mem_git_url)
                logger.info("claude-mem 插件拉取完成 result=%s", results["claude-mem"])
            except Exception:
                logger.error("claude-mem 插件拉取失败 url=%s", claude_mem_git_url, exc_info=True)
                raise
        return results

    def update_understand_plugin(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        git_url = str(self.store.get_sync_config().get("ua_git_url") or "").strip()
        return self.codegraph.ensure_understand_plugin(git_url)

    def update_claude_mem_plugin(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        git_url = str(self.store.get_sync_config().get("claude_mem_git_url") or "").strip()
        return self.memory.worker_service.ensure_plugin(git_url)

    def create_kb(self, actor: str, slug: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.create_kb(slug=slug, name=name, description=description, created_by=actor)
        if self.registry:
            for backend_slug in self.registry.list_slugs():
                adapter = self.registry.get(backend_slug)
                if adapter is not None:
                    try:
                        backend_kb_id = adapter.create_kb(slug, name)
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                        self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                    except Exception:
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
        return kb

    def delete_kb(self, actor: str, kb_slug: str) -> dict[str, Any]:
        """硬删除一个知识库。

        前置校验：该 KB 下不得仍有活动文档（用户须先逐个删除文档）。
        副作用清理：通知各检索后端删除远端 KB（容错）、清理能力平面里引用该
        KB 的 resource 规则（无外键，需手动删）。最后删除 KB 行，依赖外键
        ON DELETE CASCADE 清除 members / document_kbs / backend_targets /
        sync_jobs / sync_states。
        """
        require_admin_user(actor, self.admins)
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound(f"knowledge base '{kb_slug}' not found")
        kb_id = kb["id"]

        active_docs = self.store.list_docs_for_kb(kb_id)
        if active_docs:
            raise ValidationError(
                f"请先删除该知识库下的所有文档（仍有 {len(active_docs)} 篇）"
            )

        # 远端检索后端清理：逐个通知后端删除其上镜像的 KB（容错，失败不阻断删除）
        if self.registry:
            for target in self.store.list_backend_targets(kb_id):
                backend_kb_id = target.get("backend_kb_id")
                if not backend_kb_id:
                    continue
                adapter = self.registry.get(target["slug"])
                if adapter is None:
                    continue
                try:
                    adapter.delete_kb(backend_kb_id)
                except Exception:
                    logger.warning(
                        "删除知识库 %s 时清理远端后端 %s 失败，已忽略", kb_slug, target["slug"],
                        exc_info=True,
                    )

        # 治理软关联清理（无外键）：移除能力平面里引用该 KB 的 resource 规则
        self.store.delete_resource_rules_by_key(
            resource_type=ProfileResourceType.wiki_kb.value, resource_key=kb_slug
        )

        self.store.delete_kb(kb_id)
        logger.info("已删除文档知识库 %s", kb_slug)
        return {"slug": kb_slug, "deleted": True}

    def grant_kb_member(self, actor: str, kb_slug: str, linux_user: str, role: Any) -> dict[str, str]:
        raise ValidationError("knowledge base member roles are no longer supported; use capability profiles")

    def list_kbs(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_kbs()

    def list_kb_status_summaries(self, actor: str) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for kb in self.list_kbs(actor):
            targets = self.store.list_backend_targets(kb["id"])
            docs = self.store.list_docs_for_kb(kb["id"])
            summaries.append(
                {
                    **kb,
                    "backend_targets": targets,
                    "document_count": len(docs),
                    "sync_failed_count": len(
                        [doc for doc in docs if doc.get("sync_status") == SyncStateStatus.sync_failed.value]
                    ),
                }
            )
        return summaries

    def list_kb_members(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        if self.store.get_kb_by_slug(kb_slug) is None:
            raise NotFound("knowledge base not found")
        return []

    # -- Knowledge-base folders and document placements --

    def list_folders(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.list_folder_tree(kb["id"])

    def list_archive_entries(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.list_archive_entries(kb["id"])

    def browse_kb(
        self,
        actor: str,
        kb_slug: str,
        *,
        folder_id: int | None = None,
        archive_entry_id: int | None = None,
    ) -> dict[str, Any]:
        if folder_id is not None and archive_entry_id is not None:
            raise ValidationError("folder_id and archive_entry_id cannot be used together")

        kb = self._require_kb_admin_visible(actor, kb_slug)
        if archive_entry_id is not None:
            return self._browse_archive_context(kb, archive_entry_id)
        return self._browse_folder_context(kb, folder_id)

    @staticmethod
    def _browse_folder_context_payload(folder: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "folder",
            "id": int(folder["id"]),
            "name": folder["name"],
            "relative_path": folder.get("path") or "",
            "parent_id": folder.get("parent_id"),
            "parent_folder_id": None,
            "archive_entry_id": None,
        }

    @staticmethod
    def _browse_archive_context_payload(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": entry["kind"],
            "id": int(entry["id"]),
            "name": entry["name"],
            "relative_path": entry["relative_path"],
            "parent_id": entry.get("parent_id"),
            "parent_folder_id": entry.get("parent_folder_id"),
            "archive_entry_id": int(entry["id"]),
        }

    def _browse_folder_context(
        self,
        kb: dict[str, Any],
        folder_id: int | None,
    ) -> dict[str, Any]:
        kb_id = int(kb["id"])
        folder = (
            self.store.get_root_folder(kb_id)
            if folder_id is None
            else self.store.get_folder(kb_id, folder_id)
        )
        if folder is None:
            raise NotFound("folder not found")

        context = self._browse_folder_context_payload(folder)
        parent = None
        if folder.get("parent_id") is not None:
            parent_folder = self.store.get_folder(kb_id, int(folder["parent_id"]))
            if parent_folder is not None:
                parent = self._browse_folder_context_payload(parent_folder)

        folder_tree = self.store.list_folder_tree(kb_id)
        direct_folders = [
            item for item in folder_tree if item.get("parent_id") == folder["id"]
        ]
        archive_entries = self.store.list_archive_entries(
            kb_id,
            parent_folder_id=int(folder["id"]),
        )
        documents = [
            item
            for item in self.store.list_docs_for_kb(kb_id, folder_id=int(folder["id"]))
            if item.get("archive_entry_id") is None
        ]

        folder_counts = {
            int(item["id"]): sum(
                1 for child in folder_tree if child.get("parent_id") == item["id"]
            )
            for item in direct_folders
        }
        entries: list[dict[str, Any]] = [
            {
                "kind": "folder",
                "id": int(item["id"]),
                "name": item["name"],
                "relative_path": item.get("path") or "",
                "parent_id": int(folder["id"]),
                "parent_folder_id": None,
                "child_count": folder_counts[int(item["id"])],
            }
            for item in direct_folders
        ]
        entries.extend(
            {
                "kind": "zip",
                "id": int(item["id"]),
                "name": item["name"],
                "relative_path": item["relative_path"],
                "parent_id": item.get("parent_id"),
                "parent_folder_id": int(folder["id"]),
                "archive_entry_id": int(item["id"]),
                "child_count": len(
                    self.store.list_archive_entries(kb_id, parent_id=int(item["id"]))
                ),
            }
            for item in archive_entries
        )
        entries.extend(
            self._browse_document_entry(
                item,
                parent_id=int(folder["id"]),
                parent_folder_id=None,
            )
            for item in documents
        )
        entries.sort(key=lambda item: (self._browse_entry_order(item["kind"]), item["name"].lower(), item["id"]))
        return {"context": context, "parent": parent, "entries": entries}

    def _browse_archive_context(
        self,
        kb: dict[str, Any],
        archive_entry_id: int,
    ) -> dict[str, Any]:
        kb_id = int(kb["id"])
        entry = self.store.get_archive_entry(kb_id, archive_entry_id)
        if entry is None:
            raise NotFound("archive entry not found")
        if entry["kind"] not in {"zip", "folder"}:
            raise ValidationError("archive entry is not a container")

        context = self._browse_archive_context_payload(entry)
        parent = None
        if entry.get("parent_id") is not None:
            parent_entry = self.store.get_archive_entry(kb_id, int(entry["parent_id"]))
            if parent_entry is not None:
                parent = self._browse_archive_context_payload(parent_entry)
        elif entry.get("parent_folder_id") is not None:
            parent_folder = self.store.get_folder(kb_id, int(entry["parent_folder_id"]))
            if parent_folder is not None:
                parent = self._browse_folder_context_payload(parent_folder)

        children = self.store.list_archive_entries(
            kb_id,
            parent_id=int(entry["id"]),
        )
        document_rows = {
            int(item["id"]): item
            for item in self.store.list_docs_for_kb(kb_id)
            if item.get("archive_entry_id") is not None
        }
        entries: list[dict[str, Any]] = []
        for child in children:
            if child["kind"] in {"zip", "folder"}:
                entries.append(
                    {
                        "kind": child["kind"],
                        "id": int(child["id"]),
                        "name": child["name"],
                        "relative_path": child["relative_path"],
                        "parent_id": int(entry["id"]),
                        "parent_folder_id": None,
                        "archive_entry_id": int(child["id"]),
                        "child_count": len(
                            self.store.list_archive_entries(
                                kb_id,
                                parent_id=int(child["id"]),
                            )
                        ),
                    }
                )
                continue
            document = document_rows.get(int(child["doc_id"])) if child.get("doc_id") is not None else None
            if document is not None:
                entries.append(
                    self._browse_document_entry(
                        document,
                        parent_id=int(entry["id"]),
                        parent_folder_id=None,
                        archive_entry=child,
                    )
                )
        entries.sort(key=lambda item: (self._browse_entry_order(item["kind"]), item["name"].lower(), item["id"]))
        return {"context": context, "parent": parent, "entries": entries}

    @staticmethod
    def _browse_entry_order(kind: str) -> int:
        return {"folder": 0, "zip": 1, "document": 2}.get(kind, 3)

    def _browse_document_entry(
        self,
        document: dict[str, Any],
        *,
        parent_id: int | None,
        parent_folder_id: int | None,
        archive_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        doc_id = int(document["id"])
        full_document = self.store.get_document_by_id(doc_id) or document
        versions = self.store.list_versions(doc_id)
        current_version_id = full_document.get("current_version_id")
        version = next(
            (item for item in versions if item.get("id") == current_version_id),
            None,
        ) or (versions[-1] if versions else {})
        original_filename = version.get("original_filename") or document.get("slug", "")
        return {
            "kind": "document",
            "id": doc_id,
            "doc_id": doc_id,
            "name": Path(original_filename).name,
            "relative_path": (
                archive_entry["relative_path"]
                if archive_entry is not None
                else original_filename
            ),
            "parent_id": parent_id,
            "parent_folder_id": parent_folder_id,
            "slug": document["slug"],
            "title": document["title"],
            "original_filename": original_filename,
            "version": int(version.get("version_no") or document.get("current_version_no") or 0),
            "version_no": int(version.get("version_no") or document.get("current_version_no") or 0),
            "sync_status": document.get("sync_status") or SyncStateStatus.not_synced.value,
            "archive_entry_id": (
                int(archive_entry["id"])
                if archive_entry is not None
                else document.get("archive_entry_id")
            ),
            "status": document.get("status", "active"),
        }

    def create_folder(
        self,
        actor: str,
        kb_slug: str,
        name: str,
        parent_folder_id: int | None = None,
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.create_folder(kb["id"], parent_folder_id, name)

    def update_folder(
        self,
        actor: str,
        kb_slug: str,
        folder_id: int,
        *,
        name: str | None = None,
        parent_folder_id: int | None | object = _UNSET,
        parent_provided: bool | None = None,
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        if parent_provided is None:
            parent_provided = parent_folder_id is not _UNSET
        if name is None and not parent_provided:
            raise ValidationError("at least one folder field must be provided")

        parent_id = None if parent_folder_id is _UNSET else parent_folder_id
        previous = self.store.get_folder(kb["id"], folder_id)
        documents = self._documents_in_folder_subtree(kb["id"], folder_id) if previous else []
        updated = self.store.update_folder(
            kb["id"],
            folder_id,
            name=name,
            parent_id=parent_id,  # type: ignore[arg-type]
            parent_provided=bool(parent_provided),
        )
        if previous and previous.get("path") != updated.get("path"):
            for document in documents:
                self._queue_placement_sync_jobs(document, kb["id"])
        return updated

    def _folder_delete_preview(self, kb_id: int, folder_id: int) -> dict[str, Any]:
        folder = self.store.get_folder(kb_id, folder_id)
        if folder is None:
            raise NotFound("folder not found")
        if folder["is_root"]:
            raise ValidationError("root folder cannot be deleted")
        counts = self.store.get_subtree_counts(kb_id, folder_id)
        return {
            "requires_confirmation": True,
            "folder_id": folder_id,
            "directory_count": counts["directory_count"],
            "file_count": counts["file_count"],
            "folder_count": counts["folder_count"],
        }

    def _documents_in_folder_subtree(self, kb_id: int, folder_id: int) -> list[dict[str, Any]]:
        documents: dict[int, dict[str, Any]] = {}
        for subtree_folder_id in self.store.get_subtree_ids(kb_id, folder_id):
            for document in self.store.list_docs_for_kb(kb_id, folder_id=subtree_folder_id):
                full_document = self.store.get_document_by_slug(document["slug"])
                documents[int(document["id"])] = full_document or document
        return list(documents.values())

    def _queue_scoped_document_delete(self, doc: dict[str, Any], kb_id: int) -> None:
        for target in self.store.list_backend_targets(kb_id):
            if target["status"] != "active":
                continue
            compacted = self.store.cancel_runnable_create_update_jobs(
                doc["id"], kb_id, target["slug"]
            )
            sync_state = self.store.get_sync_state(doc["id"], kb_id, target["slug"])
            remote_exists = bool(sync_state and sync_state.get("backend_doc_id"))
            if remote_exists or compacted["running"] > 0:
                self.store.create_sync_job(
                    doc["id"], kb_id, Operation.delete, doc.get("current_version_id"),
                    backend_slug=target["slug"],
                )

    def _remove_document_from_kb_by_id(
        self,
        doc: dict[str, Any],
        kb: dict[str, Any],
        *,
        actor: str | None = None,
        later: bool = True,
    ) -> dict[str, str]:
        placement = self.store.get_document_placement(doc["id"], kb["id"])
        if placement is None:
            raise NotFound("document knowledge-base association not found")
        self._queue_scoped_document_delete(doc, kb["id"])
        if not self.store.remove_document_from_kb(doc["id"], kb["id"]):
            raise NotFound("document knowledge-base association not found")

        active_associations = [
            item
            for item in self.store.get_document_kbs(doc["id"])
            if item.get("document_kb_status") == "active"
        ]
        if not active_associations:
            self.store.soft_delete_document(doc["id"])
        if not later:
            self.sync(actor=actor or doc.get("owner_user") or "", all_users=False)
        return {"slug": doc["slug"], "status": "deleted", "kb": kb["slug"]}

    def remove_document_from_kb(
        self,
        actor: str,
        kb_slug: str,
        doc_slug: str,
        *,
        later: bool = True,
    ) -> dict[str, str]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        doc = self._require_doc_admin_visible(actor, doc_slug)
        return self._remove_document_from_kb_by_id(doc, kb, actor=actor, later=later)

    def delete_folder(
        self,
        actor: str,
        kb_slug: str,
        folder_id: int,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        if not confirm:
            return self._folder_delete_preview(kb["id"], folder_id)
        deleted = self.store.delete_folder_subtree_atomic(kb["id"], folder_id)
        return {"folder_id": folder_id, "deleted": True, **deleted}

    def _queue_placement_sync_jobs(self, doc: dict[str, Any], kb_id: int) -> None:
        for target in self.store.list_backend_targets(kb_id):
            if target["status"] != "active":
                continue
            adapter = self._get_adapter(target["slug"])
            supports_folders = self._backend_supports_folders(adapter)
            if not supports_folders:
                continue
            compacted = self.store.cancel_runnable_create_update_jobs(
                doc["id"], kb_id, target["slug"]
            )
            sync_state = self.store.get_sync_state(doc["id"], kb_id, target["slug"])
            remote_exists = bool(sync_state and sync_state.get("backend_doc_id"))
            if remote_exists:
                if compacted["running"] == 0:
                    self.store.create_sync_job(
                        doc["id"], kb_id, Operation.move, doc.get("current_version_id"),
                        backend_slug=target["slug"],
                    )
            else:
                # A pending create is replaced so the next task observes the
                # latest placement. This also covers a placement change made
                # after an unsynced upload was queued.
                if compacted["running"] == 0:
                    self.store.create_sync_job(
                        doc["id"], kb_id, Operation.create, doc.get("current_version_id"),
                        backend_slug=target["slug"],
                    )

    def place_document(
        self,
        actor: str,
        doc_slug: str,
        kb_slug: str,
        folder_id: int,
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        doc = self._require_doc_admin_visible(actor, doc_slug)
        if self.store.get_folder(kb["id"], folder_id) is None:
            raise NotFound("folder not found")
        current_placement = self.store.get_document_placement(doc["id"], kb["id"])
        if current_placement is None:
            raise NotFound("document knowledge-base association not found")
        if current_placement["folder_id"] == folder_id:
            return {**current_placement, "slug": doc_slug, "kb": kb_slug}
        placement = self.store.update_document_placement(doc["id"], kb["id"], folder_id)
        self._queue_placement_sync_jobs(doc, kb["id"])
        return {**placement, "slug": doc_slug, "kb": kb_slug}

    def attach_document(
        self,
        actor: str,
        doc_slug: str,
        kb_slug: str,
        folder_id: int,
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        doc = self._require_doc_admin_visible(actor, doc_slug)
        if self.store.get_folder(kb["id"], folder_id) is None:
            raise NotFound("folder not found")
        existing = self.store.get_document_placement(doc["id"], kb["id"])
        if existing is not None:
            if existing["folder_id"] == folder_id:
                return {**existing, "slug": doc_slug, "kb": kb_slug}
            return self.place_document(actor, doc_slug, kb_slug, folder_id)

        self.store.attach_document_to_kb(doc["id"], kb["id"], actor, folder_id=folder_id)
        self._queue_create_sync_jobs(doc["id"], doc.get("current_version_id"), [kb])
        placement = self.store.get_document_placement(doc["id"], kb["id"])
        if placement is None:
            raise NotFound("document knowledge-base association not found")
        return {**placement, "slug": doc_slug, "kb": kb_slug}

    def add_document(
        self,
        actor: str,
        source: Path,
        kb_slugs: list[str],
        later: bool,
        original_filename: str | None = None,
        source_type: str = "manual",
        source_repo_key: str = "",
        slug_override: str | None = None,
        folder_id: int | None = None,
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        if not kb_slugs:
            raise ValidationError("at least one knowledge base is required")
        require_admin_user(actor, self.admins)
        self._validate_source(source, allowed_extensions=UPLOAD_EXTENSIONS)
        if folder_id is not None and len(kb_slugs) != 1:
            raise ValidationError("folder_id can only be used with one knowledge base")
        kbs = [self._require_kb_admin_visible(actor, kb_slug) for kb_slug in kb_slugs]
        if folder_id is not None and self.store.get_folder(kbs[0]["id"], folder_id) is None:
            raise NotFound("folder not found")

        display_name = original_filename or source.name
        if source.suffix.lower() == ".zip":
            return self._add_zip_documents(
                actor, source, display_name, kbs, later, source_type, source_repo_key,
                folder_id=folder_id, relative_path=relative_path,
            )
        return self._add_single_document(
            actor=actor,
            source=source,
            kb_targets=kbs,
            display_name=display_name,
            later=later,
            source_type=source_type,
            source_repo_key=source_repo_key,
            slug_override=slug_override,
            folder_id=folder_id,
            relative_path=relative_path,
        )

    def _add_single_document(
        self,
        actor: str,
        source: Path,
        kb_targets: list[dict[str, Any]],
        display_name: str,
        later: bool,
        source_type: str = "manual",
        source_repo_key: str = "",
        slug_override: str | None = None,
        folder_id: int | None = None,
        relative_path: str | None = None,
        content_hash: str | None = None,
        file_size: int | None = None,
        archive_entry_id: int | None = None,
        archive_entry_ids: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        document_path = normalize_relative_document_path(relative_path or display_name or source.name)
        parent_parts, basename = split_document_path(document_path)
        placement_parent_parts = [] if archive_entry_id is not None or archive_entry_ids else parent_parts
        content_hash = content_hash or self.archive.content_hash(source)
        existing = next(
            (
                found
                for kb in kb_targets
                if (found := self.store.find_current_document_by_content_hash(kb["id"], content_hash))
            ),
            None,
        )
        if existing is not None:
            existing_kb_ids = {
                item["kb_id"]
                for item in self.store.get_document_kbs(existing["id"])
                if item.get("document_kb_status") == "active"
            }
            attached_new_kb = False
            for kb in kb_targets:
                if kb["id"] in existing_kb_ids:
                    continue
                target_folder_id = self._ensure_document_parent_folder(
                    kb["id"], folder_id if len(kb_targets) == 1 else None, placement_parent_parts
                )
                self.store.attach_document_to_kb(
                    existing["id"],
                    kb["id"],
                    actor,
                    folder_id=target_folder_id,
                    archive_entry_id=(
                        archive_entry_ids.get(kb["id"])
                        if archive_entry_ids is not None
                        else archive_entry_id
                    ),
                )
                self._queue_create_sync_jobs(existing["id"], existing["current_version_id"], [kb])
                attached_new_kb = True
            existing["kb_slugs"] = [
                kb["slug"]
                for kb in self.store.get_document_kbs(existing["id"])
                if kb.get("document_kb_status") == "active"
            ]
            existing["skipped"] = True
            existing["skip_reason"] = "duplicate_content"
            logger.info("跳过重复文档 doc=%s hash=%s", existing["slug"], content_hash)
            if attached_new_kb and not later:
                self.sync(actor=actor, all_users=False)
            return existing

        target_folder_ids = {
            kb["id"]: self._ensure_document_parent_folder(
                kb["id"], folder_id if len(kb_targets) == 1 else None, placement_parent_parts
            )
            for kb in kb_targets
        }
        slug = slug_override or unique_slug(make_slug(basename), self.store.list_document_slugs())
        archived = self.archive.store(
            source,
            content_hash=content_hash,
            file_size=file_size,
        )
        doc = self.store.create_document(
            slug=slug, title=Path(basename).stem, owner_user=actor,
            source_type=source_type, source_repo_key=source_repo_key,
        )
        version = self.store.create_document_version(
            doc_id=doc["id"],
            original_filename=document_path,
            content_hash=archived.content_hash,
            file_size=archived.file_size,
            mime_type=self._mime_type(document_path),
            archive_path=str(archived.archive_path),
            created_by=actor,
        )
        self._queue_create_sync_jobs(doc["id"], version["id"], kb_targets)
        for kb in kb_targets:
            self.store.attach_document_to_kb(
                doc["id"],
                kb["id"],
                actor,
                folder_id=target_folder_ids[kb["id"]],
                archive_entry_id=(
                    archive_entry_ids.get(kb["id"])
                    if archive_entry_ids is not None
                    else archive_entry_id
                ),
            )

        doc["current_version_no"] = version["version_no"]
        doc["kb_slugs"] = [kb["slug"] for kb in kb_targets]
        logger.info(
            "文档已入档 doc=%s KB数=%d 立即同步=%s", slug, len(kb_targets), not later
        )
        if not later:
            self.sync(actor=actor, all_users=False)
        return doc

    def _ensure_document_parent_folder(
        self,
        kb_id: int,
        base_folder_id: int | None,
        parent_parts: list[str],
    ) -> int:
        if base_folder_id is None:
            current = self.store.ensure_root_folder(kb_id)
        else:
            current = self.store.get_folder(kb_id, base_folder_id)
            if current is None:
                raise NotFound("folder not found")

        for name in parent_parts:
            child = next(
                (
                    folder for folder in self.store.list_folder_tree(kb_id)
                    if folder["parent_id"] == current["id"] and folder["name"] == name
                ),
                None,
            )
            if child is None:
                child = self.store.create_folder(kb_id, current["id"], name)
            current = child
        return int(current["id"])

    def _queue_create_sync_jobs(
        self,
        doc_id: int,
        version_id: int,
        kb_targets: list[dict[str, Any]],
    ) -> None:
        for kb in kb_targets:
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    self.store.create_sync_job(
                        doc_id, kb["id"], Operation.create, version_id,
                        backend_slug=target["slug"],
                    )

    def _add_zip_documents(
        self,
        actor: str,
        source: Path,
        display_name: str,
        kb_targets: list[dict[str, Any]],
        later: bool,
        source_type: str,
        source_repo_key: str,
        folder_id: int | None = None,
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        with TemporaryDirectory(prefix="agent-bridge-upload-") as temp_dir:
            try:
                extracted = extract_zip_documents(source, Path(temp_dir), ALLOWED_EXTENSIONS)
            except ValidationError as exc:
                message = str(exc)
                if "ZIP 解压失败" in message:
                    raise ValidationError(f"invalid zip archive: {message}") from exc
                if "ZIP 成员路径不安全" in message:
                    raise ValidationError(f"unsafe zip member path: {message}") from exc
                if "ZIP 压缩层没有支持的后代文档" in message:
                    raise ValidationError(
                        f"zip archive contains no supported documents: {message}"
                    ) from exc
                raise
            outer_path = normalize_relative_document_path(relative_path or display_name)
            archive_files_before = self._archive_files()
            results: list[dict[str, Any]] = []
            try:
                with self.store.transaction():
                    archive_entry_ids_by_kb: dict[int, dict[str, int]] = {}
                    for kb in kb_targets:
                        selected_folder_id = folder_id
                        if selected_folder_id is None or len(kb_targets) != 1:
                            selected_folder_id = int(self.store.ensure_root_folder(kb["id"])["id"])
                        outer_entry = self.store.create_archive_entry(
                            kb["id"],
                            kind="zip",
                            name=Path(outer_path).name,
                            relative_path=outer_path,
                            parent_folder_id=selected_folder_id,
                        )
                        path_to_id = {"": int(outer_entry["id"])}
                        for entry in sorted(
                            extracted.entries,
                            key=lambda item: (item.relative_path.count("/"), item.relative_path),
                        ):
                            parent_id = path_to_id.get(entry.parent_path or "")
                            if parent_id is None:
                                raise ValidationError(
                                    f"archive entry parent not found: {entry.relative_path}"
                                )
                            created_entry = self.store.create_archive_entry(
                                kb["id"],
                                kind=entry.kind,
                                name=entry.name,
                                relative_path=entry.relative_path,
                                parent_id=parent_id,
                            )
                            path_to_id[entry.relative_path] = int(created_entry["id"])
                        archive_entry_ids_by_kb[kb["id"]] = path_to_id

                    for item in extracted.documents:
                        document_archive_entry_ids = {
                            kb["id"]: archive_entry_ids_by_kb[kb["id"]][item.relative_path]
                            for kb in kb_targets
                        }
                        result = self._add_single_document(
                            actor=actor,
                            source=item.path,
                            kb_targets=kb_targets,
                            display_name=item.relative_path,
                            later=True,
                            source_type=source_type,
                            source_repo_key=source_repo_key,
                            folder_id=folder_id,
                            relative_path=item.relative_path,
                            content_hash=item.content_hash,
                            file_size=item.file_size,
                            archive_entry_id=(
                                next(iter(document_archive_entry_ids.values()))
                                if len(document_archive_entry_ids) == 1
                                else None
                            ),
                            archive_entry_ids=document_archive_entry_ids,
                        )
                        for kb in kb_targets:
                            entry_id = document_archive_entry_ids[kb["id"]]
                            self.store.update_archive_entry_document(entry_id, result["id"])
                            placement = self.store.get_document_placement(result["id"], kb["id"])
                            if placement is None:
                                raise NotFound("document knowledge-base placement not found")
                            self.store.update_document_placement(
                                result["id"],
                                kb["id"],
                                placement["folder_id"],
                                archive_entry_id=entry_id,
                            )
                        results.append(result)
            except Exception:
                self._remove_new_archive_files(archive_files_before)
                raise

        if not later:
            self.sync(actor=actor, all_users=False)
        skipped = [result for result in results if result.get("skipped")]
        uploaded = [result for result in results if not result.get("skipped")]
        return {
            "source_filename": display_name,
            "source_type": "zip",
            "documents": uploaded,
            "skipped": skipped,
            "uploaded_count": len(uploaded),
            "skipped_count": len(skipped),
        }

    def update_document(
        self,
        actor: str,
        doc_slug: str,
        source: Path,
        later: bool,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        doc = self._require_doc_admin_visible(actor, doc_slug)
        self._validate_source(source)
        kbs = self.store.get_document_kbs(doc["id"], active_only=True)
        display_name = original_filename or source.name
        archived = self.archive.store(source)
        version = self.store.create_document_version(
            doc_id=doc["id"],
            original_filename=display_name,
            content_hash=archived.content_hash,
            file_size=archived.file_size,
            mime_type=self._mime_type(display_name),
            archive_path=str(archived.archive_path),
            created_by=actor,
        )
        for kb in kbs:
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    self.store.create_sync_job(
                        doc["id"], kb["id"], Operation.update, version["id"],
                        backend_slug=target["slug"],
                    )
        doc["current_version_no"] = version["version_no"]
        if not later:
            self.sync(actor=actor, all_users=False)
        return doc

    def list_docs(
        self,
        actor: str,
        kb_slug: str,
        backend: str | None = None,
        folder_id: int | None = None,
    ) -> list[dict[str, Any]]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.list_docs_for_kb(kb["id"], folder_id=folder_id)

    def get_doc(self, actor: str, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        doc = self._require_doc_admin_visible(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"], active_only=True)
        versions = self.store.list_versions(doc["id"])
        for version in versions:
            version.pop("archive_path", None)
        doc["kbs"] = kbs
        doc["versions"] = versions
        doc["kb_slugs"] = [kb["slug"] for kb in kbs]
        sync_states = self.store.list_sync_states_for_doc(doc["id"])
        if backend:
            sync_states = [s for s in sync_states if s["backend_slug"] == backend]
        doc["sync_states"] = sync_states
        return doc

    def get_doc_for_kb(self, actor: str, kb_slug: str, doc_slug: str, *, profile_key: str | None = None) -> dict[str, Any]:
        kb = self._require_kb_runtime_allowed(actor, kb_slug, profile_key)
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        kbs = self.store.get_document_kbs(doc["id"], active_only=True)
        if not any(item["kb_id"] == kb["id"] for item in kbs):
            raise NotFound("document not found")
        versions = self.store.list_versions(doc["id"])
        for version in versions:
            version.pop("archive_path", None)
        doc["kbs"] = kbs
        doc["versions"] = versions
        doc["kb_slugs"] = [item["slug"] for item in kbs]
        doc["sync_states"] = self.store.list_sync_states_for_doc(doc["id"])
        return doc

    def delete_document(self, actor: str, doc_slug: str, later: bool = True) -> dict[str, str]:
        require_admin_user(actor, self.admins)
        doc = self._require_doc_admin_visible(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"], active_only=True)
        for kb in kbs:
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    compacted = self.store.cancel_runnable_create_update_jobs(
                        doc["id"], kb["id"], target["slug"]
                    )
                    sync_state = self.store.get_sync_state(doc["id"], kb["id"], target["slug"])
                    remote_exists = bool(sync_state and sync_state.get("backend_doc_id"))
                    if remote_exists or compacted["running"] > 0:
                        self.store.create_sync_job(
                            doc["id"], kb["id"], Operation.delete, doc["current_version_id"],
                            backend_slug=target["slug"],
                        )
        self.store.soft_delete_document(doc["id"])
        if not later:
            self.sync(actor=actor, all_users=False)
        return {"slug": doc_slug, "status": "deleted"}

    def sync(
        self,
        actor: str,
        all_users: bool,
        backend: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        require_admin_user(actor, self.admins)
        # A target may have been created by an older migration without its
        # remote ID.  Repair it before taking the job snapshot so that the
        # sync path never needs to use the local KB slug as a remote ID.
        self.align_backends()
        jobs = self.store.list_runnable_jobs(
            actor=None,
            backend_slug=backend,
        )
        logger.info("文档同步: %d 个待处理任务", len(jobs))
        succeeded = 0
        failed = 0
        recovery_backend_kbs: dict[tuple[int, str], str] = {}
        recovered_job_keys: set[tuple[int, int, str]] = set()
        processed_job_keys: set[tuple[int, int, str]] = set()
        pending_jobs = list(jobs)
        if progress_callback:
            progress_callback({"event": "start", "total": len(jobs), "processed": 0, "succeeded": 0, "failed": 0})
        while pending_jobs:
            job = pending_jobs.pop(0)
            job_key = (job["doc_id"], job["kb_id"], job["backend_slug"])
            if job_key in processed_job_keys:
                continue
            if progress_callback:
                progress_callback({
                    "event": "job_start",
                    "total": len(jobs),
                    "processed": len(processed_job_keys),
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
            ok = self._run_job(job, recovery_backend_kbs, recovered_job_keys)
            if job_key in recovered_job_keys:
                # rebuild_backend_target replaces the whole runnable queue for
                # this KB/backend.  Put the replacement jobs at the front so
                # this sync processes them before stale snapshot entries; the
                # group key prevents those stale entries from being run again.
                recovered_job_keys.remove(job_key)
                refreshed_jobs = self.store.list_runnable_jobs(
                    actor=None,
                    backend_slug=backend,
                )
                for refreshed_job in reversed(refreshed_jobs):
                    if (
                        refreshed_job["kb_id"] != job["kb_id"]
                        or refreshed_job["backend_slug"] != job["backend_slug"]
                    ):
                        continue
                    refreshed_key = (
                        refreshed_job["doc_id"],
                        refreshed_job["kb_id"],
                        refreshed_job["backend_slug"],
                    )
                    if refreshed_key not in processed_job_keys:
                        pending_jobs.insert(0, refreshed_job)
                continue

            processed_job_keys.add(job_key)
            if ok:
                succeeded += 1
            else:
                failed += 1
            if progress_callback:
                progress_callback({
                    "event": "job_done",
                    "total": len(jobs),
                    "processed": len(processed_job_keys),
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
        logger.info("文档同步完成: %d 成功, %d 失败", succeeded, failed)
        if progress_callback:
            progress_callback({"event": "finish", "total": len(jobs), "processed": len(processed_job_keys), "succeeded": succeeded, "failed": failed})
        return {"processed": len(processed_job_keys), "succeeded": succeeded, "failed": failed}

    # -- Code repo categories --

    def list_categories(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_categories()

    def upsert_category(self, actor: str, *, category_key: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return self.store.upsert_category(category_key=category_key, name=name, description=description)

    def delete_category(self, actor: str, category_key: str) -> None:
        require_admin_user(actor, self.admins)
        self.store.delete_category(category_key=category_key)

    def list_kb_repo_sources(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.list_kb_repo_sources(kb["id"])

    def upsert_kb_repo_source(
        self,
        actor: str,
        kb_slug: str,
        *,
        repo_key: str,
        include_suffixes: list[str],
    ) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        if self.store.get_code_repository(repo_key) is None:
            raise NotFound("code repository not found")
        suffixes = self._normalize_repo_source_suffixes(include_suffixes)
        return self.store.upsert_kb_repo_source(kb["id"], repo_key, suffixes)

    def sync_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """手动同步:转发到增量 diff 逻辑(行为与定时同步一致)。"""
        return self.sync_kb_repo_source_changes(actor, kb_slug, repo_key)

    def delete_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """删除 KB 的 git 数据源:解绑关联 + 软删除该 repo 提供的文档 + 生成 delete 同步任务。

        遵循 delete_document 的顺序:先生成 Operation.delete 任务再 soft_delete。
        保留 code_repositories 记录和本地克隆(其他 KB 可能引用)。
        """
        kb = self._require_kb_admin_visible(actor, kb_slug)
        source = self.store.get_kb_repo_source(kb["id"], repo_key)
        if source is None:
            raise NotFound("knowledge repo source not found")
        git_docs = self.store.list_git_docs_for_repo(kb["id"], repo_key)
        for doc in git_docs:
            self._delete_git_document(actor, doc)
        self.store.delete_kb_repo_source(kb["id"], repo_key)
        logger.info("git 数据源已删除 kb=%s repo=%s 删除文档数=%d", kb_slug, repo_key, len(git_docs))
        return {"kb_slug": kb_slug, "repo_key": repo_key, "deleted_docs": len(git_docs)}

    def sync_kb_repo_source_changes(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """增量同步:对比仓库文件与已导入文档,生成 create/delete 同步任务。

        diff 口径:按 slug + repo_key 匹配。
        - 新增文件 → add_document(source_type='git')
        - 仓库已删除 → delete_document(先生成 Operation.delete 任务再 soft_delete)
        - 内容修改 → 先删后加(doc_id 变化)
        - 内容不变 → 保留文档版本,按仓库相对路径对齐目录 placement
        """
        kb = self._require_kb_admin_visible(actor, kb_slug)
        source = self.store.get_kb_repo_source(kb["id"], repo_key)
        if source is None:
            raise NotFound("knowledge repo source not found")
        repo = self.store.get_code_repository(repo_key)
        if repo is None:
            raise NotFound("code repository not found")

        try:
            self.codegraph.sync_repository(actor, repo_key)
            repo = self.store.get_code_repository(repo_key) or repo
            local_path = Path(str(repo.get("local_path") or "")) if repo.get("local_path") else self.paths.repos_dir / repo_key
            if not local_path.exists():
                raise ValidationError("code repository has not been synced")

            suffixes = set(source["include_suffixes"])
            # existing: {slug: doc}
            existing = {
                d["slug"]: d
                for d in self.store.list_git_docs_for_repo(kb["id"], repo_key)
            }
            existing_slugs = set(existing.keys())

            # current: 扫描仓库,按实际可存入的 slug 计算每个文件的 (path, content_hash)。
            occupied_slugs = self.store.list_document_slugs() - existing_slugs
            current: dict[str, dict[str, Any]] = {}
            for path in sorted(local_path.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    relative_parts = path.relative_to(local_path).parts
                except ValueError:
                    continue
                if ".git" in relative_parts:
                    continue
                if path.suffix.lower() not in suffixes:
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                slug = unique_slug(make_slug(path.name), occupied_slugs | set(current.keys()))
                current[slug] = {
                    "path": path,
                    "relative_path": path.relative_to(local_path).as_posix(),
                    "content_hash": self._sha256_file(path),
                }

            added = removed = updated = unchanged = 0
            # 新增 + 修改
            for slug, item in current.items():
                if slug not in existing_slugs:
                    self._import_repo_file(
                        actor, kb_slug, repo_key, item["path"], slug, item["relative_path"]
                    )
                    added += 1
                elif (existing[slug].get("content_hash") or "") != item["content_hash"]:
                    # 修改:先删后加
                    self._delete_git_document(actor, existing[slug])
                    self._import_repo_file(
                        actor, kb_slug, repo_key, item["path"], slug, item["relative_path"]
                    )
                    updated += 1
                else:
                    # 内容不变时只修正当前 KB 的目录 placement,不重新导入全局 document。
                    self._import_repo_file(
                        actor, kb_slug, repo_key, item["path"], slug, item["relative_path"]
                    )
                    unchanged += 1
            # 删除
            for slug in existing_slugs - set(current.keys()):
                self._delete_git_document(actor, existing[slug])
                removed += 1

            self.store.mark_kb_repo_source_sync(kb["id"], repo_key, success=True)
            return {
                "kb_slug": kb_slug, "repo_key": repo_key,
                "added": added, "removed": removed, "updated": updated, "unchanged": unchanged,
            }
        except Exception as exc:
            self.store.mark_kb_repo_source_sync(kb["id"], repo_key, success=False, error=str(exc))
            raise

    def _import_repo_file(
        self,
        actor: str,
        kb_slug: str,
        repo_key: str,
        path: Path,
        slug: str,
        relative_path: str,
    ) -> None:
        existing = self.store.get_document_by_slug(slug)
        if existing is not None:
            kb = self._require_kb_admin_visible(actor, kb_slug)
            placement = self.store.get_document_placement(existing["id"], kb["id"])
            if placement is not None:
                normalized_path = normalize_relative_document_path(relative_path)
                parent_parts, basename = split_document_path(normalized_path)
                current_document = self.store.get_document_by_id(existing["id"])
                current_original_path = ""
                if current_document and current_document.get("current_version_id"):
                    current_version = next(
                        (
                            version
                            for version in self.store.list_versions(existing["id"])
                            if version["id"] == current_document["current_version_id"]
                        ),
                        None,
                    )
                    if current_version is not None:
                        current_original_path = normalize_relative_document_path(
                            current_version["original_filename"]
                        )
                        if split_document_path(current_original_path)[1] != basename:
                            return

                target_folder_path = "/".join(parent_parts)
                current_folder_path = placement.get("folder_path") or ""
                if (
                    current_original_path == normalized_path
                    and current_folder_path == target_folder_path
                ):
                    return
                if current_folder_path == target_folder_path:
                    return

                target_folder_id = self._ensure_document_parent_folder(
                    kb["id"], None, parent_parts
                )
                self.store.update_document_placement(
                    existing["id"], kb["id"], target_folder_id
                )
                if current_document is not None:
                    self._queue_placement_sync_jobs(current_document, kb["id"])
                return

        self.add_document(
            actor, path, [kb_slug], later=True,
            original_filename=relative_path,
            relative_path=relative_path,
            source_type="git", source_repo_key=repo_key,
            slug_override=slug,
        )

    def _delete_git_document(self, actor: str, doc: dict[str, Any]) -> None:
        self.delete_document(actor, doc["slug"], later=True)
        released_slug = unique_slug(
            f"{doc['slug']}-deleted-{doc['id']}",
            self.store.list_document_slugs(),
        )
        self.store.rename_document_slug(doc["id"], released_slug)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _normalize_repo_source_suffixes(suffixes: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in suffixes:
            value = str(raw or "").strip().lower()
            if not value:
                continue
            if not value.startswith("."):
                value = f".{value}"
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            raise ValidationError("at least one suffix is required")
        return normalized

    # -- Sync config & scheduler --

    def get_sync_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return self.store.get_sync_config()

    def save_sync_config(
        self,
        actor: str,
        *,
        code_sync_cron: str,
        ua_git_url: str = "",
        ua_plugin_update_cron: str = "0 3 * * 0",
        claude_mem_git_url: str = "",
        claude_mem_plugin_update_cron: str = "30 3 * * 0",
        understand_cron: str = "0 2 * * *",
        doc_sync_cron: str = "*/30 * * *",
        workflow_start_time: str = "22:00",
        workflow_stop_time: str = "07:00",
        workflow_max_runs: int = 0,
        workflow_max_runtime_minutes: int = 30,
        workflow_task_rerun_days: int = 30,
        log_retention_days: int = 180,
        mcp_timeout_seconds: int = DEFAULT_MCP_TIMEOUT_SECONDS,
        understand_timeout_minutes: int = 120,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        result = self.store.save_sync_config(
            code_sync_cron=code_sync_cron,
            ua_git_url=ua_git_url,
            ua_plugin_update_cron=ua_plugin_update_cron,
            claude_mem_git_url=claude_mem_git_url,
            claude_mem_plugin_update_cron=claude_mem_plugin_update_cron,
            understand_cron=understand_cron,
            doc_sync_cron=doc_sync_cron,
            workflow_start_time=workflow_start_time,
            workflow_stop_time=workflow_stop_time,
            workflow_max_runs=workflow_max_runs,
            workflow_max_runtime_minutes=workflow_max_runtime_minutes,
            workflow_task_rerun_days=workflow_task_rerun_days,
            log_retention_days=log_retention_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            understand_timeout_minutes=understand_timeout_minutes,
        )
        self.store.set_runtime_log_retention_days(log_retention_days)
        deleted_logs = self.store.prune_runtime_logs(force=True)
        # 配置变更后刷新全部调度器，使其读取最新 cron / 时间窗
        logger.info(
            "同步配置已保存，刷新全部调度器 code_sync_cron=%s doc_sync_cron=%s understand_cron=%s "
            "workflow_window=%s-%s log_retention_days=%s pruned_tool_call_logs=%s pruned_agent_runs=%s",
            code_sync_cron,
            doc_sync_cron,
            understand_cron,
            workflow_start_time,
            workflow_stop_time,
            log_retention_days,
            deleted_logs["tool_call_logs"],
            deleted_logs["agent_runs"],
        )
        self.codegraph_scheduler.refresh()
        self.understand_scheduler.refresh()
        self.plugin_update_scheduler.refresh()
        self.doc_sync_scheduler.refresh()
        self.workflow_scheduler.refresh()
        return result

    def get_scheduler_status(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return {
            "code_sync": self.codegraph_scheduler.get_status(),
            "understand": self.understand_scheduler.get_status(),
            "plugin_update": self.plugin_update_scheduler.get_status(),
            "doc_sync": self.doc_sync_scheduler.get_status(),
            "workflow": self.workflow_scheduler.get_status(),
        }

    def get_claude_mem_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return self.memory.worker_service.config.get_config(bootstrap=True)

    def save_claude_mem_config(
        self,
        actor: str,
        *,
        base_url: str | None = None,
        auth_token: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        clear_auth_token: bool = False,
        clear_api_key: bool = False,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        config = self.memory.worker_service.config.save_config(
            base_url=base_url,
            auth_token=auth_token,
            api_key=api_key,
            model=model,
            clear_auth_token=clear_auth_token,
            clear_api_key=clear_api_key,
        )
        self.memory.worker_service.stop_all_workers()
        return config

    def status(self, actor: str, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
        require_admin_user(actor, self.admins)
        return {"jobs": self.store.list_all_jobs(backend_slug=backend)}

    def search_all(self, actor: str, question: str, *,
                   profile_key: str | None = None,
                   top_k: int = 6) -> list[dict[str, Any]]:
        from agent_bridge.capability_hub.models import ProfileResourceType

        kbs = self.store.list_kbs()
        if actor not in self.admins or profile_key:
            self._require_profile_key(profile_key)
            allowed = set(
                self.governance.filter_resource_keys(
                    actor=actor,
                    profile_key=profile_key,
                    resource_type=ProfileResourceType.wiki_kb.value,
                    resource_keys=[kb["slug"] for kb in kbs],
                )
            )
            kbs = [kb for kb in kbs if kb["slug"] in allowed]

        results: list[dict[str, Any]] = []
        for kb in kbs:
            try:
                target = self._resolve_retrieval_target(kb, None)
                adapter = self._get_adapter(target["slug"])
                chunks = adapter.retrieve(target["backend_kb_id"], question, top_k)
                if chunks:
                    doc_names = {c.document_name for c in chunks}
                    results.append({
                        "kb_slug": kb["slug"],
                        "document_count": len(doc_names),
                        "chunk_count": len(chunks),
                    })
            except Exception:
                logger.warning("全局搜索失败: KB '%s'", kb["slug"], exc_info=True)
        return results

    def search(self, actor: str, kb_slug: str, question: str, *,
               backend_slug: str | None = None,
               profile_key: str | None = None,
               top_k: int = 6) -> list[RetrievalResult]:
        kb = self._require_kb_runtime_allowed(actor, kb_slug, profile_key)
        target = self._resolve_retrieval_target(kb, backend_slug)
        adapter = self._get_adapter(target["slug"])
        return adapter.retrieve(target["backend_kb_id"], question, top_k)

    def update_kb_defaults(self, actor: str, kb_slug: str, *,
                           default_backend_slug: str | None = None,
                           default_agent_id: str | None = None) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        self.store.update_kb_defaults(kb["id"], default_backend_slug, default_agent_id)
        return self.store.get_kb_by_slug(kb_slug)

    def resolve_retrieval_strategy(self, kb_slug: str, profile_key: str | None) -> tuple[dict[str, Any], RetrievalStrategy]:
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")

        # 1. If profile provided, check profile resource rule overrides
        if profile_key:
            rule = self.store.get_profile_resource_rule(profile_key, "wiki_kb", kb_slug)
            if rule:
                backend = rule.get("retrieval_backend_slug")
                agent = rule.get("retrieval_agent_id")
                if backend or agent:
                    return kb, RetrievalStrategy(
                        backend_slug=backend or kb.get("default_backend_slug") or self._first_active_backend(kb),
                        agent_id=agent,
                    )

        # 2. KB-level defaults
        if kb.get("default_backend_slug"):
            return kb, RetrievalStrategy(
                backend_slug=kb["default_backend_slug"],
                agent_id=kb.get("default_agent_id"),
            )

        # 3. System fallback
        return kb, RetrievalStrategy(backend_slug=self._first_active_backend(kb))

    def _first_active_backend(self, kb: dict[str, Any]) -> str:
        targets = self.store.list_backend_targets(kb["id"])
        active = [t for t in targets if t["status"] == "active"]
        if not active:
            raise NotFound(f"no retrieval backend available for knowledge base '{kb['slug']}'")
        return active[0]["slug"]

    def ask(self, actor: str, kb_slug: str, question: str, *,
            backend_slug: str | None = None,
            session_id: str | None = None,
            profile_key: str | None = None) -> AskResult:
        self._require_kb_runtime_allowed(actor, kb_slug, profile_key)
        kb, strategy = self.resolve_retrieval_strategy(kb_slug, profile_key)
        resolved_backend = backend_slug or strategy.backend_slug
        target = self._resolve_retrieval_target(kb, resolved_backend)
        adapter = self._get_adapter(target["slug"])
        agent_id = strategy.agent_id if target["slug"] == strategy.backend_slug else None

        config_json = target.get("config_json")
        existing_chat_id = None
        if config_json:
            import json
            config = json.loads(config_json) if isinstance(config_json, str) else config_json
            existing_chat_id = config.get("chat_id")
        result, new_chat_id = adapter.ask(
            target["backend_kb_id"], question,
            chat_id=existing_chat_id, session_id=session_id,
            agent_id=agent_id,
        )
        if new_chat_id and new_chat_id != existing_chat_id:
            self.store.update_backend_target_config(
                target["kb_id"], target["slug"], {"chat_id": new_chat_id},
            )
        return result

    def _resolve_retrieval_target(self, kb: dict[str, Any], backend_slug: str | None) -> dict[str, Any]:
        targets = self.store.list_backend_targets(kb["id"])
        active = [t for t in targets if t["status"] == "active"]

        if backend_slug:
            target = next((t for t in active if t["slug"] == backend_slug), None)
            if target is None:
                raise NotFound(f"backend '{backend_slug}' not found for knowledge base '{kb['slug']}'")
            return target

        if self.registry:
            from agent_bridge.core.config import load_server_config
            config = load_server_config(self.paths)
            if config.default_backend:
                target = next((t for t in active if t["slug"] == config.default_backend), None)
                if target:
                    return target

        if active and self.registry:
            return active[0]

        raise NotFound(f"no retrieval backend available for knowledge base '{kb['slug']}'")

    def _get_adapter(self, slug: str):
        if self.registry:
            adapter = self.registry.get(slug)
            if adapter is not None:
                return adapter
        return self.mock_backend

    @staticmethod
    def _backend_supports_folders(adapter: Any) -> bool:
        capability_attr = getattr(adapter, "capabilities", None)
        if capability_attr is None:
            return False
        try:
            capabilities = capability_attr() if callable(capability_attr) else capability_attr
        except Exception:
            return False
        return bool(getattr(capabilities, "supports_folders", False))

    def _archive_files(self) -> set[Path]:
        if not self.archive.archive_dir.exists():
            return set()
        return {
            path
            for path in self.archive.archive_dir.rglob("*")
            if path.is_file()
        }

    def _remove_new_archive_files(self, existing_files: set[Path]) -> None:
        for path in self._archive_files() - existing_files:
            self.archive.remove(path)
        directories = sorted(
            (path for path in self.archive.archive_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass

    # -- Backend agents (Weknora) --

    def list_backend_agents(self, actor: str, slug: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        adapter = self._get_adapter(slug)
        if not isinstance(adapter, WeknoraBackend):
            return []
        try:
            agents = adapter.list_agents()
        except Exception:
            logger.warning("列出后端 '%s' 的 agent 失败", slug, exc_info=True)
            return []
        return [self._normalize_agent(a) for a in agents if isinstance(a, dict) and a.get("id")]

    def list_backend_agent_types(self, actor: str, slug: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        adapter = self._get_adapter(slug)
        if not isinstance(adapter, WeknoraBackend):
            return []
        try:
            presets = adapter.get_type_presets()
        except Exception:
            logger.warning("列出后端 '%s' 的 agent 类型预设失败", slug, exc_info=True)
            return []
        return [self._normalize_agent_preset(p) for p in presets if isinstance(p, dict) and p.get("id")]

    def create_backend_agent(self, actor: str, slug: str, name: str, preset_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        adapter = self._get_adapter(slug)
        if not isinstance(adapter, WeknoraBackend):
            raise ValidationError(f"backend '{slug}' does not support agents")
        presets = adapter.get_type_presets()
        preset = next((p for p in presets if isinstance(p, dict) and p.get("id") == preset_id), None)
        if preset is None:
            raise NotFound(f"agent type preset '{preset_id}' not found in backend '{slug}'")
        created = adapter.create_agent(name, preset)
        return self._normalize_agent(created) if isinstance(created, dict) else {"agent_id": None, "name": name, "agent_type": preset_id, "is_builtin": False}

    @staticmethod
    def _normalize_agent(agent: dict[str, Any]) -> dict[str, Any]:
        """Project Weknora's raw agent payload into a stable contract for the frontend."""
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

    @staticmethod
    def _is_kb_gone(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "knowledge base not found" in msg:
            return True
        if "404" in msg and "1003" in msg:          # Weknora
            return True
        if "ragflow" in msg and "404" in msg:       # RagFlow HTTP 404
            return True
        return False

    def _run_job(
        self,
        job: dict[str, Any],
        recovery_backend_kbs: dict[tuple[int, str], str] | None = None,
        recovered_job_keys: set[tuple[int, int, str]] | None = None,
    ) -> bool:
        doc_title = job.get("doc_title", job.get("doc_slug", "?"))
        backend = job.get("backend_slug", "?")
        op = job.get("operation", "?")
        logger.info("文档同步任务 #%d: %s '%s' -> %s", job["id"], op, doc_title, backend)
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        adapter = self.registry.get(job["backend_slug"]) if self.registry else None
        if adapter is None:
            adapter = self.mock_backend
        supports_folders = self._backend_supports_folders(adapter)
        placement: dict[str, Any] | None = None
        previous_sync_state = self.store.get_sync_state(
            job["doc_id"], job["kb_id"], job["backend_slug"]
        )
        try:
            recovery_key = (job["kb_id"], job["backend_slug"])
            backend_kb_id = (
                recovery_backend_kbs.get(recovery_key)
                if recovery_backend_kbs is not None
                else None
            ) or job.get("backend_kb_id")
            if not backend_kb_id:
                raise RuntimeError(
                    f"backend target '{job['backend_slug']}' has no remote knowledge-base ID"
                )
            if job["operation"] == "delete":
                backend_doc_id = previous_sync_state["backend_doc_id"] if previous_sync_state else None
                if backend_doc_id:
                    adapter.delete(backend_kb_id, backend_doc_id)
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    None,
                    SyncStateStatus.deleted,
                )
            else:
                placement = self.store.get_document_placement(job["doc_id"], job["kb_id"])
                if placement is None:
                    raise NotFound("document knowledge-base placement not found")

                archive_path = job.get("archive_path")
                filename = job.get("original_filename") or job["doc_slug"]
                current_document = self.store.get_document_by_id(job["doc_id"], include_deleted=True)
                if current_document and current_document.get("current_version_id"):
                    current_version = next(
                        (
                            version
                            for version in self.store.list_versions(job["doc_id"])
                            if version["id"] == current_document["current_version_id"]
                        ),
                        None,
                    )
                    if current_version is not None:
                        archive_path = current_version["archive_path"]
                        filename = current_version["original_filename"]
                if not archive_path:
                    raise NotFound("document archive not found")

                folder_path = placement.get("folder_path") or ""
                normalized_filename = normalize_relative_document_path(filename)
                upload_filename = Path(normalized_filename).name
                remote_filename = (
                    normalized_filename
                    if placement.get("archive_entry_id") is not None
                    else upload_filename
                )
                remote_path = (
                    join_backend_path(folder_path, remote_filename)
                    if supports_folders
                    else None
                )
                if job["operation"] == Operation.move.value:
                    old_backend_doc_id = (
                        previous_sync_state.get("backend_doc_id") if previous_sync_state else None
                    )
                    if not old_backend_doc_id:
                        raise RuntimeError("cannot move document without an existing backend document")
                    move_method = getattr(adapter, "move", None) or getattr(adapter, "relocate", None)
                    if not callable(move_method):
                        raise RuntimeError(f"backend '{job['backend_slug']}' does not implement move")
                    backend_doc_id = move_method(
                        backend_kb_id=backend_kb_id,
                        backend_doc_id=old_backend_doc_id,
                        file_path=Path(archive_path),
                        filename=upload_filename,
                        remote_path=remote_path,
                    )
                else:
                    upload_kwargs: dict[str, Any] = {
                        "backend_kb_id": backend_kb_id,
                        "doc_slug": job["doc_slug"],
                        "file_path": Path(archive_path),
                        "filename": upload_filename,
                    }
                    if supports_folders:
                        upload_kwargs["remote_path"] = remote_path
                    backend_doc_id = adapter.upload(**upload_kwargs)

                if supports_folders:
                    self.store.upsert_backend_folder_mapping(
                        job["kb_id"],
                        job["backend_slug"],
                        placement["folder_id"],
                        folder_path,
                        folder_path,
                        status="synced",
                        error=None,
                    )
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    backend_doc_id,
                    SyncStateStatus.synced,
                )
            self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
            logger.info("文档同步任务 #%d: 成功", job["id"])
            return True
        except Exception as exc:
            if op != Operation.move.value and self._is_kb_gone(exc) and job.get("kb_name") and job.get("kb_slug"):
                logger.warning("文档同步任务 #%d: 后端 KB 已丢失，正在重建...", job["id"])
                try:
                    recovery_key = (job["kb_id"], job["backend_slug"])
                    new_id = (
                        recovery_backend_kbs.get(recovery_key)
                        if recovery_backend_kbs is not None
                        else None
                    )
                    if new_id is None:
                        new_id = adapter.create_kb(job["kb_slug"], job["kb_name"])
                        doc_count = self.store.rebuild_backend_target(
                            job["kb_id"], job["backend_slug"], new_id
                        )
                        if recovery_backend_kbs is not None:
                            recovery_backend_kbs[recovery_key] = new_id
                        if recovered_job_keys is not None:
                            recovered_job_keys.add(
                                (job["doc_id"], job["kb_id"], job["backend_slug"])
                            )
                    else:
                        doc_count = 0
                    self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
                    logger.info("文档同步任务 #%d: 后端 KB 已重建，%d 个文档已重新调度", job["id"], doc_count)
                    return True
                except Exception as rebuild_exc:
                    logger.exception("文档同步任务 #%d: 重建失败 — %s", job["id"], rebuild_exc)
                    self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
                return False
            logger.exception("文档同步任务 #%d: 失败 — %s", job["id"], exc)
            failed_status = (
                SyncStateStatus.delete_failed if job["operation"] == "delete" else SyncStateStatus.sync_failed
            )
            if supports_folders and placement is not None:
                try:
                    folder_path = placement.get("folder_path") or ""
                    self.store.upsert_backend_folder_mapping(
                        job["kb_id"],
                        job["backend_slug"],
                        placement["folder_id"],
                        folder_path,
                        folder_path,
                        status="failed",
                        error=str(exc),
                    )
                except Exception:
                    logger.warning("保存后端目录映射失败: job=%s", job.get("id"), exc_info=True)
            failed_backend_doc_id = (
                previous_sync_state.get("backend_doc_id")
                if job["operation"] == Operation.move.value and previous_sync_state
                else None
            )
            self.store.upsert_sync_state(
                job["doc_id"],
                job["kb_id"],
                job["backend_slug"],
                failed_backend_doc_id,
                failed_status,
                backend_error=str(exc),
            )
            self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
            return False

    def purge_document(self, actor: str, doc_slug: str, confirm: bool = False) -> dict[str, str]:
        require_admin_user(actor, self.admins)
        doc = self.store.get_document_by_slug(doc_slug, include_deleted=True)
        if doc is None:
            raise NotFound("document not found")
        if not confirm:
            raise ValidationError("purge requires confirmation")
        archive_paths = self.store.purge_document(doc["id"])
        for archive_path in archive_paths:
            self.archive.remove(Path(archive_path))
        return {"slug": doc_slug, "status": "purged"}

    def align_backends(self) -> None:
        if not self.registry:
            return
        configured_slugs = set(self.registry.list_slugs())
        kbs = self.store.list_kbs()
        for kb in kbs:
            existing_targets = self.store.list_backend_targets(kb["id"])

            # Mark removed backends as inactive
            for target in existing_targets:
                if target["slug"] not in configured_slugs and target["status"] == "active":
                    self.store.set_backend_target_status(kb["id"], target["slug"], "inactive")

            # Add new backends, repair targets whose remote ID was lost, and
            # create pending sync jobs for existing docs.
            for backend_slug in configured_slugs:
                adapter = self.registry.get(backend_slug)
                target = next(
                    (target for target in existing_targets if target["slug"] == backend_slug),
                    None,
                )
                if target is None:
                    try:
                        backend_kb_id = adapter.create_kb(kb["slug"], kb["name"]) if adapter else None
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                        if backend_kb_id:
                            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                    except Exception:
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                else:
                    if adapter and not target.get("backend_kb_id"):
                        try:
                            backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
                            self.store.update_backend_target_kb_id(
                                kb["id"], backend_slug, backend_kb_id
                            )
                        except Exception:
                            pass
                    if target["status"] == "inactive":
                        self.store.set_backend_target_status(kb["id"], backend_slug, "active")
                self._backfill_missing_backend_jobs(kb, backend_slug)

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
        for row in rows:
            row["api_key_set"] = bool(row.get("api_key"))
            row.pop("api_key", None)
            # Determine runtime status
            if self.registry and row["slug"] in self.registry.backends:
                row["runtime_status"] = "active"
            else:
                row["runtime_status"] = "inactive"
        return rows

    def add_backend(self, actor: str, slug: str, backend_type: str, base_url: str | None = None,
                    api_key: str | None = None, timeout: int = 120,
                    embedding_model_id: str | None = None, summary_model_id: str | None = None) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if backend_type not in SUPPORTED_BACKEND_TYPES:
            raise ValidationError(f"unsupported backend type: {backend_type}")
        row = self.store.upsert_backend(
            slug=slug, backend_type=backend_type, base_url=base_url,
            api_key=api_key, timeout=timeout,
            embedding_model_id=embedding_model_id, summary_model_id=summary_model_id,
        )
        config = BackendConfig(
            slug=slug, backend_type=backend_type, base_url=base_url,
            api_key=api_key, timeout=timeout,
            embedding_model_id=embedding_model_id, summary_model_id=summary_model_id,
        )
        if self.registry:
            self.registry.add_backend(config)
            self.align_backends()
        row["api_key_set"] = bool(api_key)
        row.pop("api_key", None)
        row["runtime_status"] = "active"
        return row

    def update_backend(self, actor: str, slug: str, backend_type: str | None = None,
                       base_url: str | None = None, api_key: str | None = None,
                       timeout: int | None = None, embedding_model_id: str | None = None,
                       summary_model_id: str | None = None) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        existing = self.store.get_backend(slug)
        if existing is None:
            raise NotFound(f"backend '{slug}' not found")
        resolved_type = backend_type or existing["backend_type"]
        if resolved_type not in SUPPORTED_BACKEND_TYPES:
            raise ValidationError(f"unsupported backend type: {resolved_type}")
        # Keep existing api_key if not provided (empty string means clear, None means keep)
        resolved_key = existing.get("api_key") if api_key is None else (api_key or None)
        row = self.store.upsert_backend(
            slug=slug, backend_type=resolved_type, base_url=base_url if base_url is not None else existing.get("base_url"),
            api_key=resolved_key, timeout=timeout if timeout is not None else existing.get("timeout", 120),
            embedding_model_id=embedding_model_id if embedding_model_id is not None else existing.get("embedding_model_id"),
            summary_model_id=summary_model_id if summary_model_id is not None else existing.get("summary_model_id"),
        )
        config = BackendConfig(
            slug=slug, backend_type=resolved_type,
            base_url=row.get("base_url"), api_key=resolved_key,
            timeout=row.get("timeout", 120),
            embedding_model_id=row.get("embedding_model_id"),
            summary_model_id=row.get("summary_model_id"),
        )
        if self.registry:
            self.registry.update_backend(config)
            self.align_backends()
        row["api_key_set"] = bool(resolved_key)
        row.pop("api_key", None)
        row["runtime_status"] = "active"
        return row

    def remove_backend(self, actor: str, slug: str) -> dict[str, str]:
        require_admin_user(actor, self.admins)
        existing = self.store.get_backend(slug)
        if existing is None:
            raise NotFound(f"backend '{slug}' not found")
        self.store.delete_backend(slug)
        if self.registry:
            self.registry.remove_backend(slug)
            # Mark all backend_targets for this slug as inactive
            for kb in self.store.list_kbs():
                self.store.set_backend_target_status(kb["id"], slug, "inactive")
        return {"slug": slug, "status": "removed"}

    def _require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        return kb

    def _require_kb_runtime_allowed(self, actor: str, kb_slug: str, profile_key: str | None) -> dict[str, Any]:
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        if actor in self.admins and not profile_key:
            return kb
        self._require_profile_key(profile_key)
        if not self.governance.is_resource_allowed(
            actor,
            profile_key,
            ProfileResourceType.wiki_kb.value,
            kb_slug,
        ):
            raise AccessDenied("resource is blocked by profile policy")
        return kb

    @staticmethod
    def _require_profile_key(profile_key: str | None) -> None:
        if not profile_key:
            raise AccessDenied("capability profile is required")

    def _validate_source(self, source: Path, allowed_extensions: set[str] | None = None) -> None:
        if not source.is_file():
            raise ValidationError("source file does not exist")
        if source.suffix.lower() not in (allowed_extensions or ALLOWED_EXTENSIONS):
            raise ValidationError("unsupported file type")

    def _require_doc_admin_visible(self, actor: str, doc_slug: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        return doc

    @staticmethod
    def _sync_job_progress_payload(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "operation": job.get("operation"),
            "backend_slug": job.get("backend_slug"),
            "kb_slug": job.get("kb_slug"),
            "doc_slug": job.get("doc_slug"),
            "doc_title": job.get("doc_title"),
        }

    @staticmethod
    def _mime_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"
