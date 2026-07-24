"""文档导入/上传领域服务。

负责单文件与 zip 压缩包的入档编排：去重、归档存储、版本创建、目录
placement、同步任务入队。本服务从 ``AgentBridgeService`` 门面抽出，
门面通过薄转发维持对外兼容。

注入策略：
- ``store`` / ``archive`` 是注入的存储 collaborator。
- 门面提供一组回调（校验、归档快照、权限校验、立即同步触发），保证门面
  的 monkeypatch 点（如 ``_archive_files``）仍能影响本服务的行为。
"""

from __future__ import annotations

import logging
import mimetypes
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

from agent_bridge.app.document_paths import (
    normalize_relative_document_path,
    split_document_path,
)
from agent_bridge.core.domain import (
    NotFound,
    Operation,
    ValidationError,
    require_admin_user,
)
from agent_bridge.core.slug import make_slug, unique_slug
from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
from agent_bridge.knowledge_management.docs_knowledge.uploads import extract_zip_documents
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".markdown", ".csv", ".json",
}
UPLOAD_EXTENSIONS = ALLOWED_EXTENSIONS | {".zip"}


class _FacadeCallbacks(Protocol):
    """门面对 ingest 暴露的最小回调表面。

    保留为 Protocol 而非引用整个 ``AgentBridgeService``，避免循环依赖。
    门面在构造 ingest 时把自身相关方法作为可调用对象注入。
    """

    def validate_source(self, source: Path, allowed_extensions: set[str] | None = ...) -> None: ...
    def require_kb_admin_visible(self, actor: str, kb_slug: str) -> dict[str, Any]: ...
    def require_doc_admin_visible(self, actor: str, doc_slug: str) -> dict[str, Any]: ...
    def archive_files(self) -> set[Path]: ...
    def remove_new_archive_files(self, existing_files: set[Path]) -> None: ...
    def trigger_sync(self, actor: str) -> None: ...


class DocumentIngestService:
    """文档导入与上传编排领域服务。"""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        archive: ArchiveStorage,
        admins: set[str],
        facade: _FacadeCallbacks,
    ) -> None:
        self.store = store
        self.archive = archive
        self.admins = admins
        self._facade = facade
        self._document_ingest_lock = RLock()

    # -- 入口 --

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
        self._facade.validate_source(source, allowed_extensions=UPLOAD_EXTENSIONS)
        if folder_id is not None and len(kb_slugs) != 1:
            raise ValidationError("folder_id can only be used with one knowledge base")
        kbs = [self._facade.require_kb_admin_visible(actor, kb_slug) for kb_slug in kb_slugs]
        if folder_id is not None and self.store.get_folder(kbs[0]["id"], folder_id) is None:
            raise NotFound("folder not found")

        display_name = original_filename or source.name
        with self._document_ingest_lock:
            if source.suffix.lower() == ".zip":
                result = self._add_zip_documents(
                    actor, source, display_name, kbs, True, source_type, source_repo_key,
                    folder_id=folder_id, relative_path=relative_path,
                )
            else:
                result = self._add_single_document(
                    actor=actor,
                    source=source,
                    kb_targets=kbs,
                    display_name=display_name,
                    later=True,
                    source_type=source_type,
                    source_repo_key=source_repo_key,
                    slug_override=slug_override,
                    folder_id=folder_id,
                    relative_path=relative_path,
                )
        if not later:
            self._facade.trigger_sync(actor=actor)
        return result

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
        archive_entry_pending: bool = False,
    ) -> dict[str, Any]:
        document_path = normalize_relative_document_path(relative_path or display_name or source.name)
        parent_parts, basename = split_document_path(document_path)
        placement_parent_parts = (
            []
            if archive_entry_pending or archive_entry_id is not None or archive_entry_ids
            else parent_parts
        )
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
                self._facade.trigger_sync(actor=actor)
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
            self._facade.trigger_sync(actor=actor)
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
                extracted = extract_zip_documents(
                    source,
                    Path(temp_dir),
                    ALLOWED_EXTENSIONS,
                    archive_name=display_name,
                )
            except ValidationError as exc:
                message = str(exc)
                if "ZIP 解压失败" in message:
                    raise ValidationError(f"压缩包解压失败：{message}") from exc
                if "ZIP 成员路径不安全" in message:
                    raise ValidationError(f"压缩包成员路径不安全：{message}") from exc
                if "ZIP 压缩层没有支持的后代文档" in message:
                    raise ValidationError(
                        f"压缩包中没有支持的文档：{message}"
                    ) from exc
                raise
            outer_path = normalize_relative_document_path(relative_path or display_name)
            with self._document_ingest_lock:
                archive_files_before = self._facade.archive_files()
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
                                archive_entry_pending=True,
                            )
                            for kb in kb_targets:
                                entry_id = document_archive_entry_ids[kb["id"]]
                                self.store.update_archive_entry_document(entry_id, result["id"])
                                placement = self.store.get_document_placement(result["id"], kb["id"])
                                if placement is None:
                                    raise NotFound("document knowledge-base placement not found")
                                if placement["archive_entry_id"] is None:
                                    self.store.update_document_placement(
                                        result["id"],
                                        kb["id"],
                                        placement["folder_id"],
                                        archive_entry_id=entry_id,
                                    )
                            results.append(result)
                except Exception:
                    self._facade.remove_new_archive_files(archive_files_before)
                    raise

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
        doc = self._facade.require_doc_admin_visible(actor, doc_slug)
        self._facade.validate_source(source)
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
            self._facade.trigger_sync(actor=actor)
        return doc

    @staticmethod
    def _mime_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"
