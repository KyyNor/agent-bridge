"""工作流任务查询与租约生命周期的持久化。"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from agent_bridge.core.timeutil import utc_iso, utc_now

from .workflow_common import _row_payload


class WorkflowTaskQueueRepositoryMixin:
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
                    ORDER BY set_at DESC, id DESC
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
                    WHEN 'stale' THEN 2
                    WHEN 'failed' THEN 3
                    WHEN 'abandoned' THEN 4
                    WHEN 'completed' THEN 5
                    ELSE 6
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
        clauses = ["t.workflow_key = ?"]
        params: list[Any] = [workflow_key]
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        else:
            # task_version 演进模型下，被取代的旧版本默认不进任务队列视图；
            # 调用方显式按 status 查询时（如查看历史）可仍能看到 superseded。
            clauses.append("t.status <> 'superseded'")
        if type:
            clauses.append("t.type = ?")
            params.append(type)
        if search:
            clauses.append("(lower(t.task_key) LIKE ? OR lower(t.type) LIKE ?)")
            like = f"%{search.lower()}%"
            params.extend([like, like])
        order_by = self._TASK_SORT_ORDER_BY.get(sort or "", self._TASK_SORT_DEFAULT_ORDER_BY)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, EXISTS (
                    SELECT 1 FROM workflow_artifacts a
                    WHERE a.workflow_key = t.workflow_key
                      AND a.task_key = t.task_key
                ) AS has_artifacts,
                CASE WHEN t.status = 'completed'
                  AND EXISTS (
                    SELECT 1 FROM workflow_runs successful
                    WHERE successful.workflow_key = t.workflow_key
                      AND successful.task_key = t.task_key
                      AND successful.task_version = t.task_version
                      AND successful.status = 'completed'
                  )
                  AND COALESCE((
                    SELECT successful.workflow_content_hash
                    FROM workflow_runs successful
                    WHERE successful.workflow_key = t.workflow_key
                      AND successful.task_key = t.task_key
                      AND successful.task_version = t.task_version
                      AND successful.status = 'completed'
                    ORDER BY successful.finished_at DESC, successful.id DESC
                    LIMIT 1
                  ), '') != COALESCE((
                    SELECT revision.content_hash
                    FROM workflow_definitions definition
                    JOIN workflow_definition_revisions revision
                      ON revision.workflow_key = definition.workflow_key
                     AND revision.revision_no = definition.current_revision_no
                    WHERE definition.workflow_key = t.workflow_key
                  ), '')
                  THEN 1 ELSE 0 END AS needs_refresh,
                (
                  SELECT successful.workflow_revision_no
                  FROM workflow_runs successful
                  WHERE successful.workflow_key = t.workflow_key
                    AND successful.task_key = t.task_key
                    AND successful.task_version = t.task_version
                    AND successful.status = 'completed'
                  ORDER BY successful.finished_at DESC, successful.id DESC
                  LIMIT 1
                ) AS last_completed_revision_no
                FROM workflow_tasks t
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                """,
                params,
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def lease_workflow_task(self, workflow_key: str, *, run_id: str, lease_seconds: int) -> dict[str, Any] | None:
        current = utc_now()
        now = utc_iso(current)
        expires_at = utc_iso(current + timedelta(seconds=lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                    SELECT * FROM workflow_tasks
                WHERE workflow_key = ?
                  AND (
                    status IN ('pending', 'stale')
                    OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                  )
                ORDER BY (priority_flag IS NULL), priority_flag ASC,
                    CASE status WHEN 'pending' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END,
                    id
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
                    lease_origin_status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    run_id,
                    expires_at,
                    row["status"] if row["status"] in {"pending", "stale"} else "pending",
                    row["id"],
                ),
            )
            # Back-fill the run->task link. lease_run_id already points the task
            # at this run; mirror task_key onto workflow_runs (created with
            # task_key=None by the scheduler) so the run's task is queryable
            # without a join. Same transaction as the lease -> atomic.
            conn.execute(
                """
                UPDATE workflow_runs
                SET task_key = ?, task_version = ?
                WHERE run_id = ?
                """,
                (row["task_key"], row["task_version"], run_id),
            )
            leased = conn.execute("SELECT * FROM workflow_tasks WHERE id = ?", (row["id"],)).fetchone()
            return _row_payload(leased)

    def lease_workflow_task_by_key(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str,
        run_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        """为按需运行精确租赁指定任务。

        已按任务建立计划的 run 在 ``get_task`` 节点启动时，不得退回到
        队列中领取另一条任务；选择和状态迁移必须处于同一事务中。
        """
        current = utc_now()
        now = utc_iso(current)
        expires_at = utc_iso(current + timedelta(seconds=lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_key = ?
                  AND task_key = ?
                  AND task_version = ?
                  AND (
                    status IN ('pending', 'stale')
                    OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                  )
                """,
                (workflow_key, task_key, task_version, now),
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
                    lease_origin_status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    run_id,
                    expires_at,
                    row["status"] if row["status"] in {"pending", "stale"} else "pending",
                    row["id"],
                ),
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
        flag = flagged_at or utc_iso()
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
                    WHERE id = (
                        SELECT id FROM workflow_tasks
                        WHERE workflow_key = ? AND task_key = ?
                        ORDER BY set_at DESC, id DESC
                        LIMIT 1
                    )
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
        fields. ``attempt_count`` and ``last_error`` are preserved while the
        task is waiting for retry; a successful retry clears ``last_error``
        through ``complete_workflow_task``. Detailed historical errors remain
        on the corresponding workflow run records.
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
                        lease_origin_status = NULL,
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
                        lease_origin_status = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM workflow_tasks
                        WHERE workflow_key = ? AND task_key = ?
                        ORDER BY set_at DESC, id DESC
                        LIMIT 1
                    )
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
                    last_error = NULL,
                    completed_at = CURRENT_TIMESTAMP,
                    lease_run_id = ?,
                    lease_origin_status = NULL,
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
                SELECT id, attempt_count, lease_origin_status FROM workflow_tasks
                WHERE workflow_key = ? AND lease_run_id = ? AND status = 'running'
                """,
                (workflow_key, run_id),
            ).fetchall()
            for row in rows:
                if row["attempt_count"] > max_attempts:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET status = 'abandoned', last_error = ?, lease_origin_status = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (error_message, row["id"]),
                    )
                    abandoned += 1
                else:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET status = COALESCE(NULLIF(lease_origin_status, ''), 'pending'),
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
                SET status = COALESCE(NULLIF(lease_origin_status, ''), 'pending'),
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

    def release_tasks_for_revision_mismatch(
        self,
        workflow_key: str,
        run_id: str,
        error_message: str,
    ) -> int:
        """Release a successful old-revision lease as stale for a fresh plan."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'stale',
                    lease_run_id = NULL,
                    lease_expires_at = NULL,
                    lease_origin_status = NULL,
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
