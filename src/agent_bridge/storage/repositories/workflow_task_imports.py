"""工作流任务导入预览与应用的持久化。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from agent_bridge.automation.workflows.models import WorkflowTaskStatus
from agent_bridge.core.json_util import json_loads as _json_loads
from agent_bridge.core.timeutil import utc_iso, utc_now

from .codegraph import fetch_sync_config

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
        config = fetch_sync_config(conn)
        rerun_days = int(config.get("workflow_task_rerun_days") or 0)
        return now - timedelta(days=max(rerun_days, 0))

    #: 新版本到来时会被取代的旧版本状态：尚未运行的（pending/stale）以及已经失败/放弃、
    #: 无需继续重试的（failed/abandoned）。正在跑的（running，含租约过期回收）让它跑完；
    #: 已成功完成（completed）保留为历史产物，永不取代。
    _SUPERSEDED_STATUSES = (
        WorkflowTaskStatus.pending.value,
        WorkflowTaskStatus.stale.value,
        WorkflowTaskStatus.failed.value,
        WorkflowTaskStatus.abandoned.value,
    )

    def _count_superseded_targets(
        self,
        conn: sqlite3.Connection,
        *,
        workflow_key: str,
        task_key: str,
        task_version: str,
    ) -> int:
        """统计导入 task_version 后，同 task_key 下会被取代的旧版本数量。

        见 :attr:`_SUPERSEDED_STATUSES`。预览路径不写库，用本方法预估；
        应用路径用 :meth:`_supersede_old_versions`。
        """
        placeholders = ", ".join(["?"] * len(self._SUPERSEDED_STATUSES))
        row = conn.execute(
            f"""
            SELECT COUNT(*) FROM workflow_tasks
            WHERE workflow_key = ?
              AND task_key = ?
              AND task_version <> ?
              AND status IN ({placeholders})
            """,
            (workflow_key, task_key, task_version, *self._SUPERSEDED_STATUSES),
        ).fetchone()
        return int(row[0]) if row else 0

    def _supersede_old_versions(
        self,
        conn: sqlite3.Connection,
        *,
        workflow_key: str,
        task_key: str,
        task_version: str,
    ) -> int:
        """把同 task_key 下会被取代的旧版本标为 superseded，返回受影响行数。"""
        placeholders = ", ".join(["?"] * len(self._SUPERSEDED_STATUSES))
        return conn.execute(
            f"""
            UPDATE workflow_tasks
            SET status = ?,
                lease_run_id = NULL,
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE workflow_key = ?
              AND task_key = ?
              AND task_version <> ?
              AND status IN ({placeholders})
            """,
            (
                WorkflowTaskStatus.superseded.value,
                workflow_key,
                task_key,
                task_version,
                *self._SUPERSEDED_STATUSES,
            ),
        ).rowcount

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
            "superseded": 0,
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
                # task_version 演进语义：新版本到来时，取代同 task_key 下还没运行的旧版本。
                # 仅取代 pending/stale；正在跑的（running，含租约过期回收）让它跑完，
                # 已完成/失败/放弃的不动以保留历史产物。
                superseded = self._supersede_old_versions(
                    conn, workflow_key=workflow_key, task_key=task_key, task_version=task_version
                )
                if superseded:
                    counts["superseded"] += superseded
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
                "superseded": 0,
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
                if action == "created":
                    # 预览不写库，仅预估本次导入会取代多少未运行的旧版本。
                    counts["superseded"] += self._count_superseded_targets(
                        conn, workflow_key=workflow_key, task_key=task_key, task_version=task_version
                    )
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
