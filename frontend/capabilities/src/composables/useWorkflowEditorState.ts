import { computed, nextTick, ref, watch, type Ref } from 'vue'
import {
  api,
  hasBlockingWorkflowValidationErrors,
  invalidateWorkflowValidationRun,
  workflowValidationErrorMessage,
  workflowValidationIssuesFor,
} from '../api/client'
import type { ProjectProfile, WorkflowDefinition, WorkflowDraft, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowNodeType, WorkflowTaskRefreshPolicy, WorkflowType, WorkflowValidationError } from '../api/types'
import { createDefaultGraph, migrateWorkflowGraph } from '../lib/workflowDefinition'

type Toast = (options: { title: string; description?: string; variant?: 'default' | 'success' | 'error' | 'warning' }) => number

export function useWorkflowEditorState(options: {
  defaultBackend: Ref<string>
  profiles: Ref<ProjectProfile[]>
  onSaved: (saved: WorkflowDefinition) => Promise<void>
  toast: Toast
}) {
  const form = ref({
    workflow_key: '', name: '', description: '', profile_key: '', status: 'active',
    workflow_type: 'operation' as WorkflowType,
    definition: createDefaultGraph('operation', options.defaultBackend.value) as WorkflowGraph,
  })
  const saving = ref(false)
  const formError = ref('')
  const formDirty = ref(false)
  const graphErrors = ref<WorkflowValidationError[]>([])
  const schemaEditorErrors = ref<Record<string, string>>({})
  const runValidationGuard = ref({ validating: false, token: 0 })
  const taskRefreshPolicy = ref<WorkflowTaskRefreshPolicy>('auto')
  const selectedNodeId = ref<string | null>(null)
  const selectedEdgeId = ref<string | null>(null)
  const configDrawerOpen = ref(false)
  const configDrawerMode = ref<'overlay' | 'fullscreen'>('overlay')
  const expectedEditVersion = ref<number | null>(0)
  let suppressDirty = false

  watch(form, () => {
    if (suppressDirty) return
    formDirty.value = true
    invalidateWorkflowValidationRun(runValidationGuard.value)
  }, { deep: true })
  watch(() => form.value.definition.nodes.map(node => node.id), (nodeIds) => {
    const activeIds = new Set(nodeIds)
    const next = Object.fromEntries(Object.entries(schemaEditorErrors.value).filter(([nodeId]) => activeIds.has(nodeId)))
    if (Object.keys(next).length !== Object.keys(schemaEditorErrors.value).length) schemaEditorErrors.value = next
  })

  function resetForm(next: typeof form.value) {
    invalidateWorkflowValidationRun(runValidationGuard.value)
    suppressDirty = true
    form.value = { ...next }
    schemaEditorErrors.value = {}
    formDirty.value = false
    void nextTick(() => { suppressDirty = false })
  }
  function prepareCreateForm() {
    expectedEditVersion.value = 0
    taskRefreshPolicy.value = 'auto'
    resetForm({
      workflow_key: '', name: '', description: '', profile_key: options.profiles.value[0]?.profile_key || '', status: 'active',
      workflow_type: 'operation', definition: createDefaultGraph('operation', options.defaultBackend.value),
    })
    clearEditorSelection()
  }
  function prepareEditForm(item: WorkflowDefinition) {
    expectedEditVersion.value = Number.isInteger(item.edit_version) ? item.edit_version : null
    taskRefreshPolicy.value = 'auto'
    const workflowType = item.workflow_type === 'summary' ? 'summary' : 'operation'
    resetForm({
      workflow_key: item.workflow_key, name: item.name, description: item.description, profile_key: item.profile_key, status: item.status,
      workflow_type: workflowType,
      definition: item.definition || createDefaultGraph(workflowType, options.defaultBackend.value),
    })
    clearEditorSelection()
  }
  function clearEditorSelection() {
    formError.value = ''
    graphErrors.value = []
    selectedNodeId.value = null
    selectedEdgeId.value = null
    configDrawerOpen.value = false
  }
  function normalizeIssue(value: unknown): WorkflowValidationError | null {
    if (!value || typeof value !== 'object') return null
    const raw = value as Record<string, unknown>
    if (raw.scope !== 'workflow' && raw.scope !== 'node' && raw.scope !== 'edge') return null
    const message = typeof raw.message === 'string' ? raw.message : ''
    if (!message) return null
    return { scope: raw.scope, id: typeof raw.id === 'string' ? raw.id : null, field: typeof raw.field === 'string' ? raw.field : null, message, code: typeof raw.code === 'string' ? raw.code : 'invalid_definition' }
  }
  function collectIssues(value: unknown): WorkflowValidationError[] {
    if (Array.isArray(value)) return value.map(normalizeIssue).filter((issue): issue is WorkflowValidationError => Boolean(issue))
    if (!value || typeof value !== 'object') return []
    const raw = value as Record<string, unknown>
    for (const key of ['issues', 'errors']) { const found = collectIssues(raw[key]); if (found.length) return found }
    return collectIssues(raw.detail)
  }
  function parseIssues(message: string) {
    const body = message.replace(/^\d+:\s*/, '').trim()
    try { const issues = collectIssues(JSON.parse(body)); if (issues.length) return issues } catch { /* plain text */ }
    const match = message.match(/"(?:errors|issues)"\s*:\s*(\[[\s\S]*?\])\s*[},]?/)
    if (!match) return []
    try { return collectIssues(JSON.parse(match[1])) } catch { return [] }
  }
  function scopedGraphIssues(scope: WorkflowValidationError['scope'], id: string | null) {
    return workflowValidationIssuesFor(graphErrors.value, scope, id)
  }
  function draft(): WorkflowDraft {
    return { workflow_key: form.value.workflow_key, name: form.value.name, description: form.value.description, profile_key: form.value.profile_key, status: form.value.status, workflow_type: form.value.workflow_type, definition: form.value.definition }
  }
  async function validateWorkflowDraft(request: { isCurrent?: () => boolean } = {}): Promise<boolean | null> {
    graphErrors.value = []
    const schemaError = Object.values(schemaEditorErrors.value).find(Boolean)
    if (schemaError) { formError.value = `保存前请修正 Schema：${schemaError}`; return false }
    const validation = await api.validateWorkflow(draft())
    if (request.isCurrent && !request.isCurrent()) return null
    if (!hasBlockingWorkflowValidationErrors(validation)) return true
    graphErrors.value = validation.errors
    formError.value = workflowValidationErrorMessage(validation)
    return false
  }
  async function saveWorkflow(): Promise<WorkflowDefinition | null> {
    formError.value = ''
    if (!form.value.workflow_key || !form.value.name || !form.value.profile_key) { formError.value = '请填写工作流标识、名称，并选择关联的能力平面'; return null }
    saving.value = true
    try {
      if (!await validateWorkflowDraft()) return null
      const saved = await api.upsertWorkflow({
        ...draft(),
        expected_edit_version: expectedEditVersion.value,
        task_refresh_policy: taskRefreshPolicy.value,
      })
      graphErrors.value = []
      formDirty.value = false
      expectedEditVersion.value = saved.edit_version
      await options.onSaved(saved)
      const refreshDescription = saved.task_refresh_policy === 'defer'
        ? '任务保持现有结果，未进入刷新队列。'
        : saved.tasks_marked_stale
          ? `已安排 ${saved.tasks_marked_stale} 个任务增量刷新。`
          : '没有需要刷新的历史任务。'
      options.toast({ title: '工作流已保存', description: `“${saved.name}” 已更新。${refreshDescription}`, variant: 'success' })
      return saved
    } catch (error: unknown) {
      formError.value = error instanceof Error ? error.message : '未知错误'
      graphErrors.value = parseIssues(formError.value)
      options.toast({ title: '保存工作流失败', description: formError.value, variant: 'error' })
      return null
    } finally { saving.value = false }
  }
  function changeWorkflowType(value: WorkflowType) {
    const previous = form.value.workflow_type
    form.value.workflow_type = value
    form.value.definition = migrateWorkflowGraph(form.value.definition, previous, value, options.defaultBackend.value)
    selectedNodeId.value = null; selectedEdgeId.value = null; configDrawerOpen.value = false
  }
  const selectedNode = computed(() => form.value.definition.nodes.find(node => node.id === selectedNodeId.value) || null)
  const selectedEdge = computed(() => form.value.definition.edges.find(edge => edge.id === selectedEdgeId.value) || null)
  function createNode(type: WorkflowNodeType, position = { x: 120 + form.value.definition.nodes.length * 36, y: 160 + form.value.definition.nodes.length * 32 }): WorkflowNode {
    const id = `${type}-${Date.now()}`
    if (type === 'get_task') return { id, type, name: '获取任务', position, config: { on_empty: 'terminate' } }
    if (type === 'agent') return { id, type, name: 'Agent', position, config: { prompt: '', backend_key: options.defaultBackend.value, mcp_enabled: true, skill_names: [], timeout_seconds: 600, result_mode: 'text', output_schema: null } }
    if (type === 'script') return { id, type, name: '托管脚本', position, config: { script_key: '', params: {}, timeout_seconds: 60 } }
    return { id, type, name: '输出结果', position, config: { format: 'markdown', title: '输出结果', path: 'reports/output.md', tags: [], prompt: '', backend_key: options.defaultBackend.value, mcp_enabled: false, skill_names: [], timeout_seconds: 600 } }
  }
  function addNode(type: WorkflowNodeType, position?: { x: number; y: number }) {
    if (form.value.workflow_type === 'summary' && type === 'output') {
      formError.value = '总结型工作流的输出节点已固定'
      return
    }
    form.value.definition = { ...form.value.definition, nodes: [...form.value.definition.nodes, createNode(type, position)] }
  }
  function selectWorkflowNode(id: string) {
    selectedNodeId.value = id
    selectedEdgeId.value = null
    configDrawerOpen.value = true
  }
  function selectWorkflowEdge(id: string) {
    selectedEdgeId.value = id
    selectedNodeId.value = null
    configDrawerOpen.value = true
  }
  function setConfigDrawerOpen(open: boolean) {
    configDrawerOpen.value = open
  }
  function setConfigDrawerMode(mode: 'overlay' | 'fullscreen') {
    configDrawerMode.value = mode
  }
  function setNodeSchemaValidity(nodeId: string, valid: boolean, message: string) {
    const next = { ...schemaEditorErrors.value }
    if (valid) delete next[nodeId]
    else next[nodeId] = message || 'Schema 不合法'
    schemaEditorErrors.value = next
  }
  function replaceNode(node: WorkflowNode) { form.value.definition = { ...form.value.definition, nodes: form.value.definition.nodes.map(item => item.id === node.id ? node : item) } }
  function replaceEdge(edge: WorkflowEdge) { form.value.definition = { ...form.value.definition, edges: form.value.definition.edges.map(item => item.id === edge.id ? edge : item) } }
  return { form, saving, formError, formDirty, graphErrors, schemaEditorErrors, runValidationGuard, taskRefreshPolicy, selectedNodeId, selectedEdgeId, configDrawerOpen, configDrawerMode, selectedNode, selectedEdge, resetForm, prepareCreateForm, prepareEditForm, scopedGraphIssues, parseWorkflowIssues: parseIssues, workflowDraft: draft, validateWorkflowDraft, saveWorkflow, changeWorkflowType, createNode, addNode, selectWorkflowNode, selectWorkflowEdge, setConfigDrawerOpen, setConfigDrawerMode, setNodeSchemaValidity, replaceNode, replaceEdge }
}
