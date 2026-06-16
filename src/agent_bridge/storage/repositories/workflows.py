from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_bridge.storage.types import row_to_dict
from agent_bridge.workflows.models import WorkflowTaskStatus, require_manifest


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    for source, target, default in [
        ("manifest_json", "manifest", {}),
        ("schedule_json", "schedule", {}),
        ("payload_json", "payload", {}),
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
    ]:
        if source in item:
            item[target] = _json_loads(item[source], default)
    return item


class WorkflowsRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def upsert_workflow_definition(
        self,
        *,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        manifest: dict[str, Any],
        schedule: dict[str, Any],
        status: str,
        created_by: str,
    ) -> dict[str, Any]:
        require_manifest(manifest)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_definitions (
                  workflow_key, name, description, profile_key, workflow_js,
                  manifest_json, schedule_json, status, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  profile_key = excluded.profile_key,
                  workflow_js = excluded.workflow_js,
                  manifest_json = excluded.manifest_json,
                  schedule_json = excluded.schedule_json,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    workflow_key,
                    name,
                    description,
                    profile_key,
                    workflow_js,
                    _json_dumps(manifest),
                    _json_dumps(schedule),
                    status,
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

    def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
        created = 0
        updated = 0
        skipped_completed = 0
        with self._connect() as conn:
            for task in tasks:
                task_key = str(task["task_key"])
                payload = task.get("payload") or {}
                existing = conn.execute(
                    "SELECT status FROM workflow_tasks WHERE workflow_key = ? AND task_key = ?",
                    (workflow_key, task_key),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO workflow_tasks (workflow_key, task_key, payload_json, status)
                        VALUES (?, ?, ?, 'pending')
                        """,
                        (workflow_key, task_key, _json_dumps(payload)),
                    )
                    created += 1
                elif existing["status"] == WorkflowTaskStatus.completed.value:
                    skipped_completed += 1
                else:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET payload_json = ?,
                            status = 'pending',
                            lease_run_id = NULL,
                            lease_expires_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE workflow_key = ? AND task_key = ?
                        """,
                        (_json_dumps(payload), workflow_key, task_key),
                    )
                    updated += 1
        return {"created": created, "updated": updated, "skipped_completed": skipped_completed}

    def get_workflow_task(self, workflow_key: str, task_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_tasks WHERE workflow_key = ? AND task_key = ?",
                    (workflow_key, task_key),
                ).fetchone()
            )

    def lease_workflow_task(self, workflow_key: str, *, run_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = _now_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_key = ?
                  AND (
                    status = 'pending'
                    OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                  )
                ORDER BY id
                LIMIT 1
                """,
                (workflow_key, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'running',
                    lease_run_id = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id, expires_at, row["id"]),
            )
            leased = conn.execute("SELECT * FROM workflow_tasks WHERE id = ?", (row["id"],)).fetchone()
            return _row_payload(leased)

    def complete_workflow_task(self, workflow_key: str, task_key: str, *, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_run_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ? AND task_key = ?
                """,
                (run_id, workflow_key, task_key),
            )

    def force_workflow_task_lease_expiry(self, workflow_key: str, task_key: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_tasks
                SET lease_expires_at = ?
                WHERE workflow_key = ? AND task_key = ?
                """,
                (expires_at, workflow_key, task_key),
            )
