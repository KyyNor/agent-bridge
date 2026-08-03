"""会重建表但保持幂等的工作流表迁移。"""

from __future__ import annotations

import logging
import sqlite3

from agent_bridge.storage.repositories.workflow_artifact_search import (
    upsert_workflow_artifact_search_content,
)

logger = logging.getLogger(__name__)

WORKFLOW_ARTIFACTS_FTS_VERSION = "3"


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
    """创建并维护工作流产物的 jieba + FTS5 索引。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_artifacts_fts_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )

    version = conn.execute(
        "SELECT value FROM workflow_artifacts_fts_meta WHERE key = 'index_version'"
    ).fetchone()
    fts_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'workflow_artifacts_fts'"
    ).fetchone()
    search_content_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workflow_artifacts_search_content'"
    ).fetchone() is not None
    expected_shape = (
        fts_row is not None
        and "workflow_artifacts_search_content" in str(fts_row[0])
        and "unicode61" in str(fts_row[0])
    )
    recreate = (
        version is None
        or version[0] != WORKFLOW_ARTIFACTS_FTS_VERSION
        or not search_content_exists
        or not expected_shape
    )

    if recreate:
        for trigger in (
            "workflow_artifacts_fts_ai",
            "workflow_artifacts_fts_ad",
            "workflow_artifacts_fts_au",
            "workflow_artifacts_search_content_ai",
            "workflow_artifacts_search_content_ad",
            "workflow_artifacts_search_content_au",
            "workflow_artifacts_search_content_base_ad",
            "workflow_artifacts_search_content_base_au",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DROP TABLE IF EXISTS workflow_artifacts_fts")
        conn.execute("DROP TABLE IF EXISTS workflow_artifacts_search_content")

        conn.execute(
            """
            CREATE TABLE workflow_artifacts_search_content (
              id INTEGER PRIMARY KEY REFERENCES workflow_artifacts(id) ON DELETE CASCADE,
              title TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL DEFAULT ''
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE workflow_artifacts_fts USING fts5(
                  title,
                  summary,
                  path,
                  content,
                  content='workflow_artifacts_search_content',
                  content_rowid='id',
                  tokenize = "unicode61 tokenchars '_'"
                )
                """
            )
        except sqlite3.OperationalError as exc:
            raise RuntimeError("当前 SQLite 未启用 FTS5，无法创建工作流产物全文索引") from exc

    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_search_content_ai
        AFTER INSERT ON workflow_artifacts_search_content
        BEGIN
          INSERT INTO workflow_artifacts_fts(rowid, title, summary, path, content)
          VALUES (new.id, new.title, new.summary, new.path, new.content);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_search_content_ad
        AFTER DELETE ON workflow_artifacts_search_content
        BEGIN
          INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts, rowid, title, summary, path, content)
          VALUES ('delete', old.id, old.title, old.summary, old.path, old.content);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_search_content_au
        AFTER UPDATE ON workflow_artifacts_search_content
        BEGIN
          INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts, rowid, title, summary, path, content)
          VALUES ('delete', old.id, old.title, old.summary, old.path, old.content);
          INSERT INTO workflow_artifacts_fts(rowid, title, summary, path, content)
          VALUES (new.id, new.title, new.summary, new.path, new.content);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_search_content_base_ad
        AFTER DELETE ON workflow_artifacts
        BEGIN
          DELETE FROM workflow_artifacts_search_content WHERE id = old.id;
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS workflow_artifacts_search_content_base_au
        AFTER UPDATE OF title, summary, path, content ON workflow_artifacts
        BEGIN
          DELETE FROM workflow_artifacts_search_content WHERE id = old.id;
        END
        """
    )

    content_count = conn.execute(
        "SELECT COUNT(*) FROM workflow_artifacts_search_content"
    ).fetchone()[0]
    artifact_count = conn.execute("SELECT COUNT(*) FROM workflow_artifacts").fetchone()[0]
    missing_content = conn.execute(
        """
        SELECT 1
        FROM workflow_artifacts a
        LEFT JOIN workflow_artifacts_search_content s ON s.id = a.id
        WHERE s.id IS NULL
        LIMIT 1
        """
    ).fetchone() is not None
    if force_rebuild or recreate or missing_content or content_count != artifact_count:
        conn.execute("DELETE FROM workflow_artifacts_search_content")
        rows = conn.execute(
            "SELECT id, title, summary, path, content FROM workflow_artifacts ORDER BY id"
        ).fetchall()
        for row in rows:
            upsert_workflow_artifact_search_content(
                conn,
                {
                    "id": row[0],
                    "title": row[1],
                    "summary": row[2],
                    "path": row[3],
                    "content": row[4],
                },
            )
        conn.execute(
            """
            INSERT INTO workflow_artifacts_fts_meta(key, value)
            VALUES ('index_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (WORKFLOW_ARTIFACTS_FTS_VERSION,),
        )


# task_version 演进模型引入的存量数据迁移版本号。
# 见 backfill_workflow_tasks_superseded：把同 task_key 下非最新 version 的未运行旧任务标为 superseded。
WORKFLOW_TASKS_SUPERSEDED_BACKFILL_VERSION = "1"

# 成功任务错误状态修复迁移版本号。
# 见 backfill_completed_workflow_task_errors：清理历史上成功任务残留的 last_error。
WORKFLOW_TASK_COMPLETED_ERROR_BACKFILL_VERSION = "1"


def backfill_workflow_tasks_superseded(conn: sqlite3.Connection) -> None:
    """把存量中“同 task_key 多个 version 都在排队”的旧版本回填为 superseded。

    旧模型下不同 task_version 是相互独立的任务行，全部会进队列执行；引入演进模型后，
    每个 task_key 只应保留一个最新版本参与执行。本迁移把每个 task_key 下“非最新 version 且
    仍在 pending/stale”的旧行标为 superseded（调度器永不领取），最新行保持原状。

    幂等：通过 workflow_artifacts_fts_meta 表记录已执行的迁移版本号，每个版本只跑一次。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_artifacts_fts_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    applied = conn.execute(
        "SELECT value FROM workflow_artifacts_fts_meta WHERE key = 'tasks_superseded_backfill'"
    ).fetchone()
    if applied is not None and applied[0] == WORKFLOW_TASKS_SUPERSEDED_BACKFILL_VERSION:
        return

    # 每个 (workflow_key, task_key) 下按 set_at DESC, id DESC 取最新一行，其余 pending/stale
    # 旧行视为被取代。最新行选取与运行时 current 判定保持一致。
    cursor = conn.execute(
        """
        UPDATE workflow_tasks
        SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT t.id
            FROM workflow_tasks t
            WHERE t.status IN ('pending', 'stale')
              AND t.id <> (
                SELECT tt.id
                FROM workflow_tasks tt
                WHERE tt.workflow_key = t.workflow_key
                  AND tt.task_key = t.task_key
                ORDER BY tt.set_at DESC, tt.id DESC
                LIMIT 1
              )
        )
        """
    )
    affected = int(cursor.rowcount if cursor.rowcount is not None else 0)
    if affected:
        logger.info(
            "工作流任务版本演进迁移 完成 状态=superseded 影响行数=%s 阶段=task_superseded_backfill",
            affected,
        )
    conn.execute(
        """
        INSERT INTO workflow_artifacts_fts_meta(key, value)
        VALUES ('tasks_superseded_backfill', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (WORKFLOW_TASKS_SUPERSEDED_BACKFILL_VERSION,),
    )


def backfill_completed_workflow_task_errors(conn: sqlite3.Connection) -> None:
    """清理历史上已成功任务残留的 ``last_error``。

    ``last_error`` 现在表示当前任务状态的错误提示；运行历史中的错误仍保留在
    ``workflow_runs.error`` 和对应运行日志中。因此只清理状态已经是 completed 的
    任务，pending/failed/abandoned 仍保留最近一次失败原因供重试和排查使用。

    幂等：通过 workflow_artifacts_fts_meta 表记录已执行的迁移版本号。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_artifacts_fts_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    applied = conn.execute(
        "SELECT value FROM workflow_artifacts_fts_meta WHERE key = 'completed_task_error_backfill'"
    ).fetchone()
    if applied is not None and applied[0] == WORKFLOW_TASK_COMPLETED_ERROR_BACKFILL_VERSION:
        return

    cursor = conn.execute(
        """
        UPDATE workflow_tasks
        SET last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'completed' AND last_error IS NOT NULL
        """
    )
    affected = int(cursor.rowcount if cursor.rowcount is not None else 0)
    if affected:
        logger.info(
            "工作流任务成功状态错误迁移 完成 状态=completed 影响行数=%s 阶段=completed_task_error_backfill",
            affected,
        )
    conn.execute(
        """
        INSERT INTO workflow_artifacts_fts_meta(key, value)
        VALUES ('completed_task_error_backfill', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (WORKFLOW_TASK_COMPLETED_ERROR_BACKFILL_VERSION,),
    )
