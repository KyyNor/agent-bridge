export interface McpService {
  service_key: string
  name: string
  endpoint_url: string
  headers?: Record<string, unknown>
  description: string
  tags: string[]
  status: string
  created_by: string
  created_at: string
  updated_at: string
  last_synced_at: string | null
  last_error: string | null
}

export interface McpTool {
  service_key: string
  tool_name: string
  display_name: string
  description: string
  input_schema: Record<string, unknown>
  tool_type: string
  tags: string[]
  examples: unknown[]
  status: string
}

export interface OpenApiService {
  source_type?: 'openapi_service'
  service_key: string
  name: string
  base_url: string
  spec_url: string
  spec_content: string
  auth_config?: Record<string, unknown>
  headers?: Record<string, unknown>
  description: string
  tags: string[]
  status: string
  created_by: string
  created_at: string
  updated_at: string
  last_imported_at: string | null
  last_error: string | null
}

export interface OpenApiTool extends McpTool {
  source_type?: 'openapi_service'
  operation_id: string
  method: string
  path: string
  request_mapping: Record<string, unknown>
  response_schema: Record<string, unknown>
}

export type CapabilityServiceSource =
  | (McpService & { source_type: 'mcp_service' })
  | (OpenApiService & { source_type: 'openapi_service' })

export type CapabilityTool = McpTool | OpenApiTool

export interface ExecuteCapabilityPayload {
  service: string
  tool_name: string
  params?: Record<string, unknown>
  profile_key?: string
}

export interface ExecuteCapabilityResult {
  service: string
  tool_name: string
  profile_key?: string | null
  result: unknown
  [key: string]: unknown
}

export interface OpenApiImportResult {
  service_key: string
  operations: OpenApiTool[]
}

export interface CatalogSource {
  source_type: string
  source_key: string
  name: string
  description: string
  status: string
  tags: string[]
}

export interface ProjectProfile {
  profile_key: string
  name: string
  description: string
  status: string
  allow_count: number
  deny_count: number
  rules?: ProfileSourceRule[]
  resource_rules?: ProfileResourceRule[]
}

export interface MemoryBlock {
  block_key: string
  name: string
  description: string
  status: string
  data_dir: string
  worker_base_url: string | null
  last_health?: Record<string, unknown>
  bound_profile_count?: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface ProfileMemoryBinding {
  profile_key: string
  block_key: string | null
  enabled: number | boolean
}

export interface MemorySearchResult {
  status: string
  block_key: string | null
  items: Array<{
    id: string
    summary: string
    content_preview: string
    score: number | null
    timestamp: string | null
    metadata: Record<string, unknown>
  }>
}

export interface MemoryTimelineResult {
  status: string
  block_key: string | null
  items: Array<{
    id: string
    event_type: string
    summary: string
    timestamp: string | null
    metadata: Record<string, unknown>
  }>
  next_cursor: string | null
}

export interface WorkflowDefinition {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  workflow_js: string
  status: string
  created_by: string
  created_at: string
  updated_at: string
}

export interface SkillPrompt {
  skill_name: string
  name: string
  description: string
  source: 'default' | 'database'
  prompt?: string
  default_prompt?: string
  prompt_preview?: string
  updated_at: string | null
  updated_by: string | null
}

export interface WorkflowArtifact {
  artifact_id: string
  workflow_key: string
  profile_key: string
  run_id: string
  task_key: string | null
  task_version: string
  is_current: boolean
  title: string
  path: string
  tags: string[]
  format: string
  summary: string
  snippet: string
  created_at: string
  updated_at: string
}

export interface WorkflowArtifactSearchResult {
  items: WorkflowArtifact[]
}

export interface WorkflowArtifactHistoryVersion {
  workflow_key: string
  task_key: string
  task_version: string
  is_current: boolean
  run_id: string
  updated_at: string
  artifacts: WorkflowArtifactHistoryItem[]
}

export interface WorkflowArtifactHistoryItem {
  artifact_id: string
  run_id: string
  title: string
  path: string
  tags: string[]
  format: string
  summary: string
  content: string
  created_at: string
  updated_at: string
}

export interface WorkflowArtifactHistoryResult {
  versions: WorkflowArtifactHistoryVersion[]
}

export interface ArtifactTreeNode {
  segment: string
  path: string
  children: ArtifactTreeNode[]
  artifacts: WorkflowArtifact[]
}

export interface WorkflowRun {
  run_id: string
  workflow_key: string
  profile_key: string
  task_key: string | null
  status: string
  temp_dir: string
  exit_code: number | null
  stdout_path: string | null
  stderr_path: string | null
  error: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
}

export interface WorkflowRunLog {
  run_id: string
  workflow_key: string
  task_key: string | null
  level: string
  stage: string
  message: string
  payload: Record<string, unknown>
  created_at: string
}

export interface WorkflowTask {
  workflow_key: string
  task_key: string
  task_version: string
  type: string
  payload: Record<string, unknown>
  status: string
  set_at: string
  lease_run_id: string | null
  lease_expires_at: string | null
  attempt_count: number
  last_error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface WorkflowTasksResult {
  tasks: WorkflowTask[]
}

export interface WorkflowClearResult {
  workflow_key: string
  cleared: boolean
  tasks_deleted: number
  runs_deleted: number
  logs_deleted: number
  artifacts_deleted: number
}

export interface WorkflowRunEvent {
  created_at?: string
  agent_name?: string
  source?: string
  kind: string
  status?: string
  message?: string
  tool_name?: string
  tool_use_id?: string
  session_id?: string
  total_cost_usd?: number
  num_turns?: number
  [key: string]: unknown
}

export interface WorkflowArtifactDetail {
  artifact_id: string
  workflow_key: string
  profile_key: string
  run_id: string
  task_key: string | null
  task_version: string
  is_current: boolean
  title: string
  path: string
  tags: string[]
  format: string
  summary: string
  content: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ProfileSourceRule {
  source_type: string
  source_key: string
  effect: 'allow' | 'deny'
}

export interface ProfileResourceRule {
  resource_type: string
  resource_key: string
  retrieval_backend_slug?: string | null
  retrieval_agent_id?: string | null
}

export interface ProfilePinRule {
  service_key: string
  tool_type: string
  source?: 'manual' | 'auto'
  calls?: number
}

export interface ProfilePinSettings {
  mode: 'disabled' | 'ratio' | 'count'
  ratio_percent: number | null
  count: number | null
  auto_cache_computed_at?: string | null
}

export interface ProfilePinSettingsUpdate {
  mode: 'disabled' | 'ratio' | 'count'
  ratio_percent: number | null
  count: number | null
}

export interface ProfilePinnedTool {
  generated_tool_name: string
  service_key: string
  service_name: string
  tool_name: string
  tool_type: string
  source: 'manual' | 'auto'
}

export interface ProfilePinPreview {
  profile_key: string
  settings: ProfilePinSettings
  groups: ProfilePinRule[]
  tools: ProfilePinnedTool[]
}

export interface ProfileDocRender {
  profile_key: string
  markdown: string
  rendered_hash: string
}

export interface ToolCallLog {
  id: number
  log_id: string
  actor: string
  profile_key: string | null
  entrypoint: string
  source_type: string | null
  source_key: string | null
  tool_name: string | null
  status: string
  error_message: string | null
  failure_stage: string | null
  failure_owner: string | null
  error_type: string | null
  resource_type: string | null
  resource_key: string | null
  duration_ms: number | null
  request_json?: string
  response_json?: string
  created_at: string
}

export interface ToolCallStats {
  dimensions: string[]
  items: Record<string, unknown>[]
}

export interface AgentRunEvent {
  created_at: string
  agent_name: string
  source: string
  kind: string
  status?: string
  message?: string
  tool_name?: string
  tool_use_id?: string
  session_id?: string
  total_cost_usd?: number
  num_turns?: number
  [key: string]: unknown
}

export interface AgentRun {
  id: number
  run_key: string
  agent_name: string
  profile_key: string | null
  workflow_key: string | null
  workflow_run_id: string | null
  session_id: string | null
  cwd: string | null
  model: string | null
  ok: boolean
  error: string | null
  duration_ms: number | null
  cost_usd: number | null
  num_turns: number | null
  created_at: string
  // present on detail (get) only
  prompt?: string
  result?: unknown
  output_schema?: Record<string, unknown> | null
  events?: AgentRunEvent[]
}

export interface WorkflowDesignResult {
  summary: string
  notes?: string[]
  workflow: {
    workflow_key: string
    name: string
    description: string
    profile_key: string
    status: string
    workflow_js: string
  }
}

export interface ScriptDesignResult {
  summary: string
  notes?: string[]
  script: {
    script_key: string
    name: string
    description: string
    language: string
    code: string
    status: string
    owner_type: string
    owner_key: string
  }
}

export interface DesignAgentResponse<T> {
  ok: boolean
  error: string | null
  run_key: string | null
  result: T | null
  agent_run?: AgentRun | null
}

export interface CodeRepository {
  repo_key: string
  name: string
  git_url: string
  branch: string
  description: string
  tags: string[]
  category_key: string
  sync_interval_minutes: number
  auto_understand: boolean
  status: string
  last_synced_at: string | null
  last_error: string | null
  has_auth_ref?: boolean
}

export interface TestCloneResult {
  success: boolean
  message: string
}

export interface KnowledgeBaseSummary {
  id: number
  slug: string
  name: string
  description: string
  status: string
  created_by: string
  created_at: string
  updated_at: string
  role?: string
  default_backend_slug?: string | null
  default_agent_id?: string | null
  backend_targets: { id: number; kb_id: number; slug: string; backend_type: string; backend_kb_id: string | null; config_json: string; status: string; created_at: string; updated_at: string }[]
  document_count: number
  sync_failed_count: number
}

export interface KnowledgeBase {
  id: number
  slug: string
  name: string
  description: string
  status: string
  created_by: string
  created_at: string
  updated_at: string
  role?: string
}

export interface Document {
  id: number
  slug: string
  title: string
  owner_user: string
  status: string
  current_version_no: number | null
  sync_status: string
}

export interface DocumentVersion {
  id: number
  doc_id: number
  version_no: number
  original_filename: string
  content_hash: string
  file_size: number
  mime_type: string
  created_by: string
  created_at: string
}

export interface SyncState {
  doc_id: number
  kb_id: number
  backend_slug: string
  backend_doc_id: string | null
  status: string
  backend_status: string | null
  chunk_count: number | null
  progress: number | null
  backend_error: string | null
  updated_at: string
}

export interface DocumentDetail {
  id: number
  slug: string
  title: string
  owner_user: string
  current_version_id: number | null
  status: string
  created_at: string
  updated_at: string
  deleted_at: string | null
  kb_slugs: string[]
  versions: DocumentVersion[]
  sync_states: SyncState[]
}

export interface SyncJob {
  id: number
  doc_id: number
  kb_id: number
  backend_slug: string
  operation: string
  version_id: number | null
  status: string
  error: string | null
  created_at: string
  updated_at: string
  doc_slug: string
  doc_title: string
  kb_slug: string
  kb_name: string
  version_no: number | null
}

export interface SearchResultChunk {
  chunk_id: string
  content: string
  document_name: string
  similarity: number
  dataset_id: string
}

export interface BackendInfo {
  slug: string
  backend_type: string
  status: string
  base_url: string | null
  api_key_set: boolean
  timeout: number
  embedding_model_id: string | null
  summary_model_id: string | null
  runtime_status: string
}

export interface BackendAgent {
  agent_id: string
  name: string
  agent_type: string | null
  is_builtin: boolean
}

export interface BackendAgentPreset {
  preset_id: string
  description: string
  config: Record<string, unknown>
}

export interface CodeGraphStatus {
  codegraph_installed: boolean
  message: string | null
}

export interface CodeGraphNode {
  path: string
  symbol: string
  kind: string
  language?: string | null
  line_start: number | null
  line_end: number | null
  snippet: string
  score: number | null
}

export interface CodeGraphExploreResult {
  repo: string
  query: string
  mcp_result: {
    is_error: boolean
    structured: unknown
    content: unknown[]
  }
}

export interface RepoOverview {
  repo_key: string
  name: string
  git_url: string
  branch: string
  status: string
  file_count: number
  symbol_count: number
  last_synced_at: string | null
}

export interface CodeRepoCategory {
  category_key: string
  name: string
  description: string
  created_at: string
  updated_at: string
}

export interface KnowledgeSyncConfig {
  code_sync_cron: string
  ua_git_url: string
  ua_plugin_update_cron: string
  claude_mem_git_url: string
  claude_mem_plugin_update_cron: string
  understand_cron: string
  doc_sync_cron: string
  workflow_start_time: string
  workflow_stop_time: string
  workflow_max_runs: number
  workflow_max_runtime_minutes: number
  workflow_task_rerun_days: number
  mcp_timeout_seconds: number
  understand_timeout_minutes: number
}

export interface ClaudeMemConfig {
  env_file_path: string
  config_file_path: string
  env_file_exists: boolean
  base_url: string
  model: string
  mode: string
  provider: string
  auth_method: string
  has_auth_token: boolean
  has_api_key: boolean
  has_secret: boolean
}

export interface ClaudeMemConfigUpdate {
  base_url?: string | null
  auth_token?: string | null
  api_key?: string | null
  model?: string | null
  clear_auth_token?: boolean
  clear_api_key?: boolean
}

export interface SingleSchedulerStatus {
  running: boolean
  cron: string
  jobs: {
    repo_key?: string
    plugin_key?: string
    next_run_at: string | null
    progress?: SchedulerRunProgress | null
  }[]
  current_run?: SchedulerRunProgress | null
  last_run?: SchedulerRunProgress | null
}

export interface SchedulerRunProgress {
  status: string
  started_at: string | null
  finished_at: string | null
  total?: number
  processed?: number
  succeeded?: number
  failed?: number
  current_job?: {
    id?: number
    operation?: string
    backend_slug?: string
    kb_slug?: string
    doc_slug?: string
    doc_title?: string
  } | null
  message?: string | null
  error?: string | null
}

export interface SchedulerStatus {
  code_sync: SingleSchedulerStatus
  understand: SingleSchedulerStatus
  plugin_update: SingleSchedulerStatus
  doc_sync: SingleSchedulerStatus
  workflow: Omit<SingleSchedulerStatus, 'cron'> & {
    start_time: string
    stop_time: string
    in_window: boolean
    running_workflows?: string[]
    finished_today?: string[]
    max_concurrent_workflows?: number
    max_runs?: number
    run_counts?: Record<string, number>
  }
}

export interface UAStatus {
  graph_exists: boolean
  graph_path: string | null
  stale: boolean
  node_count: number
  edge_count: number
  layer_count: number
  tour_count: number
  analyzed_at: string | null
  git_commit: string | null
  analyzed_files: number | null
  error: string | null
  dashboard_running: boolean
  dashboard_url: string | null
}

export interface UASummary {
  project_name: string | null
  description: string | null
  languages: string[]
  frameworks: string[]
  modules: { name: string; summary: string }[]
  key_nodes: { id: string; name: string; type: string; summary: string }[]
  tours: { title: string; description: string; step_count: number }[]
}

export interface UAAvailability {
  claude_installed: boolean
  ua_skill_available: boolean
  message: string | null
  ua_git_url_configured: boolean
}

export interface UAAnalyzeResult {
  success: boolean
  node_count: number
  edge_count: number
  error: string | null
  output: string | null
  duration_ms: number
}

export interface UADashboardStatus {
  running: boolean
  url?: string | null
  pid?: number | null
  started_at?: string | null
}

export interface ManagedScript {
  script_key: string
  name: string
  description: string
  language: string
  status: string
  owner_type: string
  owner_key: string
  content_hash: string
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
  code?: string
  code_preview?: string
}

export interface ScriptRun {
  run_id: string
  script_key: string
  run_type: string
  params: Record<string, unknown>
  result: Record<string, unknown>
  stdout?: string
  stderr?: string
  stdout_preview?: string
  stderr_preview?: string
  status: string
  exit_code: number | null
  error_message: string | null
  duration_ms: number
  created_by: string
  created_at: string
}

export interface ScriptRunListResult {
  runs: ScriptRun[]
}
