"""Application services for wiki-manager."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from wiki_manager.archive import ArchiveStorage
from wiki_manager.config import WikiManagerPaths, ensure_directories
from wiki_manager.domain import (
    AccessDenied,
    AskResult,
    KbRole,
    NotFound,
    Operation,
    RetrievalResult,
    SyncJobStatus,
    SyncStateStatus,
    ValidationError,
    can_manage_kb,
    can_write_own_doc,
    require_admin_user,
)
from wiki_manager.mock_backend import MockBackend
from wiki_manager.registry import BackendRegistry, create_registry
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
        self.registry: BackendRegistry | None = None

    @classmethod
    def create(cls, paths: WikiManagerPaths, admins: set[str]) -> "WikiManagerService":
        service = cls(
            paths=paths,
            store=SQLiteStore(paths.db_path),
            archive=ArchiveStorage(paths.archive_dir),
            mock_backend=MockBackend(paths.mock_backend_dir),
            admins=admins,
        )
        service.registry = create_registry(paths)
        return service

    def init_system(self) -> None:
        ensure_directories(self.paths)
        self.store.init_schema()

    def create_kb(self, actor: str, slug: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.create_kb(slug=slug, name=name, description=description, created_by=actor)
        self.store.grant_member(kb["id"], actor, KbRole.admin)
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
            targets = self.store.list_backend_targets(kb["id"])
            for target in targets:
                if target["status"] == "active":
                    self.store.create_sync_job(
                        doc["id"], kb["id"], Operation.create, version["id"],
                        backend_slug=target["slug"],
                    )

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
        kb = self._require_kb_visible(actor, kb_slug)
        return self.store.list_docs_for_kb(kb["id"])

    def get_doc(self, actor: str, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        doc = self._require_doc_visible(actor, doc_slug)
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

    def delete_document(self, actor: str, doc_slug: str, later: bool = True) -> dict[str, str]:
        doc = self._require_doc_edit(actor, doc_slug)
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

    def sync(self, actor: str, all_users: bool, backend: str | None = None) -> dict[str, int]:
        if all_users:
            require_admin_user(actor, self.admins)
        jobs = self.store.list_runnable_jobs(
            actor=None if all_users or actor in self.admins else actor,
            backend_slug=backend,
        )
        processed = 0
        for job in jobs:
            self._run_job(job)
            processed += 1
        return {"processed": processed}

    def status(self, actor: str, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
        if actor in self.admins:
            jobs = self.store.list_all_jobs(backend_slug=backend)
        else:
            jobs = self.store.list_jobs_for_user(actor, backend_slug=backend)
        return {"jobs": jobs}

    def search(self, actor: str, kb_slug: str, question: str, *,
               backend_slug: str | None = None,
               top_k: int = 6) -> list[RetrievalResult]:
        kb = self._require_kb_visible(actor, kb_slug)
        target = self._resolve_retrieval_target(kb, backend_slug)
        adapter = self._get_adapter(target["slug"])
        return adapter.retrieve(target["backend_kb_id"], question, top_k)

    def ask(self, actor: str, kb_slug: str, question: str, *,
            backend_slug: str | None = None,
            session_id: str | None = None) -> AskResult:
        kb = self._require_kb_visible(actor, kb_slug)
        target = self._resolve_retrieval_target(kb, backend_slug)
        adapter = self._get_adapter(target["slug"])
        config_json = target.get("config_json")
        existing_chat_id = None
        if config_json:
            import json
            config = json.loads(config_json) if isinstance(config_json, str) else config_json
            existing_chat_id = config.get("chat_id")
        result, new_chat_id = adapter.ask(
            target["backend_kb_id"], question,
            chat_id=existing_chat_id, session_id=session_id,
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
            from wiki_manager.config import load_server_config
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

    def _run_job(self, job: dict[str, Any]) -> None:
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        adapter = self.registry.get(job["backend_slug"]) if self.registry else None
        if adapter is None:
            adapter = self.mock_backend
        try:
            if job["operation"] == "delete":
                sync_state = self.store.get_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"])
                backend_doc_id = sync_state["backend_doc_id"] if sync_state else None
                if backend_doc_id:
                    adapter.delete(job["kb_slug"], backend_doc_id)
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    None,
                    SyncStateStatus.deleted,
                )
            else:
                backend_doc_id = adapter.upload(
                    backend_kb_id=job["kb_slug"],
                    doc_slug=job["doc_slug"],
                    file_path=Path(job["archive_path"]),
                    filename=job["doc_slug"],
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

    def purge_document(self, actor: str, doc_slug: str, confirm: bool = False) -> dict[str, str]:
        doc = self._require_doc_edit(actor, doc_slug, include_deleted=True)
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

                    # Create pending sync jobs for existing synced docs
                    synced = self.store.list_synced_docs_for_target(kb["id"], backend_slug)
                    for row in synced:
                        versions = self.store.list_versions(row["doc_id"])
                        version_id = versions[-1]["id"] if versions else None
                        self.store.create_sync_job(
                            row["doc_id"], kb["id"], Operation.create, version_id,
                            backend_slug=backend_slug,
                        )

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
        kbs = self.store.get_document_kbs(doc["id"])
        return bool(kbs) and all(can_manage_kb(self.store.get_member_role(kb["id"], actor)) for kb in kbs)

    @staticmethod
    def _mime_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"
