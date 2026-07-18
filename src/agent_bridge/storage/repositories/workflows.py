from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_bridge.storage.types import row_to_dict
from agent_bridge.automation.workflows.models import WorkflowTaskStatus
from agent_bridge.core.json_util import json_loads as _json_loads


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _artifact_id() -> str:
    return f"artifact_{uuid.uuid4().hex}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    if "is_current" in item:
        item["is_current"] = bool(item["is_current"])
    for source, target, default in [
        ("payload_json", "payload", {}),
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
        ("definition_snapshot_json", "definition_snapshot", {"nodes": [], "edges": []}),
        ("input_json", "input", {}),
        ("output_json", "output", {}),
        ("condition_results_json", "condition_results", []),
    ]:
        if source in item:
            item[target] = _json_loads(item[source], default)
    if "definition_json" in item:
        item["definition"] = _json_loads(item["definition_json"], None)
    return item


def _workflow_task_import_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    item["tasks"] = _json_loads(item.get("tasks_json"), [])
    item["preview"] = _json_loads(item.get("preview_json"), {})
    return item


def _datetime_iso(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


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
        status: str,
        created_by: str,
        workflow_type: str = "operation",
        definition: dict[str, Any] | None = None,
        workflow_js: str = "",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_definitions (
                  workflow_key, name, description, profile_key, workflow_js, definition_json, status, workflow_type, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  profile_key = excluded.profile_key,
                  workflow_js = excluded.workflow_js,
                  definition_json = excluded.definition_json,
                  status = excluded.status,
                  workflow_type = excluded.workflow_type,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    workflow_key,
                    name,
                    description,
                    profile_key,
                    workflow_js,
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
        snapshot_json = _json_dumps(snapshot)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(revision_no), 0) FROM workflow_definition_revisions WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()
            next_no = int(row[0]) + 1
            conn.execute(
                """
                INSERT INTO workflow_definition_revisions (
                  workflow_key, revision_no, content_hash, snapshot_json, created_by, source
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (workflow_key, next_no, content_hash, snapshot_json, actor, source),
            )
            conn.execute(
                "UPDATE workflow_definitions SET current_revision_no = ? WHERE workflow_key = ?",
                (next_no, workflow_key),
            )
            return dict(
                conn.execute(
                    """
                    SELECT workflow_key AS entity_key, revision_no, content_hash, created_by, source, created_at
                    FROM workflow_definition_revisions WHERE workflow_key = ? AND revision_no = ?
                    """,
                    (workflow_key, next_no),
                ).fetchone()
            )

    def list_definition_revisions(self, workflow_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = min(max(limit, 1), 500)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT workflow_key AS entity_key, revision_no, content_hash, created_by, source, created_at
                FROM workflow_definition_revisions
                WHERE workflow_key = ?
                ORDER BY revision_no DESC
                LIMIT ?
                """,
                (workflow_key, bounded),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_definition_revision(self, workflow_key: str, revision_no: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            item = row_to_dict(
                conn.execute(
                    """
                    SELECT workflow_key AS entity_key, revision_no, content_hash, snapshot_json, created_by, source, created_at
                    FROM workflow_definition_revisions
                    WHERE workflow_key = ? AND revision_no = ?
                    """,
                    (workflow_key, revision_no),
                ).fetchone()
            )
            if item is None:
                return None
            snapshot_json = item.pop("snapshot_json", None)
            try:
                snapshot = json.loads(snapshot_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("corrupt workflow revision snapshot") from exc
            if not isinstance(snapshot, dict):
                raise ValueError("corrupt workflow revision snapshot")
            item["snapshot"] = snapshot
            return item

    def get_current_definition_revision_no(self, workflow_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT current_revision_no FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def _workflow_task_action(
        self,
        *,
        existing: sqlite3.Row | None,
        now: datetime,
        rerun_cutoff: datetime,
    ) -> str:
        if existing is None:
            return "created"
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
        now_iso = now.isoformat()
        rerun_cutoff = self._workflow_task_rerun_cutoff(conn, now)
        counts = {
            "created": 0,
            "updated": 0,
            "skipped_completed": 0,
            "skipped_running": 0,
            "reopened_expired": 0,
        }
        for task in tasks:
            task_key = str(task["task_key"])
            task_version = str(task.get("task_version") or "")
            task_type = str(task.get("type") or "")
            payload = task.get("payload") or {}
            existing = conn.execute(
                """
                SELECT status, lease_expires_at, set_at
                FROM workflow_tasks
                WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                """,
                (workflow_key, task_key, task_version),
            ).fetchone()
            action = self._workflow_task_action(
                existing=existing,
                now=now,
                rerun_cutoff=rerun_cutoff,
            )
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
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._apply_workflow_tasks(conn, workflow_key, tasks, now=now)

    def preview_workflow_task_actions(
        self,
        workflow_key: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            rerun_cutoff = self._workflow_task_rerun_cutoff(conn, now)
            counts = {
                "created": 0,
                "updated": 0,
                "skipped_completed": 0,
                "skipped_running": 0,
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
                    SELECT status, lease_expires_at, set_at
                    FROM workflow_tasks
                    WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                    """,
                    (workflow_key, task_key, task_version),
                ).fetchone()
                action = self._workflow_task_action(
                    existing=existing,
                    now=now,
                    rerun_cutoff=rerun_cutoff,
                )
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
        now = datetime.now(timezone.utc)
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
                (now.isoformat(), import_id),
            )
            if updated.rowcount != 1:
                raise ValueError("workflow task import is no longer previewed")
            return {"import_id": import_id, **counts}

    def delete_expired_workflow_task_imports(
        self,
        *,
        now: datetime | str | None = None,
    ) -> int:
        expires_before = _datetime_iso(now) if now is not None else _now_iso()
        with self._connect() as conn:
            return conn.execute(
                """
                DELETE FROM workflow_task_imports
                WHERE status = 'previewed' AND expires_at <= ?
                """,
                (expires_before,),
            ).rowcount

    def get_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            if task_version is not None:
                return _row_payload(
                    conn.execute(
                        """
                        SELECT * FROM workflow_tasks
                        WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                        """,
                        (workflow_key, task_key, task_version),
                    ).fetchone()
                )
            return _row_payload(
                conn.execute(
                    """
                    SELECT * FROM workflow_tasks
                    WHERE workflow_key = ? AND task_key = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (workflow_key, task_key),
                ).fetchone()
            )

    # Recognised user-controlled sort modes for list_workflow_tasks. The
    # historical default ("status priority, then recency") is preserved when no
    # sort (or an unrecognised value) is supplied.
    _TASK_SORT_ORDER_BY = {
        "id_asc": "id ASC",
        "id_desc": "id DESC",
        "task_key_asc": "task_key ASC, task_version ASC, id ASC",
        "task_key_desc": "task_key DESC, task_version DESC, id DESC",
        "set_at_asc": "set_at ASC, id ASC",
        "set_at_desc": "set_at DESC, id DESC",
        "updated_at_desc": "updated_at DESC, id DESC",
    }
    _TASK_SORT_DEFAULT_ORDER_BY = """
                  CASE status
                    WHEN 'running' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'abandoned' THEN 3
                    WHEN 'completed' THEN 4
                    ELSE 5
                  END,
                  updated_at DESC,
                  id DESC
                """

    def list_workflow_tasks(
        self,
        workflow_key: str,
        *,
        status: str | None = None,
        type: str | None = None,
        search: str | None = None,
        sort: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["workflow_key = ?"]
        params: list[Any] = [workflow_key]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if type:
            clauses.append("type = ?")
            params.append(type)
        if search:
            clauses.append("(lower(task_key) LIKE ? OR lower(type) LIKE ?)")
            like = f"%{search.lower()}%"
            params.extend([like, like])
        order_by = self._TASK_SORT_ORDER_BY.get(sort or "", self._TASK_SORT_DEFAULT_ORDER_BY)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM workflow_tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                """,
                params,
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

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
                ORDER BY (priority_flag IS NULL), priority_flag ASC, id
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
                    priority_flag = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id, expires_at, row["id"]),
            )
            # Back-fill the run->task link. lease_run_id already points the task
            # at this run; mirror task_key onto workflow_runs (created with
            # task_key=None by the scheduler) so the run's task is queryable
            # without a join. Same transaction as the lease -> atomic.
            conn.execute(
                """
                UPDATE workflow_runs
                SET task_key = ?
                WHERE run_id = ?
                """,
                (row["task_key"], run_id),
            )
            leased = conn.execute("SELECT * FROM workflow_tasks WHERE id = ?", (row["id"],)).fetchone()
            return _row_payload(leased)

    def set_priority_for_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str | None = None,
        flagged_at: str | None = None,
    ) -> bool:
        """Stamp a one-shot priority flag on a task so the next lease picks it
        ahead of normal id ordering. ``flagged_at`` defaults to now (UTC ISO);
        the timestamp lets multiple flags be ordered chronologically. Returns
        whether a row was updated.
        """
        flag = flagged_at or _now_iso()
        with self._connect() as conn:
            if task_version is not None:
                cursor = conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET priority_flag = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                    """,
                    (flag, workflow_key, task_key, task_version),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET priority_flag = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ? AND task_key = ?
                    """,
                    (flag, workflow_key, task_key),
                )
            return cursor.rowcount > 0

    def reset_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str | None = None,
    ) -> bool:
        """Restore a task to a leasable state without triggering execution.

        Flips status to pending and clears the lease / completion / priority
        fields. ``attempt_count`` and ``last_error`` are deliberately preserved
        as an audit trail (a separate re-run does not erase retry history).
        Returns whether a row was updated.
        """
        with self._connect() as conn:
            if task_version is not None:
                cursor = conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'pending',
                        lease_run_id = NULL,
                        lease_expires_at = NULL,
                        completed_at = NULL,
                        priority_flag = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                    """,
                    (workflow_key, task_key, task_version),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'pending',
                        lease_run_id = NULL,
                        lease_expires_at = NULL,
                        completed_at = NULL,
                        priority_flag = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ? AND task_key = ?
                    """,
                    (workflow_key, task_key),
                )
            return cursor.rowcount > 0

    def complete_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str = "",
        run_id: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_run_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ?
                  AND task_key = ?
                  AND task_version = ?
                  AND status = 'running'
                  AND lease_run_id = ?
                """,
                (run_id, workflow_key, task_key, task_version, run_id),
            )
            return cursor.rowcount > 0

    def release_or_abandon_tasks_for_run(
        self,
        workflow_key: str,
        run_id: str,
        *,
        max_attempts: int,
        error_message: str,
    ) -> dict[str, int]:
        """Release or abandon tasks leased by a failed run.

        Tasks still running under this run's lease are either returned to
        pending (fast retry) when within the attempt budget, or marked
        abandoned once attempt_count exceeds the threshold. last_error is
        always recorded.
        """
        released = 0
        abandoned = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, attempt_count FROM workflow_tasks
                WHERE workflow_key = ? AND lease_run_id = ? AND status = 'running'
                """,
                (workflow_key, run_id),
            ).fetchall()
            for row in rows:
                if row["attempt_count"] > max_attempts:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET status = 'abandoned', last_error = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (error_message, row["id"]),
                    )
                    abandoned += 1
                else:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET status = 'pending',
                            lease_run_id = NULL,
                            lease_expires_at = NULL,
                            last_error = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (error_message, row["id"]),
                    )
                    released += 1
        return {"released": released, "abandoned": abandoned}

    def release_tasks_for_stopped_run(
        self,
        workflow_key: str,
        run_id: str,
        error_message: str,
    ) -> int:
        """Return tasks leased by a stopped run to the pending queue.

        Stop is a user cancellation, not a failed attempt: the retry counter is
        preserved and only the exact workflow/run lease is released.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'pending',
                    lease_run_id = NULL,
                    lease_expires_at = NULL,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ?
                  AND lease_run_id = ?
                  AND status = 'running'
                """,
                (error_message, workflow_key, run_id),
            )
            return cursor.rowcount

    def force_workflow_task_lease_expiry(
        self,
        workflow_key: str,
        task_key: str,
        expires_at: str,
        task_version: str | None = None,
    ) -> None:
        with self._connect() as conn:
            if task_version is not None:
                conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET lease_expires_at = ?
                    WHERE workflow_key = ? AND task_key = ? AND task_version = ?
                    """,
                    (expires_at, workflow_key, task_key, task_version),
                )
                return
            conn.execute(
                """
                UPDATE workflow_tasks
                SET lease_expires_at = ?
                WHERE workflow_key = ? AND task_key = ?
                """,
                (expires_at, workflow_key, task_key),
            )

    def create_workflow_run(
        self,
        *,
        run_id: str,
        workflow_key: str,
        profile_key: str,
        task_key: str | None,
        status: str,
        temp_dir: str,
        definition_snapshot: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                  run_id, workflow_key, profile_key, task_key, status, temp_dir,
                  definition_snapshot_json, input_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, workflow_key, profile_key, task_key, status, temp_dir,
                    _json_dumps(definition_snapshot or {"nodes": [], "edges": []}),
                    _json_dumps(input_data or {}),
                ),
            )
            result = _row_payload(
                conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow run not found: {run_id}")
            return result

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone())

    def list_workflow_runs(self, workflow_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_runs
                WHERE workflow_key = ?
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (workflow_key, limit),
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def delete_workflow_definition(self, workflow_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    "DELETE FROM workflow_run_logs WHERE workflow_key = ?",
                    (workflow_key,),
                )
            return cursor.rowcount > 0

    def clear_workflow_execution_data(self, workflow_key: str) -> dict[str, int]:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM workflow_task_imports WHERE workflow_key = ? AND status = 'previewed'",
                (workflow_key,),
            )
            logs = conn.execute(
                "DELETE FROM workflow_run_logs WHERE workflow_key = ?",
                (workflow_key,),
            ).rowcount
            artifacts = conn.execute(
                "DELETE FROM workflow_artifacts WHERE workflow_key = ?",
                (workflow_key,),
            ).rowcount
            runs = conn.execute(
                "DELETE FROM workflow_runs WHERE workflow_key = ?",
                (workflow_key,),
            ).rowcount
            tasks = conn.execute(
                "DELETE FROM workflow_tasks WHERE workflow_key = ?",
                (workflow_key,),
            ).rowcount
        return {
            "tasks_deleted": tasks,
            "runs_deleted": runs,
            "logs_deleted": logs,
            "artifacts_deleted": artifacts,
        }

    def finish_workflow_run(
        self,
        run_id: str,
        *,
        expected_status: str = "running",
        status: str,
        exit_code: int | None,
        stdout_path: str | None,
        stderr_path: str | None,
        error: str | None,
        duration_ms: int | None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_runs
                SET status = ?,
                    exit_code = ?,
                    stdout_path = ?,
                    stderr_path = ?,
                    error = ?,
                    duration_ms = ?,
                    output_json = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND status = ?
                """,
                (status, exit_code, stdout_path, stderr_path, error, duration_ms, _json_dumps(output or {}), run_id, expected_status),
            )
            result = _row_payload(
                conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow run not found: {run_id}")
            return result

    def create_workflow_node_runs(self, run_id: str, nodes: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO workflow_node_runs (run_id, node_id, node_type)
                VALUES (?, ?, ?)
                """,
                [(run_id, str(node["node_id"]), str(node["node_type"])) for node in nodes],
            )

    def start_workflow_node_run(
        self,
        run_id: str,
        node_id: str,
        condition_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_node_runs
                SET status = 'running', condition_results_json = ?, started_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND node_id = ?
                """,
                (_json_dumps(condition_results or []), run_id, node_id),
            )
            result = _row_payload(conn.execute(
                "SELECT * FROM workflow_node_runs WHERE run_id = ? AND node_id = ?", (run_id, node_id)
            ).fetchone())
            if result is None:
                raise KeyError(f"workflow node run not found: {run_id}/{node_id}")
            return result

    def finish_workflow_node_run(
        self,
        run_id: str,
        node_id: str,
        *,
        status: str,
        condition_results: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        agent_run_key: str | None = None,
        script_run_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_node_runs
                SET status = ?, condition_results_json = ?, output_json = ?, error = ?,
                    agent_run_key = ?, script_run_id = ?, finished_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND node_id = ?
                """,
                (
                    status, _json_dumps(condition_results or []), _json_dumps(output or {}), error,
                    agent_run_key, script_run_id, run_id, node_id,
                ),
            )
            result = _row_payload(conn.execute(
                "SELECT * FROM workflow_node_runs WHERE run_id = ? AND node_id = ?", (run_id, node_id)
            ).fetchone())
            if result is None:
                raise KeyError(f"workflow node run not found: {run_id}/{node_id}")
            return result

    def list_workflow_node_runs(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_node_runs WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def fail_workflow_task_for_run(self, workflow_key: str, run_id: str, error_message: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'failed', last_error = ?, lease_expires_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ? AND lease_run_id = ? AND status = 'running'
                """,
                (error_message, workflow_key, run_id),
            )
            return cursor.rowcount > 0

    def append_workflow_run_log(
        self,
        *,
        run_id: str,
        workflow_key: str,
        task_key: str | None,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workflow_run_logs (
                  run_id, workflow_key, task_key, level, stage, message, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, workflow_key, task_key, level, stage, message, _json_dumps(payload)),
            )
            result = _row_payload(
                conn.execute("SELECT * FROM workflow_run_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            )
            if result is None:
                raise KeyError("workflow run log not found")
            return result

    def list_workflow_run_logs(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_run_logs WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def upsert_workflow_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
        task_version: str = "",
    ) -> dict[str, Any]:
        content_hash = _content_hash(content)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_artifacts
                SET is_current = 0
                WHERE workflow_key = ?
                  AND task_key IS ?
                  AND NOT (task_version = ? AND run_id = ?)
                """,
                (workflow_key, task_key, task_version, run_id),
            )
            existing = conn.execute(
                """
                SELECT artifact_id FROM workflow_artifacts
                WHERE workflow_key = ? AND task_key IS ? AND task_version = ? AND run_id = ? AND path = ?
                """,
                (workflow_key, task_key, task_version, run_id, path),
            ).fetchone()
            artifact_id = existing["artifact_id"] if existing else _artifact_id()
            conn.execute(
                """
                INSERT INTO workflow_artifacts (
                  artifact_id, workflow_key, profile_key, run_id, task_key, task_version,
                  is_current, title, path,
                  tags_json, format, summary, content, content_hash, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key, task_key, task_version, run_id, path) DO UPDATE SET
                  profile_key = excluded.profile_key,
                  run_id = excluded.run_id,
                  task_key = excluded.task_key,
                  task_version = excluded.task_version,
                  is_current = 1,
                  title = excluded.title,
                  tags_json = excluded.tags_json,
                  format = excluded.format,
                  summary = excluded.summary,
                  content = excluded.content,
                  content_hash = excluded.content_hash,
                  metadata_json = excluded.metadata_json,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    artifact_id,
                    workflow_key,
                    profile_key,
                    run_id,
                    task_key,
                    task_version,
                    title,
                    path,
                    _json_dumps(tags),
                    format,
                    summary,
                    content,
                    content_hash,
                    _json_dumps(metadata),
                ),
            )
            result = _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow artifact not found: {artifact_id}")
            return result

    def get_workflow_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            )

    def search_workflow_artifacts(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        format: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = self._artifact_search_filters(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            format=format,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM workflow_artifacts
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [item for row in rows if (item := _row_payload(row)) is not None]

    def search_workflow_artifacts_page(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        offset: int = 0,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        format: str | None = None,
    ) -> dict[str, Any]:
        clauses, params = self._artifact_search_filters(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            format=format,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        bounded_limit = min(max(limit, 1), 50)
        bounded_offset = max(offset, 0)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM workflow_artifacts {where}",
                params,
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT * FROM workflow_artifacts
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, bounded_limit, bounded_offset),
            ).fetchall()
        items = [item for row in rows if (item := _row_payload(row)) is not None]
        return {
            "items": items,
            "total": int(total),
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

    @staticmethod
    def _artifact_search_filters(
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        task_key: str | None,
        task_version: str | None,
        run_id: str | None,
        include_history: bool,
        format: str | None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_history:
            clauses.append("is_current = 1")
        if profile_key:
            clauses.append("profile_key = ?")
            params.append(profile_key)
        if workflow_key:
            clauses.append("workflow_key = ?")
            params.append(workflow_key)
        if task_key:
            clauses.append("task_key = ?")
            params.append(task_key)
        if task_version is not None:
            clauses.append("task_version = ?")
            params.append(task_version)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if path:
            clauses.append("path LIKE ?")
            params.append(f"{path}%")
        # Format filter: by default (None) only markdown is returned so that
        # derived artifacts like HTML reports never leak into agent retrieval.
        # Pass format="all" (or "") to disable the filter.
        if format and format != "all":
            clauses.append("format = ?")
            params.append(format)
        elif format is None:
            clauses.append("format = 'markdown'")
        for tag in tags:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(workflow_artifacts.tags_json) WHERE json_each.value = ?)"
            )
            params.append(str(tag))
        if query:
            lowered = f"%{query.lower()}%"
            clauses.append(
                "(lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(content) LIKE ? OR lower(path) LIKE ?)"
            )
            params.extend([lowered, lowered, lowered, lowered])
        return clauses, params
