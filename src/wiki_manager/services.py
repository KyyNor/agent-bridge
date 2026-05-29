"""Application services for wiki-manager."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from wiki_manager.archive import ArchiveStorage
from wiki_manager.config import WikiManagerPaths, ensure_directories
from wiki_manager.domain import (
    AccessDenied,
    KbRole,
    NotFound,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    ValidationError,
    can_manage_kb,
    can_write_own_doc,
    require_admin_user,
)
from wiki_manager.mock_backend import MockBackend
from wiki_manager.slug import make_slug, unique_slug
from wiki_manager.storage import SQLiteStore


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}


class WikiManagerService:
    def __init__(
        self,
        paths: WikiManagerPaths,
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

    @classmethod
    def create(cls, paths: WikiManagerPaths, admins: set[str]) -> "WikiManagerService":
        return cls(
            paths=paths,
            store=SQLiteStore(paths.db_path),
            archive=ArchiveStorage(paths.archive_dir),
            mock_backend=MockBackend(paths.mock_backend_dir),
            admins=admins,
        )

    def init_system(self) -> None:
        ensure_directories(self.paths)
        self.store.init_schema()

    def create_kb(self, actor: str, slug: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.create_kb(slug=slug, name=name, description=description, created_by=actor)
        self.store.grant_member(kb["id"], actor, KbRole.admin)
        self.store.ensure_backend_target(kb["id"], slug="mock", backend_type="mock")
        return kb

    def grant_kb_member(self, actor: str, kb_slug: str, linux_user: str, role: KbRole) -> dict[str, str]:
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        if actor not in self.admins and not can_manage_kb(self.store.get_member_role(kb["id"], actor)):
            raise AccessDenied("knowledge base admin permission required")
        self.store.grant_member(kb["id"], linux_user, role)
        return {"kb_slug": kb_slug, "linux_user": linux_user, "role": role.value}

    def list_kbs(self, actor: str) -> list[dict[str, Any]]:
        kbs = self.store.list_kbs_for_user_or_admin(actor, self.admins)
        if actor in self.admins:
            for kb in kbs:
                kb["role"] = kb["role"] or KbRole.admin.value
        return kbs

    def list_kb_members(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
        if actor not in self.admins and not can_manage_kb(self.store.get_member_role(kb["id"], actor)):
            raise AccessDenied("knowledge base admin permission required")
        return self.store.list_members(kb["id"])

    def add_document(
        self,
        actor: str,
        source: Path,
        kb_slugs: list[str],
        later: bool,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        if not kb_slugs:
            raise ValidationError("at least one knowledge base is required")
        self._validate_source(source)
        kbs = [self._require_kb_visible(actor, kb_slug) for kb_slug in kb_slugs]
        for kb in kbs:
            self._require_kb_write(actor, kb)

        display_name = original_filename or source.name
        slug = unique_slug(make_slug(display_name), self.store.list_document_slugs())
        archived = self.archive.store(source)
        doc = self.store.create_document(slug=slug, title=Path(display_name).stem, owner_user=actor)
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
            self.store.create_sync_job(doc["id"], kb["id"], Operation.create, version["id"])

        doc["current_version_no"] = version["version_no"]
        doc["kb_slugs"] = [kb["slug"] for kb in kbs]
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
        doc = self._require_doc_edit(actor, doc_slug)
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
            self.store.create_sync_job(doc["id"], kb["id"], Operation.update, version["id"])
        doc["current_version_no"] = version["version_no"]
        if not later:
            self.sync(actor=actor, all_users=False)
        return doc

    def list_docs(self, actor: str, kb_slug: str) -> list[dict[str, Any]]:
        kb = self._require_kb_visible(actor, kb_slug)
        return self.store.list_docs_for_kb(kb["id"])

    def get_doc(self, actor: str, doc_slug: str) -> dict[str, Any]:
        doc = self._require_doc_visible(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"])
        versions = self.store.list_versions(doc["id"])
        doc["kbs"] = kbs
        doc["versions"] = versions
        doc["kb_slugs"] = [kb["slug"] for kb in kbs]
        return doc

    def delete_document(self, actor: str, doc_slug: str, later: bool = True) -> dict[str, str]:
        doc = self._require_doc_edit(actor, doc_slug)
        kbs = self.store.get_document_kbs(doc["id"])
        for kb in kbs:
            self.store.create_sync_job(doc["id"], kb["id"], Operation.delete, doc["current_version_id"])
        self.store.soft_delete_document(doc["id"])
        if not later:
            self.sync(actor=actor, all_users=False)
        return {"slug": doc_slug, "status": "deleted"}

    def sync(self, actor: str, all_users: bool) -> dict[str, int]:
        if all_users:
            require_admin_user(actor, self.admins)
        jobs = self.store.list_runnable_jobs(actor=None if all_users or actor in self.admins else actor)
        processed = 0
        for job in jobs:
            self._run_job(job)
            processed += 1
        return {"processed": processed}

    def status(self, actor: str) -> dict[str, list[dict[str, Any]]]:
        jobs = self.store.list_all_jobs() if actor in self.admins else self.store.list_jobs_for_user(actor)
        return {"jobs": jobs}

    def _run_job(self, job: dict[str, Any]) -> None:
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        try:
            if job["operation"] == "delete":
                self.mock_backend.delete_document(job["kb_slug"], job["doc_slug"])
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    None,
                    SyncStateStatus.deleted,
                )
            else:
                backend_doc_id = self.mock_backend.upsert_document(
                    kb_slug=job["kb_slug"],
                    doc_slug=job["doc_slug"],
                    version_no=job["version_no"],
                    archive_path=job["archive_path"],
                )
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    backend_doc_id,
                    SyncStateStatus.synced,
                )
            self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
        except Exception as exc:
            self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))

    def purge_document(self, actor: str, doc_slug: str) -> dict[str, str]:
        doc = self._require_doc_edit(actor, doc_slug, include_deleted=True)
        archive_paths = self.store.purge_document(doc["id"])
        for archive_path in archive_paths:
            self.archive.remove(Path(archive_path))
        return {"slug": doc_slug, "status": "purged"}

    def _require_kb_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        if actor in self.admins:
            return kb
        if self.store.get_member_role(kb["id"], actor) is None:
            raise NotFound("knowledge base not found")
        return kb

    def _require_kb_write(self, actor: str, kb: dict[str, Any]) -> KbRole:
        if actor in self.admins:
            return KbRole.admin
        role = self.store.get_member_role(kb["id"], actor)
        if not can_write_own_doc(role):
            raise AccessDenied("contributor permission required")
        return role

    def _validate_source(self, source: Path) -> None:
        if not source.is_file():
            raise ValidationError("source file does not exist")
        if source.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValidationError("unsupported file type")

    def _require_doc_visible(self, actor: str, doc_slug: str) -> dict[str, Any]:
        doc = self.store.get_document_by_slug(doc_slug)
        if doc is None:
            raise NotFound("document not found")
        if actor in self.admins or self._actor_can_access_doc(actor, doc):
            return doc
        raise NotFound("document not found")

    def _require_doc_edit(self, actor: str, doc_slug: str, include_deleted: bool = False) -> dict[str, Any]:
        doc = self.store.get_document_by_slug(doc_slug, include_deleted=include_deleted)
        if doc is None:
            raise NotFound("document not found")
        if actor in self.admins or doc["owner_user"] == actor or self._actor_admin_for_document(actor, doc):
            return doc
        if not self._actor_can_access_doc(actor, doc):
            raise NotFound("document not found")
        raise AccessDenied("document owner or knowledge base admin permission required")

    def _actor_can_access_doc(self, actor: str, doc: dict[str, Any]) -> bool:
        return any(self.store.get_member_role(kb["id"], actor) is not None for kb in self.store.get_document_kbs(doc["id"]))

    def _actor_admin_for_document(self, actor: str, doc: dict[str, Any]) -> bool:
        return any(
            can_manage_kb(self.store.get_member_role(kb["id"], actor)) for kb in self.store.get_document_kbs(doc["id"])
        )

    @staticmethod
    def _mime_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"
