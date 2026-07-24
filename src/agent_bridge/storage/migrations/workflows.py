"""会重建表但保持幂等的工作流表迁移。"""

from __future__ import annotations

import sqlite3


WORKFLOW_ARTIFACTS_FTS_VERSION = "1"


def has_unique_index(conn: sqlite3.Connection, table: str, columns: list[str]) -> bool:
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


def rebuild_workflow_tasks_if_needed(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(workflow_tasks)").fetchall()}
    if (
        "set_at" in existing
        and has_unique_index(conn, "workflow_tasks", ["workflow_key", "task_key", "task_version"])
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
          priority_flag TEXT,
          lease_origin_status TEXT,
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
          created_at, updated_at, completed_at, priority_flag, lease_origin_status
        )
        SELECT
          id, workflow_key, task_key, task_version, type, payload_json, status,
          lease_run_id, lease_expires_at, attempt_count, last_error,
          {set_at_expr},
          created_at, updated_at, completed_at, priority_flag, lease_origin_status
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



def rebuild_workflow_artifacts_if_needed(conn: sqlite3.Connection) -> bool:
    if has_unique_index(conn, "workflow_artifacts", ["workflow_key", "task_key", "task_version", "run_id", "path"]):
        return False
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
          reuse_allowed INTEGER NOT NULL DEFAULT 1,
          invalid_reason TEXT,
          producer_node_id TEXT,
          producer_node_fingerprint TEXT,
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
          task_version, is_current, reuse_allowed, invalid_reason,
          producer_node_id, producer_node_fingerprint, title, path, tags_json,
          format, summary, content, content_hash, metadata_json, created_at, updated_at
        )
        SELECT
          id, artifact_id, workflow_key, profile_key, run_id, task_key,
          task_version, is_current, reuse_allowed, invalid_reason,
          producer_node_id, producer_node_fingerprint, title, path, tags_json,
          format, summary, content, content_hash, metadata_json, created_at, updated_at
        FROM workflow_artifacts
        """
    )
    conn.execute("DROP TABLE workflow_artifacts")
    conn.execute("ALTER TABLE workflow_artifacts_new RENAME TO workflow_artifacts")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_profile ON workflow_artifacts(profile_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_path ON workflow_artifacts(path)")
    return True


def ensure_workflow_artifacts_fts(conn: sqlite3.Connection, *, force_rebuild: bool = False) -> None:
    """创建并维护工作流产物的 trigram FTS5 索引。

    FTS5 使用 external-content 表，正文仍以 ``workflow_artifacts`` 为唯一事实来源。
    trigger 负责增删改同步，迁移版本变化或底层产物表重建时执行一次全量 rebuild。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_artifacts_fts_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    fts_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workflow_artifacts_fts'"
    ).fetchone() is not None
    if not fts_exists:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE workflow_artifacts_fts USING fts5(
                  title,
                  summary,
                  path,
                  content,
                  content='workflow_artifacts',
                  content_rowid='id',
                  tokenize='trigram'
                )
                """
            )
        except sqlite3.OperationalError as exc:
            raise RuntimeError("当前 SQLite 未启用 FTS5，无法创建工作流产物全文索引") from exc

    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_fts_ai
        AFTER INSERT ON workflow_artifacts
        BEGIN
          INSERT INTO workflow_artifacts_fts(rowid, title, summary, path, content)
          VALUES (new.id, new.title, new.summary, new.path, new.content);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_fts_ad
        AFTER DELETE ON workflow_artifacts
        BEGIN
          INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts, rowid, title, summary, path, content)
          VALUES ('delete', old.id, old.title, old.summary, old.path, old.content);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_fts_au
        AFTER UPDATE OF title, summary, path, content ON workflow_artifacts
        BEGIN
          INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts, rowid, title, summary, path, content)
          VALUES ('delete', old.id, old.title, old.summary, old.path, old.content);
          INSERT INTO workflow_artifacts_fts(rowid, title, summary, path, content)
          VALUES (new.id, new.title, new.summary, new.path, new.content);
        END
        """
    )

    version = conn.execute(
        "SELECT value FROM workflow_artifacts_fts_meta WHERE key = 'index_version'"
    ).fetchone()
    if force_rebuild or version is None or version[0] != WORKFLOW_ARTIFACTS_FTS_VERSION:
        try:
            conn.execute("INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts) VALUES ('rebuild')")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("工作流产物 FTS5 索引重建失败") from exc
        conn.execute(
            """
            INSERT INTO workflow_artifacts_fts_meta(key, value)
            VALUES ('index_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (WORKFLOW_ARTIFACTS_FTS_VERSION,),
        )
