"""Application services for Agent Bridge."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any, Callable

from agent_bridge.agent_runtime.service import AgentService
from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
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


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}
SUPPORTED_BACKEND_TYPES = {"mock", "ragflow", "weknora", "pageindex"}


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

    def add_document(
        self,
        actor: str,
        source: Path,
        kb_slugs: list[str],
        later: bool,
        original_filename: str | None = None,
        source_type: str = "manual",
        source_repo_key: str = "",
    ) -> dict[str, Any]:
        if not kb_slugs:
            raise ValidationError("at least one knowledge base is required")
        require_admin_user(actor, self.admins)
        self._validate_source(source)
        kbs = [self._require_kb_admin_visible(actor, kb_slug) for kb_slug in kb_slugs]

        display_name = original_filename or source.name
        slug = unique_slug(make_slug(display_name), self.store.list_document_slugs())
        archived = self.archive.store(source)
        doc = self.store.create_document(
            slug=slug, title=Path(display_name).stem, owner_user=actor,
            source_type=source_type, source_repo_key=source_repo_key,
        )
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
            self.store.attach_document_to_kb(doc["id"], kb["id"], actor)
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    self.store.create_sync_job(
                        doc["id"], kb["id"], Operation.create, version["id"],
                        backend_slug=target["slug"],
                    )

        doc["current_version_no"] = version["version_no"]
        doc["kb_slugs"] = [kb["slug"] for kb in kbs]
        logger.info(
            "文档已入档 doc=%s KB数=%d 立即同步=%s", slug, len(kbs), not later
        )
        if not later:
            self.sync(actor=actor, all_users=False)
        return doc

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
        kbs = self.store.get_document_kbs(doc["id"])
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

    def list_docs(self, actor: str, kb_slug: str, backend: str | None = None) -> list[dict[str, Any]]:
        kb = self._require_kb_admin_visible(actor, kb_slug)
        return self.store.list_docs_for_kb(kb["id"])

    def get_doc(self, actor: str, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        doc = self._require_doc_admin_visible(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"])
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
        kbs = self.store.get_document_kbs(doc["id"])
        if not any(item["id"] == kb["id"] for item in kbs):
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
        kbs = self.store.get_document_kbs(doc["id"])
        for kb in kbs:
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
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
        jobs = self.store.list_runnable_jobs(
            actor=None,
            backend_slug=backend,
        )
        logger.info("文档同步: %d 个待处理任务", len(jobs))
        succeeded = 0
        failed = 0
        if progress_callback:
            progress_callback({"event": "start", "total": len(jobs), "processed": 0, "succeeded": 0, "failed": 0})
        for index, job in enumerate(jobs, start=1):
            if progress_callback:
                progress_callback({
                    "event": "job_start",
                    "total": len(jobs),
                    "processed": index - 1,
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
            ok = self._run_job(job)
            if ok:
                succeeded += 1
            else:
                failed += 1
            if progress_callback:
                progress_callback({
                    "event": "job_done",
                    "total": len(jobs),
                    "processed": index,
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
        logger.info("文档同步完成: %d 成功, %d 失败", succeeded, failed)
        if progress_callback:
            progress_callback({"event": "finish", "total": len(jobs), "processed": len(jobs), "succeeded": succeeded, "failed": failed})
        return {"processed": len(jobs), "succeeded": succeeded, "failed": failed}

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
            self.delete_document(actor, doc["slug"], later=True)
        self.store.delete_kb_repo_source(kb["id"], repo_key)
        logger.info("git 数据源已删除 kb=%s repo=%s 删除文档数=%d", kb_slug, repo_key, len(git_docs))
        return {"kb_slug": kb_slug, "repo_key": repo_key, "deleted_docs": len(git_docs)}

    def sync_kb_repo_source_changes(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """增量同步:对比仓库文件与已导入文档,生成 create/delete 同步任务。

        diff 口径:按 slug + repo_key 匹配。
        - 新增文件 → add_document(source_type='git')
        - 仓库已删除 → delete_document(先生成 Operation.delete 任务再 soft_delete)
        - 内容修改 → 先删后加(doc_id 变化)
        - 内容不变 → 跳过
        """
        kb = self._require_kb_admin_visible(actor, kb_slug)
        source = self.store.get_kb_repo_source(kb["id"], repo_key)
        if source is None:
            raise NotFound("knowledge repo source not found")
        repo = self.store.get_code_repository(repo_key)
        if repo is None:
            raise NotFound("code repository not found")

        local_path = Path(str(repo.get("local_path") or "")) if repo.get("local_path") else self.paths.repos_dir / repo_key
        try:
            if not local_path.exists():
                self.codegraph.sync_repository(actor, repo_key)
                repo = self.store.get_code_repository(repo_key) or repo
                local_path = Path(str(repo.get("local_path") or "")) if repo.get("local_path") else self.paths.repos_dir / repo_key
            if not local_path.exists():
                raise ValidationError("code repository has not been synced")

            suffixes = set(source["include_suffixes"])
            # existing: {slug: content_hash}
            existing = {
                d["slug"]: (d.get("content_hash") or "")
                for d in self.store.list_git_docs_for_repo(kb["id"], repo_key)
            }
            existing_slugs = set(existing.keys())

            # current: 扫描仓库,计算每个文件的 (slug, content_hash)
            current: dict[str, str] = {}
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
                slug = make_slug(Path(path.name).stem)
                current[slug] = self._sha256_file(path)

            added = removed = updated = unchanged = 0
            # 新增 + 修改
            for slug, content_hash in current.items():
                if slug not in existing_slugs:
                    self._import_repo_file(actor, kb_slug, repo_key, local_path, suffixes, slug)
                    added += 1
                elif existing[slug] != content_hash:
                    # 修改:先删后加
                    self.delete_document(actor, slug, later=True)
                    self._import_repo_file(actor, kb_slug, repo_key, local_path, suffixes, slug)
                    updated += 1
                else:
                    unchanged += 1
            # 删除
            for slug in existing_slugs - set(current.keys()):
                self.delete_document(actor, slug, later=True)
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
        local_path: Path,
        suffixes: set[str],
        target_slug: str,
    ) -> None:
        """按 slug 找到仓库内第一个匹配文件并导入为 git 文档。"""
        for path in sorted(local_path.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative_parts = path.relative_to(local_path).parts
            except ValueError:
                continue
            if ".git" in relative_parts:
                continue
            if path.suffix.lower() not in suffixes or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            if make_slug(Path(path.name).stem) == target_slug:
                self.add_document(
                    actor, path, [kb_slug], later=True,
                    original_filename=path.name,
                    source_type="git", source_repo_key=repo_key,
                )
                return

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

    def _run_job(self, job: dict[str, Any]) -> bool:
        doc_title = job.get("doc_title", job.get("doc_slug", "?"))
        backend = job.get("backend_slug", "?")
        op = job.get("operation", "?")
        logger.info("文档同步任务 #%d: %s '%s' -> %s", job["id"], op, doc_title, backend)
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        adapter = self.registry.get(job["backend_slug"]) if self.registry else None
        if adapter is None:
            adapter = self.mock_backend
        try:
            backend_kb_id = job.get("backend_kb_id") or job["kb_slug"]
            if job["operation"] == "delete":
                sync_state = self.store.get_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"])
                backend_doc_id = sync_state["backend_doc_id"] if sync_state else None
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
                backend_doc_id = adapter.upload(
                    backend_kb_id=backend_kb_id,
                    doc_slug=job["doc_slug"],
                    file_path=Path(job["archive_path"]),
                    filename=job.get("original_filename") or job["doc_slug"],
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
            if self._is_kb_gone(exc) and job.get("kb_name") and job.get("kb_slug"):
                logger.warning("文档同步任务 #%d: 后端 KB 已丢失，正在重建...", job["id"])
                try:
                    new_id = adapter.create_kb(job["kb_slug"], job["kb_name"])
                    doc_count = self.store.rebuild_backend_target(job["kb_id"], job["backend_slug"], new_id)
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
            self.store.upsert_sync_state(
                job["doc_id"],
                job["kb_id"],
                job["backend_slug"],
                None,
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
            existing_slugs = {t["slug"] for t in existing_targets}

            # Mark removed backends as inactive
            for target in existing_targets:
                if target["slug"] not in configured_slugs and target["status"] == "active":
                    self.store.set_backend_target_status(kb["id"], target["slug"], "inactive")

            # Add new backends and create pending sync jobs for existing docs
            for backend_slug in configured_slugs:
                if backend_slug not in existing_slugs:
                    adapter = self.registry.get(backend_slug)
                    try:
                        backend_kb_id = adapter.create_kb(kb["slug"], kb["name"]) if adapter else None
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                        if backend_kb_id:
                            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                    except Exception:
                        self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)

                # Reactivate previously inactive targets
                for target in existing_targets:
                    if target["slug"] == backend_slug and target["status"] == "inactive":
                        adapter = self.registry.get(backend_slug)
                        if adapter and not target.get("backend_kb_id"):
                            try:
                                backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
                                self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                            except Exception:
                                pass
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

    def _validate_source(self, source: Path) -> None:
        if not source.is_file():
            raise ValidationError("source file does not exist")
        if source.suffix.lower() not in ALLOWED_EXTENSIONS:
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
