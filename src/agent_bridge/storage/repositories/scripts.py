"""SQLite script registry repository."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict

from . import revisions as _revisions


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
        owner_group_key: str = "",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            existing = conn.execute("SELECT script_key FROM scripts WHERE script_key = ?", (script_key,)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO scripts (
                      script_key, name, description, language, code, status,
                      owner_type, owner_key, content_hash, input_schema_json, output_schema_json,
                      created_by, updated_by, owner_group_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        owner_group_key,
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
                        owner_group_key = ?,
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
                        owner_group_key,
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
        owner_group_key: str = "",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO script_runs (
                  run_id, script_key, run_type, params_json, result_json,
                  stdout, stderr, status, exit_code, error_message, duration_ms,
                  created_by, owner_group_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    owner_group_key,
                ),
            )
            run = row_to_dict(conn.execute("SELECT * FROM script_runs WHERE run_id = ?", (run_id,)).fetchone())
            if run is None:
                raise KeyError(f"script run not found: {run_id}")
            return run

    def list_script_runs(
        self,
        script_key: str,
        *,
        limit: int,
        viewer_group_key: str | None = None,
        enforce_scope: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            scope_sql = " AND owner_group_key = ?" if enforce_scope else ""
            params: list[Any] = [script_key]
            if enforce_scope:
                params.append(viewer_group_key or "")
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT * FROM script_runs
                WHERE script_key = ?
                {scope_sql}
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_script_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return row_to_dict(conn.execute("SELECT * FROM script_runs WHERE run_id = ?", (run_id,)).fetchone())

    # --- revisions -------------------------------------------------------

    def create_revision(
        self, *, script_key: str, content_hash: str, snapshot: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        with self._connect() as conn:
            return _revisions.create_revision(
                conn,
                table="script_revisions",
                key_column="script_key",
                key_value=script_key,
                content_hash=content_hash,
                snapshot=snapshot,
                actor=actor,
                owner_table="scripts",
                snapshot_label="script",
            )

    def list_revisions(self, script_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return _revisions.list_revisions(
                conn,
                table="script_revisions",
                key_column="script_key",
                key_value=script_key,
                limit=limit,
            )

    def get_revision(self, script_key: str, revision_no: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _revisions.get_revision(
                conn,
                table="script_revisions",
                key_column="script_key",
                key_value=script_key,
                revision_no=revision_no,
                snapshot_label="script",
            )

    def get_current_revision_no(self, script_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT current_revision_no FROM scripts WHERE script_key = ?",
                (script_key,),
            ).fetchone()
            return int(row[0]) if row else 0
