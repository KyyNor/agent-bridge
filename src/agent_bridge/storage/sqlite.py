"""SQLite storage facade for Agent Bridge."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from agent_bridge.storage.schema import CODEGRAPH_SCHEMA, SCHEMA, WORKFLOW_SCHEMA


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._runtime_log_retention_days = 180
        self._last_runtime_log_prune_monotonic: float | None = None
        self._runtime_log_prune_interval_seconds = 3600.0

        from agent_bridge.storage.repositories.agent_runs import AgentRunsRepository
        from agent_bridge.storage.repositories.capabilities import CapabilitiesRepository
        from agent_bridge.storage.repositories.codegraph import CodeGraphRepository
        from agent_bridge.storage.repositories.governance import GovernanceRepository
        from agent_bridge.storage.repositories.knowledge import KnowledgeRepository
        from agent_bridge.storage.repositories.memory import MemoryRepository
        from agent_bridge.storage.repositories.scripts import ScriptsRepository
        from agent_bridge.storage.repositories.workflows import WorkflowsRepository

        self.knowledge = KnowledgeRepository(db_path, self.connect)
        self.capabilities = CapabilitiesRepository(db_path, self.connect)
        self.governance = GovernanceRepository(db_path, self.connect)
        self.memory = MemoryRepository(db_path, self.connect)
        self.codegraph = CodeGraphRepository(db_path, self.connect)
        self.workflows = WorkflowsRepository(db_path, self.connect)
        self.scripts = ScriptsRepository(db_path, self.connect)
        self.agent_runs = AgentRunsRepository(db_path, self.connect, prune_callback=self.maybe_prune_runtime_logs)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
            conn.executescript(CODEGRAPH_SCHEMA)
            conn.executescript(WORKFLOW_SCHEMA)
            self._drop_column(conn, "workflow_definitions", "manifest_json")
            self._ensure_columns(
                conn,
                "workflow_tasks",
                {
                    "task_version": "TEXT NOT NULL DEFAULT ''",
                    "type": "TEXT NOT NULL DEFAULT ''",
                },
            )
            self._ensure_columns(
                conn,
                "workflow_artifacts",
                {
                    "task_version": "TEXT NOT NULL DEFAULT ''",
                    "is_current": "INTEGER NOT NULL DEFAULT 1",
                },
            )
            self._rebuild_workflow_tasks_if_needed(conn)
            self._rebuild_workflow_artifacts_if_needed(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_current
                ON workflow_artifacts(workflow_key, task_key, is_current, updated_at DESC)
                """
            )
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

            self._ensure_columns(
                conn,
                "knowledge_bases",
                {
                    "default_backend_slug": "TEXT",
                    "default_agent_id": "TEXT",
                },
            )

            self.governance._migrate_tool_call_logs_nullable_profile(conn)
            self._ensure_columns(
                conn,
                "tool_call_logs",
                {
                    "failure_stage": "TEXT",
                    "failure_owner": "TEXT",
                    "error_type": "TEXT",
                    "resource_type": "TEXT",
                    "resource_key": "TEXT",
                    "request_summary_json": "TEXT NOT NULL DEFAULT '{}'",
                    "response_summary_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            self._ensure_columns(
                conn,
                "code_repositories",
                {
                    "category_key": "TEXT NOT NULL DEFAULT ''",
                    "sync_interval_minutes": "INTEGER NOT NULL DEFAULT 60",
                    "auto_understand": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            self._ensure_columns(
                conn,
                "knowledge_sync_config",
                {
                    "ua_git_url": "TEXT NOT NULL DEFAULT ''",
                    "ua_plugin_update_cron": "TEXT NOT NULL DEFAULT '0 3 * * 0'",
                    "claude_mem_git_url": "TEXT NOT NULL DEFAULT ''",
                    "claude_mem_plugin_update_cron": "TEXT NOT NULL DEFAULT '30 3 * * 0'",
                },
            )
            self._ensure_columns(
                conn,
                "knowledge_sync_config",
                {
                    "code_sync_cron": "TEXT NOT NULL DEFAULT '*/30 * * * *'",
                    "understand_cron": "TEXT NOT NULL DEFAULT '0 2 * * *'",
                    "doc_sync_cron": "TEXT NOT NULL DEFAULT '*/30 * * * *'",
                    "workflow_start_time": "TEXT NOT NULL DEFAULT '22:00'",
                    "workflow_stop_time": "TEXT NOT NULL DEFAULT '07:00'",
                    "workflow_max_runs": "INTEGER NOT NULL DEFAULT 0",
                    "workflow_max_runtime_minutes": "INTEGER NOT NULL DEFAULT 30",
                    "workflow_task_rerun_days": "INTEGER NOT NULL DEFAULT 30",
                    "log_retention_days": "INTEGER NOT NULL DEFAULT 180",
                    "mcp_timeout_seconds": "INTEGER NOT NULL DEFAULT 150",
                    "understand_timeout_minutes": "INTEGER NOT NULL DEFAULT 120",
                },
            )
            # Workflow scheduling moved from a single global cron to a daily
            # window (start/stop). Drop the legacy per-workflow schedule column
            # and the superseded workflow_cron config column on existing DBs.
            self._drop_column(conn, "knowledge_sync_config", "workflow_cron")
            self._drop_column(conn, "workflow_definitions", "schedule_json")
            self._drop_column(conn, "workflow_definitions", "manifest_json")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_doc_cache (
                  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
                  manual_notes TEXT NOT NULL DEFAULT '',
                  auto_summary_json TEXT NOT NULL DEFAULT '{}',
                  auto_summary_hash TEXT NOT NULL DEFAULT '',
                  rendered_hash TEXT NOT NULL DEFAULT '',
                  last_rendered_markdown TEXT NOT NULL DEFAULT '',
                  last_written_at TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_blocks (
                  block_key TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'active',
                  data_dir TEXT NOT NULL,
                  worker_base_url TEXT,
                  last_health_json TEXT NOT NULL DEFAULT '{}',
                  created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_memory_bindings (
                  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
                  block_key TEXT REFERENCES memory_blocks(block_key) ON DELETE SET NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_blocks_status ON memory_blocks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_memory_bindings_block ON profile_memory_bindings(block_key)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_call_logs_failure "
                "ON tool_call_logs(failure_owner, failure_stage, error_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_call_logs_resource "
                "ON tool_call_logs(resource_type, resource_key)"
            )
            conn.executescript(CODEGRAPH_SCHEMA)
            conn.executescript(WORKFLOW_SCHEMA)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_prompts (
                  skill_name TEXT PRIMARY KEY,
                  prompt TEXT NOT NULL,
                  updated_by TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scripts (
                  script_key TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  language TEXT NOT NULL DEFAULT 'python',
                  code TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'active',
                  owner_type TEXT NOT NULL DEFAULT 'system',
                  owner_key TEXT NOT NULL DEFAULT '',
                  content_hash TEXT NOT NULL DEFAULT '',
                  created_by TEXT NOT NULL,
                  updated_by TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_owner ON scripts(owner_type, owner_key)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS script_runs (
                  run_id TEXT PRIMARY KEY,
                  script_key TEXT NOT NULL REFERENCES scripts(script_key) ON DELETE CASCADE,
                  run_type TEXT NOT NULL,
                  params_json TEXT NOT NULL DEFAULT '{}',
                  result_json TEXT NOT NULL DEFAULT '{}',
                  stdout TEXT NOT NULL DEFAULT '',
                  stderr TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL,
                  exit_code INTEGER,
                  error_message TEXT,
                  duration_ms INTEGER NOT NULL DEFAULT 0,
                  created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_script_runs_script ON script_runs(script_key, created_at DESC)")
            self._ensure_columns(conn, "workflow_tasks", {"type": "TEXT NOT NULL DEFAULT ''"})

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _drop_column(self, conn: sqlite3.Connection, table: str, column: str) -> None:
        """Drop a column if present. Uses native DROP COLUMN (SQLite >= 3.35);
        on older engines the column is left in place (harmless, just unused)."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            return
        if sqlite3.sqlite_version_info >= (3, 35, 0):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    def _has_unique_index(self, conn: sqlite3.Connection, table: str, columns: list[str]) -> bool:
        for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
            if not index["unique"]:
                continue
            actual = [
                row["name"]
                for row in conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
            ]
            if actual == columns:
                return True
        return False

    def _rebuild_workflow_tasks_if_needed(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(workflow_tasks)").fetchall()}
        if (
            "set_at" in existing
            and self._has_unique_index(conn, "workflow_tasks", ["workflow_key", "task_key", "task_version"])
        ):
            return
        conn.execute("DROP TABLE IF EXISTS workflow_tasks_new")
        conn.execute(
            """
            CREATE TABLE workflow_tasks_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
              task_key TEXT NOT NULL,
              task_version TEXT NOT NULL DEFAULT '',
              type TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending',
              lease_run_id TEXT,
              lease_expires_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              set_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              completed_at TEXT,
              UNIQUE (workflow_key, task_key, task_version)
            )
            """
        )
        set_at_expr = "COALESCE(set_at, created_at)" if "set_at" in existing else "created_at"
        conn.execute(
            """
            INSERT INTO workflow_tasks_new (
              id, workflow_key, task_key, task_version, type, payload_json, status,
              lease_run_id, lease_expires_at, attempt_count, last_error, set_at,
              created_at, updated_at, completed_at
            )
            SELECT
              id, workflow_key, task_key, task_version, type, payload_json, status,
              lease_run_id, lease_expires_at, attempt_count, last_error,
              {set_at_expr},
              created_at, updated_at, completed_at
            FROM workflow_tasks
            """.format(set_at_expr=set_at_expr)
        )
        conn.execute("DROP TABLE workflow_tasks")
        conn.execute("ALTER TABLE workflow_tasks_new RENAME TO workflow_tasks")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pick
            ON workflow_tasks(workflow_key, status, lease_expires_at, id)
            """
        )

    def _rebuild_workflow_artifacts_if_needed(self, conn: sqlite3.Connection) -> None:
        if self._has_unique_index(conn, "workflow_artifacts", ["workflow_key", "task_key", "task_version", "run_id", "path"]):
            return
        conn.execute("DROP TABLE IF EXISTS workflow_artifacts_new")
        conn.execute(
            """
            CREATE TABLE workflow_artifacts_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              artifact_id TEXT NOT NULL UNIQUE,
              workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
              profile_key TEXT NOT NULL,
              run_id TEXT NOT NULL,
              task_key TEXT,
              task_version TEXT NOT NULL DEFAULT '',
              is_current INTEGER NOT NULL DEFAULT 1,
              title TEXT NOT NULL,
              path TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              format TEXT NOT NULL DEFAULT 'markdown',
              summary TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL DEFAULT '',
              content_hash TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE (workflow_key, task_key, task_version, run_id, path)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO workflow_artifacts_new (
              id, artifact_id, workflow_key, profile_key, run_id, task_key,
              task_version, is_current, title, path, tags_json, format, summary,
              content, content_hash, metadata_json, created_at, updated_at
            )
            SELECT
              id, artifact_id, workflow_key, profile_key, run_id, task_key,
              task_version, is_current, title, path, tags_json, format, summary,
              content, content_hash, metadata_json, created_at, updated_at
            FROM workflow_artifacts
            """
        )
        conn.execute("DROP TABLE workflow_artifacts")
        conn.execute("ALTER TABLE workflow_artifacts_new RENAME TO workflow_artifacts")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_profile ON workflow_artifacts(profile_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_path ON workflow_artifacts(path)")

    def _migrate_tool_call_logs_nullable_profile(self, conn: sqlite3.Connection) -> None:
        return self.governance._migrate_tool_call_logs_nullable_profile(conn=conn)

    def get_skill_prompt_override(self, skill_name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM skill_prompts WHERE skill_name = ?", (skill_name,)).fetchone()
            return dict(row) if row is not None else None

    def list_skill_prompt_overrides(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM skill_prompts ORDER BY skill_name").fetchall()
            return [dict(row) for row in rows]

    def upsert_skill_prompt_override(self, *, skill_name: str, prompt: str, updated_by: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_prompts (skill_name, prompt, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                  prompt = excluded.prompt,
                  updated_by = excluded.updated_by,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (skill_name, prompt, updated_by),
            )
            row = conn.execute("SELECT * FROM skill_prompts WHERE skill_name = ?", (skill_name,)).fetchone()
            if row is None:
                raise KeyError(f"skill not found: {skill_name}")
            return dict(row)

    def delete_skill_prompt_override(self, skill_name: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM skill_prompts WHERE skill_name = ?", (skill_name,))
            return cursor.rowcount > 0

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
        return self.codegraph.upsert_code_repository(repo_key=repo_key, name=name, git_url=git_url, branch=branch, auth_ref=auth_ref, description=description, tags=tags, category_key=category_key, sync_interval_minutes=sync_interval_minutes, auto_understand=auto_understand, status=status)

    def list_code_repositories(self) -> list[dict[str, Any]]:
        return self.codegraph.list_code_repositories()

    def get_code_repository(self, repo_key: str) -> dict[str, Any] | None:
        return self.codegraph.get_code_repository(repo_key=repo_key)

    def mark_code_repository_sync(
        self,
        repo_key: str,
        *,
        local_path: str,
        last_commit: str | None,
        success: bool,
        error: str | None,
    ) -> None:
        return self.codegraph.mark_code_repository_sync(repo_key=repo_key, local_path=local_path, last_commit=last_commit, success=success, error=error)

    def replace_codegraph_index(self, repo_key: str, items: list[dict[str, Any]]) -> None:
        return self.codegraph.replace_codegraph_index(repo_key=repo_key, items=items)

    def create_codegraph_sync_run(self, repo_key: str, *, status: str, stage: str) -> dict[str, Any]:
        return self.codegraph.create_codegraph_sync_run(repo_key=repo_key, status=status, stage=stage)

    def update_codegraph_sync_run(self, run_id: int, *, stage: str) -> None:
        return self.codegraph.update_codegraph_sync_run(run_id=run_id, stage=stage)

    def finish_codegraph_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        stage: str,
        error: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        return self.codegraph.finish_codegraph_sync_run(run_id=run_id, status=status, stage=stage, error=error, duration_ms=duration_ms)

    def interrupt_running_codegraph_sync_runs(self, *, error: str) -> int:
        return self.codegraph.interrupt_running_codegraph_sync_runs(error=error)

    def search_codegraph_index(
        self,
        repo_key: str,
        *,
        query: str,
        item_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.codegraph.search_codegraph_index(repo_key=repo_key, query=query, item_type=item_type, limit=limit)

    def get_codegraph_file(self, repo_key: str, path: str) -> dict[str, Any] | None:
        return self.codegraph.get_codegraph_file(repo_key=repo_key, path=path)

    def count_codegraph_index_items(self, repo_key: str, item_type: str) -> int:
        return self.codegraph.count_codegraph_index_items(repo_key=repo_key, item_type=item_type)

    def _add_codegraph_snippet(self, row: dict[str, Any], query: str) -> dict[str, Any]:
        return self.codegraph._add_codegraph_snippet(row=row, query=query)

    # -- Categories --

    def upsert_category(self, *, category_key: str, name: str, description: str) -> dict[str, Any]:
        return self.codegraph.upsert_category(category_key=category_key, name=name, description=description)

    def list_categories(self) -> list[dict[str, Any]]:
        return self.codegraph.list_categories()

    def delete_category(self, category_key: str) -> None:
        return self.codegraph.delete_category(category_key=category_key)

    def delete_code_repository(self, repo_key: str) -> None:
        return self.codegraph.delete_repository(repo_key=repo_key)

    # -- Sync Config --

    def get_sync_config(self) -> dict[str, Any]:
        return self.codegraph.get_sync_config()

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
        workflow_max_runtime_minutes: int = 30,
        workflow_task_rerun_days: int = 30,
        log_retention_days: int = 180,
        mcp_timeout_seconds: int = 150,
        understand_timeout_minutes: int = 120,
    ) -> dict[str, Any]:
        return self.codegraph.save_sync_config(
            code_sync_cron=code_sync_cron,
            ua_git_url=ua_git_url,
            ua_plugin_update_cron=ua_plugin_update_cron,
            claude_mem_git_url=claude_mem_git_url,
            claude_mem_plugin_update_cron=claude_mem_plugin_update_cron,
            understand_cron=understand_cron,
            doc_sync_cron=doc_sync_cron,
            workflow_start_time=workflow_start_time,
            workflow_stop_time=workflow_stop_time,
            workflow_max_runs=workflow_max_runs,
            workflow_max_runtime_minutes=workflow_max_runtime_minutes,
            workflow_task_rerun_days=workflow_task_rerun_days,
            log_retention_days=log_retention_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            understand_timeout_minutes=understand_timeout_minutes,
        )

    def upsert_workflow_definition(
        self,
        *,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        status: str,
        created_by: str,
    ) -> dict[str, Any]:
        return self.workflows.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description=description,
            profile_key=profile_key,
            workflow_js=workflow_js,
            status=status,
            created_by=created_by,
        )

    def get_workflow_definition(self, workflow_key: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_definition(workflow_key)

    def list_workflow_definitions(self) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_definitions()

    def delete_workflow_definition(self, workflow_key: str) -> bool:
        return self.workflows.delete_workflow_definition(workflow_key)

    def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
        return self.workflows.upsert_workflow_tasks(workflow_key, tasks)

    def get_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
    ) -> dict[str, Any] | None:
        return self.workflows.get_workflow_task(workflow_key, task_key, task_version=task_version)

    def list_workflow_tasks(
        self,
        workflow_key: str,
        *,
        status: str | None = None,
        type: str | None = None,
        search: str | None = None,
        sort: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_tasks(
            workflow_key,
            status=status,
            type=type,
            search=search,
            sort=sort,
        )

    def lease_workflow_task(
        self,
        workflow_key: str,
        *,
        run_id: str,
        lease_seconds: int = 7200,
    ) -> dict[str, Any] | None:
        return self.workflows.lease_workflow_task(workflow_key, run_id=run_id, lease_seconds=lease_seconds)

    def complete_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str = "",
        run_id: str,
    ) -> bool:
        return self.workflows.complete_workflow_task(
            workflow_key,
            task_key,
            task_version=task_version,
            run_id=run_id,
        )

    def release_or_abandon_tasks_for_run(
        self,
        workflow_key: str,
        run_id: str,
        *,
        max_attempts: int,
        error_message: str,
    ) -> dict[str, int]:
        return self.workflows.release_or_abandon_tasks_for_run(
            workflow_key,
            run_id,
            max_attempts=max_attempts,
            error_message=error_message,
        )

    def force_workflow_task_lease_expiry(
        self,
        workflow_key: str,
        task_key: str,
        expires_at: str,
        task_version: str | None = None,
    ) -> None:
        return self.workflows.force_workflow_task_lease_expiry(
            workflow_key,
            task_key,
            expires_at,
            task_version=task_version,
        )

    def create_workflow_run(
        self,
        *,
        run_id: str,
        workflow_key: str,
        profile_key: str,
        task_key: str | None,
        status: str,
        temp_dir: str,
    ) -> dict[str, Any]:
        return self.workflows.create_workflow_run(
            run_id=run_id,
            workflow_key=workflow_key,
            profile_key=profile_key,
            task_key=task_key,
            status=status,
            temp_dir=temp_dir,
        )

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_run(run_id)

    def list_workflow_runs(self, workflow_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_runs(workflow_key, limit=limit)

    def clear_workflow_execution_data(self, workflow_key: str) -> dict[str, int]:
        return self.workflows.clear_workflow_execution_data(workflow_key)

    def finish_workflow_run(
        self,
        run_id: str,
        *,
        status: str,
        exit_code: int | None,
        stdout_path: str | None,
        stderr_path: str | None,
        error: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        return self.workflows.finish_workflow_run(
            run_id,
            status=status,
            exit_code=exit_code,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=error,
            duration_ms=duration_ms,
        )

    def append_workflow_run_log(
        self,
        *,
        run_id: str,
        workflow_key: str,
        task_key: str | None,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.workflows.append_workflow_run_log(
            run_id=run_id,
            workflow_key=workflow_key,
            task_key=task_key,
            level=level,
            stage=stage,
            message=message,
            payload=payload,
        )

    def list_workflow_run_logs(self, run_id: str) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_run_logs(run_id)

    def upsert_workflow_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
        task_version: str = "",
    ) -> dict[str, Any]:
        return self.workflows.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            task_version=task_version,
            title=title,
            path=path,
            tags=tags,
            format=format,
            summary=summary,
            content=content,
            metadata=metadata,
        )

    def get_workflow_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_artifact(artifact_id)

    def search_workflow_artifacts(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        task_key: str | None = None,
        task_version: str | None = None,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        return self.workflows.search_workflow_artifacts(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            include_history=include_history,
            limit=limit,
        )

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
        return self.capabilities.create_mcp_service(service_key=service_key, name=name, endpoint_url=endpoint_url, headers=headers, description=description, tags=tags, created_by=created_by)

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
        return self.capabilities.update_mcp_service(service_key=service_key, name=name, endpoint_url=endpoint_url, headers=headers, description=description, tags=tags)

    def get_mcp_service(self, service_key: str) -> dict[str, Any] | None:
        return self.capabilities.get_mcp_service(service_key=service_key)

    def list_mcp_services(self) -> list[dict[str, Any]]:
        return self.capabilities.list_mcp_services()

    def update_mcp_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        return self.capabilities.update_mcp_service_status(service_key=service_key, status=status)

    def mark_mcp_service_sync(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        return self.capabilities.mark_mcp_service_sync(service_key=service_key, success=success, error=error)

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
        return self.capabilities.upsert_mcp_tool(service_key=service_key, tool_name=tool_name, display_name=display_name, description=description, input_schema=input_schema, tool_type=tool_type, tags=tags, examples=examples)

    def update_mcp_tool_type(
        self,
        service_key: str,
        tool_name: str,
        tool_type: ToolType | str,
    ) -> dict[str, Any]:
        return self.capabilities.update_mcp_tool_type(service_key=service_key, tool_name=tool_name, tool_type=tool_type)

    def list_mcp_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        return self.capabilities.list_mcp_tools(service_key=service_key)

    def get_mcp_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        return self.capabilities.get_mcp_tool(service_key=service_key, tool_name=tool_name)

    def deactivate_missing_mcp_tools(self, service_key: str, active_tool_names: set[str]) -> None:
        return self.capabilities.deactivate_missing_mcp_tools(service_key=service_key, active_tool_names=active_tool_names)

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
        return self.capabilities.create_openapi_service(
            service_key=service_key,
            name=name,
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
            auth_config=auth_config,
            headers=headers,
            description=description,
            tags=tags,
            created_by=created_by,
        )

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
        return self.capabilities.update_openapi_service(
            service_key=service_key,
            name=name,
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
            auth_config=auth_config,
            headers=headers,
            description=description,
            tags=tags,
        )

    def get_openapi_service(self, service_key: str) -> dict[str, Any] | None:
        return self.capabilities.get_openapi_service(service_key=service_key)

    def list_openapi_services(self) -> list[dict[str, Any]]:
        return self.capabilities.list_openapi_services()

    def update_openapi_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        return self.capabilities.update_openapi_service_status(service_key=service_key, status=status)

    def mark_openapi_service_import(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        return self.capabilities.mark_openapi_service_import(service_key=service_key, success=success, error=error)

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
        return self.capabilities.upsert_openapi_tool(
            service_key=service_key,
            tool_name=tool_name,
            operation_id=operation_id,
            method=method,
            path=path,
            display_name=display_name,
            description=description,
            input_schema=input_schema,
            request_mapping=request_mapping,
            response_schema=response_schema,
            tool_type=tool_type,
            tags=tags,
            examples=examples,
        )

    def list_openapi_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        return self.capabilities.list_openapi_tools(service_key=service_key)

    def get_openapi_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        return self.capabilities.get_openapi_tool(service_key=service_key, tool_name=tool_name)

    def update_openapi_tool_type(self, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        return self.capabilities.update_openapi_tool_type(service_key=service_key, tool_name=tool_name, tool_type=tool_type)

    def delete_openapi_tool(self, service_key: str, tool_name: str) -> None:
        return self.capabilities.delete_openapi_tool(service_key=service_key, tool_name=tool_name)

    def delete_mcp_service(self, service_key: str) -> None:
        return self.capabilities.delete_mcp_service(service_key=service_key)

    def delete_openapi_service(self, service_key: str) -> None:
        return self.capabilities.delete_openapi_service(service_key=service_key)

    def upsert_project_profile(
        self,
        *,
        profile_key: str,
        name: str,
        description: str = "",
        status: str = "active",
        created_by: str,
    ) -> dict[str, Any]:
        return self.governance.upsert_project_profile(profile_key=profile_key, name=name, description=description, status=status, created_by=created_by)

    def get_project_profile(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_project_profile(profile_key=profile_key)

    def list_project_profiles(self) -> list[dict[str, Any]]:
        return self.governance.list_project_profiles()

    def replace_profile_source_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_source_rules(profile_key=profile_key, rules=rules)

    def list_profile_source_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_source_rules(profile_key=profile_key)

    def replace_profile_resource_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_resource_rules(profile_key=profile_key, rules=rules)

    def list_profile_resource_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_resource_rules(profile_key=profile_key)

    def replace_profile_pin_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_pin_rules(profile_key=profile_key, rules=rules)

    def list_profile_pin_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_pin_rules(profile_key=profile_key)

    def delete_source_rules_by_key(self, source_type: str, source_key: str) -> None:
        return self.governance.delete_source_rules_by_key(source_type=source_type, source_key=source_key)

    def delete_pin_rules_by_service(self, service_key: str) -> None:
        return self.governance.delete_pin_rules_by_service(service_key=service_key)

    def delete_resource_rules_by_key(self, resource_type: str, resource_key: str) -> None:
        return self.governance.delete_resource_rules_by_key(resource_type=resource_type, resource_key=resource_key)

    def upsert_profile_pin_settings(
        self,
        *,
        profile_key: str,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
        auto_cache: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self.governance.upsert_profile_pin_settings(
            profile_key=profile_key,
            mode=mode,
            ratio_percent=ratio_percent,
            count=count,
            auto_cache=auto_cache,
        )

    def get_profile_pin_settings(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_pin_settings(profile_key=profile_key)

    def clear_profile_pin_auto_cache(self, profile_key: str) -> None:
        return self.governance.clear_profile_pin_auto_cache(profile_key=profile_key)

    def get_profile_doc_cache(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_doc_cache(profile_key=profile_key)

    def upsert_profile_manual_notes(self, profile_key: str, manual_notes: str) -> dict[str, Any]:
        return self.governance.upsert_profile_manual_notes(profile_key=profile_key, manual_notes=manual_notes)

    def upsert_profile_rendered_doc(
        self,
        *,
        profile_key: str,
        manual_notes: str,
        auto_summary: dict[str, Any],
        auto_summary_hash: str,
        rendered_hash: str,
        markdown: str,
        mark_written: bool,
    ) -> dict[str, Any]:
        return self.governance.upsert_profile_rendered_doc(
            profile_key=profile_key,
            manual_notes=manual_notes,
            auto_summary=auto_summary,
            auto_summary_hash=auto_summary_hash,
            rendered_hash=rendered_hash,
            markdown=markdown,
            mark_written=mark_written,
        )

    def list_resource_rule_profiles(self, resource_type: str, resource_key: str) -> list[dict[str, Any]]:
        return self.governance.list_resource_rule_profiles(resource_type=resource_type, resource_key=resource_key)

    def replace_resource_rule_profiles(self, resource_type: str, resource_key: str, profile_keys: list[str], overrides: dict[str, dict[str, str | None]] | None = None) -> None:
        return self.governance.replace_resource_rule_profiles(resource_type=resource_type, resource_key=resource_key, profile_keys=profile_keys, overrides=overrides)

    def get_profile_resource_rule(self, profile_key: str, resource_type: str, resource_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_resource_rule(profile_key=profile_key, resource_type=resource_type, resource_key=resource_key)

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
        log = self.governance.create_tool_call_log(log_id=log_id, actor=actor, profile_key=profile_key, entrypoint=entrypoint, source_type=source_type, source_key=source_key, tool_name=tool_name, request=request, response=response, status=status, error_message=error_message, failure_stage=failure_stage, failure_owner=failure_owner, error_type=error_type, resource_type=resource_type, resource_key=resource_key, duration_ms=duration_ms)
        self.maybe_prune_runtime_logs()
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
        return self.governance.list_tool_call_logs(entrypoint=entrypoint, source_type=source_type, source_key=source_key, tool_name=tool_name, profile_key=profile_key, status=status, failure_stage=failure_stage, failure_owner=failure_owner, error_type=error_type, resource_type=resource_type, resource_key=resource_key, created_from=created_from, created_to=created_to, limit=limit, offset=offset)

    def aggregate_tool_call_stats(
        self,
        *,
        dimensions: list[str],
        created_from: str | None,
        created_to: str | None,
        bucket: str | None,
    ) -> list[dict[str, Any]]:
        return self.governance.aggregate_tool_call_stats(dimensions=dimensions, created_from=created_from, created_to=created_to, bucket=bucket)

    def aggregate_pin_group_usage(self, *, profile_key: str, created_from: str) -> list[dict[str, Any]]:
        return self.governance.aggregate_pin_group_usage(profile_key=profile_key, created_from=created_from)

    def get_tool_call_log(self, log_id: str) -> dict[str, Any] | None:
        return self.governance.get_tool_call_log(log_id=log_id)

    def set_runtime_log_retention_days(self, days: int) -> None:
        self._runtime_log_retention_days = max(int(days), 0)

    def maybe_prune_runtime_logs(self, force: bool = False) -> dict[str, int]:
        if self._runtime_log_retention_days <= 0:
            return {"tool_call_logs": 0, "agent_runs": 0}
        now = time.monotonic()
        if not force and self._last_runtime_log_prune_monotonic is not None:
            if now - self._last_runtime_log_prune_monotonic < self._runtime_log_prune_interval_seconds:
                return {"tool_call_logs": 0, "agent_runs": 0}
        deleted = self.prune_runtime_logs(force=True)
        self._last_runtime_log_prune_monotonic = now
        return deleted

    def prune_runtime_logs(self, force: bool = False) -> dict[str, int]:
        if self._runtime_log_retention_days <= 0:
            return {"tool_call_logs": 0, "agent_runs": 0}
        cutoff = (datetime.now(UTC) - timedelta(days=self._runtime_log_retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = {
            "tool_call_logs": self.governance.purge_tool_call_logs_before(cutoff),
            "agent_runs": self.agent_runs.purge_created_before(cutoff),
        }
        if force:
            self._last_runtime_log_prune_monotonic = time.monotonic()
        return deleted

    def create_kb(self, slug: str, name: str, description: str, created_by: str) -> dict[str, Any]:
        return self.knowledge.create_kb(slug=slug, name=name, description=description, created_by=created_by)

    def get_kb_by_id(self, kb_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self.knowledge.get_kb_by_id(kb_id=kb_id, conn=conn)

    def get_kb_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self.knowledge.get_kb_by_slug(slug=slug)

    def update_kb_defaults(self, kb_id: int, default_backend_slug: str | None, default_agent_id: str | None) -> None:
        return self.knowledge.update_kb_defaults(kb_id=kb_id, default_backend_slug=default_backend_slug, default_agent_id=default_agent_id)

    def ensure_backend_target(self, kb_id: int, slug: str, backend_type: str) -> None:
        return self.knowledge.ensure_backend_target(kb_id=kb_id, slug=slug, backend_type=backend_type)

    def list_backend_targets(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_backend_targets(kb_id=kb_id)

    def set_backend_target_status(self, kb_id: int, slug: str, status: str) -> None:
        return self.knowledge.set_backend_target_status(kb_id=kb_id, slug=slug, status=status)

    def update_backend_target_kb_id(self, kb_id: int, slug: str, backend_kb_id: str) -> None:
        return self.knowledge.update_backend_target_kb_id(kb_id=kb_id, slug=slug, backend_kb_id=backend_kb_id)

    def rebuild_backend_target(self, kb_id: int, backend_slug: str, new_backend_kb_id: str) -> int:
        return self.knowledge.rebuild_backend_target(kb_id=kb_id, backend_slug=backend_slug, new_backend_kb_id=new_backend_kb_id)

    def update_backend_target_config(self, kb_id: int, slug: str, config_updates: dict[str, Any]) -> None:
        return self.knowledge.update_backend_target_config(kb_id=kb_id, slug=slug, config_updates=config_updates)

    def list_backends(self) -> list[dict[str, Any]]:
        return self.knowledge.list_backends()

    def get_backend(self, slug: str) -> dict[str, Any] | None:
        return self.knowledge.get_backend(slug=slug)

    def upsert_backend(self, **kwargs) -> dict[str, Any]:
        return self.knowledge.upsert_backend(**kwargs)

    def delete_backend(self, slug: str) -> bool:
        return self.knowledge.delete_backend(slug=slug)

    def upsert_kb_repo_source(self, kb_id: int, repo_key: str, include_suffixes: list[str]) -> dict[str, Any]:
        return self.knowledge.upsert_kb_repo_source(kb_id=kb_id, repo_key=repo_key, include_suffixes=include_suffixes)

    def list_kb_repo_sources(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_kb_repo_sources(kb_id=kb_id)

    def get_kb_repo_source(self, kb_id: int, repo_key: str) -> dict[str, Any] | None:
        return self.knowledge.get_kb_repo_source(kb_id=kb_id, repo_key=repo_key)

    def mark_kb_repo_source_sync(
        self,
        kb_id: int,
        repo_key: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        return self.knowledge.mark_kb_repo_source_sync(kb_id=kb_id, repo_key=repo_key, success=success, error=error)

    def delete_kb(self, kb_id: int) -> None:
        return self.knowledge.delete_kb(kb_id=kb_id)

    def list_sync_states_for_doc(self, doc_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_sync_states_for_doc(doc_id=doc_id)

    def list_synced_doc_ids(self, kb_id: int) -> list[int]:
        return self.knowledge.list_synced_doc_ids(kb_id=kb_id)

    def list_kbs(self) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs()

    def list_kbs_for_user(self, linux_user: str) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs_for_user(linux_user=linux_user)

    def list_kbs_for_user_or_admin(self, linux_user: str, admins: set[str]) -> list[dict[str, Any]]:
        return self.knowledge.list_kbs_for_user_or_admin(linux_user=linux_user, admins=admins)

    def grant_member(self, kb_id: int, linux_user: str, role: KbRole) -> None:
        return self.knowledge.grant_member(kb_id=kb_id, linux_user=linux_user, role=role)

    def get_member_role(self, kb_id: int, linux_user: str) -> KbRole | None:
        return self.knowledge.get_member_role(kb_id=kb_id, linux_user=linux_user)

    def list_members(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_members(kb_id=kb_id)

    def list_document_slugs(self) -> set[str]:
        return self.knowledge.list_document_slugs()

    def create_document(self, slug: str, title: str, owner_user: str) -> dict[str, Any]:
        return self.knowledge.create_document(slug=slug, title=title, owner_user=owner_user)

    def get_document_by_slug(self, slug: str, include_deleted: bool = False) -> dict[str, Any] | None:
        return self.knowledge.get_document_by_slug(slug=slug, include_deleted=include_deleted)

    def attach_document_to_kb(self, doc_id: int, kb_id: int, added_by: str) -> None:
        return self.knowledge.attach_document_to_kb(doc_id=doc_id, kb_id=kb_id, added_by=added_by)

    def get_document_kbs(self, doc_id: int) -> list[dict[str, Any]]:
        return self.knowledge.get_document_kbs(doc_id=doc_id)

    def list_versions(self, doc_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_versions(doc_id=doc_id)

    def next_version_no(self, doc_id: int) -> int:
        return self.knowledge.next_version_no(doc_id=doc_id)

    def set_current_version(self, doc_id: int, version_id: int) -> None:
        return self.knowledge.set_current_version(doc_id=doc_id, version_id=version_id)

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
        return self.knowledge.create_document_version(doc_id=doc_id, original_filename=original_filename, content_hash=content_hash, file_size=file_size, mime_type=mime_type, archive_path=archive_path, created_by=created_by)

    def create_sync_job(
        self,
        doc_id: int,
        kb_id: int,
        operation: Operation,
        version_id: int | None,
        backend_slug: str = "mock",
    ) -> dict[str, Any]:
        return self.knowledge.create_sync_job(doc_id=doc_id, kb_id=kb_id, operation=operation, version_id=version_id, backend_slug=backend_slug)

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        return self.knowledge.list_pending_jobs()

    def list_runnable_jobs(self, actor: str | None, backend_slug: str | None = None) -> list[dict[str, Any]]:
        return self.knowledge.list_runnable_jobs(actor=actor, backend_slug=backend_slug)

    def list_all_jobs(self, backend_slug: str | None = None) -> list[dict[str, Any]]:
        return self.knowledge.list_all_jobs(backend_slug=backend_slug)

    def update_job_status(self, job_id: int, status: SyncJobStatus, error: str | None = None) -> None:
        return self.knowledge.update_job_status(job_id=job_id, status=status, error=error)

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
        return self.knowledge.upsert_sync_state(doc_id=doc_id, kb_id=kb_id, backend_slug=backend_slug, backend_doc_id=backend_doc_id, status=status, backend_status=backend_status, chunk_count=chunk_count, progress=progress, backend_error=backend_error)

    def get_sync_state(self, doc_id: int, kb_id: int, backend_slug: str = "mock") -> dict[str, Any] | None:
        return self.knowledge.get_sync_state(doc_id=doc_id, kb_id=kb_id, backend_slug=backend_slug)

    def list_docs_for_kb(self, kb_id: int) -> list[dict[str, Any]]:
        return self.knowledge.list_docs_for_kb(kb_id=kb_id)

    def list_jobs_for_user(self, linux_user: str, backend_slug: str | None = None) -> list[dict[str, Any]]:
        return self.knowledge.list_jobs_for_user(linux_user=linux_user, backend_slug=backend_slug)

    def soft_delete_document(self, doc_id: int) -> None:
        return self.knowledge.soft_delete_document(doc_id=doc_id)

    def purge_document(self, doc_id: int) -> list[str]:
        return self.knowledge.purge_document(doc_id=doc_id)
