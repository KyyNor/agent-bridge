"""知识库、目录与文档的兼容方法。"""

from __future__ import annotations

import sqlite3
from typing import Any


class KnowledgeFacadeMixin:
    def create_kb(
        self,
        slug: str,
        name: str,
        description: str,
        created_by: str,
        owner_group_key: str = "",
        visibility: str = "group",
        sync_on_upload: bool = False,
    ) -> dict[str, Any]:
        return self.knowledge.create_kb(
            slug=slug,
            name=name,
            description=description,
            created_by=created_by,
            owner_group_key=owner_group_key,
            visibility=visibility,
            sync_on_upload=sync_on_upload,
        )

    def ensure_root_folder(self, kb_id: int) -> dict[str, Any]:
        return self.folders.ensure_root_folder(kb_id=kb_id)

    def get_root_folder(self, kb_id: int) -> dict[str, Any] | None:
        return self.folders.get_root_folder(kb_id=kb_id)

    def get_folder(self, kb_id: int, folder_id: int) -> dict[str, Any] | None:
        return self.folders.get_folder(kb_id=kb_id, folder_id=folder_id)

    def create_folder(self, kb_id: int, parent_id: int | None, name: str) -> dict[str, Any]:
        return self.folders.create_folder(kb_id=kb_id, parent_id=parent_id, name=name)

    def rename_folder(self, kb_id: int, folder_id: int, name: str) -> dict[str, Any]:
        return self.folders.rename_folder(kb_id=kb_id, folder_id=folder_id, name=name)

    def move_folder(self, kb_id: int, folder_id: int, parent_id: int | None) -> dict[str, Any]:
        return self.folders.move_folder(kb_id=kb_id, folder_id=folder_id, parent_id=parent_id)

    def update_folder(
        self,
        kb_id: int,
        folder_id: int,
        *,
        name: str | None = None,
        parent_id: int | None = None,
        parent_provided: bool = False,
    ) -> dict[str, Any]:
        return self.folders.update_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            name=name,
            parent_id=parent_id,
            parent_provided=parent_provided,
        )

    def list_folder_tree(self, kb_id: int) -> list[dict[str, Any]]:
        return self.folders.list_folder_tree(kb_id=kb_id)

    def get_subtree_ids(self, kb_id: int, folder_id: int) -> list[int]:
        return self.folders.get_subtree_ids(kb_id=kb_id, folder_id=folder_id)

    def get_subtree_counts(self, kb_id: int, folder_id: int) -> dict[str, int]:
        return self.folders.get_subtree_counts(kb_id=kb_id, folder_id=folder_id)

    def delete_folder_subtree(self, kb_id: int, folder_id: int) -> dict[str, Any]:
        return self.folders.delete_folder_subtree(kb_id=kb_id, folder_id=folder_id)

    def delete_folder_subtree_atomic(self, kb_id: int, folder_id: int) -> dict[str, Any]:
        return self.knowledge.delete_folder_subtree_atomic(kb_id=kb_id, folder_id=folder_id)

    def upsert_backend_folder_mapping(self, *args, **kwargs) -> dict[str, Any]:
        return self.folders.upsert_backend_folder_mapping(*args, **kwargs)

    def get_backend_folder_mapping(self, *args, **kwargs) -> dict[str, Any] | None:
        return self.folders.get_backend_folder_mapping(*args, **kwargs)

    def delete_backend_folder_mappings(self, *args, **kwargs) -> int:
        return self.folders.delete_backend_folder_mappings(*args, **kwargs)

    def get_kb_by_id(self, kb_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self.knowledge.get_kb_by_id(kb_id=kb_id, conn=conn)

    def get_kb_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self.knowledge.get_kb_by_slug(slug=slug)

    def update_kb_defaults(self, kb_id: int, default_backend_slug: str | None, default_agent_id: str | None) -> None:
        return self.knowledge.update_kb_defaults(kb_id=kb_id, default_backend_slug=default_backend_slug, default_agent_id=default_agent_id)

    def update_kb_sync_policy(self, kb_id: int, sync_on_upload: bool) -> None:
        return self.knowledge.update_kb_sync_policy(kb_id=kb_id, sync_on_upload=sync_on_upload)

    def ensure_backend_target(self, kb_id: int, slug: str, backend_type: str) -> None:
        return self.knowledge.ensure_backend_target(kb_id=kb_id, slug=slug, backend_type=backend_type)

    def list_backend_targets(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_backend_targets(kb_id=kb_id)

    def set_backend_target_status(self, kb_id: int, slug: str, status: str) -> None:
        return self.knowledge.set_backend_target_status(kb_id=kb_id, slug=slug, status=status)

    def update_backend_target_kb_id(self, kb_id: int, slug: str, backend_kb_id: str) -> None:
        return self.knowledge.update_backend_target_kb_id(kb_id=kb_id, slug=slug, backend_kb_id=backend_kb_id)

    def mark_backend_target_error(self, kb_id: int, slug: str, error: str) -> None:
        return self.knowledge.mark_backend_target_error(kb_id=kb_id, slug=slug, error=error)

    def rebuild_backend_target(self, kb_id: int, backend_slug: str, new_backend_kb_id: str) -> int:
        return self.knowledge.rebuild_backend_target(kb_id=kb_id, backend_slug=backend_slug, new_backend_kb_id=new_backend_kb_id)

    def update_backend_target_config(self, kb_id: int, slug: str, config_updates: dict[str, Any]) -> None:
        return self.knowledge.update_backend_target_config(kb_id=kb_id, slug=slug, config_updates=config_updates)

    def list_backends(self) -> list[dict[str, Any]]:
        return self.knowledge.list_backends()

    def get_backend(self, slug: str) -> dict[str, Any] | None:
        return self.knowledge.get_backend(slug=slug)

    def upsert_backend(self, **kwargs) -> dict[str, Any]:
        return self.knowledge.upsert_backend(**kwargs)

    def delete_backend(self, slug: str) -> bool:
        return self.knowledge.delete_backend(slug=slug)

    def upsert_kb_repo_source(self, kb_id: int, repo_key: str, include_suffixes: list[str]) -> dict[str, Any]:
        return self.knowledge.upsert_kb_repo_source(kb_id=kb_id, repo_key=repo_key, include_suffixes=include_suffixes)

    def list_kb_repo_sources(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_kb_repo_sources(kb_id=kb_id)

    def get_kb_repo_source(self, kb_id: int, repo_key: str) -> dict[str, Any] | None:
        return self.knowledge.get_kb_repo_source(kb_id=kb_id, repo_key=repo_key)

    def mark_kb_repo_source_sync(
        self,
        kb_id: int,
        repo_key: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        return self.knowledge.mark_kb_repo_source_sync(kb_id=kb_id, repo_key=repo_key, success=success, error=error)

    def list_git_docs_for_repo(self, kb_id: int, repo_key: str) -> list[dict[str, Any]]:
        return self.knowledge.list_git_docs_for_repo(kb_id=kb_id, repo_key=repo_key)

    def list_all_active_repo_sources(self) -> list[dict[str, Any]]:
        return self.knowledge.list_all_active_repo_sources()

    def delete_kb_repo_source(self, kb_id: int, repo_key: str) -> None:
        return self.knowledge.delete_kb_repo_source(kb_id=kb_id, repo_key=repo_key)

    def delete_kb(self, kb_id: int) -> None:
        return self.knowledge.delete_kb(kb_id=kb_id)

    def list_sync_states_for_doc(self, doc_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_sync_states_for_doc(doc_id=doc_id)

    def list_synced_doc_ids(self, kb_id: int) -> list[int]:
        return self.knowledge.list_synced_doc_ids(kb_id=kb_id)

    def list_kbs(self) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs()

    def list_kbs_for_user(self, linux_user: str) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs_for_user(linux_user=linux_user)

    def list_kbs_for_user_or_admin(self, linux_user: str, admins: set[str]) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs_for_user_or_admin(linux_user=linux_user, admins=admins)

    def grant_member(self, kb_id: int, linux_user: str, role: KbRole) -> None:
        return self.knowledge.grant_member(kb_id=kb_id, linux_user=linux_user, role=role)

    def get_member_role(self, kb_id: int, linux_user: str) -> KbRole | None:
        return self.knowledge.get_member_role(kb_id=kb_id, linux_user=linux_user)

    def list_members(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_members(kb_id=kb_id)

    def list_document_slugs(self) -> set[str]:
        return self.knowledge.list_document_slugs()

    def create_document(
        self,
        slug: str,
        title: str,
        owner_user: str,
        owner_group_key: str = "",
        source_type: str = "manual",
        source_repo_key: str = "",
    ) -> dict[str, Any]:
        return self.knowledge.create_document(
            slug=slug, title=title, owner_user=owner_user, owner_group_key=owner_group_key,
            source_type=source_type, source_repo_key=source_repo_key,
        )

    def get_document_by_slug(self, slug: str, include_deleted: bool = False) -> dict[str, Any] | None:
        return self.knowledge.get_document_by_slug(slug=slug, include_deleted=include_deleted)

    def get_document_by_id(self, doc_id: int, include_deleted: bool = False) -> dict[str, Any] | None:
        return self.knowledge.get_document_by_id(doc_id=doc_id, include_deleted=include_deleted)

    def find_current_document_by_content_hash(self, kb_id: int, content_hash: str) -> dict[str, Any] | None:
        return self.knowledge.find_current_document_by_content_hash(kb_id=kb_id, content_hash=content_hash)

    def create_archive_entry(
        self,
        kb_id: int,
        *,
        kind: str,
        name: str,
        relative_path: str,
        parent_id: int | None = None,
        parent_folder_id: int | None = None,
        doc_id: int | None = None,
    ) -> dict[str, Any]:
        return self.knowledge.create_archive_entry(
            kb_id=kb_id,
            kind=kind,
            name=name,
            relative_path=relative_path,
            parent_id=parent_id,
            parent_folder_id=parent_folder_id,
            doc_id=doc_id,
        )

    def list_archive_entries(
        self,
        kb_id: int,
        *,
        parent_id: int | None = None,
        parent_folder_id: int | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return self.knowledge.list_archive_entries(
            kb_id=kb_id,
            parent_id=parent_id,
            parent_folder_id=parent_folder_id,
            active_only=active_only,
        )

    def get_archive_entry(self, kb_id: int, entry_id: int) -> dict[str, Any] | None:
        return self.knowledge.get_archive_entry(kb_id=kb_id, entry_id=entry_id)

    def update_archive_entry_document(self, entry_id: int, doc_id: int) -> None:
        return self.knowledge.update_archive_entry_document(entry_id=entry_id, doc_id=doc_id)

    def delete_archive_entries_for_kb(self, kb_id: int) -> None:
        return self.knowledge.delete_archive_entries_for_kb(kb_id=kb_id)

    def attach_document_to_kb(
        self,
        doc_id: int,
        kb_id: int,
        added_by: str,
        folder_id: int | None = None,
        archive_entry_id: int | None = None,
    ) -> None:
        return self.knowledge.attach_document_to_kb(
            doc_id=doc_id,
            kb_id=kb_id,
            added_by=added_by,
            folder_id=folder_id,
            archive_entry_id=archive_entry_id,
        )

    def get_document_kbs(self, doc_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
        return self.knowledge.get_document_kbs(doc_id=doc_id, active_only=active_only)

    def get_document_placement(self, doc_id: int, kb_id: int) -> dict[str, Any] | None:
        return self.knowledge.get_document_placement(doc_id=doc_id, kb_id=kb_id)

    def update_document_placement(
        self,
        doc_id: int,
        kb_id: int,
        folder_id: int,
        archive_entry_id: int | None = None,
    ) -> dict[str, Any]:
        return self.knowledge.update_document_placement(
            doc_id=doc_id,
            kb_id=kb_id,
            folder_id=folder_id,
            archive_entry_id=archive_entry_id,
        )

    def remove_document_from_kb(self, doc_id: int, kb_id: int) -> bool:
        return self.knowledge.remove_document_from_kb(doc_id=doc_id, kb_id=kb_id)

    def detach_document_from_kb(self, doc_id: int, kb_id: int) -> bool:
        return self.knowledge.detach_document_from_kb(doc_id=doc_id, kb_id=kb_id)

    def list_versions(self, doc_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_versions(doc_id=doc_id)

    def next_version_no(self, doc_id: int) -> int:
        return self.knowledge.next_version_no(doc_id=doc_id)

    def set_current_version(self, doc_id: int, version_id: int) -> None:
        return self.knowledge.set_current_version(doc_id=doc_id, version_id=version_id)

    def create_document_version(
        self,
        doc_id: int,
        original_filename: str,
        content_hash: str,
        file_size: int,
        mime_type: str,
        archive_path: str,
        created_by: str,
    ) -> dict[str, Any]:
        return self.knowledge.create_document_version(doc_id=doc_id, original_filename=original_filename, content_hash=content_hash, file_size=file_size, mime_type=mime_type, archive_path=archive_path, created_by=created_by)

    def create_sync_job(
        self,
        doc_id: int,
        kb_id: int,
        operation: Operation,
        version_id: int | None,
        backend_slug: str = "mock",
    ) -> dict[str, Any]:
        return self.knowledge.create_sync_job(doc_id=doc_id, kb_id=kb_id, operation=operation, version_id=version_id, backend_slug=backend_slug)

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        return self.knowledge.list_pending_jobs()

    def list_runnable_jobs(
        self,
        actor: str | None,
        backend_slug: str | None = None,
        kb_id: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.knowledge.list_runnable_jobs(
            actor=actor,
            backend_slug=backend_slug,
            kb_id=kb_id,
        )

    def list_all_jobs(
        self,
        backend_slug: str | None = None,
        kb_id: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.knowledge.list_all_jobs(backend_slug=backend_slug, kb_id=kb_id)

    def update_job_status(self, job_id: int, status: SyncJobStatus, error: str | None = None) -> None:
        return self.knowledge.update_job_status(job_id=job_id, status=status, error=error)

    def cancel_runnable_create_update_jobs(self, doc_id: int, kb_id: int, backend_slug: str) -> dict[str, int]:
        return self.knowledge.cancel_runnable_create_update_jobs(doc_id=doc_id, kb_id=kb_id, backend_slug=backend_slug)

    def upsert_sync_state(
        self,
        doc_id: int,
        kb_id: int,
        backend_slug: str,
        backend_doc_id: str | None,
        status: SyncStateStatus,
        backend_status: str | None = None,
        chunk_count: int | None = None,
        progress: float | None = None,
        backend_error: str | None = None,
    ) -> None:
        return self.knowledge.upsert_sync_state(doc_id=doc_id, kb_id=kb_id, backend_slug=backend_slug, backend_doc_id=backend_doc_id, status=status, backend_status=backend_status, chunk_count=chunk_count, progress=progress, backend_error=backend_error)

    def get_sync_state(self, doc_id: int, kb_id: int, backend_slug: str = "mock") -> dict[str, Any] | None:
        return self.knowledge.get_sync_state(doc_id=doc_id, kb_id=kb_id, backend_slug=backend_slug)

    def list_docs_for_kb(
        self,
        kb_id: int,
        folder_id: int | None = None,
        backend_slug: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.knowledge.list_docs_for_kb(
            kb_id=kb_id,
            folder_id=folder_id,
            backend_slug=backend_slug,
        )

    def get_kb_document_counts(self, kb_id: int) -> dict[str, int]:
        return self.knowledge.get_kb_document_counts(kb_id=kb_id)

    def list_jobs_for_user(self, linux_user: str, backend_slug: str | None = None) -> list[dict[str, Any]]:
        return self.knowledge.list_jobs_for_user(linux_user=linux_user, backend_slug=backend_slug)

    def soft_delete_document(self, doc_id: int) -> None:
        return self.knowledge.soft_delete_document(doc_id=doc_id)

    def rename_document_slug(self, doc_id: int, slug: str) -> None:
        return self.knowledge.rename_document_slug(doc_id=doc_id, slug=slug)

    def purge_document(self, doc_id: int) -> list[str]:
        return self.knowledge.purge_document(doc_id=doc_id)
