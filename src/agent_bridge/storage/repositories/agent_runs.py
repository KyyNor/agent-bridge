"""SQLite repository for agent run logs."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict


class AgentRunsRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def create(
        self,
        *,
        run_key: str,
        agent_name: str,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        ok: bool,
        error: str | None = None,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
        prompt: str,
        output_schema: dict[str, Any] | None = None,
        result: Any | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  run_key, agent_name, profile_key, workflow_key, workflow_run_id,
                  session_id, cwd, model, ok, error, duration_ms, cost_usd,
                  num_turns, prompt, output_schema_json, result_json, events_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_key,
                    agent_name,
                    profile_key,
                    workflow_key,
                    workflow_run_id,
                    session_id,
                    cwd,
                    model,
                    1 if ok else 0,
                    error,
                    duration_ms,
                    cost_usd,
                    num_turns,
                    prompt,
                    json.dumps(output_schema, ensure_ascii=False) if output_schema else None,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    json.dumps(events or [], ensure_ascii=False, default=str),
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            log = row_to_dict(row)
            if log is None:
                raise KeyError(f"agent run not found: {run_key}")
            return self._payload(log)

    def get(self, run_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            return self._payload(row_to_dict(row))

    def list(
        self,
        *,
        agent_name: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        ok: bool | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
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
        if created_from:
            clauses.append("created_at >= ?")
            params.append(created_from)
        if created_to:
            clauses.append("created_at <= ?")
            params.append(created_to)
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
