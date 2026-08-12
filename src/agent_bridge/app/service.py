"""Application services for Agent Bridge."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
from typing import Any, Callable

from agent_bridge.agent_runtime.service import AgentService
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.access_control.resources import (
    BusinessLedgerAccessAdapter,
    create_scoped_resource_registry,
)
from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
from agent_bridge.knowledge_management.docs_knowledge.ingest import DocumentIngestService
from agent_bridge.knowledge_management.docs_knowledge.repo_sync import GitRepoSyncService
from agent_bridge.knowledge_management.docs_knowledge.sync_runner import SyncJobRunner
from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.knowledge_management.code_knowledge.scheduler import CodeGraphScheduler
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.knowledge_management.code_knowledge.understand_scheduler import UnderstandingScheduler
from agent_bridge.knowledge_management.docs_knowledge.doc_sync_scheduler import DocSyncScheduler
from agent_bridge.core.config import (
    AgentBackendConfig,
    AgentBridgePaths,
    AgentRuntimeConfig,
    ensure_directories,
    load_agent_runtime_config,
    migrate_legacy_database_filename,
    migrate_toml_backends_to_db,
    save_agent_runtime_config,
)
from agent_bridge.capability_hub.models import ProfileResourceType
from agent_bridge.core.domain import (
    AccessDenied,
    AskResult,
    NotFound,
    Operation,
    RetrievalResult,
    RetrievalStrategy,
    SyncStateStatus,
    ValidationError,
    require_admin_user,
)
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry, create_registry_from_db
from agent_bridge.knowledge_management.docs_knowledge.service import DocsKnowledgeService
from agent_bridge.knowledge_management.memory.service import MemoryService
from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.scripts.service import ScriptService
from agent_bridge.system_config.skills.service import SkillService
from agent_bridge.system_config.plugin_update_scheduler import PluginUpdateScheduler
from agent_bridge.system_config.model_evaluation.service import ModelEvaluationService
from agent_bridge.app.onboarding import OnboardingService
from agent_bridge.automation.workflows.scheduler import WorkflowScheduler
from agent_bridge.automation.workflows.service import WorkflowService
from agent_bridge.automation.workflows.handlers import WorkflowNodeHandlers
from agent_bridge.automation.workflows.output_handler import OutputHandler
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.business_ledger.service import BusinessLedgerService


ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".markdown", ".csv", ".json",
}
UPLOAD_EXTENSIONS = ALLOWED_EXTENSIONS | {".zip"}
_UNSET = object()


def _agent_runtime_config_payload(config: AgentRuntimeConfig, registry: Any = None) -> dict[str, Any]:
    payload = {
        "default_backend": config.default_backend,
        "backends": [
            {
                "slug": backend.slug,
                "type": backend.agent_type,
                "command": backend.command,
                "model": backend.model,
            }
            for backend in config.backends
        ],
    }
    if registry is not None:
        payload["available_backends"] = [
            {
                "slug": backend_key,
                "display_name": registry.get(backend_key).display_name,
                "source": registry.get(backend_key).source,
                "capabilities": asdict(registry.get(backend_key).capabilities),
            }
            for backend_key in registry.keys()
        ]
    return payload


class _IngestFacade:
    """把门面中 ingest 需要的能力按 Protocol 表面暴露。

    通过属性查找而非捕获绑定方法，保证测试 monkeypatch 门面方法后仍生效。
    """

    __slots__ = ("_service",)

    def __init__(self, service: "AgentBridgeService") -> None:
        self._service = service

    def validate_source(self, source: Path, allowed_extensions: set[str] | None = None) -> None:
        return self._service._validate_source(source, allowed_extensions=allowed_extensions)

    def require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        return self._service._require_kb_admin_visible(actor, kb_slug)

    def require_doc_admin_visible(self, actor: str, doc_slug: str) -> dict[str, Any]:
        return self._service._require_doc_admin_visible(actor, doc_slug)

    def archive_files(self) -> set[Path]:
        return self._service._archive_files()

    def remove_new_archive_files(self, existing_files: set[Path]) -> None:
        return self._service._remove_new_archive_files(existing_files)

    def trigger_sync(self, actor: str) -> None:
        self._service.sync(actor=actor, all_users=False)


class _SyncRunnerFacade:
    """把门面中 sync runner 需要的能力暴露给 SyncJobRunner。"""

    __slots__ = ("_service",)

    def __init__(self, service: "AgentBridgeService") -> None:
        self._service = service

    def get_adapter(self, slug: str):
        return self._service._get_adapter(slug)

    def align_backends(self, kb_id: int | None = None) -> None:
        self._service.align_backends(kb_id=kb_id)


class _RepoSyncFacade:
    """把门面中 repo_sync 需要的能力暴露给 GitRepoSyncService。"""

    __slots__ = ("_service",)

    def __init__(self, service: "AgentBridgeService") -> None:
        self._service = service

    def require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        return self._service._require_kb_admin_visible(actor, kb_slug)

    def queue_placement_sync_jobs(self, doc: dict[str, Any], kb_id: int) -> None:
        self._service._queue_placement_sync_jobs(doc, kb_id)

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
        return self._service.add_document(
            actor, source, kb_slugs, later,
            original_filename=original_filename,
            source_type=source_type,
            source_repo_key=source_repo_key,
            slug_override=slug_override,
            folder_id=folder_id,
            relative_path=relative_path,
        )

    def delete_document(self, actor: str, doc_slug: str, later: bool = True) -> dict[str, str]:
        return self._service.delete_document(actor, doc_slug, later=later)


class AgentBridgeService:
    def __init__(
        self,
        paths: AgentBridgePaths,
        store: SQLiteStore,
        archive: ArchiveStorage,
        admins: set[str],
    ) -> None:
        self.paths = paths
        self.store = store
        self.archive = archive
        self.admins = admins
        self.access = AccessControlService(
            store.access_control,
            admins,
            create_scoped_resource_registry(store),
        )
        self.registry: BackendRegistry | None = None
        # 领域服务在所有 collaborator 就位后装配。门面通过注入的回调暴露
        # 必要的 monkeypatch 点（如 _archive_files）和编排能力（如 sync）。
        self._ingest = DocumentIngestService(
            store=store,
            archive=archive,
            admins=admins,
            facade=self._ingest_facade(),
        )
        self._sync_runner = SyncJobRunner(
            store=store,
            admins=admins,
            facade=self._sync_runner_facade(),
        )
        self.docs_knowledge = DocsKnowledgeService(
            store=store,
            admins=admins,
            registry_provider=lambda: self.registry,
        )
        self.business_ledgers = BusinessLedgerService(
            db_path=paths.ledger_db_path,
            admins=admins,
            access=self.access,
        )
        self.access.resources.register(BusinessLedgerAccessAdapter(self.business_ledgers))
        self.governance = CapabilityGovernanceService(
            store=store,
            admins=admins,
            access=self.access,
        )
        self.governance.business_ledgers = self.business_ledgers
        self.capabilities = CapabilityService(
            store=store,
            admins=admins,
            governance=self.governance,
            access=self.access,
        )
        agent_runtime_config = load_agent_runtime_config(paths)
        self.agents = AgentService(
            paths=paths,
            store=store,
            admins=admins,
            governance=self.governance,
            coding_agents=create_coding_agent_registry(agent_runtime_config),
        )
        self.codegraph = CodeGraphService(
            paths=paths,
            store=store,
            admins=admins,
            agent_service=self.agents,
            access=self.access,
        )
        self._repo_sync = GitRepoSyncService(
            store=store,
            codegraph=self.codegraph,
            paths=paths,
            facade=self._repo_sync_facade(),
            ingest=self._ingest,
        )
        self.codegraph_scheduler = CodeGraphScheduler(service=self.codegraph, store=store, admins=admins)
        self.understand_scheduler = UnderstandingScheduler(service=self.codegraph, store=store, admins=admins)
        self.doc_sync_scheduler = DocSyncScheduler(service=self, store=store, admins=admins)
        self.skills = SkillService(store=store, admins=admins)
        self.scripts = ScriptService(paths=paths, store=store, admins=admins, access=self.access)
        self.model_evaluations = ModelEvaluationService(
            paths=paths,
            store=store,
            admins=admins,
            access=self.access,
        )
        self.onboarding = OnboardingService(store=store)
        self.workflows = WorkflowService(
            store=store,
            admins=admins,
            agent_service=self.agents,
            skills=self.skills,
            scripts=self.scripts,
            artifact_search_cache_dir=paths.artifact_search_cache_dir,
            access=self.access,
        )
        self.workflow_output_handler = OutputHandler(
            agent_service=self.agents, skill_service=self.skills, workflow_service=self.workflows
        )
        self.workflow_handlers = WorkflowNodeHandlers(
            agent_service=self.agents, scripts=self.scripts, skill_service=self.skills,
            workflow_service=self.workflows, output_handler=self.workflow_output_handler,
        )
        workflow_validator = self.workflows.validator
        self.workflow_executor = WorkflowDagExecutor(
            store=store,
            handlers=self.workflow_handlers,
            validate_structure_on_run=False,
        )
        self.memory = MemoryService(
            paths=paths,
            store=store,
            admins=admins,
            governance_service=self.governance,
            access=self.access,
        )
        from agent_bridge.knowledge_management.retrieval_probe.adapters import (
            ArtifactProbeAdapter,
        )
        from agent_bridge.knowledge_management.retrieval_probe.extractor import OpenAIChatProbeKeywordExtractor
        from agent_bridge.knowledge_management.retrieval_probe.registry import RetrievalProbeRegistry
        from agent_bridge.knowledge_management.retrieval_probe.session_history import ProbeSessionHistoryStore
        from agent_bridge.knowledge_management.retrieval_probe.service import RetrievalProbeService

        retrieval_probe_registry = RetrievalProbeRegistry()
        retrieval_probe_registry.register(ArtifactProbeAdapter(workflows=self.workflows))
        retrieval_probe_history = ProbeSessionHistoryStore(paths.retrieval_probe_session_cache_dir)
        self.retrieval_probe = RetrievalProbeService(
            store=store,
            registry=retrieval_probe_registry,
            governance=self.governance,
            keyword_extractor=OpenAIChatProbeKeywordExtractor(store=store, history=retrieval_probe_history),
        )
        self.plugin_update_scheduler = PluginUpdateScheduler(service=self, store=store, admins=admins)
        self.workflow_scheduler = WorkflowScheduler(
            service=self.workflows,
            store=store,
            admins=admins,
            executor=self.workflow_executor,
            validator=workflow_validator,
            base_run_dir=paths.run_dir / "workflow-runs",
        )
        from agent_bridge.capability_hub.sources.builtin.codegraph import CodeGraphBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.business_ledger import BusinessLedgerBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.memory import MemoryBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.platform import PlatformBuiltinProvider
        from agent_bridge.capability_hub.sources.builtin.wiki import WikiBuiltinProvider

        self.capabilities.register_builtin_provider(PlatformBuiltinProvider(self))
        self.capabilities.register_builtin_provider(WikiBuiltinProvider(self))
        self.capabilities.register_builtin_provider(CodeGraphBuiltinProvider(self.codegraph, self.governance))
        self.capabilities.register_builtin_provider(MemoryBuiltinProvider(self))
        self.capabilities.register_builtin_provider(BusinessLedgerBuiltinProvider(self))

    @classmethod
    def create(cls, paths: AgentBridgePaths, admins: set[str]) -> "AgentBridgeService":
        """工厂入口：装配全部子服务，恢复中断的 CodeGraph 同步，迁移并重建后端 registry。"""
        logger.info("AgentBridgeService 开始装配 root=%s admins=%s", paths.root, sorted(admins))
        if migrate_legacy_database_filename(paths):
            logger.info("主数据库文件名已从 wiki.db 迁移至 agent-bridge.db")
        service = cls(
            paths=paths,
            store=SQLiteStore(paths.db_path, paths.log_db_path),
            archive=ArchiveStorage(paths.archive_dir),
            admins=admins,
        )
        service.store.init_schema()
        service.access.bootstrap_admin_memberships()
        service.store.migrate_phase2()
        service.business_ledgers.init_schema()
        recovered_workflows = service.store.recover_interrupted_workflow_runs()
        if recovered_workflows["runs"]:
            recovered_agent_runs = service.store.agent_runs.recover_interrupted_workflow_runs(
                recovered_workflows["run_ids"]
            )
            logger.warning(
                "已恢复上一进程遗留的工作流执行 runs=%d nodes=%d tasks=%d agent_runs=%d run_ids=%s",
                recovered_workflows["runs"],
                recovered_workflows["nodes"],
                recovered_workflows["tasks"],
                recovered_agent_runs,
                recovered_workflows["run_ids"],
            )
        recovered = service.codegraph.recover_interrupted_sync_runs()
        if recovered:
            logger.warning(
                "已恢复上一进程遗留的 CodeGraph 中断同步任务 count=%d",
                recovered,
            )
        service.model_evaluations.recover_interrupted_runs()
        migrate_toml_backends_to_db(paths, service.store)
        service.registry = create_registry_from_db(paths, service.store)
        logger.info(
            "AgentBridgeService 装配完成 子服务=governance/capabilities/agents/codegraph/"
            "memory/retrieval_probe/workflows/skills/scripts/model_evaluations 后端数=%d",
            len(service.registry.list_slugs()) if service.registry else 0,
        )
        return service

    def init_system(self) -> None:
        ensure_directories(self.paths)
        self.store.init_schema()
        self.access.bootstrap_admin_memberships()
        self.store.migrate_phase2()
        self.business_ledgers.init_schema()

    def validate_workflow_draft(self, *, actor: str, workflow: dict[str, Any]) -> dict[str, Any]:
        result = self.workflows.validator.validate(actor=actor, workflow=workflow)
        errors = [asdict(issue) for issue in result.errors]
        warnings = [asdict(issue) for issue in result.warnings]
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    def ensure_backend_resources(self) -> None:
        """对所有声明托管资源能力的后端执行自愈。"""
        self.docs_knowledge.ensure_managed_resources()

    def ensure_weknora_agents(self) -> None:
        """兼容旧调用名，新代码应使用 :meth:`ensure_backend_resources`。"""
        self.ensure_backend_resources()

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

    def create_kb(
        self,
        actor: str,
        slug: str,
        name: str,
        description: str,
        *,
        visibility: str = "group",
    ) -> dict[str, Any]:
        scope = self.access.new_resource_scope(actor=actor, visibility=visibility or "group")
        kb = self.store.create_kb(
            slug=slug,
            name=name,
            description=description,
            created_by=actor,
            owner_group_key=scope.owner_group_key,
            visibility=scope.visibility.value,
        )
        self.docs_knowledge.provision_kb(kb)
        return kb

    def delete_kb(self, actor: str, kb_slug: str) -> dict[str, Any]:
        """硬删除一个知识库。

        前置校验：该 KB 下不得仍有活动文档（用户须先逐个删除文档）。
        副作用清理：通知各检索后端删除远端 KB（容错）、清理能力平面里引用该
        KB 的 resource 规则（无外键，需手动删）。最后删除 KB 行，依赖外键
        ON DELETE CASCADE 清除 members / document_kbs / backend_targets /
        sync_jobs / sync_states。
        """
        kb = self.access.require_resource_write(
            actor=actor,
            resource_type=ScopedResourceType.knowledge_base,
            resource_key=kb_slug,
        )
        kb_id = kb["id"]

        active_docs = self.store.list_docs_for_kb(kb_id)
        if active_docs:
            raise ValidationError(
                f"请先删除该知识库下的所有文档（仍有 {len(active_docs)} 篇）"
            )

        self.docs_knowledge.delete_remote_kbs(kb)

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
        return [
            attach_edit_token(kb, self._kb_defaults_edit_snapshot(kb))
            for kb in self.access.visible_resources(
                actor=actor,
                resource_type=ScopedResourceType.knowledge_base,
            )
        ]

    def list_kb_status_summaries(self, actor: str) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for kb in self.list_kbs(actor):
            targets = self.store.list_backend_targets(kb["id"])
            counts = self.store.get_kb_document_counts(kb["id"])
            summaries.append(
                {
                    **kb,
                    "backend_targets": targets,
                    **counts,
                }
            )
        return summaries

    def list_kb_members(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        self._require_kb_admin_visible(actor, kb_slug)
        return []

    # -- Knowledge-base folders and document placements --

    def list_folders(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
        return self.store.list_folder_tree(kb["id"])

    def list_archive_entries(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
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

        kb = self._require_kb_visible(actor, kb_slug)
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
            # A failed or in-flight write may have reached the remote backend
            # before its local state was recorded. A never-started pending job
            # has nothing remote to remove, so cancelling it is sufficient.
            if remote_exists or compacted["running"] > 0 or compacted["failed"] > 0:
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
        if str(doc.get("owner_group_key") or "") != str(kb.get("owner_group_key") or ""):
            raise ValidationError("文档不能挂载到其他数据组的知识库")
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
        if str(doc.get("owner_group_key") or "") != str(kb.get("owner_group_key") or ""):
            raise ValidationError("文档不能挂载到其他数据组的知识库")
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
        return self._ingest.add_document(
            actor, source, kb_slugs, later,
            original_filename=original_filename,
            source_type=source_type,
            source_repo_key=source_repo_key,
            slug_override=slug_override,
            folder_id=folder_id,
            relative_path=relative_path,
        )

    def _ensure_document_parent_folder(
        self,
        kb_id: int,
        base_folder_id: int | None,
        parent_parts: list[str],
    ) -> int:
        return self._ingest._ensure_document_parent_folder(kb_id, base_folder_id, parent_parts)

    def _queue_create_sync_jobs(
        self,
        doc_id: int,
        version_id: int,
        kb_targets: list[dict[str, Any]],
    ) -> None:
        return self._ingest._queue_create_sync_jobs(doc_id, version_id, kb_targets)

    def update_document(
        self,
        actor: str,
        doc_slug: str,
        source: Path,
        later: bool,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        return self._ingest.update_document(
            actor, doc_slug, source, later, original_filename=original_filename
        )


    def list_docs(
        self,
        actor: str,
        kb_slug: str,
        backend: str | None = None,
        folder_id: int | None = None,
    ) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
        return self.store.list_docs_for_kb(
            kb["id"],
            folder_id=folder_id,
            backend_slug=backend,
        )

    def get_doc(self, actor: str, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        doc = self._require_doc_visible(actor, doc_slug)
        kbs = [
            kb
            for kb in self.store.get_document_kbs(doc["id"], active_only=True)
            if self.access.can_read(actor=actor, scope=ResourceScope.from_record(kb))
        ]
        versions = self.store.list_versions(doc["id"])
        for version in versions:
            version.pop("archive_path", None)
        doc["kbs"] = kbs
        doc["versions"] = versions
        doc["kb_slugs"] = [kb["slug"] for kb in kbs]
        visible_kb_ids = {int(kb["kb_id"]) for kb in kbs}
        sync_states = [
            state
            for state in self.store.list_sync_states_for_doc(doc["id"])
            if int(state["kb_id"]) in visible_kb_ids
        ]
        if backend:
            sync_states = [s for s in sync_states if s["backend_slug"] == backend]
        doc["sync_states"] = sync_states
        return doc

    def get_doc_for_kb(self, actor: str, kb_slug: str, doc_slug: str, *, profile_key: str | None = None) -> dict[str, Any]:
        kb = self._require_kb_runtime_allowed(actor, kb_slug, profile_key)
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        placements = self.store.get_document_kbs(doc["id"], active_only=True)
        requested_placement = next(
            (item for item in placements if item["kb_id"] == kb["id"]),
            None,
        )
        if requested_placement is None:
            raise NotFound("document not found")
        versions = self.store.list_versions(doc["id"])
        for version in versions:
            version.pop("archive_path", None)
        doc["kbs"] = [requested_placement]
        doc["versions"] = versions
        doc["kb_slugs"] = [requested_placement["slug"]]
        doc["sync_states"] = [
            state
            for state in self.store.list_sync_states_for_doc(doc["id"])
            if state["kb_id"] == kb["id"]
        ]
        return doc

    def delete_document(self, actor: str, doc_slug: str, later: bool = True) -> dict[str, str]:
        doc = self._require_doc_admin_visible(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"], active_only=True)
        for kb in kbs:
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    compacted = self.store.cancel_runnable_create_update_jobs(
                        doc["id"], kb["id"], target["slug"]
                    )
                    sync_state = self.store.get_sync_state(
                        doc["id"], kb["id"], target["slug"]
                    )
                    remote_exists = bool(sync_state and sync_state.get("backend_doc_id"))
                    if remote_exists or compacted["running"] > 0 or compacted["failed"] > 0:
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
        return self._sync_runner.sync(
            actor, all_users, backend=backend, progress_callback=progress_callback
        )

    def sync_kb(
        self,
        actor: str,
        kb_slug: str,
        backend: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        """仅处理指定知识库的同步任务。"""
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self._sync_runner.sync(
            actor,
            all_users=False,
            backend=backend,
            progress_callback=progress_callback,
            kb_id=int(kb["id"]),
        )

    def _run_job(
        self,
        job: dict[str, Any],
        recovery_backend_kbs: dict[tuple[int, str], str] | None = None,
        recovered_job_keys: set[tuple[int, int, str]] | None = None,
    ) -> bool:
        return self._sync_runner._run_job(job, recovery_backend_kbs, recovered_job_keys)


    def list_categories(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [
            attach_edit_token(item, self._category_edit_snapshot(item))
            for item in self.store.list_categories()
        ]

    def upsert_category(
        self,
        actor: str,
        *,
        category_key: str,
        name: str,
        description: str,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        current = next(
            (item for item in self.store.list_categories() if item["category_key"] == category_key),
            None,
        )
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._category_edit_snapshot(current) if current else None,
            resource_type="code_repo_category",
            resource_key=category_key,
            actor=actor,
        )
        saved = self.store.upsert_category(
            category_key=category_key,
            name=name,
            description=description,
        )
        return attach_edit_token(saved, self._category_edit_snapshot(saved))

    @staticmethod
    def _category_edit_snapshot(category: dict[str, Any]) -> dict[str, Any]:
        return {
            "category_key": category.get("category_key"),
            "name": category.get("name"),
            "description": category.get("description"),
        }

    def delete_category(self, actor: str, category_key: str) -> None:
        require_admin_user(actor, self.admins)
        self.store.delete_category(category_key=category_key)

    def list_kb_repo_sources(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
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
        self.access.require_resource_read(
            actor=actor,
            resource_type=ScopedResourceType.code_repository,
            resource_key=repo_key,
        )
        suffixes = self._normalize_repo_source_suffixes(include_suffixes)
        return self.store.upsert_kb_repo_source(kb["id"], repo_key, suffixes)

    def sync_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """手动同步:转发到增量 diff 逻辑(行为与定时同步一致)。"""
        return self.sync_kb_repo_source_changes(actor, kb_slug, repo_key)

    def delete_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        return self._repo_sync.delete_kb_repo_source(actor, kb_slug, repo_key)

    def sync_kb_repo_source_changes(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        return self._repo_sync.sync_kb_repo_source_changes(actor, kb_slug, repo_key)

    def _normalize_repo_source_suffixes(self, suffixes: list[str]) -> list[str]:
        return self._repo_sync._normalize_repo_source_suffixes(suffixes)


    def get_sync_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        config = self.store.get_sync_config()
        return attach_edit_token(config, self._sync_config_edit_snapshot(config))

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
        workflow_max_concurrent_runs: int = 4,
        workflow_max_concurrent_runs_per_workflow: int = 2,
        workflow_max_runtime_minutes: int = 30,
        workflow_task_rerun_days: int = 30,
        log_retention_days: int = 180,
        mcp_timeout_seconds: int = DEFAULT_MCP_TIMEOUT_SECONDS,
        understand_timeout_minutes: int = 120,
        artifact_search_cache_ttl_hours: int = 8,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        current = self.store.get_sync_config()
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._sync_config_edit_snapshot(current),
            resource_type="system_config",
            resource_key="knowledge_sync",
            actor=actor,
        )
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
            workflow_max_concurrent_runs=workflow_max_concurrent_runs,
            workflow_max_concurrent_runs_per_workflow=workflow_max_concurrent_runs_per_workflow,
            workflow_max_runtime_minutes=workflow_max_runtime_minutes,
            workflow_task_rerun_days=workflow_task_rerun_days,
            log_retention_days=log_retention_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            understand_timeout_minutes=understand_timeout_minutes,
            artifact_search_cache_ttl_hours=artifact_search_cache_ttl_hours,
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
        return attach_edit_token(result, self._sync_config_edit_snapshot(result))

    @staticmethod
    def _sync_config_edit_snapshot(config: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in config.items()
            if key not in {"edit_token", "updated_at"}
        }

    def get_scheduler_status(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return {
            "code_sync": self.codegraph_scheduler.get_status(),
            "understand": self.understand_scheduler.get_status(),
            "plugin_update": self.plugin_update_scheduler.get_status(),
            "doc_sync": self.doc_sync_scheduler.get_status(),
            "workflow": self.workflow_scheduler.get_status(),
        }

    def get_agent_runtime_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        config = load_agent_runtime_config(self.paths)
        payload = _agent_runtime_config_payload(config, self.agents.coding_agents)
        return attach_edit_token(payload, _agent_runtime_config_payload(config))

    def save_agent_runtime_config(self, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        expected_edit_token = payload.pop("expected_edit_token", None)
        current_config = load_agent_runtime_config(self.paths)
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=_agent_runtime_config_payload(current_config),
            resource_type="system_config",
            resource_key="agent_runtime",
            actor=actor,
        )
        backends = [
            AgentBackendConfig(
                slug=str(item.get("slug") or ""),
                agent_type=str(item.get("type") or item.get("agent_type") or ""),
                command=item.get("command") or None,
                model=item.get("model") or None,
            )
            for item in payload.get("backends", [])
            if isinstance(item, dict)
        ]
        config = AgentRuntimeConfig(
            default_backend=str(payload.get("default_backend") or "claude"),
            backends=tuple(backends),
        )
        try:
            saved = save_agent_runtime_config(self.paths, config)
            registry = create_coding_agent_registry(saved)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self.agents.coding_agents = registry
        logger.info(
            "Agent runtime 配置已保存 default=%s backends=%s",
            saved.default_backend,
            [item.slug for item in saved.backends],
        )
        result = _agent_runtime_config_payload(saved, registry)
        return attach_edit_token(result, _agent_runtime_config_payload(saved))

    def get_claude_mem_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        manager = self.memory.worker_service.config
        config = manager.get_config(bootstrap=True)
        return attach_edit_token(config, manager.edit_snapshot(bootstrap=False))

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
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        manager = self.memory.worker_service.config
        require_edit_token(
            expected_edit_token,
            manager.edit_snapshot(bootstrap=True),
            resource_type="Claude Mem 配置",
            resource_key="global",
            actor=actor,
        )
        config = manager.save_config(
            base_url=base_url,
            auth_token=auth_token,
            api_key=api_key,
            model=model,
            clear_auth_token=clear_auth_token,
            clear_api_key=clear_api_key,
        )
        self.memory.worker_service.stop_all_workers()
        return attach_edit_token(config, manager.edit_snapshot(bootstrap=False))

    def get_retrieval_probe_llm_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        config = self.store.get_retrieval_probe_llm_config()
        return attach_edit_token(
            self._public_retrieval_probe_llm_config(config),
            self._retrieval_probe_llm_edit_snapshot(config),
        )

    def save_retrieval_probe_llm_config(
        self,
        actor: str,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        clear_api_key: bool = False,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        require_edit_token(
            expected_edit_token,
            self._retrieval_probe_llm_edit_snapshot(
                self.store.get_retrieval_probe_llm_config()
            ),
            resource_type="全量检索探测模型配置",
            resource_key="global",
            actor=actor,
        )
        cleaned_url = base_url.strip().rstrip("/")
        cleaned_model = model.strip()
        parsed = urlparse(cleaned_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("base_url 必须是 http 或 https 的完整地址")
        if not cleaned_model:
            raise ValidationError("model 不能为空")
        if clear_api_key and api_key and api_key.strip():
            raise ValidationError("不能同时设置和清除 API Key")
        saved = self.store.save_retrieval_probe_llm_config(
            base_url=cleaned_url,
            model=cleaned_model,
            api_key=api_key.strip() if api_key else None,
            clear_api_key=clear_api_key,
        )
        logger.info(
            "全量检索探测模型配置已保存 base_url=%s model=%s api_key_set=%s",
            cleaned_url,
            cleaned_model,
            bool(saved["api_key"]),
        )
        return attach_edit_token(
            self._public_retrieval_probe_llm_config(saved),
            self._retrieval_probe_llm_edit_snapshot(saved),
        )

    @staticmethod
    def _public_retrieval_probe_llm_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "base_url": str(config.get("base_url") or ""),
            "model": str(config.get("model") or ""),
            "api_key_set": bool(config.get("api_key")),
            "updated_at": config.get("updated_at"),
        }

    @staticmethod
    def _retrieval_probe_llm_edit_snapshot(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "base_url": str(config.get("base_url") or ""),
            "model": str(config.get("model") or ""),
            "api_key": str(config.get("api_key") or ""),
        }

    def status(self, actor: str, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
        require_admin_user(actor, self.admins)
        return {"jobs": self.store.list_all_jobs(backend_slug=backend)}

    def kb_status(
        self,
        actor: str,
        kb_slug: str,
        backend: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        kb = self._require_kb_visible(actor, kb_slug)
        return {
            "jobs": self.store.list_all_jobs(
                backend_slug=backend,
                kb_id=int(kb["id"]),
            )
        }

    def search_all(self, actor: str, question: str, *,
                   profile_key: str | None = None,
                   top_k: int = 6) -> list[dict[str, Any]]:
        from agent_bridge.capability_hub.models import ProfileResourceType

        kbs = self.access.visible_resources(
            actor=actor,
            resource_type=ScopedResourceType.knowledge_base,
        )
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
                _, strategy = self.resolve_retrieval_strategy(kb["slug"], profile_key)
                target = self._resolve_retrieval_target(kb, strategy.backend_slug)
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
        self._require_kb_runtime_allowed(actor, kb_slug, profile_key)
        kb, strategy = self.resolve_retrieval_strategy(kb_slug, profile_key)
        target = self._resolve_retrieval_target(kb, backend_slug or strategy.backend_slug)
        adapter = self._get_adapter(target["slug"])
        return adapter.retrieve(target["backend_kb_id"], question, top_k)

    def update_kb_defaults(self, actor: str, kb_slug: str, *,
                           default_backend_slug: str | None = None,
                           default_agent_id: str | None = None,
                           expected_edit_token: str | None = None) -> dict[str, Any]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        require_edit_token(
            expected_edit_token,
            self._kb_defaults_edit_snapshot(kb),
            resource_type="文档知识库默认检索配置",
            resource_key=kb_slug,
            actor=actor,
        )
        self.store.update_kb_defaults(kb["id"], default_backend_slug, default_agent_id)
        saved = self.store.get_kb_by_slug(kb_slug)
        return attach_edit_token(saved or {}, self._kb_defaults_edit_snapshot(saved))

    @staticmethod
    def _kb_defaults_edit_snapshot(kb: dict[str, Any] | None) -> dict[str, Any] | None:
        if kb is None:
            return None
        return {
            "default_backend_slug": kb.get("default_backend_slug"),
            "default_agent_id": kb.get("default_agent_id"),
        }

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
        return self.docs_knowledge.get_adapter(slug)

    @staticmethod
    def _backend_supports_folders(adapter: Any) -> bool:
        return bool(adapter.capabilities().supports_folders)

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

    # -- 领域服务的门面适配器 --
    # 这些适配器把自身方法以属性查找的形式暴露给注入的领域服务，确保测试中对
    # 门面的 monkeypatch（例如 service._archive_files）仍能影响领域服务的执行。

    def _ingest_facade(self) -> "_IngestFacade":
        return _IngestFacade(self)

    def _sync_runner_facade(self) -> "_SyncRunnerFacade":
        return _SyncRunnerFacade(self)

    def _repo_sync_facade(self) -> "_RepoSyncFacade":
        return _RepoSyncFacade(self)

    # -- 后端 Agent 能力（兼容门面） --

    def list_backend_agents(self, actor: str, slug: str) -> list[dict[str, Any]]:
        return self.docs_knowledge.list_backend_agents(actor, slug)

    def list_backend_agent_types(self, actor: str, slug: str) -> list[dict[str, Any]]:
        return self.docs_knowledge.list_backend_agent_types(actor, slug)

    def create_backend_agent(self, actor: str, slug: str, name: str, preset_id: str) -> dict[str, Any]:
        return self.docs_knowledge.create_backend_agent(
            actor, slug, name, preset_id
        )

    def _is_kb_gone(self, exc: Exception) -> bool:
        return self._sync_runner._is_kb_gone(exc)


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

    def align_backends(self, kb_id: int | None = None) -> None:
        self.docs_knowledge.align_backends(kb_id=kb_id)

    def list_backends(self, actor: str) -> list[dict[str, Any]]:
        return self.docs_knowledge.list_backends(actor)

    def add_backend(self, actor: str, slug: str, backend_type: str, base_url: str | None = None,
                    api_key: str | None = None, timeout: int = 120,
                    embedding_model_id: str | None = None, summary_model_id: str | None = None,
                    rerank_model_id: str | None = None,
                    expected_edit_token: str | None = None) -> dict[str, Any]:
        return self.docs_knowledge.add_backend(
            actor=actor,
            slug=slug,
            backend_type=backend_type,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            embedding_model_id=embedding_model_id,
            summary_model_id=summary_model_id,
            rerank_model_id=rerank_model_id,
            expected_edit_token=expected_edit_token,
        )

    def update_backend(self, actor: str, slug: str, backend_type: str | None = None,
                       base_url: str | None = None, api_key: str | None = None,
                       timeout: int | None = None, embedding_model_id: str | None = None,
                       summary_model_id: str | None = None,
                       rerank_model_id: str | None = None,
                       expected_edit_token: str | None = None) -> dict[str, Any]:
        return self.docs_knowledge.update_backend(
            actor=actor,
            slug=slug,
            backend_type=backend_type,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            embedding_model_id=embedding_model_id,
            summary_model_id=summary_model_id,
            rerank_model_id=rerank_model_id,
            expected_edit_token=expected_edit_token,
        )

    def remove_backend(self, actor: str, slug: str) -> dict[str, str]:
        return self.docs_knowledge.remove_backend(actor, slug)

    def _require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        return self.access.require_resource_write(
            actor=actor,
            resource_type=ScopedResourceType.knowledge_base,
            resource_key=kb_slug,
        )

    def _require_kb_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        return self.access.require_resource_read(
            actor=actor,
            resource_type=ScopedResourceType.knowledge_base,
            resource_key=kb_slug,
        )

    def _require_kb_runtime_allowed(self, actor: str, kb_slug: str, profile_key: str | None) -> dict[str, Any]:
        kb = self._require_kb_visible(actor, kb_slug)
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
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        self.access.require_write(
            actor=actor,
            scope=ResourceScope.from_record(
                {"owner_group_key": doc.get("owner_group_key"), "visibility": "group"}
            ),
        )
        return doc

    def _require_doc_visible(self, actor: str, doc_slug: str) -> dict[str, Any]:
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        placements = self.store.get_document_kbs(doc["id"], active_only=True)
        if not placements:
            if actor not in self.admins:
                raise AccessDenied("文档尚未归属可见知识库")
            return doc
        if not any(
            self.access.can_read(actor=actor, scope=ResourceScope.from_record(kb))
            for kb in placements
        ):
            raise AccessDenied("无权访问其他小组的数据")
        return doc
