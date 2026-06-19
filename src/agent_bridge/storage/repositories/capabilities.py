"""SQLite capabilities repository."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.capability_hub.models import McpServiceStatus, ToolType
from agent_bridge.storage.types import enum_value, row_to_dict


class CapabilitiesRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def create_mcp_service(
        self,
        *,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_services (
                  service_key, name, endpoint_url, headers_json, description, tags_json, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_key,
                    name,
                    endpoint_url,
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    created_by,
                ),
            )
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"mcp service not found: {service_key}")
            return service

    def update_mcp_service(
        self,
        service_key: str,
        *,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mcp_services
                SET name = ?,
                    endpoint_url = ?,
                    headers_json = ?,
                    description = ?,
                    tags_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (
                    name,
                    endpoint_url,
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    service_key,
                ),
            )
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"mcp service not found: {service_key}")
            return service

    def get_mcp_service(self, service_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            return row_to_dict(row)

    def list_mcp_services(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM mcp_services ORDER BY service_key").fetchall()
            return [dict(row) for row in rows]

    def update_mcp_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mcp_services
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (enum_value(status), service_key),
            )

    def mark_mcp_service_sync(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        with self._connect() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE mcp_services
                    SET last_synced_at = CURRENT_TIMESTAMP,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                    """,
                    (error, service_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE mcp_services
                    SET status = ?,
                        last_synced_at = CURRENT_TIMESTAMP,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                    """,
                    (McpServiceStatus.error.value, error, service_key),
                )

    def upsert_mcp_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        tool_type: ToolType | str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_tools (
                  service_key,
                  tool_name,
                  display_name,
                  description,
                  input_schema_json,
                  tool_type,
                  tags_json,
                  examples_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_key, tool_name) DO UPDATE SET
                  display_name = excluded.display_name,
                  description = excluded.description,
                  input_schema_json = excluded.input_schema_json,
                  tags_json = excluded.tags_json,
                  examples_json = excluded.examples_json,
                  status = 'active',
                  synced_at = CURRENT_TIMESTAMP,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    service_key,
                    tool_name,
                    display_name,
                    description,
                    json.dumps(input_schema, ensure_ascii=False),
                    enum_value(tool_type),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(examples, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"mcp tool not found: {service_key}/{tool_name}")
            return tool

    def update_mcp_tool_type(
        self,
        service_key: str,
        tool_name: str,
        tool_type: ToolType | str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mcp_tools
                SET tool_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                  AND tool_name = ?
                """,
                (enum_value(tool_type), service_key, tool_name),
            )
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"mcp tool not found: {service_key}/{tool_name}")
            return tool

    def list_mcp_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if service_key is None:
                rows = conn.execute("SELECT * FROM mcp_tools ORDER BY service_key, tool_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_tools WHERE service_key = ? ORDER BY tool_name",
                    (service_key,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_mcp_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            return row_to_dict(row)

    def deactivate_missing_mcp_tools(self, service_key: str, active_tool_names: set[str]) -> None:
        with self._connect() as conn:
            if active_tool_names:
                placeholders = ", ".join("?" for _ in active_tool_names)
                conn.execute(
                    f"""
                    UPDATE mcp_tools
                    SET status = 'inactive',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                      AND tool_name NOT IN ({placeholders})
                      AND status = 'active'
                    """,
                    (service_key, *sorted(active_tool_names)),
                )
            else:
                conn.execute(
                    """
                    UPDATE mcp_tools
                    SET status = 'inactive',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                      AND status = 'active'
                    """,
                    (service_key,),
                )
