"""Git 仓库源增量同步领域服务。

负责把代码仓库中的文档按 include_suffixes 增量同步到知识库：新增文件
导入为 git 文档、删除文件触发软删除、内容变化先删后加、内容不变只对齐
目录 placement。

从 ``AgentBridgeService`` 门面抽出；门面保留 ``sync_kb_repo_source`` /
``sync_kb_repo_source_changes`` / ``delete_kb_repo_source`` 薄转发以维持
API 与测试兼容。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Protocol

from agent_bridge.app.document_paths import (
    normalize_relative_document_path,
    split_document_path,
)
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import (
    NotFound,
    ValidationError,
)
from agent_bridge.core.slug import make_slug, unique_slug
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".markdown", ".csv", ".json",
}


class _FacadeCallbacks(Protocol):
    """门面对 repo_sync 暴露的最小回调表面。"""

    def require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]: ...
    def queue_placement_sync_jobs(self, doc: dict[str, Any], kb_id: int) -> None: ...
    def add_document(
        self,
        actor: str,
        source: Path,
        kb_slugs: list[str],
        later: bool,
        original_filename: str | None = ...,
        source_type: str = ...,
        source_repo_key: str = ...,
        slug_override: str | None = ...,
        folder_id: int | None = ...,
        relative_path: str | None = ...,
    ) -> dict[str, Any]: ...
    def delete_document(self, actor: str, doc_slug: str, later: bool = ...) -> dict[str, str]: ...


class _IngestCallbacks(Protocol):
    """ingest 对 repo_sync 暴露的最小回调表面（目录 placement 创建）。"""

    def _ensure_document_parent_folder(
        self, kb_id: int, base_folder_id: int | None, parent_parts: list[str]
    ) -> int: ...


class GitRepoSyncService:
    """Git 仓库源增量同步领域服务。"""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        codegraph: CodeGraphService,
        paths: AgentBridgePaths,
        facade: _FacadeCallbacks,
        ingest: _IngestCallbacks,
    ) -> None:
        self.store = store
        self.codegraph = codegraph
        self.paths = paths
        self._facade = facade
        self._ingest = ingest

    def delete_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """删除 KB 的 git 数据源:解绑关联 + 软删除该 repo 提供的文档 + 生成 delete 同步任务。

        遵循 delete_document 的顺序:先生成 Operation.delete 任务再 soft_delete。
        保留 code_repositories 记录和本地克隆(其他 KB 可能引用)。
        """
        kb = self._facade.require_kb_admin_visible(actor, kb_slug)
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
        kb = self._facade.require_kb_admin_visible(actor, kb_slug)
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
            kb = self._facade.require_kb_admin_visible(actor, kb_slug)
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

                target_folder_id = self._ingest._ensure_document_parent_folder(
                    kb["id"], None, parent_parts
                )
                self.store.update_document_placement(
                    existing["id"], kb["id"], target_folder_id
                )
                if current_document is not None:
                    self._facade.queue_placement_sync_jobs(current_document, kb["id"])
                return

        self._facade.add_document(
            actor, path, [kb_slug], later=True,
            original_filename=relative_path,
            relative_path=relative_path,
            source_type="git", source_repo_key=repo_key,
            slug_override=slug,
        )

    def _delete_git_document(self, actor: str, doc: dict[str, Any]) -> None:
        self._facade.delete_document(actor, doc["slug"], later=True)
        released_slug = unique_slug(
            f"{doc['slug']}-deleted-{doc['id']}",
            self.store.list_document_slugs(),
        )
        self.store.rename_document_slug(doc["id"], released_slug)

    @staticmethod
    def _sha256_file(path: Path) -> str:
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
