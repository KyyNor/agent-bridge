"""SQLite script registry repository."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict


class ScriptsRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def upsert_script(
        self,
        *,
        script_key: str,
        name: str,
        description: str,
        language: str,
        code: str,
        status: str,
        owner_type: str,
        owner_key: str,
        content_hash: str,
        actor: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            existing = conn.execute("SELECT script_key FROM scripts WHERE script_key = ?", (script_key,)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO scripts (
                      script_key, name, description, language, code, status,
                      owner_type, owner_key, content_hash, created_by, updated_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        script_key,
                        name,
                        description,
                        language,
                        code,
                        status,
                        owner_type,
                        owner_key,
                        content_hash,
                        actor,
                        actor,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE scripts
                    SET name = ?,
                        description = ?,
                        language = ?,
                        code = ?,
                        status = ?,
                        owner_type = ?,
                        owner_key = ?,
                        content_hash = ?,
                        updated_by = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE script_key = ?
                    """,
                    (
                        name,
                        description,
                        language,
                        code,
                        status,
                        owner_type,
                        owner_key,
                        content_hash,
                        actor,
                        script_key,
                    ),
                )
            script = row_to_dict(conn.execute("SELECT * FROM scripts WHERE script_key = ?", (script_key,)).fetchone())
            if script is None:
                raise KeyError(f"script not found: {script_key}")
            return script

    def list_scripts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM scripts ORDER BY script_key").fetchall()
            return [dict(row) for row in rows]

    def get_script(self, script_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return row_to_dict(conn.execute("SELECT * FROM scripts WHERE script_key = ?", (script_key,)).fetchone())

    def delete_script(self, script_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM scripts WHERE script_key = ?", (script_key,))
            return cursor.rowcount > 0

    def create_script_run(
        self,
        *,
        run_id: str,
        script_key: str,
        run_type: str,
        params: dict[str, Any],
        result: dict[str, Any],
        stdout: str,
        stderr: str,
        status: str,
        exit_code: int | None,
        error_message: str | None,
        duration_ms: int,
        actor: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO script_runs (
                  run_id, script_key, run_type, params_json, result_json,
                  stdout, stderr, status, exit_code, error_message, duration_ms, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    script_key,
                    run_type,
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    stdout,
                    stderr,
                    status,
                    exit_code,
                    error_message,
                    duration_ms,
                    actor,
                ),
            )
            run = row_to_dict(conn.execute("SELECT * FROM script_runs WHERE run_id = ?", (run_id,)).fetchone())
            if run is None:
                raise KeyError(f"script run not found: {run_id}")
            return run

    def list_script_runs(self, script_key: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM script_runs
                WHERE script_key = ?
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                (script_key, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_script_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return row_to_dict(conn.execute("SELECT * FROM script_runs WHERE run_id = ?", (run_id,)).fetchone())
