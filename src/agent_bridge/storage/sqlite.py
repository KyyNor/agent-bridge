"""Agent Bridge 的 SQLite 存储装配入口。"""

from __future__ import annotations

import sqlite3
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent_bridge.storage.migrations.schema import apply_followup_schema, apply_initial_schema
from agent_bridge.storage.store_facade import SQLiteStoreFacade


class SQLiteStore(SQLiteStoreFacade):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._runtime_log_retention_days = 180
        self._last_runtime_log_prune_monotonic: float | None = None
        self._runtime_log_prune_interval_seconds = 3600.0
        self._active_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"sqlite_store_connection_{id(self)}", default=None
        )

        from agent_bridge.storage.repositories.agent_runs import AgentRunsRepository
        from agent_bridge.storage.repositories.capabilities import CapabilitiesRepository
        from agent_bridge.storage.repositories.codegraph import CodeGraphRepository
        from agent_bridge.storage.repositories.governance import GovernanceRepository
        from agent_bridge.storage.repositories.folders import FolderRepository
        from agent_bridge.storage.repositories.knowledge import KnowledgeRepository
        from agent_bridge.storage.repositories.memory import MemoryRepository
        from agent_bridge.storage.repositories.scripts import ScriptsRepository
        from agent_bridge.storage.repositories.retrieval_probe import RetrievalProbeConfigRepository
        from agent_bridge.storage.repositories.workflows import WorkflowsRepository

        self.folders = FolderRepository(db_path, self.connect)
        self.knowledge = KnowledgeRepository(db_path, self.connect, self.folders)
        self.capabilities = CapabilitiesRepository(db_path, self.connect)
        self.governance = GovernanceRepository(db_path, self.connect)
        self.memory = MemoryRepository(db_path, self.connect)
        self.codegraph = CodeGraphRepository(db_path, self.connect)
        self.workflows = WorkflowsRepository(db_path, self.connect)
        self.scripts = ScriptsRepository(db_path, self.connect)
        self.retrieval_probe_config = RetrievalProbeConfigRepository(db_path, self.connect)
        self.agent_runs = AgentRunsRepository(db_path, self.connect, prune_callback=self.maybe_prune_runtime_logs)

    def _open_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection.get()
        if active is not None:
            yield active
            return

        conn = self._open_connection()
        token = self._active_connection.set(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._active_connection.reset(token)
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection.get()
        if active is not None:
            yield active
            return

        conn = self._open_connection()
        token = self._active_connection.set(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._active_connection.reset(token)
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            apply_initial_schema(self, conn)
        self.migrate_phase2()

    def migrate_phase2(self) -> None:
        with self.connect() as conn:
            apply_followup_schema(self, conn)

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                except sqlite3.OperationalError as exc:
                    # Another process may have completed the same idempotent
                    # migration between PRAGMA table_info and ALTER TABLE.
                    if "duplicate column name" not in str(exc).lower():
                        raise

    def _drop_column(self, conn: sqlite3.Connection, table: str, column: str) -> None:
        """Drop a column if present. Uses native DROP COLUMN (SQLite >= 3.35);
        on older engines the column is left in place (harmless, just unused)."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            return
        if sqlite3.sqlite_version_info >= (3, 35, 0):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    def _remove_legacy_codegraph_text_index(self, conn: sqlite3.Connection) -> None:
        """删除旧 SQLite 文本索引，并要求受影响仓库用 CodeGraph 重新同步。

        旧表只保存可重新生成的派生数据。发现其中存在数据时，保守地清除对应
        仓库的成功同步标记，避免服务切换到正式后端后误用旧状态。
        """
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'codegraph_index_items'"
        ).fetchone()
        if table is None:
            return
        affected = [
            str(row["repo_key"])
            for row in conn.execute(
                "SELECT DISTINCT repo_key FROM codegraph_index_items ORDER BY repo_key"
            ).fetchall()
        ]
        if affected:
            placeholders = ", ".join("?" for _ in affected)
            conn.execute(
                f"""
                UPDATE code_repositories
                SET last_commit = NULL,
                    last_synced_at = NULL,
                    last_error = 'CodeGraph 索引后端已统一，请重新同步仓库',
                    updated_at = CURRENT_TIMESTAMP
                WHERE repo_key IN ({placeholders})
                """,
                affected,
            )
        conn.execute("DROP TABLE codegraph_index_items")

    def _backfill_agent_run_backend_keys(self, conn: sqlite3.Connection) -> None:
        """Infer backend_key for historical agent runs that predate the column."""
        for backend_key, marker in [
            ("opencode", "opencode_cli"),
            ("opencode", "opencode_server"),
            ("codex", "codex_cli"),
            ("claude", "claude_agent_sdk"),
        ]:
            conn.execute(
                """
                UPDATE agent_runs
                SET backend_key = ?
                WHERE (backend_key IS NULL OR backend_key = '')
                  AND events_json LIKE ?
                """,
                (backend_key, f"%{marker}%"),
            )


    def _migrate_tool_call_logs_nullable_profile(self, conn: sqlite3.Connection) -> None:
        return self.governance._migrate_tool_call_logs_nullable_profile(conn=conn)
