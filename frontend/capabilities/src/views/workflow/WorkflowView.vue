<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Play, Plus, Save } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { ManagedScript, ProjectProfile, SkillPrompt, WorkflowDefinition, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowNodeRun, WorkflowNodeType, WorkflowType, WorkflowValidationError } from '../../api/types'
import Button from '../../components/ui/button/Button.vue'
import Input from '../../components/ui/input/Input.vue'
import Textarea from '../../components/ui/textarea/Textarea.vue'
import WorkflowEditorCanvas from './WorkflowEditorCanvas.vue'
import WorkflowNodePalette from './WorkflowNodePalette.vue'
import WorkflowNodeConfigPanel from './WorkflowNodeConfigPanel.vue'
import WorkflowEdgeConfigPanel from './WorkflowEdgeConfigPanel.vue'
import WorkflowRunGraph from './WorkflowRunGraph.vue'
import { createDefaultGraph, deriveManualInputFields } from './workflowDefinition'

const props = defineProps<{ routeKey: string }>()
const workflows = ref<WorkflowDefinition[]>([])
const profiles = ref<ProjectProfile[]>([])
const skills = ref<SkillPrompt[]>([])
const scripts = ref<ManagedScript[]>([])
const defaultBackend = ref('codex')
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const validationErrors = ref<WorkflowValidationError[]>([])
const selectedNodeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const manualValues = ref<Record<string, string>>({})
const advancedInput = ref('{}')
const run = ref<{ status: string; definition_snapshot: WorkflowGraph; node_runs: WorkflowNodeRun[]; run_id: string; error: string | null } | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | undefined

type FormState = { workflow_key: string; name: string; description: string; profile_key: string; status: string; workflow_type: WorkflowType; definition: WorkflowGraph }
const form = ref<FormState>(emptyForm())
const baseline = ref('')
const routeParts = computed(() => props.routeKey.split('/').filter(Boolean))
const routeWorkflowKey = computed(() => routeParts.value[0] || '')
const isNew = computed(() => routeWorkflowKey.value === 'new')
const isProgress = computed(() => routeParts.value[1] === 'progress')
const isEditor = computed(() => isNew.value || routeParts.value[1] === 'edit')
const selectedWorkflow = computed(() => workflows.value.find(item => item.workflow_key === routeWorkflowKey.value) || null)
const selectedNode = computed(() => form.value.definition.nodes.find(node => node.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => form.value.definition.edges.find(edge => edge.id === selectedEdgeId.value) || null)
const hasTaskNode = computed(() => form.value.definition.nodes.some(node => node.type === 'get_task'))
const manualFields = computed(() => deriveManualInputFields(form.value.definition, scripts.value))
const formDirty = computed(() => JSON.stringify(form.value) !== baseline.value)
const backendKeys = computed(() => [defaultBackend.value, 'claude', 'opencode', 'codex'].filter((value, index, all) => !!value && all.indexOf(value) === index))

function emptyForm(type: WorkflowType = 'operation'): FormState { return { workflow_key: '', name: '', description: '', profile_key: '', status: 'active', workflow_type: type, definition: createDefaultGraph(type, defaultBackend.value) } }
function setForm(next: FormState) { form.value = structuredClone(next); baseline.value = JSON.stringify(form.value); selectedNodeId.value = null; selectedEdgeId.value = null; validationErrors.value = [] }
function goList() { window.location.hash = 'workflow' }
function openNew() { window.location.hash = 'workflow/new' }
function openDetail(workflow: WorkflowDefinition) { window.location.hash = `workflow/${workflow.workflow_key}` }
function openEdit() { if (selectedWorkflow.value) window.location.hash = `workflow/${selectedWorkflow.value.workflow_key}/edit` }
function openRun(runId: string) { if (selectedWorkflow.value) window.location.hash = `workflow/${selectedWorkflow.value.workflow_key}/progress/${runId}` }

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [workflowData, profileData, skillData, scriptData, runtime] = await Promise.all([api.listWorkflows(), api.listProfiles(), api.listSkills(), api.listScripts(), api.getAgentRuntimeConfig()])
    workflows.value = workflowData; profiles.value = profileData; skills.value = skillData; scripts.value = scriptData; defaultBackend.value = runtime.default_backend || defaultBackend.value
  } catch (cause) { error.value = message(cause) } finally { loading.value = false }
}
async function applyRoute() {
  stopPolling(); run.value = null
  if (!routeWorkflowKey.value) return
  if (isNew.value) { setForm(emptyForm()); return }
  try {
    const workflow = await api.getWorkflow(routeWorkflowKey.value)
    const definition = workflow.definition || createDefaultGraph(workflow.workflow_type, defaultBackend.value)
    setForm({ workflow_key: workflow.workflow_key, name: workflow.name, description: workflow.description, profile_key: workflow.profile_key, status: workflow.status, workflow_type: workflow.workflow_type, definition })
    if (isProgress.value && routeParts.value[2]) { await refreshRun(routeParts.value[2]); schedulePoll() }
  } catch (cause) { error.value = message(cause) }
}
function message(cause: unknown) { return cause instanceof Error ? cause.message : '未知错误' }
function createNode(type: WorkflowNodeType, position = { x: 120 + form.value.definition.nodes.length * 30, y: 160 + form.value.definition.nodes.length * 30 }): WorkflowNode {
  const id = `${type}-${Date.now()}`
  if (type === 'get_task') return { id, type, name: '获取任务', position, config: {} }
  if (type === 'agent') return { id, type, name: 'Agent', position, config: { prompt: '', backend_key: defaultBackend.value, mcp_enabled: true, skill_names: [], result_mode: 'text', output_schema: null } }
  if (type === 'script') return { id, type, name: '托管脚本', position, config: { script_key: '', params: {}, timeout_seconds: 60 } }
  return { id, type, name: '输出结果', position, config: { format: 'markdown', title: '输出结果', path: 'reports/output.md', tags: [], prompt: '', backend_key: defaultBackend.value, mcp_enabled: false, skill_names: [] } }
}
function addNode(type: WorkflowNodeType, position?: { x: number; y: number }) { if (form.value.workflow_type === 'summary' && type === 'output') { error.value = '总结型工作流的输出节点已固定'; return } form.value.definition = { ...form.value.definition, nodes: [...form.value.definition.nodes, createNode(type, position)] } }
function replaceNode(node: WorkflowNode) { form.value.definition = { ...form.value.definition, nodes: form.value.definition.nodes.map(item => item.id === node.id ? node : item) } }
function replaceEdge(edge: WorkflowEdge) { form.value.definition = { ...form.value.definition, edges: form.value.definition.edges.map(item => item.id === edge.id ? edge : item) } }
function changeType(type: WorkflowType) { form.value.workflow_type = type; form.value.definition = createDefaultGraph(type, defaultBackend.value) }
function parseServerErrors(text: string) { const matched = text.match(/"errors"\s*:\s*(\[[\s\S]*\])/); if (!matched) return []; try { return JSON.parse(matched[1]) as WorkflowValidationError[] } catch { return [] } }
async function saveWorkflow(): Promise<WorkflowDefinition | null> {
  error.value = ''; validationErrors.value = []
  if (!form.value.workflow_key || !form.value.name || !form.value.profile_key) { error.value = '请填写工作流标识、名称和关联 profile'; return null }
  saving.value = true
  try {
    const saved = await api.upsertWorkflow({ ...form.value })
    workflows.value = [...workflows.value.filter(item => item.workflow_key !== saved.workflow_key), saved]
    setForm({ workflow_key: saved.workflow_key, name: saved.name, description: saved.description, profile_key: saved.profile_key, status: saved.status, workflow_type: saved.workflow_type, definition: saved.definition || form.value.definition })
    return saved
  } catch (cause) { error.value = message(cause); validationErrors.value = parseServerErrors(error.value); return null } finally { saving.value = false }
}
function readManualInput(): Record<string, unknown> | null {
  let input: Record<string, unknown>
  try { const parsed = advancedInput.value.trim() ? JSON.parse(advancedInput.value) : {}; if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(); input = parsed as Record<string, unknown> } catch { error.value = '高级 JSON 必须是对象'; return null }
  for (const field of manualFields.value) {
    const raw = manualValues.value[field.path] ?? ''
    if (field.required && !raw.trim()) { error.value = `${field.path} 为必填项`; return null }
    if (!raw.trim()) continue
    const value: unknown = field.type === 'integer' || field.type === 'number' ? Number(raw) : field.type === 'boolean' ? raw === 'true' : raw
    const parts = field.path.replace(/^input\./, '').split('.'); let current = input
    parts.forEach((part, index) => { if (index === parts.length - 1) current[part] = value; else current = (current[part] ||= {}) as Record<string, unknown> })
  }
  return input
}
async function testWorkflow() {
  if (formDirty.value) { error.value = '有未保存的修改，请先保存后再测试运行'; return }
  if (!selectedWorkflow.value) return
  const input = hasTaskNode.value ? {} : readManualInput(); if (input === null) return
  testing.value = true; error.value = ''
  try { const result = await api.runWorkflow(selectedWorkflow.value.workflow_key, input); if (result.run_id) openRun(result.run_id); else error.value = result.status } catch (cause) { error.value = message(cause) } finally { testing.value = false }
}
async function refreshRun(runId: string) { try { run.value = await api.getWorkflowRun(runId) as typeof run.value } catch (cause) { error.value = message(cause); stopPolling() } }
function schedulePoll() { if (!run.value || ['completed', 'no_task', 'failed', 'stopped'].includes(run.value.status)) return; pollTimer = setTimeout(async () => { if (routeParts.value[2]) { await refreshRun(routeParts.value[2]); schedulePoll() } }, 1500) }
function stopPolling() { if (pollTimer) clearTimeout(pollTimer); pollTimer = undefined }

onMounted(async () => { await loadData(); await applyRoute() })
watch(() => props.routeKey, applyRoute)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="space-y-5">
    <div v-if="error" class="border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{{ error }}</div>
    <section v-if="!routeWorkflowKey" class="space-y-4">
      <div class="flex items-center justify-between"><div><h2 class="text-lg font-semibold">工作流</h2><p class="text-sm text-muted-foreground">结构化 DAG 定义与运行记录。</p></div><Button size="sm" @click="openNew"><Plus class="mr-1 h-4 w-4" />新建工作流</Button></div>
      <div v-if="loading" class="py-10 text-sm text-muted-foreground">加载中</div>
      <div v-else-if="!workflows.length" class="border py-10 text-center text-sm text-muted-foreground">暂无工作流</div>
      <div v-else class="divide-y border"><button v-for="workflow in workflows" :key="workflow.workflow_key" type="button" class="grid w-full gap-3 px-4 py-3 text-left hover:bg-muted/40 md:grid-cols-[minmax(0,1fr)_160px_100px] md:items-center" @click="openDetail(workflow)"><div class="min-w-0"><div class="truncate text-sm font-medium">{{ workflow.name }}</div><div class="mt-1 truncate font-mono text-xs text-muted-foreground">{{ workflow.workflow_key }}</div></div><div class="text-xs text-muted-foreground">{{ workflow.definition ? workflow.definition.nodes.length + ' 个节点' : '需要迁移' }}</div><div class="text-xs text-muted-foreground">{{ workflow.status === 'active' ? '启用' : '停用' }}</div></button></div>
    </section>

    <section v-else-if="isProgress" class="space-y-4">
      <div class="flex items-center justify-between"><div class="flex items-center gap-2"><Button variant="ghost" size="sm" class="h-8 w-8 p-0" title="返回" @click="selectedWorkflow && openDetail(selectedWorkflow)"><ArrowLeft class="h-4 w-4" /></Button><div><h2 class="text-lg font-semibold">运行详情</h2><p class="font-mono text-xs text-muted-foreground">{{ run?.run_id || routeParts[2] }}</p></div></div><span class="text-sm text-muted-foreground">{{ run?.status || '加载中' }}</span></div>
      <WorkflowRunGraph v-if="run" :definition-snapshot="run.definition_snapshot" :node-runs="run.node_runs" @open-agent-run="key => window.location.hash = 'agent-runs/' + key" @open-script-run="id => window.location.hash = 'scripts/' + id" />
    </section>

    <section v-else-if="isEditor" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3"><div class="flex items-center gap-2"><Button variant="ghost" size="sm" class="h-8 w-8 p-0" title="返回" @click="selectedWorkflow ? openDetail(selectedWorkflow) : goList"><ArrowLeft class="h-4 w-4" /></Button><div><h2 class="text-lg font-semibold">{{ isNew ? '新建工作流' : '编辑工作流' }}</h2><p v-if="!selectedWorkflow?.definition && !isNew" class="text-xs text-amber-700">历史工作流需要迁移；保存后会写入结构化定义。</p></div></div><div class="flex gap-2"><Button variant="outline" size="sm" :disabled="saving" @click="saveWorkflow"><Save class="mr-1 h-4 w-4" />{{ saving ? '保存中' : '保存' }}</Button><Button size="sm" :disabled="testing || isNew" @click="testWorkflow"><Play class="mr-1 h-4 w-4" />测试运行</Button></div></div>
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4"><div><label class="mb-1 block text-xs text-muted-foreground">workflow_key</label><Input v-model="form.workflow_key" :disabled="!isNew" /></div><div><label class="mb-1 block text-xs text-muted-foreground">名称</label><Input v-model="form.name" /></div><div><label class="mb-1 block text-xs text-muted-foreground">Profile</label><select v-model="form.profile_key" class="h-8 w-full rounded-sm border bg-background px-2 text-sm"><option value="">选择 Profile</option><option v-for="profile in profiles" :key="profile.profile_key" :value="profile.profile_key">{{ profile.name }}</option></select></div><div><label class="mb-1 block text-xs text-muted-foreground">工作流类型</label><select :value="form.workflow_type" class="h-8 w-full rounded-sm border bg-background px-2 text-sm" @change="changeType(($event.target as HTMLSelectElement).value as WorkflowType)"><option value="operation">操作</option><option value="summary">总结</option></select></div></div>
      <div><label class="mb-1 block text-xs text-muted-foreground">描述</label><Input v-model="form.description" /></div>
      <div class="grid min-h-[520px] grid-cols-[132px_minmax(0,1fr)] xl:grid-cols-[132px_minmax(0,1fr)_340px]"><WorkflowNodePalette @add-node="addNode" /><WorkflowEditorCanvas v-model:graph="form.definition" :workflow-type="form.workflow_type" :errors="validationErrors" @select-node="id => { selectedNodeId = id; selectedEdgeId = null }" @select-edge="id => { selectedEdgeId = id; selectedNodeId = null }" @add-node="addNode" /><WorkflowNodeConfigPanel v-if="selectedNode" :node="selectedNode" :scripts="scripts" :skills="skills" :backends="backendKeys" @replace="replaceNode" /><WorkflowEdgeConfigPanel v-else-if="selectedEdge" :edge="selectedEdge" @replace="replaceEdge" /><aside v-else class="hidden border-l p-4 text-sm text-muted-foreground xl:block">选择一个节点或连线进行配置。</aside></div>
      <div v-if="!hasTaskNode" class="grid gap-3 border p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"><div><div class="mb-2 text-sm font-semibold">测试输入</div><div v-for="field in manualFields" :key="field.path" class="mb-2"><label class="mb-1 block text-xs text-muted-foreground">{{ field.path }}<span v-if="field.required" class="text-destructive"> *</span></label><Input v-model="manualValues[field.path]" :placeholder="field.description || field.type" /></div><p v-if="!manualFields.length" class="text-xs text-muted-foreground">当前脚本参数没有可推导输入字段。</p></div><div><label class="mb-1 block text-sm font-semibold">高级 JSON</label><Textarea v-model="advancedInput" class="min-h-40 font-mono text-xs" /></div></div>
    </section>

    <section v-else-if="selectedWorkflow" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3"><div><h2 class="text-lg font-semibold">{{ selectedWorkflow.name }}</h2><p class="font-mono text-xs text-muted-foreground">{{ selectedWorkflow.workflow_key }}</p></div><div class="flex gap-2"><Button variant="outline" size="sm" @click="openEdit">编辑</Button><Button size="sm" :disabled="testing || !selectedWorkflow.definition" @click="testWorkflow"><Play class="mr-1 h-4 w-4" />{{ testing ? '启动中' : '测试运行' }}</Button></div></div>
      <div v-if="!selectedWorkflow.definition" class="border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">该历史工作流需要迁移。进入编辑页并显式保存后才会写入新定义。</div>
      <div v-if="selectedWorkflow.definition && !hasTaskNode" class="grid gap-3 border p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"><div><div class="mb-2 text-sm font-semibold">测试输入</div><div v-for="field in manualFields" :key="field.path" class="mb-2"><label class="mb-1 block text-xs text-muted-foreground">{{ field.path }}<span v-if="field.required" class="text-destructive"> *</span></label><Input v-model="manualValues[field.path]" :placeholder="field.description || field.type" /></div><p v-if="!manualFields.length" class="text-xs text-muted-foreground">当前脚本参数没有可推导输入字段。</p></div><div><label class="mb-1 block text-sm font-semibold">高级 JSON</label><Textarea v-model="advancedInput" class="min-h-40 font-mono text-xs" /></div></div>
      <div v-if="selectedWorkflow.definition" class="border p-4"><div class="mb-3 text-sm font-semibold">工作流定义</div><WorkflowRunGraph :definition-snapshot="selectedWorkflow.definition" :node-runs="[]" @open-agent-run="() => undefined" @open-script-run="() => undefined" /></div>
    </section>
  </div>
</template>
