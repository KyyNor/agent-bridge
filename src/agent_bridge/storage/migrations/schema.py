"""核心、知识与治理领域的架构迁移。"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_bridge.storage.schema import CODEGRAPH_SCHEMA, SCHEMA, WORKFLOW_SCHEMA
from agent_bridge.storage.migrations.workflows import (
    backfill_completed_workflow_task_errors,
    backfill_workflow_tasks_superseded,
    ensure_workflow_artifacts_fts,
    rebuild_workflow_artifacts_if_needed,
    rebuild_workflow_tasks_if_needed,
)


def apply_initial_schema(store: Any, conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executescript(CODEGRAPH_SCHEMA)
    conn.executescript(WORKFLOW_SCHEMA)
    store._remove_legacy_codegraph_text_index(conn)
    store._ensure_columns(
        conn,
        "agent_runs",
        {
            "backend_key": "TEXT",
            "status": "TEXT NOT NULL DEFAULT ''",
            "started_at": "TEXT",
            "finished_at": "TEXT",
        },
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_backend_key ON agent_runs(backend_key)"
    )
    store._backfill_agent_run_backend_keys(conn)
    store._drop_column(conn, "workflow_definitions", "manifest_json")
    store._drop_column(conn, "workflow_definitions", "workflow_js")
    store._ensure_columns(
        conn,
        "workflow_definitions",
        {
            "workflow_type": "TEXT NOT NULL DEFAULT 'operation'",
            "definition_json": "TEXT",
        },
    )
    store._ensure_columns(
        conn,
        "workflow_runs",
        {
            "definition_snapshot_json": "TEXT NOT NULL DEFAULT '{\"nodes\":[],\"edges\":[]}'",
            "input_json": "TEXT NOT NULL DEFAULT '{}'",
            "output_json": "TEXT NOT NULL DEFAULT '{}'",
            "workflow_revision_no": "INTEGER",
            "workflow_content_hash": "TEXT",
            "task_version": "TEXT NOT NULL DEFAULT ''",
            "execution_mode": "TEXT NOT NULL DEFAULT 'normal'",
            "execution_plan_json": "TEXT NOT NULL DEFAULT '{}'",
            "source_run_id": "TEXT",
        },
    )
    store._ensure_columns(
        conn,
        "scripts",
        {
            "input_schema_json": "TEXT NOT NULL DEFAULT '{\"type\":\"object\",\"properties\":{},\"additionalProperties\":true}'",
            "output_schema_json": "TEXT",
        },
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_node_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
          node_id TEXT NOT NULL,
          node_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          condition_results_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          node_fingerprint TEXT,
          action TEXT,
          reuse_reason TEXT,
          source_run_id TEXT,
          source_node_id TEXT,
          source_node_fingerprint TEXT,
          artifact_ids_json TEXT NOT NULL DEFAULT '[]',
          error TEXT,
          agent_run_key TEXT,
          script_run_id TEXT,
          started_at TEXT,
          finished_at TEXT,
          UNIQUE (run_id, node_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run ON workflow_node_runs(run_id, id)")
    store._ensure_columns(
        conn,
        "workflow_node_runs",
        {
            "node_fingerprint": "TEXT",
            "action": "TEXT",
            "reuse_reason": "TEXT",
            "source_run_id": "TEXT",
            "source_node_id": "TEXT",
            "source_node_fingerprint": "TEXT",
            "artifact_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_run_artifacts (
          run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
          node_id TEXT NOT NULL,
          artifact_id TEXT NOT NULL,
          source_run_id TEXT,
          source_node_id TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (run_id, node_id, artifact_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_run_artifacts_run_node "
        "ON workflow_run_artifacts(run_id, node_id)"
    )
    store._ensure_columns(
        conn,
        "workflow_tasks",
        {
            "task_version": "TEXT NOT NULL DEFAULT ''",
            "type": "TEXT NOT NULL DEFAULT ''",
            "priority_flag": "TEXT",
            "lease_origin_status": "TEXT",
        },
    )
    store._ensure_columns(
        conn,
        "workflow_artifacts",
        {
            "task_version": "TEXT NOT NULL DEFAULT ''",
            "is_current": "INTEGER NOT NULL DEFAULT 1",
            "reuse_allowed": "INTEGER NOT NULL DEFAULT 1",
            "invalid_reason": "TEXT",
            "producer_node_id": "TEXT",
            "producer_node_fingerprint": "TEXT",
        },
    )
    rebuild_workflow_tasks_if_needed(conn)
    artifacts_rebuilt = rebuild_workflow_artifacts_if_needed(conn)
    ensure_workflow_artifacts_fts(conn, force_rebuild=artifacts_rebuilt)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_tasks_latest "
        "ON workflow_tasks(workflow_key, task_key, set_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_tasks_version_status "
        "ON workflow_tasks(workflow_key, task_version, status)"
    )
    # task_version 演进模型：回填存量多 version 排队任务为 superseded（幂等）。
    backfill_workflow_tasks_superseded(conn)
    # 成功任务不应在任务列表中继续展示历史失败原因（幂等）。
    backfill_completed_workflow_task_errors(conn)
    store._ensure_columns(
        conn,
        "scripts",
        {"current_revision_no": "INTEGER NOT NULL DEFAULT 0"},
    )
    store._ensure_columns(
        conn,
        "workflow_definitions",
        {
            "current_revision_no": "INTEGER NOT NULL DEFAULT 0",
            "edit_version": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    store._ensure_columns(
        conn,
        "workflow_definition_revisions",
        {
            "source": "TEXT NOT NULL DEFAULT 'edit'",
            "version_hash": "TEXT NOT NULL DEFAULT ''",
            "task_refresh_policy": "TEXT NOT NULL DEFAULT 'auto'",
        },
    )
    store._ensure_columns(
        conn,
        "skill_prompts",
        {"current_revision_no": "INTEGER NOT NULL DEFAULT 0"},
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_current
        ON workflow_artifacts(workflow_key, task_key, is_current, updated_at DESC)
        """
    )


def apply_followup_schema(store: Any, conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_users (
          user_id TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'active',
          created_by TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_groups (
          group_key TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          created_by TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_group_memberships (
          user_id TEXT PRIMARY KEY,
          group_key TEXT NOT NULL REFERENCES access_groups(group_key),
          updated_by TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_group_memberships_group
        ON user_group_memberships(group_key, user_id)
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO access_users (user_id, created_by)
        SELECT user_id, updated_by FROM user_group_memberships
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_access_config (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          password_hash TEXT NOT NULL,
          session_secret TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    scoped_tables = ("knowledge_bases", "mcp_services", "openapi_services", "code_repositories")
    for table in scoped_tables:
        store._ensure_columns(
            conn,
            table,
            {
                "owner_group_key": "TEXT NOT NULL DEFAULT ''",
                "visibility": "TEXT NOT NULL DEFAULT 'group'",
            },
        )
    store._ensure_columns(conn, "code_repositories", {"created_by": "TEXT NOT NULL DEFAULT ''"})
    for table in scoped_tables:
        conn.execute(
            f"""
            UPDATE {table}
            SET owner_group_key = COALESCE(
                  (SELECT membership.group_key
                   FROM user_group_memberships membership
                   WHERE membership.user_id = {table}.created_by),
                  ''
                ),
                visibility = 'group'
            WHERE owner_group_key = ''
            """
        )

    scoped_columns = {
        "project_profiles": {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'group'",
        },
        "memory_blocks": {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'group'",
        },
        "workflow_definitions": {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'group'",
        },
        "workflow_artifacts": {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'group'",
        },
        "scripts": {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'group'",
        },
    }
    for table, columns in scoped_columns.items():
        store._ensure_columns(conn, table, columns)
    for table in ("project_profiles", "memory_blocks", "workflow_definitions", "scripts"):
        conn.execute(
            f"""
            UPDATE {table}
            SET owner_group_key = COALESCE(
              (SELECT membership.group_key
               FROM user_group_memberships membership
               WHERE membership.user_id = {table}.created_by),
              ''
            ), visibility = 'group'
            WHERE owner_group_key = ''
            """
        )

    store._ensure_columns(conn, "documents", {"owner_group_key": "TEXT NOT NULL DEFAULT ''"})
    conn.execute(
        """
        UPDATE documents
        SET owner_group_key = COALESCE(
          (SELECT membership.group_key
           FROM user_group_memberships membership
           WHERE membership.user_id = documents.owner_user),
          ''
        )
        WHERE owner_group_key = ''
        """
    )
    store._ensure_columns(conn, "workflow_runs", {"owner_group_key": "TEXT NOT NULL DEFAULT ''"})
    conn.execute(
        """
        UPDATE workflow_runs
        SET owner_group_key = COALESCE(
          (SELECT definition.owner_group_key
           FROM workflow_definitions definition
           WHERE definition.workflow_key = workflow_runs.workflow_key),
          ''
        )
        WHERE owner_group_key = ''
        """
    )
    conn.execute(
        """
        UPDATE workflow_artifacts
        SET owner_group_key = COALESCE(
          (SELECT run.owner_group_key FROM workflow_runs run
           WHERE run.run_id = workflow_artifacts.run_id),
          (SELECT definition.owner_group_key FROM workflow_definitions definition
           WHERE definition.workflow_key = workflow_artifacts.workflow_key),
          ''
        ), visibility = 'group'
        WHERE owner_group_key = ''
        """
    )
    store._ensure_columns(conn, "script_runs", {"owner_group_key": "TEXT NOT NULL DEFAULT ''"})
    conn.execute(
        """
        UPDATE script_runs
        SET owner_group_key = COALESCE(
          (SELECT script.owner_group_key FROM scripts script
           WHERE script.script_key = script_runs.script_key),
          ''
        )
        WHERE owner_group_key = ''
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metamcp_tool_settings (
          tool_name TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'enabled',
          updated_by TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    store._ensure_columns(
        conn,
        "model_evaluation_runs",
        {
            "max_samples": "INTEGER NOT NULL DEFAULT 64",
            "sampling_mode": "TEXT NOT NULL DEFAULT 'head'",
            "sample_seed": "INTEGER NOT NULL DEFAULT 42",
            "runtime": "TEXT NOT NULL DEFAULT 'docker'",
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.execute(
        """
        UPDATE model_evaluation_runs
        SET owner_group_key = COALESCE(
          (SELECT membership.group_key
           FROM user_group_memberships membership
           WHERE membership.user_id = model_evaluation_runs.created_by),
          ''
        )
        WHERE owner_group_key = ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_evaluation_executions (
          execution_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES model_evaluation_runs(run_id) ON DELETE CASCADE,
          runner_key TEXT NOT NULL,
          datasets_json TEXT NOT NULL,
          image TEXT NOT NULL,
          container_id TEXT,
          status TEXT NOT NULL,
          progress_message TEXT NOT NULL DEFAULT '',
          result_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          work_dir TEXT NOT NULL,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_model_evaluation_executions_run
        ON model_evaluation_executions(run_id, created_at ASC)
        """
    )
    store._ensure_columns(
        conn,
        "backend_targets",
        {"backend_kb_id": "TEXT", "last_error": "TEXT"},
    )
    store._ensure_columns(
        conn,
        "sync_states",
        {
            "backend_status": "TEXT",
            "chunk_count": "INTEGER",
            "progress": "REAL",
            "backend_error": "TEXT",
        },
    )

    store._ensure_columns(
        conn,
        "knowledge_bases",
        {
            "default_backend_slug": "TEXT",
            "default_agent_id": "TEXT",
            "sync_on_upload": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    store._ensure_columns(
        conn,
        "documents",
        {
            "source_type": "TEXT NOT NULL DEFAULT 'manual'",
            "source_repo_key": "TEXT NOT NULL DEFAULT ''",
        },
    )
    migrate_knowledge_folders(store, conn)
    store._ensure_columns(
        conn,
        "backends",
        {"rerank_model_id": "TEXT"},
    )

    store.governance._migrate_tool_call_logs_nullable_profile(conn)
    store._ensure_columns(
        conn,
        "tool_call_logs",
        {
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
            "failure_stage": "TEXT",
            "failure_owner": "TEXT",
            "error_type": "TEXT",
            "resource_type": "TEXT",
            "resource_key": "TEXT",
            "request_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "response_summary_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    store._ensure_columns(
        conn,
        "agent_runs",
        {
            "actor": "TEXT NOT NULL DEFAULT ''",
            "owner_group_key": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.execute(
        """
        UPDATE tool_call_logs
        SET owner_group_key = COALESCE(
          (SELECT membership.group_key
           FROM user_group_memberships membership
           WHERE membership.user_id = tool_call_logs.actor),
          ''
        )
        WHERE owner_group_key = ''
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_call_logs_owner_group "
        "ON tool_call_logs(owner_group_key, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_owner_group "
        "ON agent_runs(owner_group_key, created_at DESC)"
    )
    store._ensure_columns(
        conn,
        "code_repositories",
        {
            "category_key": "TEXT NOT NULL DEFAULT ''",
            "sync_interval_minutes": "INTEGER NOT NULL DEFAULT 60",
            "auto_understand": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    store._ensure_columns(
        conn,
        "knowledge_sync_config",
        {
            "ua_git_url": "TEXT NOT NULL DEFAULT ''",
            "ua_plugin_update_cron": "TEXT NOT NULL DEFAULT '0 3 * * 0'",
            "claude_mem_git_url": "TEXT NOT NULL DEFAULT ''",
            "claude_mem_plugin_update_cron": "TEXT NOT NULL DEFAULT '30 3 * * 0'",
        },
    )
    store._ensure_columns(
        conn,
        "knowledge_sync_config",
        {
            "code_sync_cron": "TEXT NOT NULL DEFAULT '*/30 * * * *'",
            "understand_cron": "TEXT NOT NULL DEFAULT '0 2 * * *'",
            "doc_sync_cron": "TEXT NOT NULL DEFAULT '*/30 * * * *'",
            "workflow_start_time": "TEXT NOT NULL DEFAULT '22:00'",
            "workflow_stop_time": "TEXT NOT NULL DEFAULT '07:00'",
            "workflow_max_runs": "INTEGER NOT NULL DEFAULT 0",
            "workflow_max_concurrent_runs": "INTEGER NOT NULL DEFAULT 4",
            "workflow_max_concurrent_runs_per_workflow": "INTEGER NOT NULL DEFAULT 2",
            "workflow_max_runtime_minutes": "INTEGER NOT NULL DEFAULT 30",
            "workflow_task_rerun_days": "INTEGER NOT NULL DEFAULT 30",
            "log_retention_days": "INTEGER NOT NULL DEFAULT 180",
            "mcp_timeout_seconds": "INTEGER NOT NULL DEFAULT 150",
            "understand_timeout_minutes": "INTEGER NOT NULL DEFAULT 120",
            "artifact_search_cache_ttl_hours": "INTEGER NOT NULL DEFAULT 8",
        },
    )
    # Workflow scheduling moved from a single global cron to a daily
    # window (start/stop). Drop the legacy per-workflow schedule column
    # and the superseded workflow_cron config column on existing DBs.
    store._drop_column(conn, "knowledge_sync_config", "workflow_cron")
    store._drop_column(conn, "workflow_definitions", "schedule_json")
    store._drop_column(conn, "workflow_definitions", "manifest_json")
    store._drop_column(conn, "workflow_runs", "stdout_path")
    store._drop_column(conn, "workflow_runs", "stderr_path")
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
          input_schema_json TEXT NOT NULL DEFAULT '{"type":"object","properties":{},"additionalProperties":true}',
          output_schema_json TEXT,
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
    store._ensure_columns(conn, "workflow_tasks", {"type": "TEXT NOT NULL DEFAULT ''", "priority_flag": "TEXT"})


def migrate_knowledge_folders(store: Any, conn: sqlite3.Connection) -> None:
    """原子创建目录存储并回填旧文档位置。"""
    store._ensure_columns(conn, "document_kbs", {"folder_id": "INTEGER"})
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_archive_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
          parent_id INTEGER REFERENCES knowledge_archive_entries(id) ON DELETE CASCADE,
          parent_folder_id INTEGER REFERENCES knowledge_folders(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (kind IN ('zip', 'folder', 'document')),
          name TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          doc_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK ((parent_id IS NULL) != (parent_folder_id IS NULL))
        )
        """
    )
    store._ensure_columns(
        conn,
        "document_kbs",
        {
            "archive_entry_id": (
                "INTEGER REFERENCES knowledge_archive_entries(id) ON DELETE SET NULL"
            )
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_folders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
          parent_id INTEGER REFERENCES knowledge_folders(id),
          name TEXT NOT NULL,
          is_root INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (kb_id, parent_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_folders_root
        ON knowledge_folders(kb_id)
        WHERE parent_id IS NULL AND is_root = 1
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_folders_parent_name
        ON knowledge_folders(kb_id, parent_id, name)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backend_folder_mappings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
          backend_slug TEXT NOT NULL,
          folder_id INTEGER NOT NULL REFERENCES knowledge_folders(id) ON DELETE CASCADE,
          backend_folder_id TEXT NOT NULL,
          path_snapshot TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (kb_id, backend_slug, folder_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_backend_folder_mappings_lookup
        ON backend_folder_mappings(kb_id, backend_slug, folder_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_kbs_folder_status
        ON document_kbs(kb_id, folder_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_archive_entries_parent
        ON knowledge_archive_entries(kb_id, parent_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_archive_entries_folder
        ON knowledge_archive_entries(kb_id, parent_folder_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_kbs_archive_entry
        ON document_kbs(archive_entry_id)
        """
    )
    kb_rows = conn.execute(
        "SELECT id FROM knowledge_bases WHERE status = 'active' ORDER BY id"
    ).fetchall()
    for row in kb_rows:
        store.folders.ensure_root_folder(int(row["id"]), conn=conn)
    conn.execute(
        """
        UPDATE document_kbs
        SET folder_id = (
          SELECT folder.id
          FROM knowledge_folders folder
          WHERE folder.kb_id = document_kbs.kb_id
            AND folder.parent_id IS NULL
            AND folder.is_root = 1
        )
        WHERE status = 'active' AND folder_id IS NULL
        """
    )
