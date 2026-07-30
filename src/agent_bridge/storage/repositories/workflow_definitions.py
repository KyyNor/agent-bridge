"""工作流定义、修订与定义导入的持久化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_bridge.core.timeutil import utc_iso

from . import revisions as _revisions
from .workflow_common import (
    _datetime_iso,
    _json_dumps,
    _row_payload,
    _workflow_definition_import_payload,
)


class WorkflowDefinitionsRepositoryMixin:
    def upsert_workflow_definition(
        self,
        *,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        status: str,
        created_by: str,
        workflow_type: str = "operation",
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_definitions (
                  workflow_key, name, description, profile_key, definition_json, status, workflow_type, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  profile_key = excluded.profile_key,
                  definition_json = excluded.definition_json,
                  status = excluded.status,
                  workflow_type = excluded.workflow_type,
                  edit_version = workflow_definitions.edit_version + 1,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    workflow_key,
                    name,
                    description,
                    profile_key,
                    _json_dumps(definition) if definition is not None else None,
                    status,
                    workflow_type,
                    created_by,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()
            result = _row_payload(row)
            if result is None:
                raise KeyError(f"workflow not found: {workflow_key}")
            return result

    def get_workflow_definition(self, workflow_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_definitions WHERE workflow_key = ?",
                    (workflow_key,),
                ).fetchone()
            )

    def list_workflow_definitions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_definitions ORDER BY workflow_key"
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def list_workflow_definition_summaries(self) -> list[dict[str, Any]]:
        """Return only fields needed by workflow lists and selectors."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT workflow_key, name, description, profile_key, status,
                       workflow_type, edit_version, created_by, created_at, updated_at
                FROM workflow_definitions
                ORDER BY workflow_key
                """
            ).fetchall()
            return [dict(row) for row in rows]

    # --- definition revisions --------------------------------------------

    def create_definition_revision(
        self,
        *,
        workflow_key: str,
        content_hash: str,
        snapshot: dict[str, Any],
        actor: str,
        source: str = "edit",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            return _revisions.create_revision(
                conn,
                table="workflow_definition_revisions",
                key_column="workflow_key",
                key_value=workflow_key,
                content_hash=content_hash,
                snapshot=snapshot,
                actor=actor,
                owner_table="workflow_definitions",
                snapshot_label="workflow",
                extra_columns=("source",),
                extra_values=(source,),
            )

    def list_definition_revisions(self, workflow_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return _revisions.list_revisions(
                conn,
                table="workflow_definition_revisions",
                key_column="workflow_key",
                key_value=workflow_key,
                limit=limit,
                extra_columns=("source",),
            )

    def get_definition_revision(self, workflow_key: str, revision_no: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _revisions.get_revision(
                conn,
                table="workflow_definition_revisions",
                key_column="workflow_key",
                key_value=workflow_key,
                revision_no=revision_no,
                snapshot_label="workflow",
                extra_columns=("source",),
            )

    def get_current_definition_revision_no(self, workflow_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT current_revision_no FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def set_current_definition_revision_no(self, workflow_key: str, revision_no: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE workflow_definitions SET current_revision_no = ? WHERE workflow_key = ?",
                (revision_no, workflow_key),
            )

    def mark_latest_task_stale_if_needed(
        self,
        workflow_key: str,
        revision_no: int,
        content_hash: str,
    ) -> int:
        """Mark only the current completed version of each task stale.

        A task is stale when its most recent successful run was produced for a
        different definition revision.  Older task versions and tasks that are
        still pending/running/failed remain untouched.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_tasks AS task
                SET status = 'stale',
                    completed_at = NULL,
                    priority_flag = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task.workflow_key = ?
                  AND task.status = 'completed'
                  AND NOT EXISTS (
                    SELECT 1 FROM workflow_tasks AS newer
                    WHERE newer.workflow_key = task.workflow_key
                      AND newer.task_key = task.task_key
                      AND (
                        newer.set_at > task.set_at
                        OR (newer.set_at = task.set_at AND newer.id > task.id)
                      )
                  )
                  AND EXISTS (
                    SELECT 1 FROM workflow_runs AS successful
                    WHERE successful.workflow_key = task.workflow_key
                      AND successful.task_key = task.task_key
                      AND successful.task_version = task.task_version
                      AND successful.status = 'completed'
                  )
                  AND (
                    COALESCE((
                      SELECT successful.workflow_revision_no
                      FROM workflow_runs AS successful
                      WHERE successful.workflow_key = task.workflow_key
                        AND successful.task_key = task.task_key
                        AND successful.task_version = task.task_version
                        AND successful.status = 'completed'
                      ORDER BY successful.finished_at DESC, successful.id DESC
                      LIMIT 1
                    ), -1) != ?
                    OR COALESCE((
                      SELECT successful.workflow_content_hash
                      FROM workflow_runs AS successful
                      WHERE successful.workflow_key = task.workflow_key
                        AND successful.task_key = task.task_key
                        AND successful.task_version = task.task_version
                        AND successful.status = 'completed'
                      ORDER BY successful.finished_at DESC, successful.id DESC
                      LIMIT 1
                    ), '') != ?
                  )
                """,
                (workflow_key, revision_no, content_hash),
            )
            return cursor.rowcount

    def create_workflow_definition_import(
        self,
        *,
        import_id: str,
        actor: str,
        filename: str,
        source_workflow_key: str,
        target_workflow_key: str,
        operation: str,
        workflow: dict[str, Any],
        target_revision_no: int,
        expires_at: datetime | str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_definition_imports (
                  import_id, actor, filename, source_workflow_key,
                  target_workflow_key, operation, workflow_json,
                  target_revision_no, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    actor,
                    filename,
                    source_workflow_key,
                    target_workflow_key,
                    operation,
                    _json_dumps(workflow),
                    target_revision_no,
                    _datetime_iso(expires_at),
                ),
            )
            result = _workflow_definition_import_payload(
                conn.execute(
                    "SELECT * FROM workflow_definition_imports WHERE import_id = ?",
                    (import_id,),
                ).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow definition import not found: {import_id}")
            return result

    def get_workflow_definition_import(self, import_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _workflow_definition_import_payload(
                conn.execute(
                    "SELECT * FROM workflow_definition_imports WHERE import_id = ?",
                    (import_id,),
                ).fetchone()
            )

    def confirm_workflow_definition_import(self, import_id: str, *, actor: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_definition_imports
                SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP
                WHERE import_id = ? AND actor = ? AND status = 'previewed'
                """,
                (import_id, actor),
            )
            return cursor.rowcount == 1

    def delete_workflow_definition_import(self, import_id: str, *, actor: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM workflow_definition_imports WHERE import_id = ? AND actor = ?",
                (import_id, actor),
            )
            return cursor.rowcount == 1

    def delete_expired_workflow_definition_imports(
        self,
        *,
        now: datetime | str | None = None,
    ) -> int:
        expires_before = _datetime_iso(now) if now is not None else utc_iso()
        with self._connect() as conn:
            return conn.execute(
                """
                DELETE FROM workflow_definition_imports
                WHERE status = 'previewed' AND expires_at <= ?
                """,
                (expires_before,),
            ).rowcount
