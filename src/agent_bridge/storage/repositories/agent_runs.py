"""SQLite repository for agent run logs."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict


class AgentRunsRepository:
    def __init__(self, db_path, connect, prune_callback=None) -> None:
        self._db_path = db_path
        self._connect = connect
        self._prune_callback = prune_callback

    def create(
        self,
        *,
        run_key: str,
        agent_name: str,
        backend_key: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        ok: bool = False,
        status: str = "running",
        error: str | None = None,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
        prompt: str = "",
        output_schema: dict[str, Any] | None = None,
        result: Any | None = None,
        events: list[dict[str, Any]] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  run_key, agent_name, backend_key, profile_key, workflow_key, workflow_run_id,
                  session_id, cwd, model, ok, status, error, duration_ms, cost_usd,
                  num_turns, prompt, output_schema_json, result_json, events_json,
                  started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_key,
                    agent_name,
                    backend_key,
                    profile_key,
                    workflow_key,
                    workflow_run_id,
                    session_id,
                    cwd,
                    model,
                    1 if ok else 0,
                    status,
                    error,
                    duration_ms,
                    cost_usd,
                    num_turns,
                    prompt,
                    json.dumps(output_schema, ensure_ascii=False) if output_schema else None,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    json.dumps(events or [], ensure_ascii=False, default=str),
                    started_at,
                    finished_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            log = row_to_dict(row)
            if log is None:
                raise KeyError(f"agent run not found: {run_key}")
        if callable(self._prune_callback):
            self._prune_callback()
        return self._payload(log)

    def finish_run(
        self,
        run_key: str,
        *,
        ok: bool,
        status: str,
        error: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
        result: Any | None = None,
        events: list[dict[str, Any]] | None = None,
        finished_at: str | None = None,
    ) -> bool:
        """Backfill a run's outcome. Used when a placeholder ``running`` row was
        created at start and the run has now reached a terminal state."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_runs SET
                  ok = ?, status = ?, error = ?, session_id = ?, model = COALESCE(?, model),
                  duration_ms = ?, cost_usd = ?, num_turns = ?,
                  result_json = ?, events_json = ?, finished_at = ?
                WHERE run_key = ? AND status = 'running'
                """,
                (
                    1 if ok else 0,
                    status,
                    error,
                    session_id,
                    model,
                    duration_ms,
                    cost_usd,
                    num_turns,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    json.dumps(events or [], ensure_ascii=False, default=str),
                    finished_at,
                    run_key,
                ),
            )
            return bool(cursor.rowcount)

    def get(self, run_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            return self._payload(row_to_dict(row))

    def update_cwd(self, run_key: str, cwd: str) -> None:
        """Backfill the work-dir path on a placeholder row once it is known."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_runs SET cwd = ? WHERE run_key = ?",
                (cwd, run_key),
            )

    def list(
        self,
        *,
        agent_name: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        ok: bool | None = None,
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = self._filters(
            agent_name=agent_name,
            profile_key=profile_key,
            workflow_key=workflow_key,
            workflow_run_id=workflow_run_id,
            ok=ok,
            status=status,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        bounded_limit = min(max(limit, 1), 200)
        bounded_offset = max(offset, 0)
        sql = (
            f"SELECT * FROM agent_runs{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (*params, bounded_limit, bounded_offset)).fetchall()
            return [self._summary(row_to_dict(row)) for row in rows]

    def list_paginated(
        self,
        *,
        agent_name: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        ok: bool | None = None,
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        bounded_limit = min(max(limit, 1), 200)
        bounded_offset = max(offset, 0)
        list_clauses, list_params = self._filters(
            agent_name=agent_name,
            profile_key=profile_key,
            workflow_key=workflow_key,
            workflow_run_id=workflow_run_id,
            ok=ok,
            status=status,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        base_clauses, base_params = self._filters(
            agent_name=agent_name,
            profile_key=profile_key,
            workflow_key=workflow_key,
            workflow_run_id=workflow_run_id,
            ok=None,
            status=None,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        list_where = (" WHERE " + " AND ".join(list_clauses)) if list_clauses else ""
        base_where = (" WHERE " + " AND ".join(base_clauses)) if base_clauses else ""
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM agent_runs{list_where}",
                list_params,
            ).fetchone()["total"]
            count_row = conn.execute(
                f"""
                SELECT
                  COUNT(*) AS all_count,
                  SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                  SUM(
                    CASE
                      WHEN status = 'failed'
                        OR (status <> 'running' AND status <> 'stopped' AND ok = 0)
                      THEN 1 ELSE 0
                    END
                  ) AS failed,
                  SUM(
                    CASE
                      WHEN status <> 'running'
                        AND status <> 'failed'
                        AND status <> 'stopped'
                        AND (status IN ('completed', 'success') OR ok = 1)
                      THEN 1 ELSE 0
                    END
                  ) AS success,
                  SUM(CASE WHEN status = 'stopped' THEN 1 ELSE 0 END) AS stopped
                FROM agent_runs{base_where}
                """,
                base_params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT * FROM agent_runs{list_where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*list_params, bounded_limit, bounded_offset),
            ).fetchall()
        return {
            "items": [self._summary(row_to_dict(row)) for row in rows],
            "total": int(total),
            "limit": bounded_limit,
            "offset": bounded_offset,
            "counts": {
                "all": int(count_row["all_count"] or 0),
                "success": int(count_row["success"] or 0),
                "failed": int(count_row["failed"] or 0),
                "running": int(count_row["running"] or 0),
                "stopped": int(count_row["stopped"] or 0),
            },
        }

    @staticmethod
    def _filters(
        *,
        agent_name: str | None,
        profile_key: str | None,
        workflow_key: str | None,
        workflow_run_id: str | None,
        ok: bool | None,
        status: str | None,
        created_from: str | None,
        created_to: str | None,
        search: str | None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_name:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if profile_key:
            clauses.append("profile_key = ?")
            params.append(profile_key)
        if workflow_key:
            clauses.append("workflow_key = ?")
            params.append(workflow_key)
        if workflow_run_id:
            clauses.append("workflow_run_id = ?")
            params.append(workflow_run_id)
        if ok is not None:
            clauses.append("ok = ?")
            params.append(1 if ok else 0)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if created_from:
            clauses.append("created_at >= ?")
            params.append(created_from)
        if created_to:
            clauses.append("created_at <= ?")
            params.append(created_to)
        if search:
            like = f"%{search.lower()}%"
            clauses.append(
                "(lower(COALESCE(agent_name, '')) LIKE ? "
                "OR lower(COALESCE(profile_key, '')) LIKE ? "
                "OR lower(COALESCE(workflow_key, '')) LIKE ? "
                "OR lower(COALESCE(error, '')) LIKE ? "
                "OR lower(COALESCE(run_key, '')) LIKE ? "
                "OR lower(COALESCE(status, '')) LIKE ?)"
            )
            params.extend([like] * 6)
        return clauses, params

    def purge_created_before(self, cutoff_created_at: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM agent_runs WHERE created_at < ?",
                (cutoff_created_at,),
            )
            return int(cursor.rowcount or 0)

    @staticmethod
    def _payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        row["ok"] = bool(row.get("ok"))
        row["output_schema"] = _loads(row.pop("output_schema_json", None))
        row["result"] = _loads(row.pop("result_json", None))
        row["events"] = _loads(row.pop("events_json", "[]")) or []
        return row

    @staticmethod
    def _summary(row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        row["ok"] = bool(row.get("ok"))
        # Drop heavy columns from list view; callers can `get()` for full detail.
        row.pop("events_json", None)
        row.pop("prompt", None)
        row.pop("result_json", None)
        row.pop("output_schema_json", None)
        return row


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
