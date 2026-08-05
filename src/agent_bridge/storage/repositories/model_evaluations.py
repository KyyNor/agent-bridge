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
        work_dir: str,
        created_by: str,
    ) -> dict[str, Any]:
        now = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_evaluation_runs (
                  run_id, model_name, base_url, datasets_json, max_samples, status, work_dir, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (run_id, model_name, base_url, json.dumps(datasets), max_samples, work_dir, created_by, now),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_evaluation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._decode(row)

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM model_evaluation_runs ORDER BY created_at DESC LIMIT ?", (limit,)
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
        return int(cursor.rowcount)

    @staticmethod
    def _decode(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["datasets"] = json.loads(item.pop("datasets_json") or "[]")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item
