"""SQLite codegraph repository."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.storage.types import row_to_dict


class CodeGraphRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def upsert_code_repository(
        self,
        *,
        repo_key: str,
        name: str,
        git_url: str,
        branch: str,
        auth_ref: str,
        description: str,
        tags: list[str],
        category_key: str,
        sync_interval_minutes: int,
        status: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO code_repositories (
                  repo_key,
                  name,
                  git_url,
                  branch,
                  auth_ref,
                  description,
                  tags_json,
                  category_key,
                  sync_interval_minutes,
                  status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                  name = excluded.name,
                  git_url = excluded.git_url,
                  branch = excluded.branch,
                  auth_ref = excluded.auth_ref,
                  description = excluded.description,
                  tags_json = excluded.tags_json,
                  category_key = excluded.category_key,
                  sync_interval_minutes = excluded.sync_interval_minutes,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    repo_key,
                    name,
                    git_url,
                    branch,
                    auth_ref,
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    category_key,
                    sync_interval_minutes,
                    status,
                ),
            )
            row = conn.execute("SELECT * FROM code_repositories WHERE repo_key = ?", (repo_key,)).fetchone()
            repository = row_to_dict(row)
            if repository is None:
                raise KeyError(f"code repository not found: {repo_key}")
            return repository

    def list_code_repositories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM code_repositories ORDER BY repo_key").fetchall()
            return [dict(row) for row in rows]

    def get_code_repository(self, repo_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM code_repositories WHERE repo_key = ?", (repo_key,)).fetchone()
            return row_to_dict(row)

    def mark_code_repository_sync(
        self,
        repo_key: str,
        *,
        local_path: str,
        last_commit: str | None,
        success: bool,
        error: str | None,
    ) -> None:
        with self._connect() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE code_repositories
                    SET local_path = ?,
                        last_commit = ?,
                        last_synced_at = CURRENT_TIMESTAMP,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE repo_key = ?
                    """,
                    (local_path, last_commit, repo_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE code_repositories
                    SET local_path = ?,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE repo_key = ?
                    """,
                    (local_path, error, repo_key),
                )

    def replace_codegraph_index(self, repo_key: str, items: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM codegraph_index_items WHERE repo_key = ?", (repo_key,))
            for item in items:
                conn.execute(
                    """
                    INSERT INTO codegraph_index_items (
                      repo_key,
                      item_type,
                      path,
                      symbol,
                      language,
                      line_start,
                      line_end,
                      content
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo_key,
                        item["item_type"],
                        item["path"],
                        item.get("symbol"),
                        item.get("language"),
                        item.get("line_start"),
                        item.get("line_end"),
                        item.get("content", ""),
                    ),
                )

    def create_codegraph_sync_run(self, repo_key: str, *, status: str, stage: str) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO codegraph_sync_runs (repo_key, status, stage)
                VALUES (?, ?, ?)
                """,
                (repo_key, status, stage),
            )
            row = conn.execute("SELECT * FROM codegraph_sync_runs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            run = row_to_dict(row)
            if run is None:
                raise KeyError(f"codegraph sync run not found: {cursor.lastrowid}")
            return run

    def update_codegraph_sync_run(self, run_id: int, *, stage: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE codegraph_sync_runs SET stage = ? WHERE id = ?",
                (stage, run_id),
            )

    def finish_codegraph_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        stage: str,
        error: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE codegraph_sync_runs
                SET status = ?,
                    stage = ?,
                    error = ?,
                    duration_ms = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, stage, error, duration_ms, run_id),
            )
            row = conn.execute("SELECT * FROM codegraph_sync_runs WHERE id = ?", (run_id,)).fetchone()
            run = row_to_dict(row)
            if run is None:
                raise KeyError(f"codegraph sync run not found: {run_id}")
            return run

    def search_codegraph_index(
        self,
        repo_key: str,
        *,
        query: str,
        item_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        filters = ["repo_key = ?"]
        params: list[Any] = [repo_key]
        if item_type is not None:
            filters.append("item_type = ?")
            params.append(item_type)
        if query:
            like = f"%{query.lower()}%"
            filters.append("(LOWER(path) LIKE ? OR LOWER(COALESCE(symbol, '')) LIKE ? OR LOWER(content) LIKE ?)")
            params.extend([like, like, like])
        params.append(limit)
        where_clause = " AND ".join(filters)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM codegraph_index_items
                WHERE {where_clause}
                ORDER BY path, item_type, COALESCE(line_start, 0), id
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._add_codegraph_snippet(dict(row), query) for row in rows]

    def get_codegraph_file(self, repo_key: str, path: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM codegraph_index_items
                WHERE repo_key = ?
                  AND path = ?
                  AND item_type = 'file'
                ORDER BY id
                LIMIT 1
                """,
                (repo_key, path),
            ).fetchone()
            return row_to_dict(row)

    def count_codegraph_index_items(self, repo_key: str, item_type: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM codegraph_index_items
                WHERE repo_key = ?
                  AND item_type = ?
                """,
                (repo_key, item_type),
            ).fetchone()
            return int(row["count"])

    def _add_codegraph_snippet(self, row: dict[str, Any], query: str) -> dict[str, Any]:
        content = row.get("content") or ""
        if not content:
            row["snippet"] = ""
            return row
        query_index = content.lower().find(query.lower()) if query else -1
        if query_index < 0:
            row["snippet"] = content[:240]
            return row
        start = max(0, query_index - 80)
        end = min(len(content), query_index + len(query) + 160)
        row["snippet"] = content[start:end]
        return row

    # -- Categories --

    def upsert_category(
        self,
        *,
        category_key: str,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO code_repo_categories (category_key, name, description)
                VALUES (?, ?, ?)
                ON CONFLICT(category_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (category_key, name, description),
            )
            row = conn.execute("SELECT * FROM code_repo_categories WHERE category_key = ?", (category_key,)).fetchone()
            cat = row_to_dict(row)
            if cat is None:
                raise KeyError(f"category not found: {category_key}")
            return cat

    def list_categories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM code_repo_categories ORDER BY name").fetchall()
            return [dict(row) for row in rows]

    def delete_category(self, category_key: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE code_repositories SET category_key = '' WHERE category_key = ?", (category_key,))
            conn.execute("DELETE FROM code_repo_categories WHERE category_key = ?", (category_key,))

    # -- Sync Config --

    def get_sync_config(self) -> dict[str, Any]:
        defaults = {"code_sync_enabled": False, "code_sync_cron": "*/30 * * * *", "ua_git_url": ""}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_sync_config WHERE id = 1").fetchone()
            if row is None:
                return defaults
            result = row_to_dict(row)
            result["code_sync_enabled"] = bool(result.get("code_sync_enabled"))
            result.setdefault("ua_git_url", "")
            return result

    def save_sync_config(self, *, code_sync_enabled: bool, code_sync_cron: str, ua_git_url: str = "") -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_sync_config (id, code_sync_enabled, code_sync_cron, ua_git_url)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  code_sync_enabled = excluded.code_sync_enabled,
                  code_sync_cron = excluded.code_sync_cron,
                  ua_git_url = excluded.ua_git_url,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (int(code_sync_enabled), code_sync_cron, ua_git_url),
            )
            return self.get_sync_config()
