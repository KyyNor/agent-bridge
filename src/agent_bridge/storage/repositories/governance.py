"""SQLite governance repository."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.capabilities.models import CallLogStatus
from agent_bridge.storage.types import enum_value, json_bytes, json_summary, row_to_dict


class GovernanceRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def _migrate_tool_call_logs_nullable_profile(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(tool_call_logs)").fetchall()
        profile_column = next((column for column in columns if column[1] == "profile_key"), None)
        if profile_column is None or profile_column[3] == 0:
            return

        conn.execute("ALTER TABLE tool_call_logs RENAME TO tool_call_logs_old")
        conn.execute(
            """
            CREATE TABLE tool_call_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              log_id TEXT NOT NULL UNIQUE,
              actor TEXT NOT NULL,
              profile_key TEXT,
              entrypoint TEXT NOT NULL,
              source_type TEXT,
              source_key TEXT,
              tool_name TEXT,
              request_json TEXT NOT NULL DEFAULT '{}',
              response_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL,
              error_message TEXT,
              duration_ms INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tool_call_logs (
              id,
              log_id,
              actor,
              profile_key,
              entrypoint,
              source_type,
              source_key,
              tool_name,
              request_json,
              response_json,
              status,
              error_message,
              duration_ms,
              created_at
            )
            SELECT
              id,
              log_id,
              actor,
              profile_key,
              entrypoint,
              source_type,
              source_key,
              tool_name,
              request_json,
              response_json,
              status,
              error_message,
              duration_ms,
              created_at
            FROM tool_call_logs_old
            """
        )
        conn.execute("DROP TABLE tool_call_logs_old")
        conn.execute("CREATE INDEX idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC, id DESC)")
        conn.execute("CREATE INDEX idx_tool_call_logs_profile ON tool_call_logs(profile_key)")
        conn.execute("CREATE INDEX idx_tool_call_logs_source ON tool_call_logs(source_type, source_key)")

    def upsert_project_profile(
        self,
        *,
        profile_key: str,
        name: str,
        description: str = "",
        status: str = "active",
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_profiles (profile_key, name, description, status, created_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, name, description, status, created_by),
            )
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            profile = row_to_dict(row)
            if profile is None:
                raise KeyError(f"project profile not found: {profile_key}")
            return profile

    def get_project_profile(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def list_project_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  profile.*,
                  COALESCE(SUM(CASE WHEN rule.effect = 'allow' THEN 1 ELSE 0 END), 0) AS allow_count,
                  COALESCE(SUM(CASE WHEN rule.effect = 'deny' THEN 1 ELSE 0 END), 0) AS deny_count
                FROM project_profiles profile
                LEFT JOIN profile_source_rules rule ON rule.profile_key = profile.profile_key
                GROUP BY profile.id
                ORDER BY profile.profile_key
                """
            ).fetchall()
            return [
                {
                    **dict(row),
                    "allow_count": int(row["allow_count"]),
                    "deny_count": int(row["deny_count"]),
                }
                for row in rows
            ]

    def replace_profile_source_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profile_source_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_source_rules (profile_key, source_type, source_key, effect)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        profile_key,
                        enum_value(rule["source_type"]),
                        rule["source_key"],
                        enum_value(rule["effect"]),
                    ),
                )

    def list_profile_source_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_source_rules
                WHERE profile_key = ?
                ORDER BY source_key, effect
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_profile_resource_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profile_resource_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_resource_rules (profile_key, resource_type, resource_key)
                    VALUES (?, ?, ?)
                    """,
                    (profile_key, rule["resource_type"], rule["resource_key"]),
                )

    def list_profile_resource_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_resource_rules
                WHERE profile_key = ?
                ORDER BY resource_type, resource_key
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_profile_pin_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profile_pin_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_pin_rules (profile_key, service_key, tool_type, created_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (profile_key, rule["service_key"], rule["tool_type"], rule["created_by"]),
                )

    def list_profile_pin_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_pin_rules
                WHERE profile_key = ?
                ORDER BY service_key, tool_type
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_profile_pin_settings(
        self,
        *,
        profile_key: str,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
        auto_cache: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cache_json = json.dumps(auto_cache, ensure_ascii=False, default=str) if auto_cache is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_pin_settings (
                  profile_key, mode, ratio_percent, count, auto_cache_json, auto_cache_computed_at
                )
                VALUES (?, ?, ?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)
                ON CONFLICT(profile_key) DO UPDATE SET
                  mode = excluded.mode,
                  ratio_percent = excluded.ratio_percent,
                  count = excluded.count,
                  auto_cache_json = excluded.auto_cache_json,
                  auto_cache_computed_at = excluded.auto_cache_computed_at,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, mode, ratio_percent, count, cache_json, cache_json),
            )
            row = conn.execute(
                "SELECT * FROM profile_pin_settings WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            settings = row_to_dict(row)
            if settings is None:
                raise KeyError(f"profile pin settings not found: {profile_key}")
            return settings

    def get_profile_pin_settings(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profile_pin_settings WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def clear_profile_pin_auto_cache(self, profile_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE profile_pin_settings
                SET auto_cache_json = NULL,
                    auto_cache_computed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE profile_key = ?
                """,
                (profile_key,),
            )

    def list_resource_rule_profiles(
        self, resource_type: str, resource_key: str
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM profile_resource_rules
                WHERE resource_type = ? AND resource_key = ?
                ORDER BY profile_key
                """,
                (resource_type, resource_key),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_resource_rule_profiles(
        self, resource_type: str, resource_key: str, profile_keys: list[str], overrides: dict[str, dict[str, str | None]] | None = None
    ) -> None:
        overrides = overrides or {}
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM profile_resource_rules WHERE resource_type = ? AND resource_key = ?",
                (resource_type, resource_key),
            )
            for profile_key in profile_keys:
                ovr = overrides.get(profile_key, {})
                conn.execute(
                    """
                    INSERT INTO profile_resource_rules (profile_key, resource_type, resource_key, retrieval_backend_slug, retrieval_agent_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        profile_key,
                        resource_type,
                        resource_key,
                        ovr.get("retrieval_backend_slug"),
                        ovr.get("retrieval_agent_id"),
                    ),
                )

    def create_tool_call_log(
        self,
        *,
        log_id: str,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        request: Any | None = None,
        response: Any | None = None,
        status: CallLogStatus | str,
        error_message: str | None = None,
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        request_value = {} if request is None else request
        response_value = {} if response is None else response
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_call_logs (
                  log_id,
                  actor,
                  profile_key,
                  entrypoint,
                  source_type,
                  source_key,
                  tool_name,
                  request_json,
                  response_json,
                  status,
                  error_message,
                  failure_stage,
                  failure_owner,
                  error_type,
                  resource_type,
                  resource_key,
                  request_summary_json,
                  response_summary_json,
                  duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    actor,
                    profile_key,
                    entrypoint,
                    enum_value(source_type),
                    source_key,
                    tool_name,
                    json.dumps(request_value, ensure_ascii=False, default=str),
                    json.dumps(response_value, ensure_ascii=False, default=str),
                    enum_value(status),
                    error_message,
                    enum_value(failure_stage),
                    enum_value(failure_owner),
                    error_type,
                    resource_type,
                    resource_key,
                    json.dumps(json_summary(request_value), ensure_ascii=False, default=str),
                    json.dumps(json_summary(response_value), ensure_ascii=False, default=str),
                    duration_ms,
                ),
            )
            row = conn.execute("SELECT * FROM tool_call_logs WHERE log_id = ?", (log_id,)).fetchone()
            log = row_to_dict(row)
            if log is None:
                raise KeyError(f"tool call log not found: {log_id}")
            return log

    def list_tool_call_logs(
        self,
        *,
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: CallLogStatus | str | None = None,
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        for column, value in [
            ("entrypoint", entrypoint),
            ("source_type", enum_value(source_type)),
            ("source_key", source_key),
            ("tool_name", tool_name),
            ("profile_key", profile_key),
            ("status", enum_value(status)),
            ("failure_stage", enum_value(failure_stage)),
            ("failure_owner", enum_value(failure_owner)),
            ("error_type", error_type),
            ("resource_type", resource_type),
            ("resource_key", resource_key),
        ]:
            if value is not None:
                filters.append(f"{column} = ?")
                params.append(value)
        if created_from is not None:
            filters.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            filters.append("created_at < ?")
            params.append(created_to)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM tool_call_logs
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def aggregate_tool_call_stats(
        self,
        *,
        dimensions: list[str],
        created_from: str | None,
        created_to: str | None,
        bucket: str | None,
    ) -> list[dict[str, Any]]:
        allowed_dimensions = {
            "profile_key",
            "entrypoint",
            "source_type",
            "source_key",
            "tool_name",
            "tool_type",
            "status",
            "failure_stage",
            "failure_owner",
            "error_type",
            "resource_type",
            "resource_key",
        }
        invalid = [dimension for dimension in dimensions if dimension not in allowed_dimensions]
        if invalid:
            raise ValueError(f"invalid stats dimension: {invalid[0]}")

        dimension_expressions = {
            "profile_key": "tool_call_logs.profile_key",
            "entrypoint": "tool_call_logs.entrypoint",
            "source_type": "tool_call_logs.source_type",
            "source_key": "tool_call_logs.source_key",
            "tool_name": "tool_call_logs.tool_name",
            "tool_type": "mcp_tools.tool_type",
            "status": "tool_call_logs.status",
            "failure_stage": "tool_call_logs.failure_stage",
            "failure_owner": "tool_call_logs.failure_owner",
            "error_type": "tool_call_logs.error_type",
            "resource_type": "tool_call_logs.resource_type",
            "resource_key": "tool_call_logs.resource_key",
        }
        selected = [f"{dimension_expressions[dimension]} AS {dimension}" for dimension in dimensions]
        if bucket:
            if bucket == "hour":
                selected.insert(0, "strftime('%Y-%m-%d %H:00:00', tool_call_logs.created_at) AS bucket")
            elif bucket == "day":
                selected.insert(0, "date(tool_call_logs.created_at) AS bucket")
            else:
                raise ValueError("invalid stats bucket")

        group_columns = ["bucket"] if bucket else []
        group_columns.extend(dimension_expressions[dimension] for dimension in dimensions)
        select_clause = ", ".join(selected) if selected else "'all' AS scope"
        group_clause = f"GROUP BY {', '.join(group_columns)}" if group_columns else ""
        filters: list[str] = []
        params: list[Any] = []
        if created_from is not None:
            filters.append("tool_call_logs.created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            filters.append("tool_call_logs.created_at < ?")
            params.append(created_to)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        join_clause = (
            """
                LEFT JOIN mcp_tools
                  ON mcp_tools.service_key = tool_call_logs.source_key
                 AND mcp_tools.tool_name = tool_call_logs.tool_name
                 AND tool_call_logs.source_type = 'mcp_service'
            """
            if "tool_type" in dimensions
            else ""
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                  {select_clause},
                  COUNT(*) AS calls,
                  SUM(CASE WHEN tool_call_logs.status = 'success' THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN tool_call_logs.status = 'error' THEN 1 ELSE 0 END) AS error,
                  SUM(CASE WHEN tool_call_logs.status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                  ROUND(AVG(COALESCE(tool_call_logs.duration_ms, 0)), 0) AS avg_duration_ms,
                  MAX(tool_call_logs.duration_ms) AS max_duration_ms
                FROM tool_call_logs
                {join_clause}
                {where_clause}
                {group_clause}
                ORDER BY calls DESC
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def aggregate_pin_group_usage(self, *, profile_key: str, created_from: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  logs.source_key AS service_key,
                  tools.tool_type AS tool_type,
                  COUNT(*) AS calls
                FROM tool_call_logs logs
                JOIN mcp_tools tools
                  ON tools.service_key = logs.source_key
                 AND tools.tool_name = logs.tool_name
                WHERE logs.profile_key = ?
                  AND logs.source_type = 'mcp_service'
                  AND logs.entrypoint = 'metamcp_execute'
                  AND logs.status = 'success'
                  AND logs.created_at >= ?
                GROUP BY logs.source_key, tools.tool_type
                ORDER BY calls DESC, logs.source_key, tools.tool_type
                """,
                (profile_key, created_from),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_tool_call_log(self, log_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tool_call_logs WHERE log_id = ?",
                (log_id,),
            ).fetchone()
            return row_to_dict(row)

    def migrate_profile_resource_retrieval_columns(self) -> None:
        with self._connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(profile_resource_rules)").fetchall()}
            if "retrieval_backend_slug" not in columns:
                conn.execute("ALTER TABLE profile_resource_rules ADD COLUMN retrieval_backend_slug TEXT")
            if "retrieval_agent_id" not in columns:
                conn.execute("ALTER TABLE profile_resource_rules ADD COLUMN retrieval_agent_id TEXT")

    def get_profile_resource_rule(self, profile_key: str, resource_type: str, resource_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM profile_resource_rules
                WHERE profile_key = ? AND resource_type = ? AND resource_key = ?
                """,
                (profile_key, resource_type, resource_key),
            ).fetchone()
            return row_to_dict(row)
