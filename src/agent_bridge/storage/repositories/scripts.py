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
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | None,
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
                      owner_type, owner_key, content_hash, input_schema_json, output_schema_json, created_by, updated_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        self._dump_schema(input_schema),
                        self._dump_schema(output_schema),
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
                        input_schema_json = ?,
                        output_schema_json = ?,
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
                        self._dump_schema(input_schema),
                        self._dump_schema(output_schema),
                        actor,
                        script_key,
                    ),
                )
            script = row_to_dict(conn.execute("SELECT * FROM scripts WHERE script_key = ?", (script_key,)).fetchone())
            if script is None:
                raise KeyError(f"script not found: {script_key}")
            return self._payload(script)

    def list_scripts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM scripts ORDER BY script_key").fetchall()
            return [self._payload(dict(row)) for row in rows]

    def get_script(self, script_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            script = row_to_dict(conn.execute("SELECT * FROM scripts WHERE script_key = ?", (script_key,)).fetchone())
            return self._payload(script) if script else None

    @staticmethod
    def _payload(script: dict[str, Any]) -> dict[str, Any]:
        payload = dict(script)
        payload["input_schema"] = ScriptsRepository._load_schema(
            payload.get("input_schema_json"),
            fallback='{"type":"object","properties":{},"additionalProperties":true}',
        )
        payload["output_schema"] = ScriptsRepository._load_schema(payload.get("output_schema_json"))
        return payload

    @staticmethod
    def _dump_schema(schema: dict[str, Any] | None) -> str | None:
        if schema is None:
            return None
        return json.dumps(schema, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _load_schema(value: str | None, *, fallback: str | None = None) -> dict[str, Any] | None:
        raw = value if value is not None else fallback
        if raw is None:
            return None
        return json.loads(raw)

    def delete_script(self, script_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM scripts WHERE script_key = ?", (script_key,))
            return cursor.rowcount > 0

    def has_script_runs(self, script_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM script_runs WHERE script_key = ? LIMIT 1",
                (script_key,),
            ).fetchone()
            return row is not None

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

    # --- revisions -------------------------------------------------------

    def create_revision(
        self, *, script_key: str, content_hash: str, snapshot: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(revision_no), 0) FROM script_revisions WHERE script_key = ?",
                (script_key,),
            ).fetchone()
            next_no = int(row[0]) + 1
            conn.execute(
                """
                INSERT INTO script_revisions (script_key, revision_no, content_hash, snapshot_json, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (script_key, next_no, content_hash, snapshot_json, actor),
            )
            conn.execute(
                "UPDATE scripts SET current_revision_no = ? WHERE script_key = ?",
                (next_no, script_key),
            )
            revision = row_to_dict(
                conn.execute(
                    """
                    SELECT script_key AS entity_key, revision_no, content_hash, created_by, created_at
                    FROM script_revisions WHERE script_key = ? AND revision_no = ?
                    """,
                    (script_key, next_no),
                ).fetchone()
            )
            return revision

    def list_revisions(self, script_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = min(max(limit, 1), 500)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT script_key AS entity_key, revision_no, content_hash, created_by, created_at
                FROM script_revisions
                WHERE script_key = ?
                ORDER BY revision_no DESC
                LIMIT ?
                """,
                (script_key, bounded),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_revision(self, script_key: str, revision_no: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = row_to_dict(
                conn.execute(
                    """
                    SELECT script_key AS entity_key, revision_no, content_hash, snapshot_json, created_by, created_at
                    FROM script_revisions
                    WHERE script_key = ? AND revision_no = ?
                    """,
                    (script_key, revision_no),
                ).fetchone()
            )
            if row is None:
                return None
            snapshot_json = row.pop("snapshot_json", None)
            try:
                snapshot = json.loads(snapshot_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("corrupt script revision snapshot") from exc
            if not isinstance(snapshot, dict):
                raise ValueError("corrupt script revision snapshot")
            row["snapshot"] = snapshot
            return row

    def get_current_revision_no(self, script_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT current_revision_no FROM scripts WHERE script_key = ?",
                (script_key,),
            ).fetchone()
            return int(row[0]) if row else 0
