from __future__ import annotations


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  default_backend_slug TEXT,
  default_agent_id TEXT,
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
CREATE TABLE IF NOT EXISTS backends (
  slug TEXT PRIMARY KEY,
  backend_type TEXT NOT NULL,
  base_url TEXT,
  api_key TEXT,
  timeout INTEGER NOT NULL DEFAULT 120,
  embedding_model_id TEXT,
  summary_model_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS profile_resource_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  resource_type TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  retrieval_backend_slug TEXT,
  retrieval_agent_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, resource_type, resource_key)
);
CREATE INDEX IF NOT EXISTS idx_profile_resource_rules_profile ON profile_resource_rules(profile_key);
CREATE INDEX IF NOT EXISTS idx_profile_resource_rules_resource ON profile_resource_rules(resource_type, resource_key);
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
"""

CODEGRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  git_url TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  auth_ref TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  category_key TEXT NOT NULL DEFAULT '',
  sync_interval_minutes INTEGER NOT NULL DEFAULT 60,
  auto_understand INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  local_path TEXT,
  last_commit TEXT,
  last_synced_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS codegraph_sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL REFERENCES code_repositories(repo_key) ON DELETE CASCADE,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  error TEXT,
  duration_ms INTEGER,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS codegraph_index_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL REFERENCES code_repositories(repo_key) ON DELETE CASCADE,
  item_type TEXT NOT NULL,
  path TEXT NOT NULL,
  symbol TEXT,
  language TEXT,
  line_start INTEGER,
  line_end INTEGER,
  content TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_codegraph_index_repo_path ON codegraph_index_items(repo_key, path);
CREATE INDEX IF NOT EXISTS idx_codegraph_index_symbol ON codegraph_index_items(repo_key, symbol);
CREATE TABLE IF NOT EXISTS code_repo_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS knowledge_sync_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  code_sync_cron TEXT NOT NULL DEFAULT '*/30 * * * *',
  ua_git_url TEXT NOT NULL DEFAULT '',
  understand_cron TEXT NOT NULL DEFAULT '0 2 * * *',
  doc_sync_cron TEXT NOT NULL DEFAULT '*/30 * * * *',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
