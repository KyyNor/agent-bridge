"""SQLite knowledge repository."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.core.domain import DocumentStatus, KbRole, Operation, SyncJobStatus, SyncStateStatus
from agent_bridge.storage.types import enum_value, row_to_dict


class KnowledgeRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def create_kb(self, slug: str, name: str, description: str, created_by: str) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_bases (slug, name, description, created_by) VALUES (?, ?, ?, ?)",
                (slug, name, description, created_by),
            )
            return self.get_kb_by_id(cursor.lastrowid, conn)

    def get_kb_by_id(self, kb_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        if conn is not None:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            if row is None:
                raise KeyError(f"kb not found: {kb_id}")
            return dict(row)

        with self._connect() as own_conn:
            return self.get_kb_by_id(kb_id, own_conn)

    def get_kb_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE slug = ?", (slug,)).fetchone()
            return row_to_dict(row)

    def list_kbs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_bases WHERE status = 'active' ORDER BY slug",
            ).fetchall()
            return [dict(row) for row in rows]

    def list_kbs_for_user(self, linux_user: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT kb.*
                FROM knowledge_bases kb
                JOIN knowledge_base_members member ON member.kb_id = kb.id
                WHERE member.linux_user = ? AND kb.status = 'active'
                ORDER BY kb.slug
                """,
                (linux_user,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_kbs_for_user_or_admin(self, linux_user: str, admins: set[str]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if linux_user in admins:
                rows = conn.execute(
                    """
                    SELECT kb.*, member.role AS role
                    FROM knowledge_bases kb
                    LEFT JOIN knowledge_base_members member
                      ON member.kb_id = kb.id AND member.linux_user = ?
                    WHERE kb.status = 'active'
                    ORDER BY kb.slug
                    """,
                    (linux_user,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT kb.*, member.role AS role
                    FROM knowledge_bases kb
                    JOIN knowledge_base_members member ON member.kb_id = kb.id
                    WHERE member.linux_user = ? AND kb.status = 'active'
                    ORDER BY kb.slug
                    """,
                    (linux_user,),
                ).fetchall()
            return [dict(row) for row in rows]

    def grant_member(self, kb_id: int, linux_user: str, role: KbRole) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_base_members (kb_id, linux_user, role)
                VALUES (?, ?, ?)
                ON CONFLICT(kb_id, linux_user) DO UPDATE SET
                  role = excluded.role,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (kb_id, linux_user, role.value),
            )

    def get_member_role(self, kb_id: int, linux_user: str) -> KbRole | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT role FROM knowledge_base_members WHERE kb_id = ? AND linux_user = ?",
                (kb_id, linux_user),
            ).fetchone()
            if row is None:
                return None
            return KbRole(row["role"])

    def list_members(self, kb_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT linux_user, role, created_at, updated_at
                FROM knowledge_base_members
                WHERE kb_id = ?
                ORDER BY linux_user
                """,
                (kb_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_document_slugs(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT slug FROM documents").fetchall()
            return {row["slug"] for row in rows}

    def create_document(self, slug: str, title: str, owner_user: str) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO documents (slug, title, owner_user) VALUES (?, ?, ?)",
                (slug, title, owner_user),
            )
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
            document = row_to_dict(row)
            if document is None:
                raise KeyError(f"document not found: {cursor.lastrowid}")
            return document

    def get_document_by_slug(self, slug: str, include_deleted: bool = False) -> dict[str, Any] | None:
        with self._connect() as conn:
            if include_deleted:
                row = conn.execute("SELECT * FROM documents WHERE slug = ?", (slug,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM documents WHERE slug = ? AND status != ?",
                    (slug, DocumentStatus.deleted.value),
                ).fetchone()
            return row_to_dict(row)

    def attach_document_to_kb(self, doc_id: int, kb_id: int, added_by: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO document_kbs (doc_id, kb_id, added_by)
                VALUES (?, ?, ?)
                ON CONFLICT(doc_id, kb_id) DO UPDATE SET
                  status = 'active',
                  added_by = excluded.added_by,
                  deleted_at = NULL
                """,
                (doc_id, kb_id, added_by),
            )

    def get_document_kbs(self, doc_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT kb.*, dk.status AS document_kb_status
                FROM document_kbs dk
                JOIN knowledge_bases kb ON kb.id = dk.kb_id
                WHERE dk.doc_id = ?
                ORDER BY kb.slug
                """,
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def soft_delete_document(self, doc_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET status = ?, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (DocumentStatus.deleted.value, doc_id),
            )
            conn.execute(
                """
                UPDATE document_kbs
                SET status = ?, deleted_at = CURRENT_TIMESTAMP
                WHERE doc_id = ?
                """,
                (DocumentStatus.deleted.value, doc_id),
            )

    def purge_document(self, doc_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT archive_path FROM document_versions WHERE doc_id = ? ORDER BY version_no",
                (doc_id,),
            ).fetchall()
            archive_paths = list(dict.fromkeys(row["archive_path"] for row in rows))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            if not archive_paths:
                return []
            placeholders = ", ".join("?" for _ in archive_paths)
            remaining_rows = conn.execute(
                f"SELECT DISTINCT archive_path FROM document_versions WHERE archive_path IN ({placeholders})",
                archive_paths,
            ).fetchall()
            remaining_paths = {row["archive_path"] for row in remaining_rows}
            return [archive_path for archive_path in archive_paths if archive_path not in remaining_paths]

    def list_versions(self, doc_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_versions WHERE doc_id = ? ORDER BY version_no",
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def next_version_no(self, doc_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no FROM document_versions WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            return int(row["version_no"])

    def set_current_version(self, doc_id: int, version_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (version_id, doc_id),
            )

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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no FROM document_versions WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            version_no = row["version_no"]
            cursor = conn.execute(
                """
                INSERT INTO document_versions (
                  doc_id, version_no, original_filename, content_hash, file_size, mime_type, archive_path, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, version_no, original_filename, content_hash, file_size, mime_type, archive_path, created_by),
            )
            conn.execute(
                "UPDATE documents SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (cursor.lastrowid, doc_id),
            )
            version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            result = row_to_dict(version)
            if result is None:
                raise KeyError(f"document version not found: {cursor.lastrowid}")
            return result

    def create_sync_job(
        self,
        doc_id: int,
        kb_id: int,
        operation: Operation,
        version_id: int | None,
        backend_slug: str = "mock",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_jobs (doc_id, kb_id, backend_slug, operation, version_id, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, kb_id, backend_slug, operation.value, version_id, SyncJobStatus.pending.value),
            )
            row = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            job = row_to_dict(row)
            if job is None:
                raise KeyError(f"sync job not found: {cursor.lastrowid}")
            return job

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_jobs WHERE status = ? ORDER BY created_at, id",
                (SyncJobStatus.pending.value,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_runnable_jobs(self, actor: str | None, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT
                  job.id,
                  job.doc_id,
                  job.kb_id,
                  job.backend_slug,
                  job.operation,
                  d.slug AS doc_slug,
                  d.title AS doc_title,
                  kb.slug AS kb_slug,
                  kb.name AS kb_name,
                  COALESCE(target.backend_kb_id, kb.slug) AS backend_kb_id,
                  v.version_no AS version_no,
                  v.archive_path AS archive_path
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                LEFT JOIN backend_targets target
                  ON target.kb_id = job.kb_id AND target.slug = job.backend_slug
                LEFT JOIN document_versions v ON v.id = job.version_id
                LEFT JOIN knowledge_base_members member
                  ON member.kb_id = kb.id AND member.linux_user = ?
                WHERE job.status IN (?, ?)
                  AND (
                    ? IS NULL
                    OR d.owner_user = ?
                    OR member.role IN (?, ?)
                  )
                  AND (job.backend_slug = ? OR ? IS NULL)
                ORDER BY job.created_at, job.id
                """,
                (
                    actor,
                    SyncJobStatus.pending.value,
                    SyncJobStatus.failed.value,
                    actor,
                    actor,
                    KbRole.contributor.value,
                    KbRole.admin.value,
                    backend_slug,
                    backend_slug,
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_all_jobs(self, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  job.*,
                  d.slug AS doc_slug,
                  d.title AS doc_title,
                  kb.slug AS kb_slug,
                  kb.name AS kb_name,
                  v.version_no AS version_no
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                LEFT JOIN document_versions v ON v.id = job.version_id
                WHERE (job.backend_slug = ? OR ? IS NULL)
                ORDER BY job.created_at, job.id
                """,
                (backend_slug, backend_slug),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_job_status(self, job_id: int, status: SyncJobStatus, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_jobs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status.value, error, job_id),
            )

    def list_jobs_for_user(self, linux_user: str, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  job.*,
                  d.slug AS doc_slug,
                  d.title AS doc_title,
                  kb.slug AS kb_slug,
                  kb.name AS kb_name,
                  v.version_no AS version_no
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                JOIN knowledge_base_members member ON member.kb_id = kb.id
                LEFT JOIN document_versions v ON v.id = job.version_id
                WHERE (member.linux_user = ? OR d.owner_user = ?)
                  AND (job.backend_slug = ? OR ? IS NULL)
                GROUP BY job.id
                ORDER BY job.created_at, job.id
                """,
                (linux_user, linux_user, backend_slug, backend_slug),
            ).fetchall()
            return [dict(row) for row in rows]

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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_states (doc_id, kb_id, backend_slug, backend_doc_id, status, backend_status, chunk_count, progress, backend_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id, kb_id, backend_slug) DO UPDATE SET
                  backend_doc_id = excluded.backend_doc_id,
                  status = excluded.status,
                  backend_status = excluded.backend_status,
                  chunk_count = excluded.chunk_count,
                  progress = excluded.progress,
                  backend_error = excluded.backend_error,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (doc_id, kb_id, backend_slug, backend_doc_id, status.value, backend_status, chunk_count, progress, backend_error),
            )

    def get_sync_state(self, doc_id: int, kb_id: int, backend_slug: str = "mock") -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sync_states
                WHERE doc_id = ? AND kb_id = ? AND backend_slug = ?
                """,
                (doc_id, kb_id, backend_slug),
            ).fetchone()
            return row_to_dict(row)

    def list_sync_states_for_doc(self, doc_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_states WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_synced_docs_for_target(self, kb_id: int, backend_slug: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT s.doc_id
                FROM sync_states s
                JOIN document_kbs dk ON dk.doc_id = s.doc_id AND dk.kb_id = s.kb_id
                WHERE s.kb_id = ? AND s.backend_slug = ? AND s.status = ?
                """,
                (kb_id, backend_slug, SyncStateStatus.synced.value),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_docs_for_kb(self, kb_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  d.id,
                  d.slug,
                  d.title,
                  d.owner_user,
                  d.status,
                  v.version_no AS current_version_no,
                  COALESCE(s.status, ?) AS sync_status
                FROM document_kbs dk
                JOIN documents d ON d.id = dk.doc_id
                LEFT JOIN document_versions v ON v.id = d.current_version_id
                LEFT JOIN sync_states s ON s.doc_id = d.id AND s.kb_id = dk.kb_id AND s.backend_slug = 'mock'
                WHERE dk.kb_id = ?
                  AND dk.status = 'active'
                  AND d.status != ?
                ORDER BY d.slug
                """,
                (SyncStateStatus.not_synced.value, kb_id, DocumentStatus.deleted.value),
            ).fetchall()
            return [dict(row) for row in rows]

    def ensure_backend_target(self, kb_id: int, slug: str, backend_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backend_targets (kb_id, slug, backend_type)
                VALUES (?, ?, ?)
                ON CONFLICT(kb_id, slug) DO NOTHING
                """,
                (kb_id, slug, backend_type),
            )

    def list_backend_targets(self, kb_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backend_targets WHERE kb_id = ? ORDER BY slug",
                (kb_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_backend_target_status(self, kb_id: int, slug: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE backend_targets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (status, kb_id, slug),
            )

    def update_backend_target_kb_id(self, kb_id: int, slug: str, backend_kb_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE backend_targets SET backend_kb_id = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (backend_kb_id, kb_id, slug),
            )

    def rebuild_backend_target(self, kb_id: int, backend_slug: str, new_backend_kb_id: str) -> None:
        """Re-create backend target after Weknora KB was deleted: update ID, reset states, reschedule all docs."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE backend_targets SET backend_kb_id = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (new_backend_kb_id, kb_id, backend_slug),
            )
            conn.execute(
                "UPDATE sync_states SET backend_doc_id = NULL, status = ?, backend_status = NULL WHERE kb_id = ? AND backend_slug = ?",
                (SyncStateStatus.not_synced.value, kb_id, backend_slug),
            )
            docs = conn.execute(
                "SELECT d.id AS doc_id, v.id AS version_id FROM document_kbs dk JOIN documents d ON d.id = dk.doc_id LEFT JOIN document_versions v ON v.id = d.current_version_id WHERE dk.kb_id = ? AND d.deleted_at IS NULL",
                (kb_id,),
            ).fetchall()
            for row in docs:
                conn.execute(
                    "INSERT INTO sync_jobs (doc_id, kb_id, backend_slug, operation, version_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (row["doc_id"], kb_id, backend_slug, Operation.create.value, row["version_id"], SyncJobStatus.pending.value),
                )

    def update_backend_target_config(self, kb_id: int, slug: str, config_updates: dict[str, Any]) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config_json FROM backend_targets WHERE kb_id = ? AND slug = ?",
                (kb_id, slug),
            ).fetchone()
            existing = json.loads(row["config_json"]) if row and row["config_json"] else {}
            existing.update(config_updates)
            conn.execute(
                "UPDATE backend_targets SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (json.dumps(existing, ensure_ascii=False), kb_id, slug),
            )

    # ── Backends ──

    def list_backends(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM backends ORDER BY slug").fetchall()
            return [dict(row) for row in rows]

    def get_backend(self, slug: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM backends WHERE slug = ?", (slug,)).fetchone()
            return dict(row) if row else None

    def upsert_backend(self, *, slug: str, backend_type: str, base_url: str | None = None,
                       api_key: str | None = None, timeout: int = 120,
                       embedding_model_id: str | None = None, summary_model_id: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backends (slug, backend_type, base_url, api_key, timeout, embedding_model_id, summary_model_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                  backend_type = excluded.backend_type,
                  base_url = excluded.base_url,
                  api_key = excluded.api_key,
                  timeout = excluded.timeout,
                  embedding_model_id = excluded.embedding_model_id,
                  summary_model_id = excluded.summary_model_id,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (slug, backend_type, base_url, api_key, timeout, embedding_model_id, summary_model_id),
            )
            row = conn.execute("SELECT * FROM backends WHERE slug = ?", (slug,)).fetchone()
            return dict(row)

    def delete_backend(self, slug: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM backends WHERE slug = ?", (slug,))
            return cursor.rowcount > 0
