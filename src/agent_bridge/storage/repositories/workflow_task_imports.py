"""工作流任务导入预览与应用的持久化。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from agent_bridge.automation.workflows.models import WorkflowTaskStatus
from agent_bridge.core.json_util import json_loads as _json_loads
from agent_bridge.core.timeutil import utc_iso, utc_now

from .workflow_common import (
    _datetime_iso,
    _json_dumps,
    _parse_datetime,
    _workflow_task_import_payload,
)


class WorkflowTaskImportsRepositoryMixin:
    def _workflow_task_action(
        self,
        *,
        existing: sqlite3.Row | None,
        is_current: bool,
        now: datetime,
        rerun_cutoff: datetime,
    ) -> str:
        if existing is None:
            return "created"
        if not is_current:
            return "skipped_historical"
        if existing["status"] == WorkflowTaskStatus.completed.value:
            set_at = _parse_datetime(existing["set_at"])
            return "reopened_expired" if set_at is not None and set_at < rerun_cutoff else "skipped_completed"
        if existing["status"] == WorkflowTaskStatus.running.value:
            lease_expires_at = existing["lease_expires_at"]
            if lease_expires_at is None:
                return "skipped_running"
            parsed_lease_expires_at = _parse_datetime(lease_expires_at)
            if parsed_lease_expires_at is not None and parsed_lease_expires_at >= now:
                return "skipped_running"
        return "updated"

    def _workflow_task_rerun_cutoff(self, conn: sqlite3.Connection, now: datetime) -> datetime:
        config = conn.execute(
            "SELECT workflow_task_rerun_days FROM knowledge_sync_config WHERE id = 1"
        ).fetchone()
        rerun_days = (
            int(config["workflow_task_rerun_days"])
            if config is not None and config["workflow_task_rerun_days"] is not None
            else 30
        )
        return now - timedelta(days=max(rerun_days, 0))

    def _apply_workflow_tasks(
        self,
        conn: sqlite3.Connection,
        workflow_key: str,
        tasks: list[dict[str, Any]],
        *,
        now: datetime,
    ) -> dict[str, int]:
        now_iso = utc_iso(now)
        rerun_cutoff = self._workflow_task_rerun_cutoff(conn, now)
        counts = {
            "created": 0,
            "updated": 0,
            "skipped_completed": 0,
            "skipped_running": 0,
            "skipped_historical": 0,
            "reopened_expired": 0,
        }
        for task in tasks:
            task_key = str(task["task_key"])
            task_version = str(task.get("task_version") or "")
            task_type = str(task.get("type") or "")
            payload = task.get("payload") or {}
            existing = conn.execute(
                """
                SELECT id, status, lease_expires_at, set_at
                FROM workflow_tasks
                WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                """,
                (workflow_key, task_key, task_version),
            ).fetchone()
            current = conn.execute(
                """
                SELECT id FROM workflow_tasks
                WHERE workflow_key = ? AND task_key = ?
                ORDER BY set_at DESC, id DESC
                LIMIT 1
                """,
                (workflow_key, task_key),
            ).fetchone()
            action = self._workflow_task_action(
                existing=existing,
                is_current=existing is None or current is None or existing["id"] == current["id"],
                now=now,
                rerun_cutoff=rerun_cutoff,
            )
            if action in counts:
                counts[action] += 1
            if action == "created":
                conn.execute(
                    """
                    INSERT INTO workflow_tasks (workflow_key, task_key, task_version, type, payload_json, status, set_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (workflow_key, task_key, task_version, task_type, _json_dumps(payload), now_iso),
                )
            elif action == "reopened_expired":
                conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET type = ?,
                        payload_json = ?,
                        status = 'pending',
                        lease_run_id = NULL,
                        lease_expires_at = NULL,
                        last_error = NULL,
                        set_at = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        completed_at = NULL
                    WHERE workflow_key = ? AND task_key = ?
                      AND task_version = ?
                    """,
                    (task_type, _json_dumps(payload), now_iso, workflow_key, task_key, task_version),
                )
            elif action == "updated":
                conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET type = ?,
                        payload_json = ?,
                        status = 'pending',
                        lease_run_id = NULL,
                        lease_expires_at = NULL,
                        set_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ? AND task_key = ?
                      AND task_version = ?
                    """,
                    (task_type, _json_dumps(payload), now_iso, workflow_key, task_key, task_version),
                )
        return counts

    def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._apply_workflow_tasks(conn, workflow_key, tasks, now=now)

    def preview_workflow_task_actions(
        self,
        workflow_key: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as conn:
            rerun_cutoff = self._workflow_task_rerun_cutoff(conn, now)
            counts = {
                "created": 0,
                "updated": 0,
                "skipped_completed": 0,
                "skipped_running": 0,
                "skipped_historical": 0,
                "reopened_expired": 0,
            }
            rows: list[dict[str, Any]] = []
            for task in tasks:
                task_key = str(task["task_key"])
                task_version = str(task.get("task_version") or "")
                task_type = str(task.get("type") or "")
                payload = task.get("payload") or {}
                existing = conn.execute(
                    """
                    SELECT id, status, lease_expires_at, set_at
                    FROM workflow_tasks
                    WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                    """,
                    (workflow_key, task_key, task_version),
                ).fetchone()
                current = conn.execute(
                    """
                    SELECT id FROM workflow_tasks
                    WHERE workflow_key = ? AND task_key = ?
                    ORDER BY set_at DESC, id DESC
                    LIMIT 1
                    """,
                    (workflow_key, task_key),
                ).fetchone()
                action = self._workflow_task_action(
                    existing=existing,
                    is_current=existing is None or current is None or existing["id"] == current["id"],
                    now=now,
                    rerun_cutoff=rerun_cutoff,
                )
                if action in counts:
                    counts[action] += 1
                rows.append(
                    {
                        "task_key": task_key,
                        "task_version": task_version,
                        "type": task_type,
                        "payload": payload,
                        "action": action,
                    }
                )
        return {"rows": rows, "summary": counts}

    def create_workflow_task_import(
        self,
        *,
        import_id: str,
        workflow_key: str,
        actor: str,
        filename: str,
        sheet_name: str,
        tasks: list[dict[str, Any]],
        preview: dict[str, Any],
        expires_at: datetime | str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_task_imports (
                  import_id, workflow_key, actor, filename, sheet_name,
                  tasks_json, preview_json, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    workflow_key,
                    actor,
                    filename,
                    sheet_name,
                    _json_dumps(tasks),
                    _json_dumps(preview),
                    _datetime_iso(expires_at),
                ),
            )
            result = _workflow_task_import_payload(
                conn.execute(
                    "SELECT * FROM workflow_task_imports WHERE import_id = ?",
                    (import_id,),
                ).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow task import not found: {import_id}")
            return result

    def get_workflow_task_import(self, import_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _workflow_task_import_payload(
                conn.execute(
                    "SELECT * FROM workflow_task_imports WHERE import_id = ?",
                    (import_id,),
                ).fetchone()
            )

    def confirm_workflow_task_import(
        self,
        workflow_key: str,
        *,
        import_id: str,
        actor: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = conn.execute(
                "SELECT * FROM workflow_task_imports WHERE import_id = ?",
                (import_id,),
            ).fetchone()
            if snapshot is None:
                raise KeyError(f"workflow task import not found: {import_id}")
            if snapshot["workflow_key"] != workflow_key:
                raise ValueError("workflow task import workflow mismatch")
            if snapshot["actor"] != actor:
                raise ValueError("workflow task import actor mismatch")
            if snapshot["status"] != "previewed":
                raise ValueError("workflow task import is not previewed")
            expires_at = _parse_datetime(snapshot["expires_at"])
            if expires_at is None or expires_at <= now:
                raise ValueError("workflow task import expired")
            tasks = _json_loads(snapshot["tasks_json"], None)
            if not isinstance(tasks, list):
                raise ValueError("workflow task import tasks are invalid")
            counts = self._apply_workflow_tasks(conn, workflow_key, tasks, now=now)
            updated = conn.execute(
                """
                UPDATE workflow_task_imports
                SET status = 'confirmed', confirmed_at = ?
                WHERE import_id = ? AND status = 'previewed'
                """,
                (utc_iso(now), import_id),
            )
            if updated.rowcount != 1:
                raise ValueError("workflow task import is no longer previewed")
            return {"import_id": import_id, **counts}

    def delete_expired_workflow_task_imports(
        self,
        *,
        now: datetime | str | None = None,
    ) -> int:
        expires_before = _datetime_iso(now) if now is not None else utc_iso()
        with self._connect() as conn:
            return conn.execute(
                """
                DELETE FROM workflow_task_imports
                WHERE status = 'previewed' AND expires_at <= ?
                """,
                (expires_before,),
            ).rowcount
