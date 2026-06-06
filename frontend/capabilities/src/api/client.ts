import type {
  BackendInfo,
  CatalogSource,
  CodeGraphStatus,
  CodeGraphNode,
  CodeGraphExploreResult,
  CodeRepository,
  Document,
  DocumentDetail,
  KnowledgeBase,
  KnowledgeBaseSummary,
  KbMember,
  McpService,
  McpTool,
  ProjectProfile,
  ProfileSourceRule,
  ProfileResourceRule,
  RepoOverview,
  SearchResultChunk,
  SyncJob,
  ToolCallLog,
  ToolCallStats,
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

async function post<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json' },
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

  // Profiles
  listProfiles: () => get<ProjectProfile[]>('/capability-profiles'),
  getProfile: (key: string) => get<ProjectProfile>(`/capability-profiles/${key}`),
  upsertProfile: (p: Partial<ProjectProfile> & { profile_key: string; name: string }) =>
    post<ProjectProfile>('/capability-profiles', { status: 'active', ...p }),
  replaceProfileRules: (key: string, rules: ProfileSourceRule[]) =>
    put(`/capability-profiles/${key}/rules`, { rules }),
  replaceProfileResources: (key: string, resources: ProfileResourceRule[]) =>
    put(`/capability-profiles/${key}/resources`, { resources }),

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

  // Builtins
  listCodeRepos: () => get<CodeRepository[]>('/builtin/codegraph/repositories'),
  upsertCodeRepo: (r: Partial<CodeRepository> & { repo_key: string; name: string; git_url: string }) =>
    post<CodeRepository>('/builtin/codegraph/repositories', { status: 'active', ...r }),
  syncCodeRepo: (key: string) => post(`/builtin/codegraph/repositories/${key}/sync`),
  listWikiKbs: () => get<KnowledgeBaseSummary[]>('/builtin/wiki/kbs'),

  // CodeGraph detail
  getCodeGraphStatus: () => get<CodeGraphStatus>('/builtin/codegraph/status'),
  getRepoOverview: (repoKey: string) => get<RepoOverview>(`/builtin/codegraph/repositories/${repoKey}/overview`),
  listRepoFiles: (repoKey: string) => get<{ files: { path: string; language: string }[] }>(`/builtin/codegraph/repositories/${repoKey}/files`),
  queryRepo: (repoKey: string, query: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/builtin/codegraph/repositories/${repoKey}/query`, { query, limit }),
  exploreRepo: (repoKey: string, query: string) =>
    post<CodeGraphExploreResult>(`/builtin/codegraph/repositories/${repoKey}/explore`, { query }),
  findCallers: (repoKey: string, symbol: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/builtin/codegraph/repositories/${repoKey}/callers`, { query: symbol, limit }),
  findCallees: (repoKey: string, symbol: string, limit = 20) =>
    post<{ matches: CodeGraphNode[] }>(`/builtin/codegraph/repositories/${repoKey}/callees`, { query: symbol, limit }),
  analyzeImpact: (repoKey: string, symbol: string) =>
    post<{ matches: CodeGraphNode[] }>(`/builtin/codegraph/repositories/${repoKey}/impact`, { query: symbol }),

  // Knowledge Bases
  listKbs: () => get<KnowledgeBase[]>('/kbs'),
  createKb: (data: { slug: string; name: string; description?: string }) =>
    post<KnowledgeBase>('/kbs', data),
  listKbMembers: (slug: string) => get<KbMember[]>(`/kbs/${slug}/members`),
  grantKbMember: (slug: string, data: { linux_user: string; role: string }) =>
    post(`/kbs/${slug}/members`, data),

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
}
