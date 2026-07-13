import type {
  BackendInfo,
  BackendAgent,
  BackendAgentPreset,
  CatalogSource,
  ClaudeMemConfig,
  ClaudeMemConfigUpdate,
  CodeGraphStatus,
  CodeGraphNode,
  CodeGraphExploreResult,
  CodeRepository,
  Document,
  DocumentDetail,
  ExecuteCapabilityPayload,
  ExecuteCapabilityResult,
  KnowledgeBase,
  KnowledgeBaseSummary,
  KbRepoSource,
  KbRepoSourceSyncResult,
  OpenApiImportResult,
  OpenApiService,
  OpenApiTool,
  McpService,
  McpTool,
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
  UAStatus,
  UASummary,
  UAAvailability,
  UAAnalyzeResult,
  UADashboardStatus,
  TestCloneResult,
  WorkflowArtifactSearchResult,
  WorkflowArtifactHistoryResult,
  WorkflowArtifactDetail,
  WorkflowClearResult,
  WorkflowDefinition,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowRunLog,
  WorkflowSubagentDetail,
  WorkflowTasksResult,
  WorkflowTaskListParams,
  WorkflowGraph,
  ManagedScript,
  ScriptRun,
  ScriptRunListResult,
} from './types'

const DEFAULT_USER = (window as unknown as Record<string, string>).AGENT_BRIDGE_DEFAULT_USER || 'root'

function headers(): Record<string, string> {
  return { 'X-Agent-Bridge-User': DEFAULT_USER }
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: headers() })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

async function post<T>(url: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json', ...(extraHeaders || {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

async function put<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'PUT',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

async function del<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: 'DELETE', headers: headers() })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

async function postFormData<T>(url: string, formData: FormData): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: headers(),
    body: formData,
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

export const api = {
  // MCP Services
  listServices: () => get<McpService[]>('/capabilities/mcp-services'),
  registerService: (s: Partial<McpService> & { service_key: string; name: string; endpoint_url: string }) =>
    post<McpService>('/capabilities/mcp-services', s),
  updateServiceStatus: (key: string, status: string) =>
    post(`/capabilities/mcp-services/${key}/status`, { status }),
  syncServiceTools: (key: string) =>
    post(`/capabilities/mcp-services/${key}/sync`),
  listTools: (key: string) => get<McpTool[]>(`/capabilities/mcp-services/${key}/tools`),
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
  listOpenApiServices: () => get<OpenApiService[]>('/capabilities/openapi-services'),
  registerOpenApiService: (s: Partial<OpenApiService> & { service_key: string; name: string; base_url: string }) =>
    post<OpenApiService>('/capabilities/openapi-services', s),
  updateOpenApiServiceStatus: (key: string, status: string) =>
    post(`/capabilities/openapi-services/${key}/status`, { status }),
  importOpenApiOperations: (key: string, specContent?: string) =>
    post<OpenApiImportResult>(`/capabilities/openapi-services/${key}/import`, specContent ? { spec_content: specContent } : {}),
  listOpenApiTools: (key: string) => get<OpenApiTool[]>(`/capabilities/openapi-services/${key}/tools`),
  upsertOpenApiTool: (serviceKey: string, toolName: string, tool: Partial<OpenApiTool> & { tool_name: string }) =>
    put<OpenApiTool>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`, tool),
  updateOpenApiToolType: (serviceKey: string, toolName: string, toolType: string) =>
    put(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}/type`, { tool_type: toolType }),
  deleteOpenApiTool: (serviceKey: string, toolName: string) =>
    del<{ ok: boolean }>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`),
  deleteOpenApiService: (key: string) => post<{ ok: boolean }>(`/capabilities/openapi-services/${key}/delete`),

  // Profiles
  listProfiles: () => get<ProjectProfile[]>('/capability-profiles'),
  getProfile: (key: string) => get<ProjectProfile>(`/capability-profiles/${key}`),
  upsertProfile: (p: Partial<ProjectProfile> & { profile_key: string; name: string }) =>
    post<ProjectProfile>('/capability-profiles', { status: 'active', ...p }),
  replaceProfileRules: (key: string, rules: ProfileSourceRule[]) =>
    put(`/capability-profiles/${key}/rules`, { rules }),
  replaceProfileResources: (key: string, resources: ProfileResourceRule[]) =>
    put(`/capability-profiles/${key}/resources`, { resources }),
  getProfilePins: (key: string) => get<ProfilePinPreview>(`/capability-profiles/${key}/pins`),
  replaceProfilePins: (key: string, pins: ProfilePinRule[]) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins`, { pins }),
  updateProfilePinSettings: (key: string, settings: ProfilePinSettingsUpdate) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins/settings`, settings),
  refreshProfilePins: (key: string) =>
    post<ProfilePinPreview>(`/capability-profiles/${key}/pins/refresh`),
  renderProfileDoc: (key: string) =>
    post<ProfileDocRender>(`/capability-profiles/${key}/doc/render`),
  updateProfileManualNotes: (key: string, manual_notes: string) =>
    put<ProfileDocRender>(`/capability-profiles/${key}/doc/manual-notes`, { manual_notes }),
  getProfileMemory: (key: string) =>
    get<ProfileMemoryBinding>(`/capability-profiles/${key}/memory`),
  setProfileMemory: (key: string, blockKey: string | null, enabled = true) =>
    put<ProfileMemoryBinding>(`/capability-profiles/${key}/memory`, { block_key: blockKey, enabled }),

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

  // Workflows
  listWorkflows: () => get<WorkflowDefinition[]>('/workflows'),
  getWorkflow: (key: string) => get<WorkflowDefinition>(`/workflows/${key}`),
  deleteWorkflow: (key: string) => post<{ workflow_key: string; deleted: boolean }>(`/workflows/${key}/delete`),
  clearWorkflowExecutionData: (key: string) =>
    post<WorkflowClearResult>(`/workflows/${key}/clear`),
  listWorkflowRuns: (key: string, limit = 200) => {
    const qs = new URLSearchParams({ limit: String(limit) })
    return get<WorkflowRun[]>(`/workflows/${key}/runs?${qs}`)
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
  getWorkflowRunLogs: (runId: string) => get<WorkflowRunLog[]>(`/workflow-runs/${runId}/logs`),
  upsertWorkflow: (w: Partial<WorkflowDefinition> & {
    workflow_key: string
    name: string
    profile_key: string
    definition: WorkflowGraph
  }) => post<WorkflowDefinition>('/workflows', { status: 'active', ...w }),
  runWorkflow: (key: string, input: Record<string, unknown> = {}) =>
    post<{ status: string; run_id?: string }>(`/workflows/${key}/run`, { input }),
  searchWorkflowArtifacts: (params: {
    profile_key?: string
    workflow_key?: string
    query?: string
    path?: string
    task_key?: string
    task_version?: string
    run_id?: string
    include_history?: boolean
    full?: boolean
    tags?: string[]
    format?: string
    limit?: number
  } = {}) => {
    const qs = new URLSearchParams()
    if (params.profile_key) qs.set('profile_key', params.profile_key)
    if (params.workflow_key) qs.set('workflow_key', params.workflow_key)
    if (params.query) qs.set('query', params.query)
    if (params.path) qs.set('path', params.path)
    if (params.task_key) qs.set('task_key', params.task_key)
    if (params.task_version) qs.set('task_version', params.task_version)
    if (params.run_id) qs.set('run_id', params.run_id)
    if (params.include_history) qs.set('include_history', 'true')
    if (params.full) qs.set('full', 'true')
    if (params.format) qs.set('format', params.format)
    if (params.limit) qs.set('limit', String(params.limit))
    ;(params.tags || []).forEach(tag => qs.append('tags', tag))
    return get<WorkflowArtifactSearchResult>(`/workflow-artifacts?${qs}`)
  },
  getWorkflowArtifactHistory: (params: {
    profile_key?: string
    workflow_key: string
    task_key: string
    limit?: number
  }) => {
    const qs = new URLSearchParams()
    if (params.profile_key) qs.set('profile_key', params.profile_key)
    qs.set('workflow_key', params.workflow_key)
    qs.set('task_key', params.task_key)
    if (params.limit) qs.set('limit', String(params.limit))
    return get<WorkflowArtifactHistoryResult>(`/workflow-artifacts/history?${qs}`)
  },
  getWorkflowArtifact: (artifactId: string, profileKey?: string) => {
    const qs = new URLSearchParams()
    if (profileKey) qs.set('profile_key', profileKey)
    const tail = qs.toString() ? `?${qs}` : ''
    return get<WorkflowArtifactDetail>(`/workflow-artifacts/${artifactId}${tail}`)
  },
  executeWorkflowTask: (workflowKey: string, taskKey: string, taskVersion?: string) => {
    const qs = new URLSearchParams()
    if (taskVersion) qs.set('task_version', taskVersion)
    const tail = qs.toString() ? `?${qs}` : ''
    return post<{ workflow_key: string; task_key: string; priority: boolean; run_id?: string; run_status?: string }>(
      `/workflows/${workflowKey}/tasks/${encodeURIComponent(taskKey)}/execute${tail}`,
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
  getWorkflowRun: (runId: string) => get<WorkflowRun>(`/workflow-runs/${runId}`),

  // Logs
  listLogs: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => qs.set(k, String(v)))
    return get<ToolCallLog[]>(`/tool-call-logs?${qs}`)
  },
  getLog: (id: string) => get<ToolCallLog>(`/tool-call-logs/${id}`),
  stats: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params)
    return get<ToolCallStats>(`/tool-call-stats?${qs}`)
  },

  // Agent runs
  listAgentRuns: (params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => qs.set(k, String(v)))
    return get<AgentRun[]>(`/agent-runs?${qs}`)
  },
  getAgentRun: (runKey: string) => get<AgentRun>(`/agent-runs/${runKey}`),
  /** Live event stream for an agent run (reads events.jsonl in real time,
   *  falls back to persisted DB events for historical runs). */
  getAgentRunEvents: (runKey: string) => get<WorkflowRunEvent[]>(`/agent-runs/${runKey}/events`),
  getAgentRunSubagentDetail: (runKey: string, taskId: string) => {
    const qs = new URLSearchParams({ task_id: taskId })
    return get<WorkflowSubagentDetail>(`/agent-runs/${runKey}/subagent-detail?${qs}`)
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
  designWorkflow: (body: { mode: 'create' | 'modify'; prompt: string; current?: Record<string, unknown>; profile_key?: string }) =>
    post<DesignAgentResponse<WorkflowDesignResult>>('/agent-runs/design/workflow', body),
  designScript: (body: { mode: 'create' | 'modify'; prompt: string; current?: Record<string, unknown>; profile_key?: string }) =>
    post<DesignAgentResponse<ScriptDesignResult>>('/agent-runs/design/script', body),

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
  listCodeRepos: () => get<CodeRepository[]>('/code-repo/repositories'),
  upsertCodeRepo: (r: Partial<CodeRepository> & { repo_key: string; name: string; git_url: string }) =>
    post<CodeRepository>('/code-repo/repositories', { status: 'active', ...r }),
  testClone: (gitUrl: string, authRef: string) =>
    post<TestCloneResult>('/code-repo/test-clone', { git_url: gitUrl, auth_ref: authRef }),
  syncCodeRepo: (key: string) => post(`/code-repo/repositories/${key}/sync`),
  deleteCodeRepo: (key: string) => post<{ deleted: boolean }>(`/code-repo/repositories/${key}/delete`),
  listWikiKbs: () => get<KnowledgeBaseSummary[]>('/builtin/wiki/kbs'),

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
  listCategories: () => get<CodeRepoCategory[]>('/code-repo/categories'),
  upsertCategory: (c: { category_key: string; name: string; description?: string }) =>
    post<CodeRepoCategory>('/code-repo/categories', c),
  deleteCategory: (key: string) => post<{ ok: boolean }>(`/code-repo/categories/${key}/delete`),

  // Sync Config
  getSyncConfig: () => get<KnowledgeSyncConfig>('/sync-config'),
  saveSyncConfig: (config: KnowledgeSyncConfig) => post<KnowledgeSyncConfig>('/sync-config', config),
  getSchedulerStatus: () => get<SchedulerStatus>('/sync-config/scheduler-status'),
  getAgentRuntimeConfig: () => get<AgentRuntimeConfig>('/agent-runtime/config'),
  saveAgentRuntimeConfig: (config: AgentRuntimeConfig) => post<AgentRuntimeConfig>('/agent-runtime/config', config),

  // Skills
  listSkills: () => get<SkillPrompt[]>('/skills'),
  getSkill: (skillName: string) => get<SkillPrompt>(`/skills/${skillName}`),
  saveSkill: (skillName: string, prompt: string) => post<SkillPrompt>(`/skills/${skillName}`, { prompt }),
  resetSkill: (skillName: string) => post<SkillPrompt>(`/skills/${skillName}/reset`),

  // Scripts
  listScripts: () => get<ManagedScript[]>('/scripts'),
  getScript: (scriptKey: string) => get<ManagedScript>(`/scripts/${scriptKey}`),
  upsertScript: (s: Partial<ManagedScript> & { script_key: string; name: string; code: string; input_schema: Record<string, unknown> }) =>
    post<ManagedScript>('/scripts', { language: 'python', status: 'active', owner_type: 'system', owner_key: '', description: '', ...s }),
  resetScript: (scriptKey: string) => post<ManagedScript>(`/scripts/${scriptKey}/reset`),
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

  // Knowledge Bases
  listKbs: () => get<KnowledgeBase[]>('/kbs'),
  createKb: (data: { slug: string; name: string; description?: string }) =>
    post<KnowledgeBase>('/kbs', data),
  updateKbDefaults: (kbSlug: string, data: { default_backend_slug?: string | null; default_agent_id?: string | null }) =>
    put<{ ok: boolean }>(`/kbs/${kbSlug}/defaults`, data),
  listKbRepoSources: (kbSlug: string) =>
    get<KbRepoSource[]>(`/kbs/${kbSlug}/repo-sources`),
  saveKbRepoSource: (kbSlug: string, data: { repo_key: string; include_suffixes: string[] }) =>
    post<KbRepoSource>(`/kbs/${kbSlug}/repo-sources`, data),
  syncKbRepoSource: (kbSlug: string, repoKey: string) =>
    post<KbRepoSourceSyncResult>(`/kbs/${kbSlug}/repo-sources/${repoKey}/sync`),
  deleteKbRepoSource: (kbSlug: string, repoKey: string) =>
    post<{ kb_slug: string; repo_key: string; deleted_docs: number }>(`/kbs/${kbSlug}/repo-sources/${repoKey}/delete`),
  deleteKnowledgeBase: (kbSlug: string) => post<{ deleted: boolean }>(`/kbs/${kbSlug}/delete`),

  // Documents
  listDocs: (kb: string, backend?: string) => {
    const qs = new URLSearchParams({ kb })
    if (backend) qs.set('backend', backend)
    return get<Document[]>(`/docs?${qs}`)
  },
  getDoc: (slug: string, backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return get<DocumentDetail>(`/docs/${slug}${qs.toString() ? '?' + qs : ''}`)
  },
  addDocument: (file: File, kbs: string[], later = false) => {
    const form = new FormData()
    form.append('file', file)
    kbs.forEach(kb => form.append('kb', kb))
    if (later) form.append('later', 'true')
    return postFormData<DocumentDetail>('/docs', form)
  },
  updateDocument: (slug: string, file: File, later = false) => {
    const form = new FormData()
    form.append('file', file)
    if (later) form.append('later', 'true')
    return postFormData<DocumentDetail>(`/docs/${slug}/versions`, form)
  },
  deleteDocument: (slug: string) =>
    post<{ slug: string; status: string }>(`/docs/${slug}/delete`),
  purgeDocument: (slug: string) =>
    post<{ slug: string; status: string }>(`/docs/${slug}/purge`, { confirm: true }),

  // Sync
  triggerSync: (backend?: string, allUsers = false) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return post<{ processed: number }>(`/sync${qs.toString() ? '?' + qs : ''}`, { all_users: allUsers })
  },
  getSyncStatus: (backend?: string) => {
    const qs = new URLSearchParams()
    if (backend) qs.set('backend', backend)
    return get<{ jobs: SyncJob[] }>(`/status${qs.toString() ? '?' + qs : ''}`)
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
  listBackends: () => get<BackendInfo[]>('/backends'),
  createBackend: (data: { slug: string; backend_type: string; base_url?: string | null; api_key?: string | null; timeout?: number; embedding_model_id?: string | null; summary_model_id?: string | null }) =>
    post<BackendInfo>('/backends', data),
  updateBackend: (slug: string, data: { backend_type?: string; base_url?: string | null; api_key?: string | null; timeout?: number; embedding_model_id?: string | null; summary_model_id?: string | null }) =>
    put<BackendInfo>(`/backends/${slug}`, data),
  deleteBackend: (slug: string) =>
    post<{ slug: string; status: string }>(`/backends/${slug}/delete`),

  // Backend Agents (Weknora)
  listBackendAgents: (slug: string) => get<BackendAgent[]>(`/backends/${slug}/agents`),
  listBackendAgentTypes: (slug: string) => get<BackendAgentPreset[]>(`/backends/${slug}/agent-types`),
  createBackendAgent: (slug: string, name: string, presetId: string) =>
    post<BackendAgent>(`/backends/${slug}/agents`, { name, preset_id: presetId }),
}
