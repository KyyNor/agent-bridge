"""OpenCompass 模型评估任务的持久化。"""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.core.timeutil import utc_iso


class ModelEvaluationRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def create_run(
        self,
        *,
        run_id: str,
        model_name: str,
        base_url: str,
        datasets: list[str],
        max_samples: int,
        sampling_mode: str,
        sample_seed: int,
        work_dir: str,
        created_by: str,
        runtime: str = "docker",
        owner_group_key: str = "",
    ) -> dict[str, Any]:
        now = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_evaluation_runs (
                  run_id, model_name, base_url, runtime, datasets_json, max_samples,
                  sampling_mode, sample_seed, status, work_dir, created_by,
                  owner_group_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    model_name,
                    base_url,
                    runtime,
                    json.dumps(datasets),
                    max_samples,
                    sampling_mode,
                    sample_seed,
                    work_dir,
                    created_by,
                    owner_group_key,
                    now,
                ),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_evaluation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._decode(row)

    def list_runs(
        self,
        *,
        limit: int = 50,
        viewer_group_key: str | None = None,
        enforce_scope: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            where_sql = "WHERE owner_group_key = ?" if enforce_scope else ""
            params: list[Any] = []
            if enforce_scope:
                params.append(viewer_group_key or "")
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM model_evaluation_runs {where_sql} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [decoded for row in rows if (decoded := self._decode(row)) is not None]

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        progress_message: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        assignments: list[str] = []
        values: list[Any] = []
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if progress_message is not None:
            assignments.append("progress_message = ?")
            values.append(progress_message)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            assignments.append("error = ?")
            values.append(error)
        if started:
            assignments.append("started_at = ?")
            values.append(utc_iso())
        if finished:
            assignments.append("finished_at = ?")
            values.append(utc_iso())
        if not assignments:
            return self.get_run(run_id)
        values.append(run_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE model_evaluation_runs SET {', '.join(assignments)} WHERE run_id = ?", values)
        return self.get_run(run_id)

    def create_execution(
        self,
        *,
        execution_id: str,
        run_id: str,
        runner_key: str,
        datasets: list[str],
        image: str,
        work_dir: str,
    ) -> dict[str, Any]:
        now = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_evaluation_executions (
                  execution_id, run_id, runner_key, datasets_json, image, status, work_dir, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (execution_id, run_id, runner_key, json.dumps(datasets), image, work_dir, now),
            )
        return self.get_execution(execution_id) or {}

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM model_evaluation_executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        return self._decode_execution(row)

    def list_executions(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM model_evaluation_executions WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            ).fetchall()
        return [item for row in rows if (item := self._decode_execution(row)) is not None]

    def update_execution(
        self,
        execution_id: str,
        *,
        status: str | None = None,
        progress_message: str | None = None,
        container_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (("status", status), ("progress_message", progress_message), ("container_id", container_id), ("error", error)):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False))
        if started:
            assignments.append("started_at = ?")
            values.append(utc_iso())
        if finished:
            assignments.append("finished_at = ?")
            values.append(utc_iso())
        if not assignments:
            return self.get_execution(execution_id)
        values.append(execution_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE model_evaluation_executions SET {', '.join(assignments)} WHERE execution_id = ?", values
            )
        return self.get_execution(execution_id)

    def abandon_active_runs(self) -> int:
        now = utc_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE model_evaluation_runs
                SET status = 'abandoned', error = '服务重启导致评估进程中断', finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )
            conn.execute(
                """
                UPDATE model_evaluation_executions
                SET status = 'abandoned', error = '服务重启导致评估进程中断', finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )
        return int(cursor.rowcount)

    @staticmethod
    def _decode(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["datasets"] = json.loads(item.pop("datasets_json") or "[]")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item

    @staticmethod
    def _decode_execution(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["datasets"] = json.loads(item.pop("datasets_json") or "[]")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item
