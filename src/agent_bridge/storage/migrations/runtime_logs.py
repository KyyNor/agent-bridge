"""运行审计独立数据库的 schema 与兼容迁移。"""

from __future__ import annotations

import sqlite3


RUNTIME_LOG_SCHEMA = """
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
  failure_stage TEXT,
  failure_owner TEXT,
  error_type TEXT,
  resource_type TEXT,
  resource_key TEXT,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  response_summary_json TEXT NOT NULL DEFAULT '{}',
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_profile ON tool_call_logs(profile_key);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_source ON tool_call_logs(source_type, source_key);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_failure ON tool_call_logs(failure_owner, failure_stage, error_type);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_resource ON tool_call_logs(resource_type, resource_key);

CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_key TEXT NOT NULL UNIQUE,
  agent_name TEXT NOT NULL,
  backend_key TEXT,
  profile_key TEXT,
  workflow_key TEXT,
  workflow_run_id TEXT,
  session_id TEXT,
  cwd TEXT,
  model TEXT,
  ok INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '',
  error TEXT,
  duration_ms INTEGER,
  cost_usd REAL,
  num_turns INTEGER,
  prompt TEXT NOT NULL,
  output_schema_json TEXT,
  result_json TEXT,
  events_json TEXT NOT NULL DEFAULT '[]',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_created_at ON agent_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_profile ON agent_runs(profile_key);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_name ON agent_runs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow_run_id ON agent_runs(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_backend_key ON agent_runs(backend_key);
"""


def apply_runtime_log_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(RUNTIME_LOG_SCHEMA)
