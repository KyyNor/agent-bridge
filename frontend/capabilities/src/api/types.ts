export interface McpService {
  service_key: string
  name: string
  endpoint_url: string
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

export interface WorkflowDefinition {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  workflow_js: string
  manifest: {
    name: string
    nodes: Record<string, unknown>[]
    edges: Record<string, unknown>[]
    schemas: Record<string, unknown>
    [key: string]: unknown
  }
  status: string
  created_by: string
  created_at: string
  updated_at: string
}

export interface WorkflowArtifact {
  artifact_id: string
  workflow_key: string
  profile_key: string
  run_id: string
  task_key: string | null
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

export interface WorkflowArtifactDetail {
  artifact_id: string
  workflow_key: string
  profile_key: string
  run_id: string
  task_key: string | null
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
  understand_cron: string
  doc_sync_cron: string
  workflow_start_time: string
  workflow_stop_time: string
}

export interface SingleSchedulerStatus {
  running: boolean
  cron: string
  jobs: {
    repo_key: string
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
  doc_sync: SingleSchedulerStatus
  workflow: Omit<SingleSchedulerStatus, 'cron'> & {
    start_time: string
    stop_time: string
    in_window: boolean
    running_workflows?: string[]
    finished_today?: string[]
    max_concurrent_workflows?: number
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
