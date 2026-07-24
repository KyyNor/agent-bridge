"""SQLite codegraph repository."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.storage.types import row_to_dict


SYNC_CONFIG_COLUMNS = (
    "code_sync_cron, ua_git_url, ua_plugin_update_cron, claude_mem_git_url, "
    "claude_mem_plugin_update_cron, understand_cron, doc_sync_cron, "
    "workflow_start_time, workflow_stop_time, workflow_max_runs, "
    "workflow_max_concurrent_runs, workflow_max_concurrent_runs_per_workflow, "
    "workflow_max_runtime_minutes, workflow_task_rerun_days, log_retention_days, "
    "mcp_timeout_seconds, understand_timeout_minutes"
)

SYNC_CONFIG_DEFAULTS: dict[str, Any] = {
    "code_sync_cron": "0 * * * *",
    "ua_git_url": "",
    "ua_plugin_update_cron": "0 3 * * 0",
    "claude_mem_git_url": "",
    "claude_mem_plugin_update_cron": "30 3 * * 0",
    "understand_cron": "0 2 * * *",
    "doc_sync_cron": "*/30 * * * *",
    "workflow_start_time": "22:00",
    "workflow_stop_time": "07:00",
    "workflow_max_runs": 0,
    "workflow_max_concurrent_runs": 4,
    "workflow_max_concurrent_runs_per_workflow": 2,
    "workflow_max_runtime_minutes": 30,
    "workflow_task_rerun_days": 30,
    "log_retention_days": 180,
    "mcp_timeout_seconds": DEFAULT_MCP_TIMEOUT_SECONDS,
    "understand_timeout_minutes": 120,
}


def resolve_sync_config(row: sqlite3.Row | None) -> dict[str, Any]:
    """将 ``knowledge_sync_config`` 的单行查询结果解析为配置字典。

    单一权威解析点：``get_sync_config`` 的主路径与在同一事务内读取单个
    配置项的调用方（如工作流任务重跑窗口）都经过此处，保证默认值与字段
    映射只有一份实现。``row`` 为 ``None`` 时返回完整默认配置。
    """
    defaults = SYNC_CONFIG_DEFAULTS
    if row is None:
        return dict(defaults)
    return {
        "code_sync_cron": row[0] or defaults["code_sync_cron"],
        "ua_git_url": row[1] or "",
        "ua_plugin_update_cron": row[2] or defaults["ua_plugin_update_cron"],
        "claude_mem_git_url": row[3] or "",
        "claude_mem_plugin_update_cron": row[4] or defaults["claude_mem_plugin_update_cron"],
        "understand_cron": row[5] or defaults["understand_cron"],
        "doc_sync_cron": row[6] if len(row) > 6 and row[6] else defaults["doc_sync_cron"],
        "workflow_start_time": row[7] if len(row) > 7 and row[7] else defaults["workflow_start_time"],
        "workflow_stop_time": row[8] if len(row) > 8 and row[8] else defaults["workflow_stop_time"],
        "workflow_max_runs": int(row[9]) if len(row) > 9 and row[9] is not None else 0,
        "workflow_max_concurrent_runs": int(row[10]) if len(row) > 10 and row[10] is not None else 4,
        "workflow_max_concurrent_runs_per_workflow": int(row[11]) if len(row) > 11 and row[11] is not None else 2,
        "workflow_max_runtime_minutes": int(row[12]) if len(row) > 12 and row[12] is not None else 30,
        "workflow_task_rerun_days": int(row[13]) if len(row) > 13 and row[13] is not None else 30,
        "log_retention_days": int(row[14]) if len(row) > 14 and row[14] is not None else 180,
        "mcp_timeout_seconds": int(row[15]) if len(row) > 15 and row[15] is not None else DEFAULT_MCP_TIMEOUT_SECONDS,
        "understand_timeout_minutes": int(row[16]) if len(row) > 16 and row[16] is not None else 120,
    }


def fetch_sync_config(conn: sqlite3.Connection) -> dict[str, Any]:
    """在指定连接上读取全局同步配置，复用 :func:`resolve_sync_config`。"""
    row = conn.execute(
        f"SELECT {SYNC_CONFIG_COLUMNS} FROM knowledge_sync_config WHERE id = 1"
    ).fetchone()
    return resolve_sync_config(row)


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
        auto_understand: bool,
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
                  auto_understand,
                  status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                  name = excluded.name,
                  git_url = excluded.git_url,
                  branch = excluded.branch,
                  auth_ref = excluded.auth_ref,
                  description = excluded.description,
                  tags_json = excluded.tags_json,
                  category_key = excluded.category_key,
                  sync_interval_minutes = excluded.sync_interval_minutes,
                  auto_understand = excluded.auto_understand,
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
                    int(auto_understand),
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

    def interrupt_running_codegraph_sync_runs(self, *, error: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE codegraph_sync_runs
                SET status = 'interrupted',
                    stage = 'interrupted',
                    error = ?,
                    duration_ms = CAST((julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400000 AS INTEGER),
                    finished_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """,
                (error,),
            )
            return cursor.rowcount

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

    def delete_repository(self, repo_key: str) -> None:
        """硬删除一个代码仓库。

        依赖外键 ON DELETE CASCADE 清除 codegraph_sync_runs。
        删除前应由 service 层清理本地镜像目录、停止 Understand Anything 进程，
        并清理能力平面里引用该仓库的 resource 规则（无外键，需手动删）。
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM code_repositories WHERE repo_key = ?", (repo_key,))

    # -- Sync Config --

    def get_sync_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            return fetch_sync_config(conn)

    def save_sync_config(
        self,
        *,
        code_sync_cron: str,
        ua_git_url: str = "",
        ua_plugin_update_cron: str = "0 3 * * 0",
        claude_mem_git_url: str = "",
        claude_mem_plugin_update_cron: str = "30 3 * * 0",
        understand_cron: str = "0 2 * * *",
        doc_sync_cron: str = "*/30 * * * *",
        workflow_start_time: str = "22:00",
        workflow_stop_time: str = "07:00",
        workflow_max_runs: int = 0,
        workflow_max_concurrent_runs: int = 4,
        workflow_max_concurrent_runs_per_workflow: int = 2,
        workflow_max_runtime_minutes: int = 30,
        workflow_task_rerun_days: int = 30,
        log_retention_days: int = 180,
        mcp_timeout_seconds: int = DEFAULT_MCP_TIMEOUT_SECONDS,
        understand_timeout_minutes: int = 120,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_sync_config (id, code_sync_cron, ua_git_url, ua_plugin_update_cron, claude_mem_git_url, claude_mem_plugin_update_cron, understand_cron, doc_sync_cron, workflow_start_time, workflow_stop_time, workflow_max_runs, workflow_max_concurrent_runs, workflow_max_concurrent_runs_per_workflow, workflow_max_runtime_minutes, workflow_task_rerun_days, log_retention_days, mcp_timeout_seconds, understand_timeout_minutes)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  code_sync_cron = excluded.code_sync_cron,
                  ua_git_url = excluded.ua_git_url,
                  ua_plugin_update_cron = excluded.ua_plugin_update_cron,
                  claude_mem_git_url = excluded.claude_mem_git_url,
                  claude_mem_plugin_update_cron = excluded.claude_mem_plugin_update_cron,
                  understand_cron = excluded.understand_cron,
                  doc_sync_cron = excluded.doc_sync_cron,
                  workflow_start_time = excluded.workflow_start_time,
                  workflow_stop_time = excluded.workflow_stop_time,
                  workflow_max_runs = excluded.workflow_max_runs,
                  workflow_max_concurrent_runs = excluded.workflow_max_concurrent_runs,
                  workflow_max_concurrent_runs_per_workflow = excluded.workflow_max_concurrent_runs_per_workflow,
                  workflow_max_runtime_minutes = excluded.workflow_max_runtime_minutes,
                  workflow_task_rerun_days = excluded.workflow_task_rerun_days,
                  log_retention_days = excluded.log_retention_days,
                  mcp_timeout_seconds = excluded.mcp_timeout_seconds,
                  understand_timeout_minutes = excluded.understand_timeout_minutes,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (code_sync_cron, ua_git_url, ua_plugin_update_cron, claude_mem_git_url, claude_mem_plugin_update_cron, understand_cron, doc_sync_cron, workflow_start_time, workflow_stop_time, workflow_max_runs, workflow_max_concurrent_runs, workflow_max_concurrent_runs_per_workflow, workflow_max_runtime_minutes, workflow_task_rerun_days, log_retention_days, mcp_timeout_seconds, understand_timeout_minutes),
            )
            return {
                "code_sync_cron": code_sync_cron,
                "ua_git_url": ua_git_url,
                "ua_plugin_update_cron": ua_plugin_update_cron,
                "claude_mem_git_url": claude_mem_git_url,
                "claude_mem_plugin_update_cron": claude_mem_plugin_update_cron,
                "understand_cron": understand_cron,
                "doc_sync_cron": doc_sync_cron,
                "workflow_start_time": workflow_start_time,
                "workflow_stop_time": workflow_stop_time,
                "workflow_max_runs": workflow_max_runs,
                "workflow_max_concurrent_runs": workflow_max_concurrent_runs,
                "workflow_max_concurrent_runs_per_workflow": workflow_max_concurrent_runs_per_workflow,
                "workflow_max_runtime_minutes": workflow_max_runtime_minutes,
                "workflow_task_rerun_days": workflow_task_rerun_days,
                "log_retention_days": log_retention_days,
                "mcp_timeout_seconds": mcp_timeout_seconds,
                "understand_timeout_minutes": understand_timeout_minutes,
            }
