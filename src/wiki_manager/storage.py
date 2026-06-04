"""SQLite storage for wiki-manager."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from wiki_manager.capabilities import CallLogStatus, McpServiceStatus, ToolType
from wiki_manager.domain import DocumentStatus, KbRole, Operation, SyncJobStatus, SyncStateStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS knowledge_base_members (
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  linux_user TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (kb_id, linux_user)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_user TEXT NOT NULL,
  current_version_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS document_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  original_filename TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  mime_type TEXT NOT NULL,
  archive_path TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (doc_id, version_no)
);
CREATE TABLE IF NOT EXISTS document_kbs (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  added_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  PRIMARY KEY (doc_id, kb_id)
);
CREATE TABLE IF NOT EXISTS backend_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  slug TEXT NOT NULL DEFAULT 'mock',
  backend_type TEXT NOT NULL DEFAULT 'mock',
  backend_kb_id TEXT,
  config_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kb_id, slug)
);
CREATE TABLE IF NOT EXISTS sync_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  backend_slug TEXT NOT NULL DEFAULT 'mock',
  operation TEXT NOT NULL,
  version_id INTEGER REFERENCES document_versions(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sync_states (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  backend_slug TEXT NOT NULL DEFAULT 'mock',
  backend_doc_id TEXT,
  status TEXT NOT NULL,
  backend_status TEXT,
  chunk_count INTEGER,
  progress REAL,
  backend_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (doc_id, kb_id, backend_slug)
);
CREATE TABLE IF NOT EXISTS mcp_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  endpoint_url TEXT NOT NULL,
  headers_json TEXT NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'enabled',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at TEXT,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS mcp_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL REFERENCES mcp_services(service_key) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  tool_type TEXT NOT NULL DEFAULT 'unconfigured',
  tags_json TEXT NOT NULL DEFAULT '[]',
  examples_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (service_key, tool_name)
);
CREATE TABLE IF NOT EXISTS project_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS profile_source_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, source_type, source_key, effect)
);
CREATE TABLE IF NOT EXISTS tool_call_logs (
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
);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_profile ON tool_call_logs(profile_key);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_source ON tool_call_logs(source_type, source_key);
"""


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self.migrate_phase2()

    def migrate_phase2(self) -> None:
        with self.connect() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(backend_targets)").fetchall()}
            if "backend_kb_id" not in existing:
                conn.execute("ALTER TABLE backend_targets ADD COLUMN backend_kb_id TEXT")

            existing = {row[1] for row in conn.execute("PRAGMA table_info(sync_states)").fetchall()}
            for col, col_type in [
                ("backend_status", "TEXT"),
                ("chunk_count", "INTEGER"),
                ("progress", "REAL"),
                ("backend_error", "TEXT"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE sync_states ADD COLUMN {col} {col_type}")

            self._migrate_tool_call_logs_nullable_profile(conn)

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
        with self.connect() as conn:
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
            service = _row_to_dict(row)
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
        with self.connect() as conn:
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
            service = _row_to_dict(row)
            if service is None:
                raise KeyError(f"mcp service not found: {service_key}")
            return service

    def get_mcp_service(self, service_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            return _row_to_dict(row)

    def list_mcp_services(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM mcp_services ORDER BY service_key").fetchall()
            return [dict(row) for row in rows]

    def update_mcp_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE mcp_services
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (_enum_value(status), service_key),
            )

    def mark_mcp_service_sync(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        with self.connect() as conn:
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
        with self.connect() as conn:
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
                    _enum_value(tool_type),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(examples, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = _row_to_dict(row)
            if tool is None:
                raise KeyError(f"mcp tool not found: {service_key}/{tool_name}")
            return tool

    def update_mcp_tool_type(
        self,
        service_key: str,
        tool_name: str,
        tool_type: ToolType | str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE mcp_tools
                SET tool_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                  AND tool_name = ?
                """,
                (_enum_value(tool_type), service_key, tool_name),
            )
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = _row_to_dict(row)
            if tool is None:
                raise KeyError(f"mcp tool not found: {service_key}/{tool_name}")
            return tool

    def list_mcp_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if service_key is None:
                rows = conn.execute("SELECT * FROM mcp_tools ORDER BY service_key, tool_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_tools WHERE service_key = ? ORDER BY tool_name",
                    (service_key,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_mcp_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            return _row_to_dict(row)

    def deactivate_missing_mcp_tools(self, service_key: str, active_tool_names: set[str]) -> None:
        with self.connect() as conn:
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

    def upsert_project_profile(
        self,
        *,
        profile_key: str,
        name: str,
        description: str = "",
        status: str = "active",
        created_by: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
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
            profile = _row_to_dict(row)
            if profile is None:
                raise KeyError(f"project profile not found: {profile_key}")
            return profile

    def get_project_profile(self, profile_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return _row_to_dict(row)

    def list_project_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
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
        with self.connect() as conn:
            conn.execute("DELETE FROM profile_source_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_source_rules (profile_key, source_type, source_key, effect)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        profile_key,
                        _enum_value(rule["source_type"]),
                        rule["source_key"],
                        _enum_value(rule["effect"]),
                    ),
                )

    def list_profile_source_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
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
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
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
                  duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    actor,
                    profile_key,
                    entrypoint,
                    _enum_value(source_type),
                    source_key,
                    tool_name,
                    json.dumps({} if request is None else request, ensure_ascii=False, default=str),
                    json.dumps({} if response is None else response, ensure_ascii=False, default=str),
                    _enum_value(status),
                    error_message,
                    duration_ms,
                ),
            )
            row = conn.execute("SELECT * FROM tool_call_logs WHERE log_id = ?", (log_id,)).fetchone()
            log = _row_to_dict(row)
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
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        for column, value in [
            ("entrypoint", entrypoint),
            ("source_type", _enum_value(source_type)),
            ("source_key", source_key),
            ("tool_name", tool_name),
            ("profile_key", profile_key),
            ("status", _enum_value(status)),
        ]:
            if value is not None:
                filters.append(f"{column} = ?")
                params.append(value)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self.connect() as conn:
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

    def get_tool_call_log(self, log_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tool_call_logs WHERE log_id = ?",
                (log_id,),
            ).fetchone()
            return _row_to_dict(row)

    def create_kb(self, slug: str, name: str, description: str, created_by: str) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_bases (slug, name, description, created_by) VALUES (?, ?, ?, ?)",
                (slug, name, description, created_by),
            )
            return self.get_kb_by_id(cursor.lastrowid, conn)

    def get_kb_by_id(self, kb_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        if conn is not None:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            if row is None:
                raise KeyError(f"kb not found: {kb_id}")
            return dict(row)

        with self.connect() as own_conn:
            return self.get_kb_by_id(kb_id, own_conn)

    def get_kb_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE slug = ?", (slug,)).fetchone()
            return _row_to_dict(row)

    def ensure_backend_target(self, kb_id: int, slug: str, backend_type: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO backend_targets (kb_id, slug, backend_type)
                VALUES (?, ?, ?)
                ON CONFLICT(kb_id, slug) DO NOTHING
                """,
                (kb_id, slug, backend_type),
            )

    def list_backend_targets(self, kb_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backend_targets WHERE kb_id = ? ORDER BY slug",
                (kb_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_backend_target_status(self, kb_id: int, slug: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE backend_targets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (status, kb_id, slug),
            )

    def update_backend_target_kb_id(self, kb_id: int, slug: str, backend_kb_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE backend_targets SET backend_kb_id = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (backend_kb_id, kb_id, slug),
            )

    def update_backend_target_config(self, kb_id: int, slug: str, config_updates: dict[str, Any]) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT config_json FROM backend_targets WHERE kb_id = ? AND slug = ?",
                (kb_id, slug),
            ).fetchone()
            existing = json.loads(row["config_json"]) if row and row["config_json"] else {}
            existing.update(config_updates)
            conn.execute(
                "UPDATE backend_targets SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (json.dumps(existing, ensure_ascii=False), kb_id, slug),
            )

    def list_sync_states_for_doc(self, doc_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_states WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_synced_docs_for_target(self, kb_id: int, backend_slug: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT s.doc_id
                FROM sync_states s
                JOIN document_kbs dk ON dk.doc_id = s.doc_id AND dk.kb_id = s.kb_id
                WHERE s.kb_id = ? AND s.backend_slug = ? AND s.status = ?
                """,
                (kb_id, backend_slug, SyncStateStatus.synced.value),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_kbs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_bases WHERE status = 'active' ORDER BY slug",
            ).fetchall()
            return [dict(row) for row in rows]

    def list_kbs_for_user(self, linux_user: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT kb.*
                FROM knowledge_bases kb
                JOIN knowledge_base_members member ON member.kb_id = kb.id
                WHERE member.linux_user = ? AND kb.status = 'active'
                ORDER BY kb.slug
                """,
                (linux_user,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_kbs_for_user_or_admin(self, linux_user: str, admins: set[str]) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if linux_user in admins:
                rows = conn.execute(
                    """
                    SELECT kb.*, member.role AS role
                    FROM knowledge_bases kb
                    LEFT JOIN knowledge_base_members member
                      ON member.kb_id = kb.id AND member.linux_user = ?
                    WHERE kb.status = 'active'
                    ORDER BY kb.slug
                    """,
                    (linux_user,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT kb.*, member.role AS role
                    FROM knowledge_bases kb
                    JOIN knowledge_base_members member ON member.kb_id = kb.id
                    WHERE member.linux_user = ? AND kb.status = 'active'
                    ORDER BY kb.slug
                    """,
                    (linux_user,),
                ).fetchall()
            return [dict(row) for row in rows]

    def grant_member(self, kb_id: int, linux_user: str, role: KbRole) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_base_members (kb_id, linux_user, role)
                VALUES (?, ?, ?)
                ON CONFLICT(kb_id, linux_user) DO UPDATE SET
                  role = excluded.role,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (kb_id, linux_user, role.value),
            )

    def get_member_role(self, kb_id: int, linux_user: str) -> KbRole | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT role FROM knowledge_base_members WHERE kb_id = ? AND linux_user = ?",
                (kb_id, linux_user),
            ).fetchone()
            if row is None:
                return None
            return KbRole(row["role"])

    def list_members(self, kb_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT linux_user, role, created_at, updated_at
                FROM knowledge_base_members
                WHERE kb_id = ?
                ORDER BY linux_user
                """,
                (kb_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_document_slugs(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT slug FROM documents").fetchall()
            return {row["slug"] for row in rows}

    def create_document(self, slug: str, title: str, owner_user: str) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO documents (slug, title, owner_user) VALUES (?, ?, ?)",
                (slug, title, owner_user),
            )
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
            document = _row_to_dict(row)
            if document is None:
                raise KeyError(f"document not found: {cursor.lastrowid}")
            return document

    def get_document_by_slug(self, slug: str, include_deleted: bool = False) -> dict[str, Any] | None:
        with self.connect() as conn:
            if include_deleted:
                row = conn.execute("SELECT * FROM documents WHERE slug = ?", (slug,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM documents WHERE slug = ? AND status != ?",
                    (slug, DocumentStatus.deleted.value),
                ).fetchone()
            return _row_to_dict(row)

    def attach_document_to_kb(self, doc_id: int, kb_id: int, added_by: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO document_kbs (doc_id, kb_id, added_by)
                VALUES (?, ?, ?)
                ON CONFLICT(doc_id, kb_id) DO UPDATE SET
                  status = 'active',
                  added_by = excluded.added_by,
                  deleted_at = NULL
                """,
                (doc_id, kb_id, added_by),
            )

    def get_document_kbs(self, doc_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT kb.*, dk.status AS document_kb_status
                FROM document_kbs dk
                JOIN knowledge_bases kb ON kb.id = dk.kb_id
                WHERE dk.doc_id = ?
                ORDER BY kb.slug
                """,
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_versions(self, doc_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_versions WHERE doc_id = ? ORDER BY version_no",
                (doc_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def next_version_no(self, doc_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no FROM document_versions WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            return int(row["version_no"])

    def set_current_version(self, doc_id: int, version_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE documents SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (version_id, doc_id),
            )

    def create_document_version(
        self,
        doc_id: int,
        original_filename: str,
        content_hash: str,
        file_size: int,
        mime_type: str,
        archive_path: str,
        created_by: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no FROM document_versions WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            version_no = row["version_no"]
            cursor = conn.execute(
                """
                INSERT INTO document_versions (
                  doc_id, version_no, original_filename, content_hash, file_size, mime_type, archive_path, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, version_no, original_filename, content_hash, file_size, mime_type, archive_path, created_by),
            )
            conn.execute(
                "UPDATE documents SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (cursor.lastrowid, doc_id),
            )
            version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            result = _row_to_dict(version)
            if result is None:
                raise KeyError(f"document version not found: {cursor.lastrowid}")
            return result

    def create_sync_job(
        self,
        doc_id: int,
        kb_id: int,
        operation: Operation,
        version_id: int | None,
        backend_slug: str = "mock",
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_jobs (doc_id, kb_id, backend_slug, operation, version_id, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, kb_id, backend_slug, operation.value, version_id, SyncJobStatus.pending.value),
            )
            row = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            job = _row_to_dict(row)
            if job is None:
                raise KeyError(f"sync job not found: {cursor.lastrowid}")
            return job

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_jobs WHERE status = ? ORDER BY created_at, id",
                (SyncJobStatus.pending.value,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_runnable_jobs(self, actor: str | None, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT
                  job.id,
                  job.doc_id,
                  job.kb_id,
                  job.backend_slug,
                  job.operation,
                  d.slug AS doc_slug,
                  kb.slug AS kb_slug,
                  v.version_no AS version_no,
                  v.archive_path AS archive_path
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                LEFT JOIN document_versions v ON v.id = job.version_id
                LEFT JOIN knowledge_base_members member
                  ON member.kb_id = kb.id AND member.linux_user = ?
                WHERE job.status IN (?, ?)
                  AND (
                    ? IS NULL
                    OR d.owner_user = ?
                    OR member.role IN (?, ?)
                  )
                  AND (job.backend_slug = ? OR ? IS NULL)
                ORDER BY job.created_at, job.id
                """,
                (
                    actor,
                    SyncJobStatus.pending.value,
                    SyncJobStatus.failed.value,
                    actor,
                    actor,
                    KbRole.contributor.value,
                    KbRole.admin.value,
                    backend_slug,
                    backend_slug,
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_all_jobs(self, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  job.*,
                  d.slug AS doc_slug,
                  d.title AS doc_title,
                  kb.slug AS kb_slug,
                  kb.name AS kb_name,
                  v.version_no AS version_no
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                LEFT JOIN document_versions v ON v.id = job.version_id
                WHERE (job.backend_slug = ? OR ? IS NULL)
                ORDER BY job.created_at, job.id
                """,
                (backend_slug, backend_slug),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_job_status(self, job_id: int, status: SyncJobStatus, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_jobs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status.value, error, job_id),
            )

    def upsert_sync_state(
        self,
        doc_id: int,
        kb_id: int,
        backend_slug: str,
        backend_doc_id: str | None,
        status: SyncStateStatus,
        backend_status: str | None = None,
        chunk_count: int | None = None,
        progress: float | None = None,
        backend_error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_states (doc_id, kb_id, backend_slug, backend_doc_id, status, backend_status, chunk_count, progress, backend_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id, kb_id, backend_slug) DO UPDATE SET
                  backend_doc_id = excluded.backend_doc_id,
                  status = excluded.status,
                  backend_status = excluded.backend_status,
                  chunk_count = excluded.chunk_count,
                  progress = excluded.progress,
                  backend_error = excluded.backend_error,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (doc_id, kb_id, backend_slug, backend_doc_id, status.value, backend_status, chunk_count, progress, backend_error),
            )

    def get_sync_state(self, doc_id: int, kb_id: int, backend_slug: str = "mock") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sync_states
                WHERE doc_id = ? AND kb_id = ? AND backend_slug = ?
                """,
                (doc_id, kb_id, backend_slug),
            ).fetchone()
            return _row_to_dict(row)

    def list_docs_for_kb(self, kb_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  d.id,
                  d.slug,
                  d.title,
                  d.owner_user,
                  d.status,
                  v.version_no AS current_version_no,
                  COALESCE(s.status, ?) AS sync_status
                FROM document_kbs dk
                JOIN documents d ON d.id = dk.doc_id
                LEFT JOIN document_versions v ON v.id = d.current_version_id
                LEFT JOIN sync_states s ON s.doc_id = d.id AND s.kb_id = dk.kb_id AND s.backend_slug = 'mock'
                WHERE dk.kb_id = ?
                  AND dk.status = 'active'
                  AND d.status != ?
                ORDER BY d.slug
                """,
                (SyncStateStatus.not_synced.value, kb_id, DocumentStatus.deleted.value),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_jobs_for_user(self, linux_user: str, backend_slug: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  job.*,
                  d.slug AS doc_slug,
                  d.title AS doc_title,
                  kb.slug AS kb_slug,
                  kb.name AS kb_name,
                  v.version_no AS version_no
                FROM sync_jobs job
                JOIN documents d ON d.id = job.doc_id
                JOIN knowledge_bases kb ON kb.id = job.kb_id
                JOIN knowledge_base_members member ON member.kb_id = kb.id
                LEFT JOIN document_versions v ON v.id = job.version_id
                WHERE (member.linux_user = ? OR d.owner_user = ?)
                  AND (job.backend_slug = ? OR ? IS NULL)
                GROUP BY job.id
                ORDER BY job.created_at, job.id
                """,
                (linux_user, linux_user, backend_slug, backend_slug),
            ).fetchall()
            return [dict(row) for row in rows]

    def soft_delete_document(self, doc_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET status = ?, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (DocumentStatus.deleted.value, doc_id),
            )
            conn.execute(
                """
                UPDATE document_kbs
                SET status = ?, deleted_at = CURRENT_TIMESTAMP
                WHERE doc_id = ?
                """,
                (DocumentStatus.deleted.value, doc_id),
            )

    def purge_document(self, doc_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT archive_path FROM document_versions WHERE doc_id = ? ORDER BY version_no",
                (doc_id,),
            ).fetchall()
            archive_paths = list(dict.fromkeys(row["archive_path"] for row in rows))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            if not archive_paths:
                return []
            placeholders = ", ".join("?" for _ in archive_paths)
            remaining_rows = conn.execute(
                f"SELECT DISTINCT archive_path FROM document_versions WHERE archive_path IN ({placeholders})",
                archive_paths,
            ).fetchall()
            remaining_paths = {row["archive_path"] for row in remaining_rows}
            return [archive_path for archive_path in archive_paths if archive_path not in remaining_paths]
