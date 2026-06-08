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

export interface ProfileSourceRule {
  source_type: string
  source_key: string
  effect: 'allow' | 'deny'
}

export interface ProfileResourceRule {
  resource_type: string
  resource_key: string
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
  status: string
  last_synced_at: string | null
  last_error: string | null
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
  role: string
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
  role: string
}

export interface KbMember {
  linux_user: string
  role: string
  created_at: string
  updated_at: string
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
  code_sync_enabled: boolean
  code_sync_cron: string
  ua_git_url: string
}

export interface SchedulerStatus {
  running: boolean
  cron: string
  jobs: {
    repo_key: string
    next_run_at: string | null
  }[]
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
