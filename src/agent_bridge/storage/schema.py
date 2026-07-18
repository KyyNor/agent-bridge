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
CREATE TABLE IF NOT EXISTS knowledge_folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  parent_id INTEGER REFERENCES knowledge_folders(id),
  name TEXT NOT NULL,
  is_root INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kb_id, parent_id, name),
  CHECK ((is_root = 1 AND parent_id IS NULL) OR (is_root = 0 AND parent_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_folders_root
  ON knowledge_folders(kb_id)
  WHERE parent_id IS NULL AND is_root = 1;
CREATE INDEX IF NOT EXISTS idx_knowledge_folders_parent_name
  ON knowledge_folders(kb_id, parent_id, name);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_user TEXT NOT NULL,
  current_version_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_repo_key TEXT NOT NULL DEFAULT ''
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
);
CREATE INDEX IF NOT EXISTS idx_knowledge_archive_entries_parent
  ON knowledge_archive_entries(kb_id, parent_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_archive_entries_folder
  ON knowledge_archive_entries(kb_id, parent_folder_id, status);
CREATE TABLE IF NOT EXISTS document_kbs (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  folder_id INTEGER REFERENCES knowledge_folders(id),
  archive_entry_id INTEGER REFERENCES knowledge_archive_entries(id) ON DELETE SET NULL,
  added_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  PRIMARY KEY (doc_id, kb_id)
);
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
);
CREATE INDEX IF NOT EXISTS idx_backend_folder_mappings_lookup
  ON backend_folder_mappings(kb_id, backend_slug, folder_id, status);
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
CREATE TABLE IF NOT EXISTS openapi_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  spec_url TEXT NOT NULL DEFAULT '',
  spec_content TEXT NOT NULL DEFAULT '',
  auth_config_json TEXT NOT NULL DEFAULT '{}',
  headers_json TEXT NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'enabled',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_imported_at TEXT,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS openapi_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL REFERENCES openapi_services(service_key) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  operation_id TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  request_mapping_json TEXT NOT NULL DEFAULT '{}',
  response_schema_json TEXT NOT NULL DEFAULT '{}',
  tool_type TEXT NOT NULL DEFAULT 'unconfigured',
  tags_json TEXT NOT NULL DEFAULT '[]',
  examples_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (service_key, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_openapi_tools_service_status ON openapi_tools(service_key, status);
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
CREATE TABLE IF NOT EXISTS profile_pin_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  service_key TEXT NOT NULL,
  tool_type TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, service_key, tool_type)
);
CREATE INDEX IF NOT EXISTS idx_profile_pin_rules_profile ON profile_pin_rules(profile_key);
CREATE INDEX IF NOT EXISTS idx_profile_pin_rules_service_type ON profile_pin_rules(service_key, tool_type);
CREATE TABLE IF NOT EXISTS profile_pin_settings (
  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  mode TEXT NOT NULL DEFAULT 'disabled',
  ratio_percent INTEGER,
  count INTEGER,
  auto_cache_json TEXT,
  auto_cache_computed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
);
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
);
CREATE TABLE IF NOT EXISTS profile_memory_bindings (
  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  block_key TEXT REFERENCES memory_blocks(block_key) ON DELETE SET NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_status ON memory_blocks(status);
CREATE INDEX IF NOT EXISTS idx_profile_memory_bindings_block ON profile_memory_bindings(block_key);
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
CREATE TABLE IF NOT EXISTS skill_prompts (
  skill_name TEXT PRIMARY KEY,
  prompt TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
);
CREATE INDEX IF NOT EXISTS idx_scripts_owner ON scripts(owner_type, owner_key);
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
);
CREATE INDEX IF NOT EXISTS idx_script_runs_script ON script_runs(script_key, created_at DESC);

CREATE TABLE IF NOT EXISTS script_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  script_key TEXT NOT NULL,
  revision_no INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (script_key, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_script_revisions_key ON script_revisions(script_key, revision_no DESC);

CREATE TABLE IF NOT EXISTS skill_prompt_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_name TEXT NOT NULL,
  revision_no INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (skill_name, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_revisions_name ON skill_prompt_revisions(skill_name, revision_no DESC);
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
CREATE TABLE IF NOT EXISTS kb_repo_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  repo_key TEXT NOT NULL REFERENCES code_repositories(repo_key) ON DELETE CASCADE,
  include_suffixes_json TEXT NOT NULL DEFAULT '[".md",".txt"]',
  status TEXT NOT NULL DEFAULT 'active',
  last_synced_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kb_id, repo_key)
);
CREATE INDEX IF NOT EXISTS idx_kb_repo_sources_kb ON kb_repo_sources(kb_id);
CREATE INDEX IF NOT EXISTS idx_kb_repo_sources_repo ON kb_repo_sources(repo_key);
CREATE TABLE IF NOT EXISTS knowledge_sync_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  code_sync_cron TEXT NOT NULL DEFAULT '*/30 * * * *',
  ua_git_url TEXT NOT NULL DEFAULT '',
  ua_plugin_update_cron TEXT NOT NULL DEFAULT '0 3 * * 0',
  claude_mem_git_url TEXT NOT NULL DEFAULT '',
  claude_mem_plugin_update_cron TEXT NOT NULL DEFAULT '30 3 * * 0',
  understand_cron TEXT NOT NULL DEFAULT '0 2 * * *',
  doc_sync_cron TEXT NOT NULL DEFAULT '*/30 * * * *',
  workflow_start_time TEXT NOT NULL DEFAULT '22:00',
  workflow_stop_time TEXT NOT NULL DEFAULT '07:00',
  workflow_max_runs INTEGER NOT NULL DEFAULT 0,
  workflow_max_runtime_minutes INTEGER NOT NULL DEFAULT 30,
  workflow_task_rerun_days INTEGER NOT NULL DEFAULT 30,
  log_retention_days INTEGER NOT NULL DEFAULT 180,
  mcp_timeout_seconds INTEGER NOT NULL DEFAULT 150,
  understand_timeout_minutes INTEGER NOT NULL DEFAULT 120,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE RESTRICT,
  workflow_js TEXT NOT NULL DEFAULT '',
  definition_json TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  workflow_type TEXT NOT NULL DEFAULT 'operation',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_profile ON workflow_definitions(profile_key);

CREATE TABLE IF NOT EXISTS workflow_tasks (
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
  UNIQUE (workflow_key, task_key, task_version)
);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pick
  ON workflow_tasks(workflow_key, status, lease_expires_at, id);

CREATE TABLE IF NOT EXISTS workflow_task_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id TEXT NOT NULL UNIQUE,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  filename TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  tasks_json TEXT NOT NULL DEFAULT '[]',
  preview_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'previewed',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_task_imports_expiry
  ON workflow_task_imports(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_workflow_task_imports_workflow
  ON workflow_task_imports(workflow_key, created_at DESC);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  profile_key TEXT NOT NULL,
  task_key TEXT,
  status TEXT NOT NULL,
  temp_dir TEXT NOT NULL DEFAULT '',
  definition_snapshot_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  exit_code INTEGER,
  stdout_path TEXT,
  stderr_path TEXT,
  error TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_key, started_at DESC);

CREATE TABLE IF NOT EXISTS workflow_node_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  condition_results_json TEXT NOT NULL DEFAULT '[]',
  output_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  agent_run_key TEXT,
  script_run_id TEXT,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run ON workflow_node_runs(run_id, id);

CREATE TABLE IF NOT EXISTS workflow_run_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  workflow_key TEXT NOT NULL,
  task_key TEXT,
  level TEXT NOT NULL DEFAULT 'info',
  stage TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_run_logs_run ON workflow_run_logs(run_id, id);

CREATE TABLE IF NOT EXISTS workflow_artifacts (
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
);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_profile ON workflow_artifacts(profile_key);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_path ON workflow_artifacts(path);

CREATE TABLE IF NOT EXISTS workflow_definition_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_key TEXT NOT NULL,
  revision_no INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'edit',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (workflow_key, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_wf_def_revisions_key ON workflow_definition_revisions(workflow_key, revision_no DESC);

CREATE TABLE IF NOT EXISTS workflow_definition_imports (
  import_id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  filename TEXT NOT NULL,
  source_workflow_key TEXT NOT NULL,
  target_workflow_key TEXT NOT NULL,
  operation TEXT NOT NULL,
  workflow_json TEXT NOT NULL,
  target_revision_no INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'previewed',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_definition_imports_expiry
  ON workflow_definition_imports(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_wf_definition_imports_actor
  ON workflow_definition_imports(actor, created_at DESC);
"""
