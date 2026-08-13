export type ResourceVisibility = 'group' | 'shared'

export interface ResourceScopeFields {
  owner_group_key: string
  visibility: ResourceVisibility
}

export interface AccessActorContext {
  user_id: string
  group_key: string | null
  group_name: string | null
  is_maintenance_admin: boolean
}

export interface AdminAccessStatus {
  configured: boolean
  active: boolean
  initialized?: boolean
  subject_user_id: string | null
}

export interface AccessGroup {
  group_key: string
  name: string
  description: string
  status: string
  member_count: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface AccessUser {
  user_id: string
  status: string
  group_key: string | null
  group_name: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface UserGroupMembership {
  user_id: string
  group_key: string
  group_name: string
  updated_by: string
  created_at: string
  updated_at: string
}

export interface McpService extends ResourceScopeFields {
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
  tool_count?: number
  source_type?: 'mcp_service'
  edit_token?: string
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

export interface TopLevelMcpTool {
  name: string
  title: string
  description: string
  kind: 'artifacts' | 'direct_builtin' | 'workflow'
  service_key: string | null
  tool_name: string | null
  status: 'enabled' | 'disabled'
  updated_by: string | null
  updated_at: string | null
}

export interface OpenApiService extends ResourceScopeFields {
  source_type?: 'openapi_service'
  service_key: string
  name: string
  base_url: string
  spec_url: string
  spec_content?: string
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
  tool_count?: number
  edit_token?: string
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

export interface CapabilityToolSummary {
  source_type: 'mcp_service' | 'openapi_service'
  service_key: string
  service_name: string
  tool_name: string
  display_name: string
  description: string
  tool_type: string
  tags: string[]
  status?: string
  operation_id?: string | null
  method?: string | null
  path?: string | null
}

export interface CapabilityToolPage {
  items: CapabilityToolSummary[]
  total: number
  limit: number
  offset: number
  counts: Record<string, number>
}

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
  edit_token?: string
  rules_edit_token?: string
  resources_edit_token?: string
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
  edit_token: string
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

export interface MemoryDashboardStatus {
  success?: boolean
  running: boolean
  url: string | null
  pid?: number | null
  port?: number | null
  started_at?: number | string | null
  error?: string | null
}

export type WorkflowNodeType = 'get_task' | 'agent' | 'script' | 'output'
export type WorkflowType = 'operation' | 'summary'
export type ConditionOperator = 'equals' | 'not_equals' | 'exists' | 'not_exists' | 'contains'

export interface WorkflowPosition { x: number; y: number }
export interface WorkflowCondition { field: string; operator: ConditionOperator; value?: unknown }
export interface WorkflowEdge { id: string; source: string; target: string; condition: WorkflowCondition | null; system_role?: 'summary_markdown_to_html' | null }
export interface GetTaskWorkflowNode { id: string; type: 'get_task'; name: string; position: WorkflowPosition; config: { on_empty?: 'terminate' | 'continue' } }
export interface AgentWorkflowNode { id: string; type: 'agent'; name: string; position: WorkflowPosition; config: { prompt: string; backend_key: string; mcp_enabled: boolean; skill_names: string[]; timeout_seconds?: number; result_mode: 'text' | 'json'; output_schema: Record<string, unknown> | null } }
export interface ScriptWorkflowNode { id: string; type: 'script'; name: string; position: WorkflowPosition; config: { script_key: string; params: Record<string, unknown>; timeout_seconds: number } }
export interface OutputWorkflowNode { id: string; type: 'output'; name: string; position: WorkflowPosition; config: { format: 'markdown' | 'html'; title: string; path: string; tags: string[]; prompt: string; backend_key: string; mcp_enabled: boolean; skill_names: string[]; timeout_seconds?: number; system_role?: 'summary_markdown' | 'summary_html' | null } }
export type WorkflowNode = GetTaskWorkflowNode | AgentWorkflowNode | ScriptWorkflowNode | OutputWorkflowNode
export interface WorkflowGraph { nodes: WorkflowNode[]; edges: WorkflowEdge[] }
export interface WorkflowValidationIssue { scope: 'workflow' | 'node' | 'edge'; id: string | null; field: string | null; code: string; message: string }

export type OnboardingTourStatus = 'completed' | 'skipped'

export interface OnboardingTourProgress {
  tour_key: string
  tour_version: number
  status: OnboardingTourStatus | null
  updated_at: string | null
  should_show: boolean
}
export type WorkflowValidationError = WorkflowValidationIssue
export interface WorkflowValidationResult { valid: boolean; errors: WorkflowValidationIssue[]; warnings: WorkflowValidationIssue[] }
export interface WorkflowDraft {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  definition: WorkflowGraph
  status: string
  workflow_type: WorkflowType
}

export interface WorkflowDefinition {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  /** Historical rows can have no structured graph. workflow_js is never executable. */
  definition: WorkflowGraph | null
  status: string
  workflow_type: WorkflowType
  created_by: string
  created_at: string
  updated_at: string
  edit_version: number
  revision_no?: number
  content_hash?: string
  task_refresh_policy?: WorkflowTaskRefreshPolicy
  tasks_marked_stale?: number
}

export type WorkflowTaskRefreshPolicy = 'auto' | 'defer'

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
  revision_no?: number
  edit_token: string
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
  /** Full body — present only when the caller asks for `full` content or does
   *  an exact-path lookup (see WorkflowTaskListParams / searchArtifacts). */
  content?: string
  created_at: string
  updated_at: string
  reusable?: boolean
  reuse_validation_reason?: string | null
  producer_node_id?: string | null
  producer_node_fingerprint?: string | null
  source_run_id?: string | null
  source_node_id?: string | null
  owner_group_key: string
  visibility: ResourceVisibility
}

export type WorkflowExecutionMode = 'normal' | 'incremental' | 'force_full'
export type WorkflowNodeAction = 'execute' | 'reuse'

export interface WorkflowNodePlan {
  node_id: string
  action: WorkflowNodeAction
  reason: string
  node_fingerprint: string
  source_run_id: string | null
  source_node_id: string | null
  runtime_deferred?: boolean
}

export interface WorkflowExecutionPlan {
  mode: WorkflowExecutionMode | string
  baseline_run_id: string | null
  affected_node_ids: string[]
  reusable_node_ids: string[]
  nodes: WorkflowNodePlan[]
  warnings: string[]
}

export interface WorkflowArtifactSearchResult {
  items: WorkflowArtifact[]
  total?: number
  limit?: number
  offset?: number
}

export interface WorkflowArtifactHistoryVersion {
  workflow_key: string
  task_key: string
  task_version: string
  is_current: boolean
  updated_at: string
  owner_group_key: string
  visibility: ResourceVisibility
  runs: WorkflowArtifactHistoryRun[]
}

export interface WorkflowArtifactHistoryRun {
  run_id: string
  is_current: boolean
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
  status: 'running' | 'completed' | 'no_task' | 'failed' | 'stopped' | string
  temp_dir: string
  exit_code: number | null
  stdout_path: string | null
  stderr_path: string | null
  error: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  definition_snapshot: WorkflowGraph
  input: Record<string, unknown>
  output: Record<string, unknown>
  workflow_revision_no?: number | null
  workflow_content_hash?: string | null
  task_version?: string
  execution_mode?: WorkflowExecutionMode | string
  execution_plan?: WorkflowExecutionPlan | Record<string, unknown>
  source_run_id?: string | null
  node_runs?: WorkflowNodeRun[]
}

export interface WorkflowRunSummary {
  run_id: string
  workflow_key: string
  profile_key: string
  task_key: string | null
  status: 'running' | 'completed' | 'no_task' | 'failed' | 'stopped' | string
  exit_code: number | null
  error: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  workflow_revision_no?: number | null
  workflow_content_hash?: string | null
  task_version?: string
  execution_mode?: WorkflowExecutionMode | string
  source_run_id?: string | null
}

export interface WorkflowRunSummaryPage {
  runs: WorkflowRunSummary[]
  total: number
  limit: number
  offset: number
}

export interface WorkflowRunOverview {
  workflow_key: string
  run_count: number
  latest_run: WorkflowRunSummary | null
  running_run: WorkflowRunSummary | null
  /** 基于 task 代表版本聚合的 workflow 状态（旧后端可能缺失，前端回退到 latest_run.status）。 */
  task_aggregated_status?: string
  task_total?: number
  task_completed?: number
  task_running?: number
  task_failed?: number
}

export interface CompletedWorkflowTopItem {
  workflow_key: string
  workflow_name: string
  completed_count: number
}

export interface CompletedWorkflowTopResponse {
  period_start: string
  period_end: string
  period_label: string
  items: CompletedWorkflowTopItem[]
}

export interface WorkflowNodeRun {
  node_id: string
  node_type: WorkflowNodeType
  status: 'pending' | 'running' | 'completed' | 'skipped' | 'failed' | 'cancelled' | 'warning'
  condition_results: Array<{ edge_id: string; field: string | null; operator: ConditionOperator | null; expected: unknown; actual: unknown; matched: boolean }>
  output: Record<string, unknown>
  error: string | null
  agent_run_key: string | null
  script_run_id: string | null
  started_at: string | null
  finished_at: string | null
  action?: WorkflowNodeAction | string | null
  reuse_reason?: string | null
  node_fingerprint?: string | null
  source_run_id?: string | null
  source_node_id?: string | null
  source_node_fingerprint?: string | null
  artifact_ids?: string[]
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
  priority_flag: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  /** 该任务是否已有任一版本的产物（按 task_key 从 workflow_artifacts 聚合派生）。 */
  has_artifacts: boolean
  /** 最新完成结果是否来自当前工作流执行语义之前的版本。 */
  needs_refresh: boolean
  last_completed_revision_no: number | null
}

export interface WorkflowTaskRefreshResult {
  workflow_key: string
  revision_no: number
  requested: number | null
  marked_stale: number
}

export interface WorkflowTasksResult {
  tasks: WorkflowTask[]
}

export interface WorkflowTaskImportRow {
  row_number: number
  task_key: string
  task_version: string
  type: string
  payload: Record<string, unknown>
  action: 'created' | 'updated' | 'skipped_running' | 'skipped_completed' | 'skipped_historical' | 'reopened_expired' | 'error'
  errors: string[]
}

export interface WorkflowTaskImportSummary {
  total_rows: number
  valid_rows: number
  invalid_rows: number
  created: number
  updated: number
  skipped_running: number
  skipped_completed: number
  skipped_historical: number
  reopened_expired: number
  /** 本次导入将取代的同 task_key 未运行旧版本数量。 */
  superseded: number
}

export interface WorkflowTaskImportPreview {
  import_id: string
  filename: string
  sheet_name: string
  expires_at: string
  can_confirm: boolean
  summary: WorkflowTaskImportSummary
  rows: WorkflowTaskImportRow[]
}

export interface WorkflowTaskImportResult {
  import_id: string
  created: number
  updated: number
  skipped_running: number
  skipped_completed: number
  skipped_historical: number
  reopened_expired: number
  /** 本次导入实际取代的同 task_key 未运行旧版本数量。 */
  superseded: number
}

/** Server-side query params for listing a workflow's tasks (筛选/搜索/排序). */
export interface WorkflowTaskListParams {
  status?: string
  type?: string
  search?: string
  /** Recognised: default | task_key_asc | task_key_desc | set_at_asc | set_at_desc | updated_at_desc */
  sort?: string
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
  /** Per-agent-run monotonic id used for SSE replay and UI deduplication. */
  event_id?: number
  created_at?: string
  agent_name?: string
  source?: string
  kind: string
  status?: string
  message?: string
  detail?: string
  tool_name?: string
  tool_use_id?: string
  call_id?: string
  input?: unknown
  output?: unknown
  input_preview?: string
  output_preview?: string
  detail_preview?: string
  input_payload_ref?: string
  output_payload_ref?: string
  detail_payload_ref?: string
  input_bytes?: number
  output_bytes?: number
  detail_bytes?: number
  input_content_type?: string
  output_content_type?: string
  detail_content_type?: string
  input_truncated?: boolean
  output_truncated?: boolean
  detail_truncated?: boolean
  input_storage_status?: string
  output_storage_status?: string
  detail_storage_status?: string
  started_at?: string
  finished_at?: string
  duration_ms?: number
  duration_status?: string
  stage_name?: string
  session_id?: string
  total_cost_usd?: number
  num_turns?: number
  /** Sub-agent attribution (feature 5). "main" for the top-level agent,
   *  "subagent" for events produced by a Task-spawned sub-agent. */
  agent_role?: 'main' | 'subagent'
  /** Task id of the sub-agent this event belongs to (absent for main agent). */
  task_id?: string
  /** Human-readable sub-agent description / label. */
  description?: string
  /** The tool_use_id of the Task call that spawned this sub-agent. */
  parent_tool_use_id?: string
  /** Sub-agent usage stats (tokens / tool_uses / duration) on progress/end. */
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number }
  summary?: string
  last_tool_name?: string
  [key: string]: unknown
}

export interface WorkflowSubagentTranscriptEvent {
  kind: string
  role: string
  created_at?: string
  uuid?: string
  agent_id?: string
  content?: unknown
  tool_use_id?: unknown
  tool_name?: unknown
  input?: unknown
  is_error?: unknown
  usage?: Record<string, unknown>
  [key: string]: unknown
}

export interface WorkflowSubagentTranscriptAgent {
  agent_id: string
  index?: number
  label?: string
  prompt_preview?: string
  result: unknown
  events: WorkflowSubagentTranscriptEvent[]
}

export interface WorkflowSubagentDetail {
  task_id: string
  transcript_dir: string | null
  workflow_subrun_id?: string | null
  task_output_status?: string | null
  task_output?: string | null
  agent_count?: number
  agents: WorkflowSubagentTranscriptAgent[]
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
  owner_group_key: string
  visibility: ResourceVisibility
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
  edit_token: string
  settings: ProfilePinSettings
  groups: ProfilePinRule[]
  tools: ProfilePinnedTool[]
}

export interface ProfileDocRender {
  profile_key: string
  edit_token: string
  markdown: string
  rendered_hash: string
  /** Stored manual notes — echoed so the edit textarea can be pre-filled. */
  manual_notes?: string
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

export interface ToolCallLogCounts {
  all: number
  success: number
  failed: number
  running: number
  error: number
  blocked: number
}

export interface ToolCallLogPage {
  items: ToolCallLog[]
  total: number
  limit: number
  offset: number
  counts: ToolCallLogCounts
}

export interface AgentRun {
  id: number
  run_key: string
  agent_name: string
  backend_key: string | null
  profile_key: string | null
  workflow_key: string | null
  workflow_run_id: string | null
  session_id: string | null
  cwd: string | null
  model: string | null
  ok: boolean
  status?: 'running' | 'completed' | 'failed' | 'stopped' | string
  error: string | null
  duration_ms: number | null
  cost_usd: number | null
  num_turns: number | null
  created_at: string
  // present on detail (get) only
  prompt?: string
  result?: unknown
  output_schema?: Record<string, unknown> | null
  // Agent run events share the same shape as workflow run events (both are
  // produced by agent_runtime/events.py); the unified WorkflowRunEvent type is
  // the canonical one. Sub-agent fields are simply absent for plain agent runs.
  events?: WorkflowRunEvent[]
}

export interface RunStopResponse {
  status: string
  run_key?: string
  run_id?: string
}

export interface AgentRunCounts {
  all: number
  success: number
  failed: number
  running: number
  stopped: number
}

export interface AgentRunPage {
  items: AgentRun[]
  total: number
  limit: number
  offset: number
  counts: AgentRunCounts
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
    workflow_type?: WorkflowType
    definition?: WorkflowGraph
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
    input_schema: Record<string, unknown>
    output_schema?: Record<string, unknown> | null
    status: string
    owner_type: string
    owner_key: string
  }
}

export interface BusinessLedgerDesignResult {
  summary: string
  notes?: string[]
  ledger: {
    ledger_key: string
    name: string
    description: string
    fields: Array<{
      field_key: string
      name: string
      field_type: BusinessLedgerFieldType
      required: boolean
      fuzzy_match: boolean
      agent_readable: boolean
      enum_values: string[]
    }>
  }
}

export interface DesignAgentResponse<T> {
  ok: boolean
  error: string | null
  run_key: string | null
  result: T | null
  agent_run?: AgentRun | null
}

export interface CodeRepository extends ResourceScopeFields {
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
  edit_token?: string
}

export interface KbRepoSource {
  id: number
  kb_id: number
  repo_key: string
  repo_name: string
  include_suffixes: string[]
  status: string
  last_synced_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
  doc_count: number
}

export interface KbRepoSourceSyncResult {
  kb_slug: string
  repo_key: string
  added: number
  removed: number
  updated: number
  unchanged: number
}

export interface TestCloneResult {
  success: boolean
  message: string
}

export interface KnowledgeBaseSummary extends ResourceScopeFields {
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
  edit_token?: string
  backend_targets: { id: number; kb_id: number; slug: string; backend_type: string; backend_kb_id: string | null; config_json: string; status: string; created_at: string; updated_at: string }[]
  document_count: number
  sync_failed_count: number
}

export interface KnowledgeBase extends ResourceScopeFields {
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
  edit_token?: string
}

export interface FolderCounts {
  /** Number of directories in the subtree, including the selected directory. */
  directory_count?: number
  /** Backward-compatible alias for directory_count in delete responses. */
  folder_count?: number
  /** Number of active files in the subtree. */
  file_count?: number
  /** Number of active files placed directly in this directory. */
  direct_file_count?: number
  /** Number of active files in this directory and all descendants. */
  descendant_file_count?: number
  /** Number of descendant directories, excluding this directory. */
  descendant_folder_count?: number
}

export interface KnowledgeFolder extends FolderCounts {
  id: number
  kb_id: number
  parent_id: number | null
  name: string
  is_root: boolean
  path: string
  created_at: string
  updated_at: string
}

export interface KnowledgeBrowseContext {
  kind: 'folder' | 'zip'
  id: number
  name: string
  relative_path: string
  parent_id: number | null
  parent_folder_id: number | null
  archive_entry_id: number | null
}

export interface KnowledgeBrowseFolderEntry {
  kind: 'folder'
  id: number
  name: string
  relative_path: string
  parent_id: number | null
  parent_folder_id: number | null
  archive_entry_id?: number | null
  child_count: number
}

export interface KnowledgeBrowseZipEntry {
  kind: 'zip'
  id: number
  name: string
  relative_path: string
  parent_id: number | null
  parent_folder_id: number | null
  archive_entry_id: number
  child_count: number
}

export interface KnowledgeBrowseDocumentEntry {
  kind: 'document'
  id: number
  doc_id: number
  name: string
  relative_path: string
  parent_id: number | null
  parent_folder_id: number | null
  slug: string
  title: string
  original_filename: string
  version: number
  version_no: number
  sync_status: string
  archive_entry_id: number | null
  status: string
}

export type KnowledgeBrowseEntry = KnowledgeBrowseFolderEntry | KnowledgeBrowseZipEntry | KnowledgeBrowseDocumentEntry

export interface KnowledgeBrowseResponse {
  context: KnowledgeBrowseContext
  parent: KnowledgeBrowseContext | null
  entries: KnowledgeBrowseEntry[]
}

export interface DocumentPlacement {
  doc_id: number
  kb_id: number
  folder_id: number
  folder_name: string | null
  folder_path: string | null
  document_kb_status: string
  slug?: string
  kb?: string
}

export interface FolderDeleteResult extends FolderCounts {
  folder_id: number
  requires_confirmation?: boolean
  deleted?: boolean
  directory_ids?: number[]
}

export interface Document {
  id: number
  slug: string
  title: string
  owner_user: string
  status: string
  current_version_no: number | null
  sync_status: string
  folder_id: number | null
  folder_path: string | null
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

export type UploadProgressCallback = (loaded: number, total: number) => void

export interface DocumentUploadSummary {
  source_filename: string
  source_type: 'zip'
  documents: DocumentDetail[]
  skipped: DocumentDetail[]
  uploaded_count: number
  skipped_count: number
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
  rerank_model_id: string | null
  runtime_status: string
  edit_token?: string
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
  edit_token?: string
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
  workflow_max_concurrent_runs: number
  workflow_max_concurrent_runs_per_workflow: number
  workflow_max_runtime_minutes: number
  workflow_task_rerun_days: number
  log_retention_days: number
  mcp_timeout_seconds: number
  understand_timeout_minutes: number
  artifact_search_cache_ttl_hours: number
  edit_token?: string
}

export interface AgentBackendConfig {
  slug: string
  type: string
  command: string | null
  model: string | null
}

export interface AvailableAgentBackend {
  slug: string
  display_name: string
  source: string
  capabilities: Partial<{
    supports_mcp: boolean
    supports_native_json_schema: boolean
    supports_skills: boolean
    supports_subagents: boolean
    supports_cost: boolean
    supports_turn_count: boolean
    supports_abort: boolean
    supports_partial_messages: boolean
  }>
}

export interface AgentRuntimeConfig {
  default_backend: string
  backends: AgentBackendConfig[]
  available_backends?: AvailableAgentBackend[]
  edit_token?: string
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
  edit_token: string
}

export interface ClaudeMemConfigUpdate {
  base_url?: string | null
  auth_token?: string | null
  api_key?: string | null
  model?: string | null
  clear_auth_token?: boolean
  clear_api_key?: boolean
  expected_edit_token?: string | null
}

export interface RetrievalProbeLlmConfig {
  base_url: string
  model: string
  api_key_set: boolean
  updated_at: string | null
  edit_token: string
}

export interface RetrievalProbeLlmConfigUpdate {
  base_url: string
  model: string
  api_key?: string | null
  clear_api_key?: boolean
  expected_edit_token?: string | null
}

export interface ModelEvaluationDataset {
  key: string
  label: string
  description: string
  dimension: 'general_knowledge' | 'math' | 'instruction_following' | 'code' | 'agent'
  dimension_label: string
  runner: 'opencompass' | 'code' | 'swebench'
  metric: string
  default_max_samples: number
}

export interface ModelEvaluationRuntimeStatus {
  configured: boolean
  runtime: 'docker'
  message: string
  docker: { available: boolean; version?: string; message: string }
  images: Record<string, { image: string; available: boolean }>
}

export interface ModelEvaluationModel {
  id: string
  label: string
}

export interface ModelEvaluationRun {
  run_id: string
  model_name: string
  base_url: string
  datasets: string[]
  max_samples: number
  sampling_mode: 'head' | 'random'
  sample_seed: number
  runtime: 'docker'
  status: 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'abandoned'
  progress_message: string
  result: { rows?: Record<string, string>[]; summary_found?: boolean; sample_manifests?: { dataset: string; mode: 'head' | 'random'; seed: number; source_indices: number[]; source_ids: Array<number | string> }[] }
  executions: ModelEvaluationExecution[]
  error: string | null
  output_ref: string
  created_by: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ModelEvaluationExecution {
  execution_id: string
  run_id: string
  runner_key: 'opencompass' | 'code' | 'swebench'
  datasets: string[]
  image: string
  container_id: string | null
  status: 'queued' | 'running' | 'completed' | 'failed' | 'abandoned'
  progress_message: string
  result: { rows?: Record<string, string>[]; sample_manifests?: { dataset: string; mode: 'head' | 'random'; seed: number; source_indices: number[]; source_ids: Array<number | string> }[] }
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
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
    running_run_counts?: Record<string, number>
    finished_today?: string[]
    max_concurrent_runs?: number
    max_concurrent_runs_per_workflow?: number
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
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown> | null
  is_builtin: boolean
  source?: 'default' | 'database'
  code?: string
  code_preview?: string
  revision_no?: number
  syntax_check?: SyntaxCheckResult
  edit_token: string
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

// --- Versioning / diff / syntax-check -------------------------------------

export type VersionedEntity = 'script' | 'workflow' | 'skill'

export type WorkflowRevisionSource = 'edit' | 'import' | 'restore'

export interface Revision {
  entity_key: string
  revision_no: number
  content_hash: string
  created_by: string
  created_at: string
  source?: WorkflowRevisionSource
  task_refresh_policy?: WorkflowTaskRefreshPolicy
  is_current?: boolean
}

export interface RevisionWithSnapshot extends Revision {
  snapshot: Record<string, unknown>
}

export interface SyntaxCheckError {
  line: number | null
  col: number | null
  msg: string
  text?: string | null
}

export interface SyntaxCheckResult {
  ok: boolean
  errors: SyntaxCheckError[]
}

export interface DiffText {
  format: 'unified'
  content: string
  from_label: string
  to_label: string
  identical: boolean
}

/** One segment of an optional token-level inline diff attached to a field
 * change when both values are long strings. Older clients ignore `inline`
 * and fall back to rendering `from`/`to` verbatim. */
export interface WorkflowStructuredChangeSegment {
  type: 'ctx' | 'add' | 'del'
  text: string
}

export interface WorkflowStructuredFieldChange {
  field: string
  from: unknown
  to: unknown
  inline?: WorkflowStructuredChangeSegment[]
}

export interface WorkflowStructuredNodeChange {
  id: string
  type?: string
  label?: string
  source?: string
  target?: string
  source_handle?: string | null
  target_handle?: string | null
  changes?: WorkflowStructuredFieldChange[]
}

export interface WorkflowStructuredDiff {
  nodes: { added: WorkflowStructuredNodeChange[]; removed: WorkflowStructuredNodeChange[]; changed: WorkflowStructuredNodeChange[] }
  edges: { added: WorkflowStructuredNodeChange[]; removed: WorkflowStructuredNodeChange[]; changed: WorkflowStructuredNodeChange[] }
  metadata: WorkflowStructuredFieldChange[]
  identical: boolean
}

export interface DiffResult {
  entity_type: VersionedEntity
  entity_key: string
  from_revision: number
  to_revision: number | null
  text: DiffText
  structured?: WorkflowStructuredDiff
}

export type BusinessLedgerFieldType = 'text' | 'number' | 'enum' | 'date' | 'datetime'
export interface BusinessLedgerField {
  field_key: string
  name: string
  field_type: BusinessLedgerFieldType
  required: boolean
  query_modes: string[]
  sortable: boolean
  agent_readable: boolean
  enum_values: string[]
}
export interface BusinessLedger {
  ledger_key: string
  name: string
  description: string
  fields: BusinessLedgerField[]
  record_count: number
  edit_token?: string
  owner_group_key: string
  visibility: ResourceVisibility
}
export interface BusinessLedgerRecords {
  ledger_key: string
  total: number
  limit: number
  offset: number
  items: Array<{ record_id: string; values: Record<string, unknown> }>
}

export type WorkflowImportTargetMode = 'auto' | 'new' | 'overwrite'

export interface WorkflowImportPreview {
  import_id: string
  filename: string
  expires_at: string
  source_workflow_key: string
  target_workflow_key: string
  operation: 'create' | 'overwrite'
  target_revision_no: number
  can_confirm: boolean
  workflow: WorkflowDefinition
  diff: DiffResult | null
}

export interface WorkflowRestoreResult extends WorkflowDefinition {
  restored_from_revision: number
  revision_created: boolean
  revision_source: WorkflowRevisionSource
}

export interface WorkflowImportResult extends WorkflowDefinition {
  import_id: string
  operation: 'create' | 'overwrite'
  revision_source: WorkflowRevisionSource
}
