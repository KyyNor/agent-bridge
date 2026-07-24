"""工作流运行、节点运行、日志与执行清理的持久化。"""

from __future__ import annotations

from typing import Any

from .workflow_common import _json_dumps, _row_payload, _run_summary_from_prefixed_row


class WorkflowRunsRepositoryMixin:
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
        workflow_revision_no: int | None = None,
        workflow_content_hash: str | None = None,
        task_version: str = "",
        execution_mode: str = "normal",
        execution_plan: dict[str, Any] | list[Any] | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                  run_id, workflow_key, profile_key, task_key, status, temp_dir,
                  definition_snapshot_json, input_json, workflow_revision_no,
                  workflow_content_hash, task_version, execution_mode,
                  execution_plan_json, source_run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, workflow_key, profile_key, task_key, status, temp_dir,
                    _json_dumps(definition_snapshot or {"nodes": [], "edges": []}),
                    _json_dumps(input_data or {}),
                    workflow_revision_no,
                    workflow_content_hash,
                    task_version,
                    execution_mode,
                    _json_dumps(execution_plan or {}),
                    source_run_id,
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

    def list_workflow_run_summaries(
        self,
        workflow_key: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a paginated run list without snapshots or JSON payloads."""
        bounded_limit = min(max(limit, 1), 100)
        bounded_offset = max(offset, 0)
        columns = """
            run_id, workflow_key, profile_key, task_key, status,
            workflow_revision_no, workflow_content_hash, task_version,
            execution_mode, source_run_id, exit_code, error,
            started_at, finished_at, duration_ms
        """
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS total FROM workflow_runs WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT {columns}
                FROM workflow_runs
                WHERE workflow_key = ?
                ORDER BY started_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (workflow_key, bounded_limit, bounded_offset),
            ).fetchall()
        return {
            "runs": [dict(row) for row in rows],
            "total": int(total),
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

    def list_workflow_run_overviews(self) -> list[dict[str, Any]]:
        """Return one latest/running summary per workflow for list pages."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH latest AS (
                  SELECT
                    r.run_id, r.workflow_key, r.profile_key, r.task_key, r.status,
                    r.workflow_revision_no, r.workflow_content_hash, r.task_version,
                    r.execution_mode, r.source_run_id, r.exit_code, r.error,
                    r.started_at, r.finished_at, r.duration_ms,
                    COUNT(*) OVER (PARTITION BY r.workflow_key) AS run_count,
                    ROW_NUMBER() OVER (
                      PARTITION BY r.workflow_key
                      ORDER BY r.started_at DESC, r.id DESC
                    ) AS row_number
                  FROM workflow_runs r
                ), running AS (
                  SELECT
                    r.run_id, r.workflow_key, r.profile_key, r.task_key, r.status,
                    r.workflow_revision_no, r.workflow_content_hash, r.task_version,
                    r.execution_mode, r.source_run_id, r.exit_code, r.error,
                    r.started_at, r.finished_at, r.duration_ms,
                    ROW_NUMBER() OVER (
                      PARTITION BY r.workflow_key
                      ORDER BY r.started_at DESC, r.id DESC
                    ) AS row_number
                  FROM workflow_runs r
                  WHERE r.status = 'running'
                )
                SELECT
                  w.workflow_key,
                  COALESCE(latest.run_count, 0) AS run_count,
                  latest.run_id AS latest_run_id,
                  latest.profile_key AS latest_profile_key,
                  latest.task_key AS latest_task_key,
                  latest.status AS latest_status,
                  latest.workflow_revision_no AS latest_workflow_revision_no,
                  latest.workflow_content_hash AS latest_workflow_content_hash,
                  latest.task_version AS latest_task_version,
                  latest.execution_mode AS latest_execution_mode,
                  latest.source_run_id AS latest_source_run_id,
                  latest.exit_code AS latest_exit_code,
                  latest.error AS latest_error,
                  latest.started_at AS latest_started_at,
                  latest.finished_at AS latest_finished_at,
                  latest.duration_ms AS latest_duration_ms,
                  running.run_id AS running_run_id,
                  running.profile_key AS running_profile_key,
                  running.task_key AS running_task_key,
                  running.status AS running_status,
                  running.workflow_revision_no AS running_workflow_revision_no,
                  running.workflow_content_hash AS running_workflow_content_hash,
                  running.task_version AS running_task_version,
                  running.execution_mode AS running_execution_mode,
                  running.source_run_id AS running_source_run_id,
                  running.exit_code AS running_exit_code,
                  running.error AS running_error,
                  running.started_at AS running_started_at,
                  running.finished_at AS running_finished_at,
                  running.duration_ms AS running_duration_ms
                FROM workflow_definitions w
                LEFT JOIN latest
                  ON latest.workflow_key = w.workflow_key
                 AND latest.row_number = 1
                LEFT JOIN running
                  ON running.workflow_key = w.workflow_key
                 AND running.row_number = 1
                ORDER BY w.workflow_key
                """
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            latest_run = _run_summary_from_prefixed_row(item, "latest_")
            running_run = _run_summary_from_prefixed_row(item, "running_")
            result.append(
                {
                    "workflow_key": item["workflow_key"],
                    "run_count": int(item["run_count"] or 0),
                    "latest_run": latest_run,
                    "running_run": running_run,
                }
            )
        return result

    def list_completed_workflow_runs_for_task(
        self,
        workflow_key: str,
        task_key: str,
        task_version: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = min(max(limit, 1), 500)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_runs
                WHERE workflow_key = ?
                  AND task_key = ?
                  AND task_version = ?
                  AND status = 'completed'
                ORDER BY finished_at DESC, id DESC
                LIMIT ?
                """,
                (workflow_key, task_key, task_version, bounded),
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def delete_workflow_definition(self, workflow_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            )
            if cursor.rowcount > 0:
                # A deleted workflow key starts a new logical entity if it is
                # later reused. Do not let the old entity's revisions or
                # pending import confirmations leak into that new history.
                conn.execute(
                    "DELETE FROM workflow_definition_revisions WHERE workflow_key = ?",
                    (workflow_key,),
                )
                conn.execute(
                    "DELETE FROM workflow_definition_imports WHERE target_workflow_key = ?",
                    (workflow_key,),
                )
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
            if status == "completed":
                conn.execute(
                    """
                    UPDATE workflow_artifacts
                    SET is_current = 0
                    WHERE workflow_key = (SELECT workflow_key FROM workflow_runs WHERE run_id = ?)
                      AND task_key IS (SELECT task_key FROM workflow_runs WHERE run_id = ?)
                      AND run_id <> ?
                    """,
                    (run_id, run_id, run_id),
                )
                conn.execute(
                    "UPDATE workflow_artifacts SET is_current = 1 WHERE run_id = ?",
                    (run_id,),
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
                INSERT OR IGNORE INTO workflow_node_runs (
                  run_id, node_id, node_type, node_fingerprint, action,
                  reuse_reason, source_run_id, source_node_id, source_node_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        str(node["node_id"]),
                        str(node["node_type"]),
                        node.get("node_fingerprint"),
                        node.get("action"),
                        node.get("reuse_reason"),
                        node.get("source_run_id"),
                        node.get("source_node_id"),
                        node.get("source_node_fingerprint"),
                    )
                    for node in nodes
                ],
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
        artifact_ids: list[str] | None = None,
        node_fingerprint: str | None = None,
        action: str | None = None,
        reuse_reason: str | None = None,
        source_run_id: str | None = None,
        source_node_id: str | None = None,
        source_node_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_node_runs
                SET status = ?, condition_results_json = ?, output_json = ?, error = ?,
                    agent_run_key = ?, script_run_id = ?,
                    artifact_ids_json = COALESCE(?, artifact_ids_json),
                    node_fingerprint = COALESCE(?, node_fingerprint),
                    action = COALESCE(?, action),
                    reuse_reason = COALESCE(?, reuse_reason),
                    source_run_id = COALESCE(?, source_run_id),
                    source_node_id = COALESCE(?, source_node_id),
                    source_node_fingerprint = COALESCE(?, source_node_fingerprint),
                    finished_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND node_id = ?
                """,
                (
                    status, _json_dumps(condition_results or []), _json_dumps(output or {}), error,
                    agent_run_key, script_run_id,
                    _json_dumps(artifact_ids) if artifact_ids is not None else None,
                    node_fingerprint, action, reuse_reason, source_run_id,
                    source_node_id, source_node_fingerprint, run_id, node_id,
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

    def associate_workflow_run_artifacts(
        self,
        run_id: str,
        node_id: str,
        artifact_ids: list[str],
        *,
        source_run_id: str | None = None,
        source_node_id: str | None = None,
    ) -> None:
        """Append artifact lineage for a current run without mutating the source artifact."""
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO workflow_run_artifacts (
                  run_id, node_id, artifact_id, source_run_id, source_node_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (run_id, node_id, str(artifact_id), source_run_id, source_node_id)
                    for artifact_id in artifact_ids
                ],
            )

    def list_workflow_run_artifacts(
        self,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if node_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM workflow_run_artifacts
                    WHERE run_id = ?
                    ORDER BY node_id, artifact_id
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM workflow_run_artifacts
                    WHERE run_id = ? AND node_id = ?
                    ORDER BY artifact_id
                    """,
                    (run_id, node_id),
                ).fetchall()
            return [dict(row) for row in rows]

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
