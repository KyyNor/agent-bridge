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

    def list_mcp_service_summaries(self) -> list[dict[str, Any]]:
        """Return service metadata without credentials or tool payloads."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  s.service_key, s.name, s.endpoint_url, s.description,
                  s.tags_json, s.status, s.created_by, s.created_at,
                  s.updated_at, s.last_synced_at, s.last_error,
                  COUNT(t.id) AS tool_count
                FROM mcp_services s
                LEFT JOIN mcp_tools t
                  ON t.service_key = s.service_key AND t.status = 'active'
                GROUP BY s.id
                ORDER BY s.service_key
                """
            ).fetchall()
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

    def list_tool_summaries(
        self,
        *,
        source_type: str | None = None,
        service_key: str | None = None,
        tool_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Page active MCP/OpenAPI tools without schemas, mappings or examples."""
        bounded_limit = min(max(int(limit), 1), 200)
        bounded_offset = max(int(offset), 0)
        union_sql = """
          SELECT
            'mcp_service' AS source_type,
            t.service_key,
            s.name AS service_name,
            t.tool_name,
            t.display_name,
            t.description,
            t.tool_type,
            t.tags_json,
            NULL AS operation_id,
            NULL AS method,
            NULL AS path
          FROM mcp_tools t
          JOIN mcp_services s ON s.service_key = t.service_key
          WHERE t.status = 'active' AND s.status = 'enabled'
          UNION ALL
          SELECT
            'openapi_service' AS source_type,
            t.service_key,
            s.name AS service_name,
            t.tool_name,
            t.display_name,
            t.description,
            t.tool_type,
            t.tags_json,
            t.operation_id,
            t.method,
            t.path
          FROM openapi_tools t
          JOIN openapi_services s ON s.service_key = t.service_key
          WHERE t.status = 'active' AND s.status = 'enabled'
        """
        filters: list[str] = []
        params: list[Any] = []
        if source_type:
            filters.append("source_type = ?")
            params.append(source_type)
        if service_key:
            filters.append("service_key = ?")
            params.append(service_key)
        if tool_type:
            filters.append("tool_type = ?")
            params.append(tool_type)
        if query:
            like = f"%{query.lower()}%"
            filters.append(
                "(lower(tool_name) LIKE ? OR lower(display_name) LIKE ? OR lower(COALESCE(description, '')) LIKE ?)"
            )
            params.extend([like, like, like])
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        count_filters = [item for item in filters if item != "tool_type = ?"]
        count_params = []
        param_index = 0
        for item in filters:
            if item == "tool_type = ?":
                param_index += 1
                continue
            if "LIKE ?" in item:
                count_params.extend(params[param_index:param_index + 3])
                param_index += 3
            else:
                count_params.append(params[param_index])
                param_index += 1
        count_where = f"WHERE {' AND '.join(count_filters)}" if count_filters else ""
        with self._connect() as conn:
            total = conn.execute(
                f"WITH tools AS ({union_sql}) SELECT COUNT(*) AS total FROM tools {where_clause}",
                params,
            ).fetchone()["total"]
            count_rows = conn.execute(
                f"WITH tools AS ({union_sql}) SELECT tool_type, COUNT(*) AS count FROM tools {count_where} GROUP BY tool_type",
                count_params,
            ).fetchall()
            rows = conn.execute(
                f"""
                WITH tools AS ({union_sql})
                SELECT * FROM tools
                {where_clause}
                ORDER BY service_key, tool_name
                LIMIT ? OFFSET ?
                """,
                (*params, bounded_limit, bounded_offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            items.append(item)
        return {
            "items": items,
            "total": int(total),
            "limit": bounded_limit,
            "offset": bounded_offset,
            "counts": {str(row["tool_type"]): int(row["count"]) for row in count_rows},
        }

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

    def create_openapi_service(
        self,
        *,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any],
        headers: dict[str, Any],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openapi_services (
                  service_key, name, base_url, spec_url, spec_content,
                  auth_config_json, headers_json, description, tags_json, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_key,
                    name,
                    base_url,
                    spec_url,
                    spec_content,
                    json.dumps(auth_config, ensure_ascii=False),
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    created_by,
                ),
            )
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"openapi service not found: {service_key}")
            return service

    def update_openapi_service(
        self,
        service_key: str,
        *,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any],
        headers: dict[str, Any],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_services
                SET name = ?,
                    base_url = ?,
                    spec_url = ?,
                    spec_content = ?,
                    auth_config_json = ?,
                    headers_json = ?,
                    description = ?,
                    tags_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (
                    name,
                    base_url,
                    spec_url,
                    spec_content,
                    json.dumps(auth_config, ensure_ascii=False),
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    service_key,
                ),
            )
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"openapi service not found: {service_key}")
            return service

    def get_openapi_service(self, service_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            return row_to_dict(row)

    def list_openapi_services(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM openapi_services ORDER BY service_key").fetchall()
            return [dict(row) for row in rows]

    def list_openapi_service_summaries(self) -> list[dict[str, Any]]:
        """Return OpenAPI service metadata without spec/auth/header blobs."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  s.service_key, s.name, s.base_url, s.spec_url, s.description,
                  s.tags_json, s.status, s.created_by, s.created_at,
                  s.updated_at, s.last_imported_at, s.last_error,
                  COUNT(t.id) AS tool_count
                FROM openapi_services s
                LEFT JOIN openapi_tools t
                  ON t.service_key = s.service_key AND t.status = 'active'
                GROUP BY s.id
                ORDER BY s.service_key
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def update_openapi_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_services
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (enum_value(status), service_key),
            )

    def mark_openapi_service_import(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_services
                SET last_imported_at = CURRENT_TIMESTAMP,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (None if success else error, service_key),
            )

    def upsert_openapi_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        operation_id: str,
        method: str,
        path: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        request_mapping: dict[str, Any],
        response_schema: dict[str, Any],
        tool_type: ToolType | str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openapi_tools (
                  service_key,
                  tool_name,
                  operation_id,
                  method,
                  path,
                  display_name,
                  description,
                  input_schema_json,
                  request_mapping_json,
                  response_schema_json,
                  tool_type,
                  tags_json,
                  examples_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_key, tool_name) DO UPDATE SET
                  operation_id = excluded.operation_id,
                  method = excluded.method,
                  path = excluded.path,
                  display_name = excluded.display_name,
                  description = excluded.description,
                  input_schema_json = excluded.input_schema_json,
                  request_mapping_json = excluded.request_mapping_json,
                  response_schema_json = excluded.response_schema_json,
                  tool_type = excluded.tool_type,
                  tags_json = excluded.tags_json,
                  examples_json = excluded.examples_json,
                  status = 'active',
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    service_key,
                    tool_name,
                    operation_id,
                    method.upper(),
                    path,
                    display_name,
                    description,
                    json.dumps(input_schema, ensure_ascii=False),
                    json.dumps(request_mapping, ensure_ascii=False),
                    json.dumps(response_schema, ensure_ascii=False),
                    enum_value(tool_type),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(examples, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"openapi tool not found: {service_key}/{tool_name}")
            return tool

    def list_openapi_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if service_key is None:
                rows = conn.execute("SELECT * FROM openapi_tools ORDER BY service_key, tool_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM openapi_tools WHERE service_key = ? ORDER BY tool_name",
                    (service_key,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_openapi_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            return row_to_dict(row)

    def update_openapi_tool_type(self, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_tools
                SET tool_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                  AND tool_name = ?
                """,
                (enum_value(tool_type), service_key, tool_name),
            )
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"openapi tool not found: {service_key}/{tool_name}")
            return tool

    def delete_openapi_tool(self, service_key: str, tool_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_tools
                SET status = 'inactive',
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                  AND tool_name = ?
                """,
                (service_key, tool_name),
            )

    def delete_mcp_service(self, service_key: str) -> None:
        """硬删除一个 MCP 服务；mcp_tools 经外键 ON DELETE CASCADE 自动清除。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_services WHERE service_key = ?", (service_key,))

    def delete_openapi_service(self, service_key: str) -> None:
        """硬删除一个 OpenAPI 服务；openapi_tools 经外键 ON DELETE CASCADE 自动清除。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM openapi_services WHERE service_key = ?", (service_key,))
