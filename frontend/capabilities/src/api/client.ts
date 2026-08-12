import type {
  BackendInfo,
  BackendAgent,
  BackendAgentPreset,
  CatalogSource,
  ClaudeMemConfig,
  ClaudeMemConfigUpdate,
  RetrievalProbeLlmConfig,
  RetrievalProbeLlmConfigUpdate,
  ModelEvaluationDataset,
  ModelEvaluationRuntimeStatus,
  ModelEvaluationModel,
  ModelEvaluationRun,
  CodeGraphStatus,
  CodeGraphNode,
  CodeGraphExploreResult,
  CodeRepository,
  Document,
  DocumentDetail,
  DocumentPlacement,
  DocumentUploadSummary,
  FolderDeleteResult,
  KnowledgeFolder,
  ExecuteCapabilityPayload,
  ExecuteCapabilityResult,
  KnowledgeBase,
  KnowledgeBaseSummary,
  KnowledgeBrowseResponse,
  KbRepoSource,
  KbRepoSourceSyncResult,
  OpenApiImportResult,
  OpenApiService,
  OpenApiTool,
  McpService,
  McpTool,
  TopLevelMcpTool,
  CapabilityToolPage,
  ProjectProfile,
  ProfileDocRender,
  ProfilePinPreview,
  ProfilePinRule,
  ProfilePinSettingsUpdate,
  ProfileSourceRule,
  ProfileResourceRule,
  RepoOverview,
  SearchResultChunk,
  SyncJob,
  CodeRepoCategory,
  KnowledgeSyncConfig,
  MemoryBlock,
  MemoryDashboardStatus,
  MemorySearchResult,
  MemoryTimelineResult,
  SchedulerStatus,
  SkillPrompt,
  ProfileMemoryBinding,
  ToolCallLog,
  ToolCallStats,
  AgentRun,
  AgentRuntimeConfig,
  DesignAgentResponse,
  WorkflowDesignResult,
  ScriptDesignResult,
  BusinessLedgerDesignResult,
  UAStatus,
  UASummary,
  UAAvailability,
  UAAnalyzeResult,
  UADashboardStatus,
  TestCloneResult,
  WorkflowArtifactSearchResult,
  WorkflowArtifactHistoryResult,
  WorkflowArtifactDetail,
  AgentRunPage,
  ToolCallLogPage,
  WorkflowClearResult,
  WorkflowDefinition,
  WorkflowDraft,
  WorkflowRun,
  WorkflowRunOverview,
  CompletedWorkflowTopResponse,
  WorkflowRunSummaryPage,
  WorkflowRunEvent,
  WorkflowRunLog,
  WorkflowExecutionMode,
  WorkflowExecutionPlan,
  WorkflowSubagentDetail,
  WorkflowTasksResult,
  WorkflowTaskListParams,
  WorkflowTaskImportPreview,
  WorkflowTaskRefreshPolicy,
  WorkflowTaskRefreshResult,
  WorkflowTaskImportResult,
  WorkflowImportPreview,
  WorkflowImportResult,
  WorkflowImportTargetMode,
  WorkflowRestoreResult,
  WorkflowGraph,
  WorkflowValidationIssue,
  WorkflowValidationResult,
  ManagedScript,
  ScriptRun,
  ScriptRunListResult,
  RunStopResponse,
  UploadProgressCallback,
  Revision,
  RevisionWithSnapshot,
  DiffResult,
  SyntaxCheckResult,
  BusinessLedger,
  BusinessLedgerRecords,
  OnboardingTourProgress,
  OnboardingTourStatus,
  AccessActorContext,
  AdminAccessStatus,
  AccessGroup,
  UserGroupMembership,
  ResourceVisibility,
} from './types'
import { scriptResetPath } from '../lib/scriptManagement.ts'

const API_BASE = '/api/v1'

function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export function workflowValidationIssuesFor(
  issues: WorkflowValidationIssue[],
  scope: WorkflowValidationIssue['scope'],
  id: string | null,
): WorkflowValidationIssue[] {
  if (!id) return []
  return issues.filter(issue => issue.scope === scope && issue.id === id)
}

export function hasBlockingWorkflowValidationErrors(result: WorkflowValidationResult): boolean {
  return !result.valid || result.errors.length > 0
}

export function workflowValidationErrorMessage(result: WorkflowValidationResult): string {
  if (!hasBlockingWorkflowValidationErrors(result)) return ''
  return result.errors.map(issue => issue.message).filter(Boolean).join('\n') || '工作流校验未通过'
}

export interface WorkflowValidationRunGuard { validating: boolean; token: number }

export function beginWorkflowValidationRun(guard: WorkflowValidationRunGuard): number | null {
  if (guard.validating) return null
  guard.validating = true
  guard.token += 1
  return guard.token
}

export function invalidateWorkflowValidationRun(guard: WorkflowValidationRunGuard): void {
  guard.token += 1
  guard.validating = false
}

export function isCurrentWorkflowValidationRun(guard: WorkflowValidationRunGuard, token: number | null): boolean {
  return token !== null && guard.validating && guard.token === token
}

export function finishWorkflowValidationRun(guard: WorkflowValidationRunGuard, token: number | null): boolean {
  if (!isCurrentWorkflowValidationRun(guard, token)) return false
  guard.validating = false
  return true
}

function headers(): Record<string, string> {
  return {}
}

function formatValidationIssue(value: unknown): string {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return String(value ?? '')
  const issue = value as Record<string, unknown>
  const message = typeof issue.message === 'string' ? issue.message : ''
  if (!message) return JSON.stringify(value)

  const scopeLabel = issue.scope === 'node'
    ? '节点'
    : issue.scope === 'edge'
      ? '边'
      : '工作流'
  const target = typeof issue.id === 'string' && issue.id
    ? `${scopeLabel}「${issue.id}」`
    : scopeLabel
  const field = typeof issue.field === 'string' && issue.field ? `（${issue.field}）` : ''
  return `${target}${field}：${message}`
}

function formatHttpError(_status: number, raw: string): string {
  let detail = raw.trim()
  try {
    const payload: unknown = raw ? JSON.parse(raw) : null
    if (payload && typeof payload === 'object') {
      const response = payload as Record<string, unknown>
      const headline = typeof response.detail === 'string' ? response.detail : ''
      const errors = Array.isArray(response.errors)
        ? response.errors.map(formatValidationIssue).filter(Boolean)
        : []
      if (errors.length) {
        detail = [headline || '请求失败', ...errors.map(item => `- ${item}`)].join('\n')
      } else if ('detail' in response) {
        const value = response.detail
        detail = typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value)
      } else if ('errors' in response) {
        const value = response.errors
        detail = Array.isArray(value)
          ? value.map(formatValidationIssue).filter(Boolean).join('\n')
          : String(value ?? '')
      }
    }
  } catch {
    // Keep plain-text server responses as the fallback detail.
  }
  return detail || '请求失败，请稍后重试'
}

export type ApiRequestOptions = {
  signal?: AbortSignal
}

async function get<T>(url: string, options: ApiRequestOptions = {}): Promise<T> {
  const r = await fetch(apiUrl(url), { headers: headers(), cache: 'no-store', signal: options.signal })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

/** 使用 fetch 读取 SSE，沿用同源会话 Cookie 并支持 Last-Event-ID 断线续传。 */
export async function openAgentRunEventStream(
  runKey: string,
  lastEventId: number,
  signal: AbortSignal,
): Promise<Response> {
  const streamHeaders: Record<string, string> = {
    ...headers(),
    Accept: 'text/event-stream',
  }
  if (lastEventId > 0) streamHeaders['Last-Event-ID'] = String(lastEventId)
  const response = await fetch(apiUrl(`/agent-runs/${encodeURIComponent(runKey)}/events/stream`), {
    headers: streamHeaders,
    cache: 'no-store',
    signal,
  })
  if (!response.ok) throw new Error(formatHttpError(response.status, await response.text()))
  if (!response.body) throw new Error('SSE 响应不包含可读取的数据流')
  return response
}

async function post<T>(url: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const r = await fetch(apiUrl(url), {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json', ...(extraHeaders || {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

async function put<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(apiUrl(url), {
    method: 'PUT',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

async function patch<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(apiUrl(url), {
    method: 'PATCH',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

async function del<T>(url: string): Promise<T> {
  const r = await fetch(apiUrl(url), { method: 'DELETE', headers: headers() })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

async function postFormData<T>(url: string, formData: FormData): Promise<T> {
  const r = await fetch(apiUrl(url), {
    method: 'POST',
    headers: headers(),
    body: formData,
  })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.json()
}

function parseUploadErrorDetail(raw: string): string | undefined {
  if (!raw) return undefined
  try {
    const payload: unknown = JSON.parse(raw)
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      const detail = (payload as { detail?: unknown }).detail
      if (typeof detail === 'string' && detail) return detail
      if (detail != null) return typeof detail === 'string' ? detail : JSON.stringify(detail)
    }
  } catch {
    // The response may be plain text; the caller handles that fallback.
  }
  return undefined
}

function postFormDataWithProgress<T>(
  url: string,
  formData: FormData,
  onProgress?: UploadProgressCallback,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl(url))
    xhr.upload.onprogress = event => onProgress?.(event.loaded, event.total)
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as T)
        } catch {
          reject(new Error('上传响应解析失败'))
        }
        return
      }
      const detail = parseUploadErrorDetail(xhr.responseText)
      const message = detail || xhr.responseText || '上传失败，请稍后重试'
      reject(new Error(message))
    }
    xhr.onerror = () => reject(new Error('网络错误：上传请求失败'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    xhr.send(formData)
  })
}

async function getBlob(url: string, options: ApiRequestOptions = {}): Promise<Blob> {
  const r = await fetch(apiUrl(url), { headers: headers(), signal: options.signal })
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()))
  return r.blob()
}

export const api = {
  // 管理员密码与浏览器提权会话
  getAdminAccessStatus: () => get<AdminAccessStatus>('/auth/admin/status'),
  createAdminSession: (password: string) =>
    post<AdminAccessStatus>('/auth/admin/session', { password }),
  deleteAdminSession: () => del<{ active: boolean }>('/auth/admin/session'),
  changeAdminPassword: (currentPassword: string, newPassword: string) =>
    put<{ updated: boolean }>('/auth/admin/password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),

  // 当前身份与小组映射
  getAccessContext: () => get<AccessActorContext>('/access/me'),
  listAccessGroups: () => get<AccessGroup[]>('/access/groups'),
  upsertAccessGroup: (group: Pick<AccessGroup, 'group_key' | 'name' | 'description'>) =>
    post<AccessGroup>('/access/groups', group),
  listGroupMemberships: () => get<UserGroupMembership[]>('/access/memberships'),
  setUserGroup: (membership: { user_id: string; group_key: string }) =>
    put<UserGroupMembership>('/access/memberships', membership),
  deleteUserGroup: (userId: string) =>
    del<{ deleted: boolean }>(`/access/memberships/${encodeURIComponent(userId)}`),

  // MCP Services
  listTopLevelMcpTools: () => get<TopLevelMcpTool[]>('/capabilities/top-level-mcp-tools'),
  updateTopLevelMcpToolStatus: (name: string, status: 'enabled' | 'disabled') =>
    post<TopLevelMcpTool>(`/capabilities/top-level-mcp-tools/${name}/status`, { status }),
  listServices: (summary = false, options?: ApiRequestOptions) => get<McpService[]>(`/capabilities/mcp-services${summary ? '?summary=true' : ''}`, options),
  getService: (key: string) => get<McpService>(`/capabilities/mcp-services/${key}`),
  registerService: (s: Partial<McpService> & { service_key: string; name: string; endpoint_url: string; expected_edit_token?: string | null }) =>
    post<McpService>('/capabilities/mcp-services', s),
  updateServiceStatus: (key: string, status: string) =>
    post(`/capabilities/mcp-services/${key}/status`, { status }),
  syncServiceTools: (key: string) =>
    post(`/capabilities/mcp-services/${key}/sync`),
  listTools: (key: string, summary = false) => get<McpTool[]>(`/capabilities/mcp-services/${key}/tools${summary ? '?summary=true' : ''}`),
  getTool: (serviceKey: string, toolName: string) => get<McpTool>(`/capabilities/mcp-services/${serviceKey}/tools/${toolName}`),
  updateToolType: (serviceKey: string, toolName: string, toolType: string) =>
    put(`/capabilities/mcp-services/${serviceKey}/tools/${toolName}/type`, { tool_type: toolType }),
  deleteMcpService: (key: string) => post<{ ok: boolean }>(`/capabilities/mcp-services/${key}/delete`),
  executeCapability: (
    payload: ExecuteCapabilityPayload,
    runtimeHeaders?: {
      profile_key?: string
    },
  ) =>
    post<ExecuteCapabilityResult>(
      '/capabilities/execute',
      payload,
      {
        ...(runtimeHeaders?.profile_key ? { 'X-Agent-Bridge-MetaMCP-Profile': runtimeHeaders.profile_key } : {}),
      },
    ),

  // OpenAPI Services
  listOpenApiServices: (summary = false, options?: ApiRequestOptions) => get<OpenApiService[]>(`/capabilities/openapi-services${summary ? '?summary=true' : ''}`, options),
  getOpenApiService: (key: string) => get<OpenApiService>(`/capabilities/openapi-services/${key}`),
  registerOpenApiService: (s: Partial<OpenApiService> & { service_key: string; name: string; base_url: string; expected_edit_token?: string | null }) =>
    post<OpenApiService>('/capabilities/openapi-services', s),
  updateOpenApiServiceStatus: (key: string, status: string) =>
    post(`/capabilities/openapi-services/${key}/status`, { status }),
  importOpenApiOperations: (key: string, specContent?: string) =>
    post<OpenApiImportResult>(`/capabilities/openapi-services/${key}/import`, specContent ? { spec_content: specContent } : {}),
  listOpenApiTools: (key: string, summary = false) => get<OpenApiTool[]>(`/capabilities/openapi-services/${key}/tools${summary ? '?summary=true' : ''}`),
  getOpenApiTool: (serviceKey: string, toolName: string) => get<OpenApiTool>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`),
  upsertOpenApiTool: (serviceKey: string, toolName: string, tool: Partial<OpenApiTool> & { tool_name: string }) =>
    put<OpenApiTool>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`, tool),
  updateOpenApiToolType: (serviceKey: string, toolName: string, toolType: string) =>
    put(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}/type`, { tool_type: toolType }),
  deleteOpenApiTool: (serviceKey: string, toolName: string) =>
    del<{ ok: boolean }>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`),
  deleteOpenApiService: (key: string) => post<{ ok: boolean }>(`/capabilities/openapi-services/${key}/delete`),
  listCapabilityTools: (params: {
    source_type?: string
    service_key?: string
    tool_type?: string
    query?: string
    limit?: number
    offset?: number
  } = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') qs.set(key, String(value))
    })
    return get<CapabilityToolPage>(`/capability-tools?${qs}`)
  },

  // Profiles
  listProfiles: (options?: ApiRequestOptions) => get<ProjectProfile[]>('/capability-profiles', options),
  getProfile: (key: string) => get<ProjectProfile>(`/capability-profiles/${key}`),
  upsertProfile: (p: Partial<ProjectProfile> & { profile_key: string; name: string; expected_edit_token?: string | null }) =>
    post<ProjectProfile>('/capability-profiles', { status: 'active', ...p }),
  replaceProfileRules: (key: string, rules: ProfileSourceRule[], expectedEditToken?: string | null) =>
    put<ProjectProfile>(`/capability-profiles/${key}/rules`, { rules, expected_edit_token: expectedEditToken }),
  replaceProfileResources: (key: string, resources: ProfileResourceRule[], expectedEditToken?: string | null) =>
    put<ProjectProfile>(`/capability-profiles/${key}/resources`, { resources, expected_edit_token: expectedEditToken }),
  getProfilePins: (key: string) => get<ProfilePinPreview>(`/capability-profiles/${key}/pins`),
  replaceProfilePins: (key: string, pins: ProfilePinRule[], expectedEditToken?: string | null) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins`, { pins, expected_edit_token: expectedEditToken }),
  updateProfilePinSettings: (key: string, settings: ProfilePinSettingsUpdate, expectedEditToken?: string | null) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins/settings`, { ...settings, expected_edit_token: expectedEditToken }),
  refreshProfilePins: (key: string) =>
    post<ProfilePinPreview>(`/capability-profiles/${key}/pins/refresh`),
  renderProfileDoc: (key: string) =>
    post<ProfileDocRender>(`/capability-profiles/${key}/doc/render`),
  updateProfileManualNotes: (key: string, manual_notes: string, expectedEditToken?: string | null) =>
    put<ProfileDocRender>(`/capability-profiles/${key}/doc/manual-notes`, { manual_notes, expected_edit_token: expectedEditToken }),
  getProfileMemory: (key: string) =>
    get<ProfileMemoryBinding>(`/capability-profiles/${key}/memory`),
  setProfileMemory: (key: string, blockKey: string | null, enabled = true, expectedEditToken?: string | null) =>
    put<ProfileMemoryBinding>(`/capability-profiles/${key}/memory`, {
      block_key: blockKey,
      enabled,
      expected_edit_token: expectedEditToken,
    }),

  // Memory Blocks
  listMemoryBlocks: () => get<MemoryBlock[]>('/memory/blocks'),
  createMemoryBlock: (block: { block_key: string; name: string; description?: string }) =>
    post<MemoryBlock>('/memory/blocks', block),
  deleteMemoryBlock: (blockKey: string) => post<{ deleted: boolean }>(`/memory/blocks/${blockKey}/delete`),
  getMemoryBlockHealth: (blockKey: string) =>
    get<Record<string, unknown>>(`/memory/blocks/${blockKey}/health`),
  getMemoryDashboardStatus: (blockKey: string) =>
    get<MemoryDashboardStatus>(`/memory/blocks/${blockKey}/dashboard`),
  startMemoryDashboard: (blockKey: string) =>
    post<MemoryDashboardStatus>(`/memory/blocks/${blockKey}/dashboard/start`),
  stopMemoryDashboard: (blockKey: string) =>
    post<{ stopped: boolean }>(`/memory/blocks/${blockKey}/dashboard/stop`),
  touchMemoryDashboard: (blockKey: string) =>
    post<{ ok: boolean }>(`/memory/blocks/${blockKey}/dashboard/touch`),
  searchMemoryBlock: (blockKey: string, query: string, limit = 10) => {
    const qs = new URLSearchParams({ q: query, limit: String(limit) })
    return get<MemorySearchResult>(`/memory/blocks/${blockKey}/search?${qs}`)
  },
  getMemoryTimeline: (blockKey: string, limit = 20, cursor?: string) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    if (cursor) qs.set('cursor', cursor)
    return get<MemoryTimelineResult>(`/memory/blocks/${blockKey}/timeline?${qs}`)
  },
  getClaudeMemConfig: () => get<ClaudeMemConfig>('/claude-mem/config'),
  saveClaudeMemConfig: (config: ClaudeMemConfigUpdate) =>
    post<ClaudeMemConfig>('/claude-mem/config', config),
  getRetrievalProbeLlmConfig: () => get<RetrievalProbeLlmConfig>('/retrieval-probe/llm-config'),
  saveRetrievalProbeLlmConfig: (config: RetrievalProbeLlmConfigUpdate) =>
    put<RetrievalProbeLlmConfig>('/retrieval-probe/llm-config', config),
  listModelEvaluationDatasets: (options?: ApiRequestOptions) => get<ModelEvaluationDataset[]>('/model-evaluations/datasets', options),
  getModelEvaluationRuntime: (options?: ApiRequestOptions) => get<ModelEvaluationRuntimeStatus>('/model-evaluations/runtime', options),
  listEvaluationModels: (connection: { base_url?: string; api_key?: string }) =>
    post<ModelEvaluationModel[]>('/model-evaluations/models', connection),
  listModelEvaluationRuns: (options?: ApiRequestOptions) => get<ModelEvaluationRun[]>('/model-evaluations', options),
  startModelEvaluationRun: (payload: { model_name: string; datasets: string[]; max_samples: number; sampling_mode: 'head' | 'random'; sample_seed: number; base_url?: string; api_key?: string }) =>
    post<ModelEvaluationRun>('/model-evaluations', payload),

  // Workflows
  getOnboardingTourProgress: (tourKey: string, version: number) =>
    get<OnboardingTourProgress>(`/onboarding/tours/${encodeURIComponent(tourKey)}?version=${version}`),
  saveOnboardingTourProgress: (tourKey: string, version: number, status: OnboardingTourStatus) =>
    put<OnboardingTourProgress>(`/onboarding/tours/${encodeURIComponent(tourKey)}`, { version, status }),
  listWorkflows: () => get<WorkflowDefinition[]>('/workflows'),
  getWorkflow: (key: string) => get<WorkflowDefinition>(`/workflows/${key}`),
  deleteWorkflow: (key: string) => post<{ workflow_key: string; deleted: boolean }>(`/workflows/${key}/delete`),
  clearWorkflowExecutionData: (key: string) =>
    post<WorkflowClearResult>(`/workflows/${key}/clear`),
  listWorkflowRevisions: (key: string, limit = 100) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<Revision[]>(`/workflows/${key}/revisions?${qs}`)
  },
  getWorkflowRevision: (key: string, revisionNo: number) =>
    get<RevisionWithSnapshot>(`/workflows/${key}/revisions/${revisionNo}`),
  diffWorkflow: (key: string, fromRevision?: number, toRevision?: number) => {
    const qs = new URLSearchParams()
    if (fromRevision != null) qs.set('from_revision', String(fromRevision))
    if (toRevision != null) qs.set('to_revision', String(toRevision))
    return get<DiffResult>(`/workflows/${key}/diff?${qs}`)
  },
  restoreWorkflowRevision: (key: string, revisionNo: number) =>
    post<WorkflowRestoreResult>(`/workflows/${key}/revisions/${revisionNo}/restore`),
  exportWorkflow: (key: string) => getBlob(`/workflows/${key}/export`),
  previewWorkflowImport: (
    file: File,
    targetWorkflowKey?: string,
    targetMode: WorkflowImportTargetMode = 'auto',
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (targetWorkflowKey) form.append('target_workflow_key', targetWorkflowKey)
    form.append('target_mode', targetMode)
    return postFormData<WorkflowImportPreview>('/workflows/import/preview', form)
  },
  confirmWorkflowImport: (importId: string) =>
    post<WorkflowImportResult>('/workflows/import/confirm', { import_id: importId }),
  listWorkflowRuns: (key: string, limit = 200) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<WorkflowRun[]>(`/workflows/${key}/runs?${qs}`)
  },
  listWorkflowRunOverviews: () => get<WorkflowRunOverview[]>('/workflows/run-summaries'),
  listCompletedWorkflowTop: () => get<CompletedWorkflowTopResponse>('/workflows/completed-top'),
  listWorkflowRunSummaries: (key: string, limit = 10, offset = 0) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    return get<WorkflowRunSummaryPage>(`/workflows/${key}/runs/summary?${qs}`)
  },
  listWorkflowTasks: (key: string, params: WorkflowTaskListParams = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.type) qs.set('type', params.type)
    if (params.search) qs.set('search', params.search)
    if (params.sort) qs.set('sort', params.sort)
    const tail = qs.toString() ? `?${qs}` : ''
    return get<WorkflowTasksResult>(`/workflows/${key}/tasks${tail}`)
  },
  refreshWorkflowTasks: (
    key: string,
    tasks?: Array<{ task_key: string; task_version?: string }>,
  ) => post<WorkflowTaskRefreshResult>(`/workflows/${key}/tasks/refresh`, tasks ? { tasks } : {}),
  downloadWorkflowTaskTemplate: (workflowKey: string) =>
    getBlob(`/workflows/${workflowKey}/tasks/import/template`),
  previewWorkflowTaskImport: (workflowKey: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return postFormData<WorkflowTaskImportPreview>(
      `/workflows/${workflowKey}/tasks/import/preview`,
      form,
    )
  },
  confirmWorkflowTaskImport: (workflowKey: string, importId: string) =>
    post<WorkflowTaskImportResult>(
      `/workflows/${workflowKey}/tasks/import/confirm`,
      { import_id: importId },
    ),
  getWorkflowRunLogs: (runId: string) => get<WorkflowRunLog[]>(`/workflow-runs/${runId}/logs`),
  validateWorkflow: (workflow: WorkflowDraft) =>
    post<WorkflowValidationResult>('/workflows/validate', { workflow }),
  upsertWorkflow: (w: Partial<WorkflowDefinition> & {
    workflow_key: string
    name: string
    profile_key: string
    definition: WorkflowGraph
    expected_edit_version?: number | null
    task_refresh_policy?: WorkflowTaskRefreshPolicy
  }) => post<WorkflowDefinition>('/workflows', { status: 'active', ...w }),
  runWorkflow: (
    key: string,
    input: Record<string, unknown> = {},
    options: { task_key?: string; task_version?: string; execution_mode?: WorkflowExecutionMode } = {},
  ) => post<{ status?: string; run_id?: string; run_status?: string; execution_mode?: string; plan?: WorkflowExecutionPlan }>(
    `/workflows/${key}/run`, { input, ...options },
  ),
  previewWorkflowRun: (
    key: string,
    options: { task_key?: string; task_version?: string; execution_mode?: WorkflowExecutionMode } = {},
  ) => post<WorkflowExecutionPlan>(`/workflows/${key}/run/preview`, options),
  searchWorkflowArtifacts: (params: {
    profile_key?: string
    workflow_key?: string
    query?: string
    path?: string
    path_match?: string
    task_key?: string
    task_version?: string
    run_id?: string
    include_history?: boolean
    full?: boolean
    tags?: string[]
    format?: string
    limit?: number
    offset?: number
  } = {}, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams()
    if (params.profile_key) qs.set('profile_key', params.profile_key)
    if (params.workflow_key) qs.set('workflow_key', params.workflow_key)
    if (params.query) qs.set('query', params.query)
    if (params.path) qs.set('path', params.path)
    if (params.path_match) qs.set('path_match', params.path_match)
    if (params.task_key) qs.set('task_key', params.task_key)
    if (params.task_version) qs.set('task_version', params.task_version)
    if (params.run_id) qs.set('run_id', params.run_id)
    if (params.include_history) qs.set('include_history', 'true')
    if (params.full) qs.set('full', 'true')
    if (params.format) qs.set('format', params.format)
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    ;(params.tags || []).forEach(tag => qs.append('tags', tag))
    return get<WorkflowArtifactSearchResult>(`/workflow-artifacts?${qs}`, options)
  },
  getWorkflowArtifactHistory: (params: {
    profile_key?: string
    workflow_key: string
    task_key: string
    limit?: number
  }, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams()
    if (params.profile_key) qs.set('profile_key', params.profile_key)
    qs.set('workflow_key', params.workflow_key)
    qs.set('task_key', params.task_key)
    if (params.limit) qs.set('limit', String(params.limit))
    return get<WorkflowArtifactHistoryResult>(`/workflow-artifacts/history?${qs}`, options)
  },
  getWorkflowArtifact: (artifactId: string, profileKey?: string, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams()
    if (profileKey) qs.set('profile_key', profileKey)
    const tail = qs.toString() ? `?${qs}` : ''
    return get<WorkflowArtifactDetail>(`/workflow-artifacts/${artifactId}${tail}`, options)
  },
  setWorkflowArtifactVisibility: (artifactId: string, visibility: ResourceVisibility) =>
    put<WorkflowArtifactDetail>(`/workflow-artifacts/${artifactId}/visibility`, { visibility }),
  executeWorkflowTask: (workflowKey: string, taskKey: string, taskVersion?: string, executionMode: WorkflowExecutionMode = 'normal') => {
    const qs = new URLSearchParams()
    if (taskVersion) qs.set('task_version', taskVersion)
    const tail = qs.toString() ? `?${qs}` : ''
    return post<{ workflow_key: string; task_key: string; priority: boolean; run_id?: string; run_status?: string; execution_mode?: string; plan?: WorkflowExecutionPlan }>(
      `/workflows/${workflowKey}/tasks/${encodeURIComponent(taskKey)}/execute${tail}`,
      { execution_mode: executionMode, task_version: taskVersion },
    )
  },
  resetWorkflowTask: (workflowKey: string, taskKey: string, taskVersion?: string) => {
    const qs = new URLSearchParams()
    if (taskVersion) qs.set('task_version', taskVersion)
    const tail = qs.toString() ? `?${qs}` : ''
    return post<{ workflow_key: string; task_key: string; status: string }>(
      `/workflows/${workflowKey}/tasks/${encodeURIComponent(taskKey)}/reset${tail}`,
    )
  },
  getWorkflowRun: (runId: string, options?: ApiRequestOptions) => get<WorkflowRun>(`/workflow-runs/${runId}`, options),
  stopWorkflowRun: (runId: string) =>
    post<RunStopResponse>(`/workflow-runs/${encodeURIComponent(runId)}/stop`),

  // Logs
  listLogs: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => qs.set(k, String(v)))
    return get<ToolCallLog[]>(`/tool-call-logs?${qs}`)
  },
  listLogsPage: (params: Record<string, string | number | boolean> = {}, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams()
    Object.entries({ ...params, paginated: true }).forEach(([k, v]) => qs.set(k, String(v)))
    return get<ToolCallLogPage>(`/tool-call-logs?${qs}`, options)
  },
  getLog: (id: string, options?: ApiRequestOptions) => get<ToolCallLog>(`/tool-call-logs/${id}`, options),
  stats: (params: Record<string, string> = {}, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams(params)
    return get<ToolCallStats>(`/tool-call-stats?${qs}`, options)
  },

  // Agent runs
  listAgentRuns: (params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => qs.set(k, String(v)))
    return get<AgentRun[]>(`/agent-runs?${qs}`)
  },
  listAgentRunsPage: (params: Record<string, string | number | boolean> = {}, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams()
    Object.entries({ ...params, paginated: true }).forEach(([k, v]) => qs.set(k, String(v)))
    return get<AgentRunPage>(`/agent-runs?${qs}`, options)
  },
  getAgentRun: (runKey: string, options?: ApiRequestOptions) => get<AgentRun>(`/agent-runs/${runKey}`, options),
  stopAgentRun: (runKey: string) =>
    post<RunStopResponse>(`/agent-runs/${encodeURIComponent(runKey)}/stop`),
  /** Live event stream for an agent run (reads events.jsonl in real time,
   *  falls back to persisted DB events for historical runs). */
  getAgentRunEvents: (runKey: string, options?: ApiRequestOptions) => get<WorkflowRunEvent[]>(`/agent-runs/${runKey}/events`, options),
  getAgentRunPayload: (runKey: string, ref: string, options?: ApiRequestOptions) =>
    getBlob(`/agent-runs/${encodeURIComponent(runKey)}/payload?ref=${encodeURIComponent(ref)}`, options),
  getAgentRunSubagentDetail: (runKey: string, taskId: string, options?: ApiRequestOptions) => {
    const qs = new URLSearchParams({ task_id: taskId })
    return get<WorkflowSubagentDetail>(`/agent-runs/${runKey}/subagent-detail?${qs}`, options)
  },
  /** Fetch the single agent run (with full events) associated with a workflow run. */
  getAgentRunForWorkflowRun: async (workflowRunId: string): Promise<AgentRun | null> => {
    // A summary workflow_run may have multiple agent runs (the main workflow
    // agent plus a derived html-report agent). We want the MAIN workflow
    // agent's logs, so filter by agent_name=workflow rather than taking the
    // newest (which would be the report agent created afterwards).
    const rows = await get<AgentRun[]>(`/agent-runs?workflow_run_id=${encodeURIComponent(workflowRunId)}&agent_name=workflow&limit=1`)
    if (!rows.length) return null
    // The list view omits events/result; fetch the full detail.
    return get<AgentRun>(`/agent-runs/${rows[0].run_key}`)
  },
  listAgentRunsForWorkflowRun: async (workflowRunId: string): Promise<AgentRun[]> => {
    // All agent runs attached to a workflow_run (main workflow agent plus any
    // derived agents like the html reporter). Ordered oldest-first so the
    // main workflow agent (created first) is the natural default.
    const rows = await get<AgentRun[]>(`/agent-runs?workflow_run_id=${encodeURIComponent(workflowRunId)}&limit=50`)
    return rows
  },
  designWorkflow: (body: { run_key: string; mode: 'create' | 'modify'; prompt: string; current?: Record<string, unknown>; profile_key?: string }) =>
    post<DesignAgentResponse<WorkflowDesignResult>>('/agent-runs/design/workflow', body),
  designScript: (body: { run_key: string; mode: 'create' | 'modify'; prompt: string; current?: Record<string, unknown>; profile_key?: string }) =>
    post<DesignAgentResponse<ScriptDesignResult>>('/agent-runs/design/script', body),
  designBusinessLedger: (body: { run_key: string; mode: 'create' | 'modify'; prompt: string; current?: Record<string, unknown> }) =>
    post<DesignAgentResponse<BusinessLedgerDesignResult>>('/agent-runs/design/business-ledger', body),

  // Catalog
  catalog: (profileKey?: string, query?: string) => {
    const qs = new URLSearchParams()
    if (profileKey) qs.set('profile_key', profileKey)
    if (query) qs.set('query', query)
    return get<{ sources: CatalogSource[] }>(`/capability-catalog?${qs}`)
  },
  sourceDetail: (type: string, key: string) =>
    get<{ source_type: string; source: McpService; tools: McpTool[] }>(`/capability-catalog/sources/${type}/${key}`),
  toolDetail: (type: string, key: string, tool: string) =>
    get(`/capability-catalog/sources/${type}/${key}/tools/${tool}`),

  // Resource-Profile 双向关联
  getResourceProfiles: (resourceType: string, resourceKey: string) =>
    get<ProfileResourceRule[]>(`/resource-profiles/${resourceType}/${resourceKey}`),
  setResourceProfiles: (resourceType: string, resourceKey: string, profileKeys: string[], overrides?: Record<string, { retrieval_backend_slug?: string | null; retrieval_agent_id?: string | null }>) =>
    put<{ resource_type: string; resource_key: string; profile_keys: string[] }>(
      `/resource-profiles/${resourceType}/${resourceKey}`,
      { profile_keys: profileKeys, overrides }
    ),

  // Code Repos
  listCodeRepos: (options?: ApiRequestOptions) => get<CodeRepository[]>('/code-repo/repositories', options),
  getCodeRepo: (key: string) => get<CodeRepository>(`/code-repo/repositories/${key}`),
  upsertCodeRepo: (r: Partial<CodeRepository> & { repo_key: string; name: string; git_url: string; expected_edit_token?: string | null }) =>
    post<CodeRepository>('/code-repo/repositories', { status: 'active', ...r }),
  testClone: (gitUrl: string, authRef: string) =>
    post<TestCloneResult>('/code-repo/test-clone', { git_url: gitUrl, auth_ref: authRef }),
  syncCodeRepo: (key: string) => post(`/code-repo/repositories/${key}/sync`),
  deleteCodeRepo: (key: string) => post<{ deleted: boolean }>(`/code-repo/repositories/${key}/delete`),
  listWikiKbs: (options?: ApiRequestOptions) => get<KnowledgeBaseSummary[]>('/builtin/wiki/kbs', options),

  // CodeGraph detail
  getCodeGraphStatus: () => get<CodeGraphStatus>('/code-repo/status'),
  getRepoOverview: (repoKey: string) => get<RepoOverview>(`/code-repo/repositories/${repoKey}/overview`),
  listRepoFiles: (repoKey: string) => get<{ files: { path: string; language: string }[] }>(`/code-repo/repositories/${repoKey}/files`),
  queryRepo: (repoKey: string, query: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/code-repo/repositories/${repoKey}/query`, { query, limit }),
  exploreRepo: (repoKey: string, query: string) =>
    post<CodeGraphExploreResult>(`/code-repo/repositories/${repoKey}/explore`, { query }),
  findCallers: (repoKey: string, symbol: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/code-repo/repositories/${repoKey}/callers`, { query: symbol, limit }),
  findCallees: (repoKey: string, symbol: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/code-repo/repositories/${repoKey}/callees`, { query: symbol, limit }),
  analyzeImpact: (repoKey: string, symbol: string) =>
    post<{ matches: CodeGraphNode[] }>(`/code-repo/repositories/${repoKey}/impact`, { query: symbol }),

  // Understand Anything
  getUAStatus: (repoKey: string) => get<UAStatus>(`/code-repo/repositories/${repoKey}/understand/status`),
  getUASummary: (repoKey: string) => get<UASummary>(`/code-repo/repositories/${repoKey}/understand/summary`),
  checkUAAvailability: (repoKey: string) => get<UAAvailability>(`/code-repo/repositories/${repoKey}/understand/availability`),
  triggerUAAnalyze: (repoKey: string) => post<UAAnalyzeResult>(`/code-repo/repositories/${repoKey}/understand/analyze`),
  getUADashboardStatus: (repoKey: string) => get<UADashboardStatus>(`/code-repo/repositories/${repoKey}/understand/dashboard`),
  startUADashboard: (repoKey: string) => post<UADashboardStatus>(`/code-repo/repositories/${repoKey}/understand/dashboard/start`),
  stopUADashboard: (repoKey: string) => post<{ stopped: boolean }>(`/code-repo/repositories/${repoKey}/understand/dashboard/stop`),
  touchDashboard: (repoKey: string) => post<{ ok: boolean }>(`/code-repo/repositories/${repoKey}/understand/dashboard/touch`),

  // Categories
  listCategories: (options?: ApiRequestOptions) => get<CodeRepoCategory[]>('/code-repo/categories', options),
  upsertCategory: (c: { category_key: string; name: string; description?: string; expected_edit_token?: string | null }) =>
    post<CodeRepoCategory>('/code-repo/categories', c),
  deleteCategory: (key: string) => post<{ ok: boolean }>(`/code-repo/categories/${key}/delete`),

  // Sync Config
  getSyncConfig: () => get<KnowledgeSyncConfig>('/sync-config'),
  saveSyncConfig: (config: KnowledgeSyncConfig) => {
    const { edit_token, ...payload } = config
    return post<KnowledgeSyncConfig>('/sync-config', { ...payload, expected_edit_token: edit_token })
  },
  getSchedulerStatus: () => get<SchedulerStatus>('/sync-config/scheduler-status'),
  getAgentRuntimeConfig: () => get<AgentRuntimeConfig>('/agent-runtime/config'),
  saveAgentRuntimeConfig: (config: AgentRuntimeConfig) => {
    const { edit_token, available_backends: _availableBackends, ...payload } = config
    return post<AgentRuntimeConfig>('/agent-runtime/config', { ...payload, expected_edit_token: edit_token })
  },

  // Skills
  listSkills: () => get<SkillPrompt[]>('/skills'),
  getSkill: (skillName: string) => get<SkillPrompt>(`/skills/${skillName}`),
  saveSkill: (skillName: string, prompt: string, expectedEditToken?: string | null) =>
    post<SkillPrompt>(`/skills/${skillName}`, { prompt, expected_edit_token: expectedEditToken }),
  resetSkill: (skillName: string, expectedEditToken?: string | null) =>
    post<SkillPrompt>(`/skills/${skillName}/reset`, { expected_edit_token: expectedEditToken }),
  listSkillRevisions: (skillName: string, limit = 100) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<Revision[]>(`/skills/${skillName}/revisions?${qs}`)
  },
  getSkillRevision: (skillName: string, revisionNo: number) =>
    get<RevisionWithSnapshot>(`/skills/${skillName}/revisions/${revisionNo}`),
  diffSkill: (skillName: string, fromRevision?: number, toRevision?: number) => {
    const qs = new URLSearchParams()
    if (fromRevision != null) qs.set('from_revision', String(fromRevision))
    if (toRevision != null) qs.set('to_revision', String(toRevision))
    return get<DiffResult>(`/skills/${skillName}/diff?${qs}`)
  },

  // Scripts
  listScripts: () => get<ManagedScript[]>('/scripts'),
  getScript: (scriptKey: string) => get<ManagedScript>(`/scripts/${scriptKey}`),
  upsertScript: (s: Partial<ManagedScript> & { script_key: string; name: string; code: string; input_schema: Record<string, unknown>; expected_edit_token?: string | null }) =>
    post<ManagedScript>('/scripts', { language: 'python', status: 'active', owner_type: 'system', owner_key: '', description: '', ...s }),
  resetScript: (scriptKey: string, expectedEditToken?: string | null) =>
    post<ManagedScript>(scriptResetPath(scriptKey), { expected_edit_token: expectedEditToken }),
  deleteScript: (scriptKey: string) => post<{ script_key: string; deleted: boolean }>(`/scripts/${scriptKey}/delete`),
  testScript: (
    scriptKey: string,
    body: { params?: Record<string, unknown>; timeout_seconds?: number },
    runtimeHeaders?: {
      profile_key?: string
      workflow_enabled?: boolean
      workflow_key?: string
      workflow_run_id?: string
    },
  ) =>
    post<ScriptRun>(
      `/scripts/${scriptKey}/test`,
      body,
      {
        ...(runtimeHeaders?.profile_key ? { 'X-Agent-Bridge-MetaMCP-Profile': runtimeHeaders.profile_key } : {}),
        ...(runtimeHeaders?.workflow_enabled ? { 'X-Agent-Bridge-Workflow': 'true' } : {}),
        ...(runtimeHeaders?.workflow_enabled && runtimeHeaders.workflow_key ? { 'X-Agent-Bridge-Workflow-Key': runtimeHeaders.workflow_key } : {}),
        ...(runtimeHeaders?.workflow_enabled && runtimeHeaders.workflow_run_id ? { 'X-Agent-Bridge-Workflow-Run-Id': runtimeHeaders.workflow_run_id } : {}),
      },
    ),
  listScriptRuns: (scriptKey: string, limit = 20) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<ScriptRunListResult>(`/scripts/${scriptKey}/runs?${qs}`)
  },
  getScriptRun: (runId: string) => get<ScriptRun>(`/script-runs/${runId}`),
  validateScriptCode: (code: string) => post<SyntaxCheckResult>('/scripts/validate', { code }),
  listScriptRevisions: (scriptKey: string, limit = 100) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<Revision[]>(`/scripts/${scriptKey}/revisions?${qs}`)
  },
  getScriptRevision: (scriptKey: string, revisionNo: number) =>
    get<RevisionWithSnapshot>(`/scripts/${scriptKey}/revisions/${revisionNo}`),
  diffScript: (scriptKey: string, fromRevision?: number, toRevision?: number) => {
    const qs = new URLSearchParams()
    if (fromRevision != null) qs.set('from_revision', String(fromRevision))
    if (toRevision != null) qs.set('to_revision', String(toRevision))
    return get<DiffResult>(`/scripts/${scriptKey}/diff?${qs}`)
  },

  // Business ledgers
  listBusinessLedgers: () => get<BusinessLedger[]>('/business-ledgers'),
  getBusinessLedger: (ledgerKey: string) => get<BusinessLedger>(`/business-ledgers/${ledgerKey}`),
  createBusinessLedger: (payload: Omit<BusinessLedger, 'record_count' | 'edit_token' | 'owner_group_key'> & { expected_edit_token?: string | null }) => post<BusinessLedger>('/business-ledgers', payload),
  updateBusinessLedger: (ledgerKey: string, payload: Omit<BusinessLedger, 'ledger_key' | 'record_count' | 'edit_token' | 'owner_group_key'> & { expected_edit_token?: string | null }) => put<BusinessLedger>(`/business-ledgers/${ledgerKey}`, payload),
  deleteBusinessLedger: (ledgerKey: string) => del<{ ledger_key: string; deleted: boolean }>(`/business-ledgers/${ledgerKey}`),
  queryBusinessLedgerRecords: (ledgerKey: string, payload: Record<string, unknown> = {}) => post<BusinessLedgerRecords>(`/business-ledgers/${ledgerKey}/records/query`, payload),
  addBusinessLedgerRecord: (ledgerKey: string, values: Record<string, unknown>) => post<{ record_id: string; values: Record<string, unknown> }>(`/business-ledgers/${ledgerKey}/records`, { values }),
  updateBusinessLedgerRecord: (ledgerKey: string, recordId: string, values: Record<string, unknown>) => put<{ record_id: string; values: Record<string, unknown> }>(`/business-ledgers/${ledgerKey}/records/${recordId}`, { values }),
  deleteBusinessLedgerRecord: (ledgerKey: string, recordId: string) => del<{ record_id: string; deleted: boolean }>(`/business-ledgers/${ledgerKey}/records/${recordId}`),
  previewBusinessLedgerImport: (ledgerKey: string, file: File) => { const form = new FormData(); form.append('file', file); return postFormData<{ preview_id: string; rows: number; errors: Array<{ row: number; error: string }> }>(`/business-ledgers/${ledgerKey}/imports/xlsx/preview`, form) },
  downloadBusinessLedgerTemplate: (ledgerKey: string) => getBlob(`/business-ledgers/${ledgerKey}/imports/xlsx/template`),
  confirmBusinessLedgerImport: (ledgerKey: string, previewId: string) => post<{ imported: number }>(`/business-ledgers/${ledgerKey}/imports/xlsx/${previewId}/confirm`),
  exportBusinessLedger: (ledgerKey: string) => getBlob(`/business-ledgers/${ledgerKey}/exports/xlsx`),

  // Knowledge Bases
  listKbs: () => get<KnowledgeBase[]>('/kbs'),
  createKb: (data: { slug: string; name: string; description?: string; visibility: ResourceVisibility }) =>
    post<KnowledgeBase>('/kbs', data),
  updateKbDefaults: (kbSlug: string, data: { default_backend_slug?: string | null; default_agent_id?: string | null; expected_edit_token?: string | null }) =>
    put<KnowledgeBase>(`/kbs/${kbSlug}/defaults`, data),
  listKbRepoSources: (kbSlug: string) =>
    get<KbRepoSource[]>(`/kbs/${kbSlug}/repo-sources`),
  saveKbRepoSource: (kbSlug: string, data: { repo_key: string; include_suffixes: string[] }) =>
    post<KbRepoSource>(`/kbs/${kbSlug}/repo-sources`, data),
  syncKbRepoSource: (kbSlug: string, repoKey: string) =>
    post<KbRepoSourceSyncResult>(`/kbs/${kbSlug}/repo-sources/${repoKey}/sync`),
  deleteKbRepoSource: (kbSlug: string, repoKey: string) =>
    post<{ kb_slug: string; repo_key: string; deleted_docs: number }>(`/kbs/${kbSlug}/repo-sources/${repoKey}/delete`),
  deleteKnowledgeBase: (kbSlug: string) => post<{ deleted: boolean }>(`/kbs/${kbSlug}/delete`),

  // Knowledge-base folders
  listFolders: (kbSlug: string) => get<KnowledgeFolder[]>(`/kbs/${kbSlug}/folders`),
  listBrowse: (kbSlug: string, folderId?: number, archiveEntryId?: number) => {
    const qs = new URLSearchParams()
    if (folderId != null) qs.set('folder_id', String(folderId))
    if (archiveEntryId != null) qs.set('archive_entry_id', String(archiveEntryId))
    const tail = qs.toString() ? `?${qs}` : ''
    return get<KnowledgeBrowseResponse>(`/kbs/${kbSlug}/browse${tail}`)
  },
  createFolder: (kbSlug: string, parentFolderId: number | null, name: string) =>
    post<KnowledgeFolder>(`/kbs/${kbSlug}/folders`, { parent_folder_id: parentFolderId, name }),
  updateFolder: (
    kbSlug: string,
    folderId: number,
    payload: { name?: string; parent_folder_id?: number | null },
  ) => patch<KnowledgeFolder>(`/kbs/${kbSlug}/folders/${folderId}`, payload),
  deleteFolder: async (kbSlug: string, folderId: number, confirm = false): Promise<FolderDeleteResult> => {
    const r = await fetch(apiUrl(`/kbs/${kbSlug}/folders/${folderId}`), {
      method: 'DELETE',
      headers: { ...headers(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm }),
    })
    const raw = await r.text()
    let payload: unknown = {}
    try { payload = raw ? JSON.parse(raw) : {} } catch { payload = {} }
    if (r.status === 409 && !confirm && payload && typeof payload === 'object' && 'detail' in payload) {
      return (payload as { detail: FolderDeleteResult }).detail
    }
    if (!r.ok) throw new Error(`${r.status}: ${raw}`)
    return payload as FolderDeleteResult
  },

  // Documents
  listDocs: (kb: string, backend?: string, folderId?: number) => {
    const qs = new URLSearchParams({ kb })
    if (backend) qs.set('backend', backend)
    if (folderId != null) qs.set('folder_id', String(folderId))
    return get<Document[]>(`/docs?${qs}`)
  },
  getDoc: (slug: string, backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return get<DocumentDetail>(`/docs/${slug}${qs.toString() ? '?' + qs : ''}`)
  },
  addDocument: (
    file: File,
    kbs: string[],
    later = false,
    folderId?: number | null,
    relativePath?: string,
    onProgress?: UploadProgressCallback,
  ) => {
    const form = new FormData()
    form.append('file', file)
    kbs.forEach(kb => form.append('kb', kb))
    if (later) form.append('later', 'true')
    if (folderId != null) form.append('folder_id', String(folderId))
    if (relativePath) form.append('relative_path', relativePath)
    return postFormDataWithProgress<DocumentDetail | DocumentUploadSummary>('/docs', form, onProgress)
  },
  updateDocument: (slug: string, file: File, later = false) => {
    const form = new FormData()
    form.append('file', file)
    if (later) form.append('later', 'true')
    return postFormData<DocumentDetail>(`/docs/${slug}/versions`, form)
  },
  deleteDocument: (slug: string) =>
    post<{ slug: string; status: string }>(`/docs/${slug}/delete`),
  deleteDocumentFromKb: (kbSlug: string, slug: string) =>
    post<{ slug: string; status: string; kb: string }>(`/kbs/${kbSlug}/docs/${slug}/delete`),
  placeDocument: (slug: string, kbSlug: string, folderId: number) =>
    patch<DocumentPlacement>(`/docs/${slug}/placement`, { kb: kbSlug, folder_id: folderId }),
  attachDocument: (slug: string, kbSlug: string, folderId: number) =>
    post<DocumentPlacement>(`/docs/${slug}/attach`, { kb: kbSlug, folder_id: folderId }),
  purgeDocument: (slug: string) =>
    post<{ slug: string; status: string }>(`/docs/${slug}/purge`, { confirm: true }),

  // Sync
  triggerSync: (backend?: string, allUsers = false) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return post<{ processed: number }>(`/sync${qs.toString() ? '?' + qs : ''}`, { all_users: allUsers })
  },
  triggerKbSync: (kbSlug: string, backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return post<{ processed: number }>(`/kbs/${encodeURIComponent(kbSlug)}/sync${qs.toString() ? '?' + qs : ''}`)
  },
  getSyncStatus: (backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return get<{ jobs: SyncJob[] }>(`/status${qs.toString() ? '?' + qs : ''}`)
  },
  getKbSyncStatus: (kbSlug: string, backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return get<{ jobs: SyncJob[] }>(`/kbs/${encodeURIComponent(kbSlug)}/status${qs.toString() ? '?' + qs : ''}`)
  },

  // Search & Ask
  search: (kb: string, q: string, backend?: string, topK = 6) => {
    const qs = new URLSearchParams({ kb, q, top_k: String(topK) })
    if (backend) qs.set('backend', backend)
    return get<{ results: SearchResultChunk[] }>(`/search?${qs}`)
  },
  ask: (data: { kb: string; question: string; backend?: string; session_id?: string }) =>
    post<{ answer: string; chunks: SearchResultChunk[]; session_id: string | null }>('/ask', data),

  // Backends
  listBackends: (options?: ApiRequestOptions) => get<BackendInfo[]>('/backends', options),
  createBackend: (data: { slug: string; backend_type: string; base_url?: string | null; api_key?: string | null; timeout?: number; embedding_model_id?: string | null; summary_model_id?: string | null; rerank_model_id?: string | null; expected_edit_token?: string | null }) =>
    post<BackendInfo>('/backends', data),
  updateBackend: (slug: string, data: { backend_type?: string; base_url?: string | null; api_key?: string | null; timeout?: number; embedding_model_id?: string | null; summary_model_id?: string | null; rerank_model_id?: string | null; expected_edit_token?: string | null }) =>
    put<BackendInfo>(`/backends/${slug}`, data),
  deleteBackend: (slug: string) =>
    post<{ slug: string; status: string }>(`/backends/${slug}/delete`),

  // Backend Agents (Weknora)
  listBackendAgents: (slug: string) => get<BackendAgent[]>(`/backends/${slug}/agents`),
  listBackendAgentTypes: (slug: string) => get<BackendAgentPreset[]>(`/backends/${slug}/agent-types`),
  createBackendAgent: (slug: string, name: string, presetId: string) =>
    post<BackendAgent>(`/backends/${slug}/agents`, { name, preset_id: presetId }),
}
