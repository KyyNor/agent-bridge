<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { ArrowLeft, Check, HelpCircle, Save, WandSparkles } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { ProjectProfile, ArtifactTreeNode, DesignAgentResponse, WorkflowArtifact, WorkflowArtifactDetail, WorkflowArtifactHistoryVersion, WorkflowDefinition, WorkflowDesignResult, WorkflowRun, WorkflowRunEvent, WorkflowRunLog, WorkflowTask } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import WorkflowDagGraph from './WorkflowDagGraph.vue'
import { parseWorkflowDag } from './workflowDag'
import {
  ALL_TYPE_SENTINEL,
  filterAndSortTasks,
  distinctStatuses,
  distinctTypes,
  taskStats as computeTaskStats,
  taskStatusLabel as labelTaskStatus,
} from '../../lib/workflowTasks'
import {
  distinctActors,
  filterEventsByActor,
  groupEventsByActor,
  subagentUsage,
  subagentStatus,
} from '../../lib/workflowEvents'

const artifactToolName = 'artifacts_search'
const WORKFLOW_RUN_LIMIT = 200
const props = defineProps<{ routeKey: string }>()

const workflows = ref<WorkflowDefinition[]>([])
const profiles = ref<ProjectProfile[]>([])
const artifacts = ref<WorkflowArtifact[]>([])
const selectedKey = ref('')
const loading = ref(true)
const artifactLoading = ref(false)
const error = ref('')
const artifactError = ref('')
const saving = ref(false)
const formError = ref('')
const artifactQuery = ref('')
const artifactPath = ref('')
const artifactTags = ref('')
const artifactDetail = ref<WorkflowArtifactDetail | null>(null)
const artifactHistory = ref<WorkflowArtifactHistoryVersion[]>([])
const artifactHistoryTarget = ref<WorkflowArtifact | null>(null)
const detailLoading = ref(false)
const historyLoading = ref(false)
const showArtifact = ref(false)
const showArtifactHistory = ref(false)
const showGuide = ref(false)
const showClearConfirm = ref(false)
const progressWorkflowKey = ref('')
const progressRunId = ref('')
const taskWorkflowKey = ref('')
const workflowRuns = ref<Record<string, WorkflowRun[]>>({})
const workflowTasks = ref<Record<string, WorkflowTask[]>>({})
const runsLoading = ref(false)
const tasksLoading = ref(false)
const selectedRunId = ref('')
const runEvents = ref<WorkflowRunEvent[]>([])
const runLogs = ref<WorkflowRunLog[]>([])
const logsLoading = ref(false)
const taskError = ref('')
const clearing = ref(false)
const clearTarget = ref<WorkflowDefinition | null>(null)
const expandedTaskIds = ref<Set<string>>(new Set())
const taskRunLogs = ref<Record<string, WorkflowRunLog[]>>({})
const taskRunEvents = ref<Record<string, WorkflowRunEvent[]>>({})
const taskLogLoading = ref<Set<string>>(new Set())
// Task progress page: client-side filter / search / sort (feature 1).
const taskStatusFilter = ref('')
const taskTypeFilter = ref('__all__')
const taskSearchInput = ref('')
const taskSearch = ref('')
const taskSort = ref('default')
// Per-task artifacts fetched on demand (feature 2 — view outputs from tasks page).
const expandedArtifactIds = ref<Set<string>>(new Set())
const taskArtifacts = ref<Record<string, WorkflowArtifact[]>>({})
const taskArtifactLoading = ref<Set<string>>(new Set())
const taskArtifactError = ref('')
// Execute (priority run) / reset (feature 3 & 4).
const taskActionLoading = ref<Set<string>>(new Set())
const taskActionError = ref('')
const resetTarget = ref<WorkflowTask | null>(null)
const resetting = ref(false)
// Per-task sub-agent event filter (feature 5). Keyed by task id; "" = all.
const taskActorFilter = ref<Record<string, string>>({})
const taskArtifactActive = ref<Record<string, string>>({})
let taskSearchDebounce: ReturnType<typeof setTimeout> | null = null
const testing = ref(false)
const testingRunId = ref('')
const testError = ref('')
const routeError = ref('')
const showDesigner = ref(false)
const designMode = ref<'create' | 'modify'>('modify')
const designPrompt = ref('')
const designing = ref(false)
const designError = ref('')
const designResponse = ref<DesignAgentResponse<WorkflowDesignResult> | null>(null)
let testPoll: ReturnType<typeof setInterval> | null = null

const artifactHtml = computed(() =>
  artifactDetail.value ? marked.parse(artifactDetail.value.content, { async: false }) as string : '',
)

function renderMarkdown(content: string) {
  return marked.parse(content, { async: false }) as string
}

const form = ref({
  workflow_key: '',
  name: '',
  description: '',
  profile_key: '',
  status: 'active',
  workflow_js: '',
})

const selectedWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === selectedKey.value) || workflows.value[0] || null
)
const routeParts = computed(() => props.routeKey.split('/').filter(Boolean))
const routeWorkflowKey = computed(() => routeParts.value[0] || '')
const routeMode = computed<'list' | 'new' | 'edit' | 'detail' | 'tasks' | 'progress'>(() => {
  if (!props.routeKey) return 'list'
  if (routeParts.value[0] === 'new') return 'new'
  const action = routeParts.value[1]
  if (action === 'edit' || action === 'tasks' || action === 'progress') return action
  return 'detail'
})
const isWorkflowFormPage = computed(() => routeMode.value === 'new' || routeMode.value === 'edit')
const pageWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === routeWorkflowKey.value) || null
)

const workflowDag = computed(() => parseWorkflowDag(selectedWorkflow.value?.workflow_js || ''))
const selectedProfileName = computed(() => profileName(selectedWorkflow.value?.profile_key || ''))
const runs = computed(() => workflowRuns.value[selectedWorkflow.value?.workflow_key || ''] || [])
const hasAnyRunningRun = computed(() =>
  Object.values(workflowRuns.value).some(items => items.some(run => run.status === 'running')),
)
const progressWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === progressWorkflowKey.value) || selectedWorkflow.value
)
const taskWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === taskWorkflowKey.value) || selectedWorkflow.value
)
const tasks = computed(() => workflowTasks.value[taskWorkflow.value?.workflow_key || ''] || [])
const taskStats = computed(() => computeTaskStats(tasks.value))
/** Distinct status values present, in the canonical display order. */
const taskStatuses = computed(() => distinctStatuses(tasks.value))
/** Distinct, non-empty type values present. */
const taskTypes = computed(() => distinctTypes(tasks.value))
/** Tasks after client-side filter + sort. The server already applies a default
 *  status-priority order; client sort only reshuffles when the user picks an
 *  explicit mode. */
const filteredTasks = computed(() =>
  filterAndSortTasks(tasks.value, {
    status: taskStatusFilter.value,
    type: taskTypeFilter.value,
    search: taskSearch.value,
    sort: taskSort.value,
  }),
)
function toggleStatusFilter(status: string) {
  taskStatusFilter.value = taskStatusFilter.value === status ? '' : status
}
function resetTaskFilters() {
  taskStatusFilter.value = ''
  taskTypeFilter.value = ALL_TYPE_SENTINEL
  taskSearchInput.value = ''
  taskSearch.value = ''
  taskSort.value = 'default'
}
const progressRun = computed(() =>
  (workflowRuns.value[progressWorkflowKey.value] || []).find(run => run.run_id === progressRunId.value) || null,
)
const workflowDesignDraft = computed(() => designResponse.value?.result?.workflow || null)

const collapsedPaths = ref<Set<string>>(new Set())

function togglePath(path: string) {
  const next = new Set(collapsedPaths.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsedPaths.value = next
}

function countArtifacts(node: ArtifactTreeNode): number {
  return node.artifacts.length + node.children.reduce((sum, child) => sum + countArtifacts(child), 0)
}

const artifactTree = computed<ArtifactTreeNode[]>(() => {
  const root: ArtifactTreeNode[] = []
  for (const item of artifacts.value) {
    const segments = item.path.split('/').filter(Boolean)
    let nodes = root
    let acc = ''
    segments.forEach((segment, index) => {
      acc = acc ? `${acc}/${segment}` : segment
      let node = nodes.find(child => child.segment === segment)
      if (!node) {
        node = { segment, path: acc, children: [], artifacts: [] }
        nodes.push(node)
      }
      if (index === segments.length - 1) node.artifacts.push(item)
      nodes = node.children
    })
  }
  const sortNodes = (nodes: ArtifactTreeNode[]) => {
    nodes.sort((a, b) => a.segment.localeCompare(b.segment))
    nodes.forEach(child => sortNodes(child.children))
  }
  sortNodes(root)
  return root
})

interface ArtifactTreeRow {
  type: 'folder' | 'artifact'
  depth: number
  path: string
  segment?: string
  count?: number
  artifact?: WorkflowArtifact
}

const artifactRows = computed<ArtifactTreeRow[]>(() => {
  const rows: ArtifactTreeRow[] = []
  const walk = (nodes: ArtifactTreeNode[], depth: number) => {
    for (const node of nodes) {
      rows.push({ type: 'folder', depth, path: node.path, segment: node.segment, count: countArtifacts(node) })
      if (!collapsedPaths.value.has(node.path)) {
        for (const item of node.artifacts) {
          rows.push({ type: 'artifact', depth: depth + 1, path: item.path, artifact: item })
        }
        walk(node.children, depth + 1)
      }
    }
  }
  walk(artifactTree.value, 0)
  return rows
})

onMounted(async () => {
  await loadAll()
  await applyRoute()
})

watch(selectedKey, () => {
  testError.value = ''
})

watch(
  () => props.routeKey,
  async () => applyRoute(),
)

onUnmounted(() => stopTestPolling())

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [workflowList, profileList] = await Promise.all([
      api.listWorkflows(),
      api.listProfiles(),
    ])
    workflows.value = workflowList
    profiles.value = profileList
    selectedKey.value = selectedKey.value || workflowList[0]?.workflow_key || ''
    await loadRunsForWorkflows(workflowList)
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function loadRunsForWorkflows(items = workflows.value) {
  runsLoading.value = true
  try {
    const entries = await Promise.all(
      items.map(async item => [item.workflow_key, await api.listWorkflowRuns(item.workflow_key, WORKFLOW_RUN_LIMIT)] as const),
    )
    workflowRuns.value = Object.fromEntries(entries)
    const runningEntry = entries.find(([, runList]) => runList.some(run => run.status === 'running'))
    const running = runningEntry?.[1].find(run => run.status === 'running')
    if (running && runningEntry) {
      testing.value = true
      testingRunId.value = running.run_id
      progressWorkflowKey.value = progressWorkflowKey.value || runningEntry[0]
    } else {
      testing.value = false
      testingRunId.value = ''
      stopTestPolling()
    }
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    runsLoading.value = false
  }
}

async function searchArtifacts() {
  artifactLoading.value = true
  artifactError.value = ''
  try {
    const result = await api.searchWorkflowArtifacts({
      profile_key: selectedWorkflow.value?.profile_key || form.value.profile_key || undefined,
      workflow_key: selectedWorkflow.value?.workflow_key || undefined,
      query: artifactQuery.value || undefined,
      path: artifactPath.value || undefined,
      tags: artifactTags.value.split(',').map(tag => tag.trim()).filter(Boolean),
      limit: 30,
    })
    artifacts.value = result.items
  } catch (e: unknown) {
    artifactError.value = errorMessage(e)
  } finally {
    artifactLoading.value = false
  }
}

function openCreate() {
  window.location.hash = 'workflow/new'
}

function prepareCreateForm() {
  form.value = {
    workflow_key: '',
    name: '',
    description: '',
    profile_key: profiles.value[0]?.profile_key || '',
    status: 'active',
    workflow_js: '',
  }
  formError.value = ''
}

function openEdit(item: WorkflowDefinition) {
  window.location.hash = `workflow/${item.workflow_key}/edit`
}

function prepareEditForm(item: WorkflowDefinition) {
  form.value = {
    workflow_key: item.workflow_key,
    name: item.name,
    description: item.description,
    profile_key: item.profile_key,
    status: item.status,
    workflow_js: item.workflow_js,
  }
  formError.value = ''
}

async function saveWorkflow(): Promise<WorkflowDefinition | null> {
  formError.value = ''
  if (!form.value.workflow_key || !form.value.name || !form.value.profile_key) {
    formError.value = '请填写工作流标识、名称，并选择关联的能力平面'
    return null
  }
  saving.value = true
  try {
    const saved = await api.upsertWorkflow({
      workflow_key: form.value.workflow_key,
      name: form.value.name,
      description: form.value.description,
      profile_key: form.value.profile_key,
      status: form.value.status,
      workflow_js: form.value.workflow_js,
    })
    selectedKey.value = saved.workflow_key
    workflows.value = await api.listWorkflows()
    await loadRunsForWorkflows()
    window.location.hash = `workflow/${saved.workflow_key}/detail`
    return saved
  } catch (e: unknown) {
    formError.value = errorMessage(e)
    return null
  } finally {
    saving.value = false
  }
}

function openWorkflowDesigner(mode: 'create' | 'modify' = 'modify') {
  designMode.value = mode
  showDesigner.value = true
  designError.value = ''
}

function workflowDesignerCurrent() {
  if (designMode.value === 'modify') {
    return {
      workflow_key: form.value.workflow_key,
      name: form.value.name,
      description: form.value.description,
      profile_key: form.value.profile_key,
      status: form.value.status,
      workflow_js: form.value.workflow_js,
    }
  }
  return {
    profile_key: form.value.profile_key,
    status: 'active',
  }
}

async function runWorkflowDesigner() {
  designError.value = ''
  if (!designPrompt.value.trim()) {
    designError.value = '请输入提示词'
    return
  }
  designing.value = true
  try {
    designResponse.value = await api.designWorkflow({
      mode: designMode.value,
      prompt: designPrompt.value,
      current: workflowDesignerCurrent(),
      profile_key: form.value.profile_key || undefined,
    })
    if (!designResponse.value.ok) {
      designError.value = designResponse.value.error || '设计 agent 执行失败'
    }
  } catch (e: unknown) {
    designError.value = errorMessage(e)
  } finally {
    designing.value = false
  }
}

async function acceptWorkflowDesign() {
  const draft = workflowDesignDraft.value
  if (!draft) return
  form.value = {
    workflow_key: draft.workflow_key,
    name: draft.name,
    description: draft.description,
    profile_key: draft.profile_key,
    status: draft.status,
    workflow_js: draft.workflow_js,
  }
  const saved = await saveWorkflow()
  if (saved) showDesigner.value = false
}

async function openDetail(item: WorkflowDefinition) {
  window.location.hash = `workflow/${item.workflow_key}/detail`
}

async function prepareDetail(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  await Promise.all([searchArtifacts(), loadRuns(item.workflow_key)])
}

function goList() {
  window.location.hash = 'workflow'
}

async function applyRoute() {
  routeError.value = ''
  if (!props.routeKey || loading.value) return
  if (routeMode.value === 'new') {
    prepareCreateForm()
    return
  }
  const workflow = pageWorkflow.value
  if (!workflow) {
    routeError.value = '无法加载该工作流（可能已被删除或不存在）'
    return
  }
  selectedKey.value = workflow.workflow_key
  if (routeMode.value === 'edit') {
    prepareEditForm(workflow)
  } else if (routeMode.value === 'detail') {
    await prepareDetail(workflow)
  } else if (routeMode.value === 'tasks') {
    taskWorkflowKey.value = workflow.workflow_key
    await loadTasks(workflow.workflow_key)
  } else if (routeMode.value === 'progress') {
    const runId = routeParts.value[2] || runningRunFor(workflow.workflow_key)?.run_id || ''
    progressWorkflowKey.value = workflow.workflow_key
    progressRunId.value = runId
    selectedRunId.value = runId
    await loadRuns(workflow.workflow_key)
    if (runId) await loadLogs()
    const run = (workflowRuns.value[workflow.workflow_key] || []).find(item => item.run_id === runId)
    if (run?.status === 'running') {
      testing.value = true
      testingRunId.value = run.run_id
      stopTestPolling()
      testPoll = setInterval(pollTestRun, 1500)
    }
  }
}

function profileName(profileKey: string) {
  const profile = profiles.value.find(item => item.profile_key === profileKey)
  return profile ? `${profile.name} / ${profile.profile_key}` : profileKey
}

function statusLabel(status: string) {
  if (status === 'active') return '启用'
  if (status === 'disabled') return '停用'
  return status
}

function runStatusLabel(status: string) {
  const map: Record<string, string> = {
    running: '执行中',
    completed: '成功',
    no_task: '无任务',
    failed: '失败',
    stopped: '已停止',
  }
  return map[status] || status
}

function runBadgeClass(status: string) {
  if (status === 'completed') return 'bg-green-50 text-green-700'
  if (status === 'failed') return 'bg-red-50 text-red-700'
  if (status === 'running') return 'bg-blue-50 text-blue-700'
  return ''
}

function taskStatusLabel(status: string) {
  return labelTaskStatus(status)
}

function taskBadgeClass(status: string) {
  if (status === 'completed') return 'bg-green-50 text-green-700'
  if (status === 'failed' || status === 'abandoned') return 'bg-red-50 text-red-700'
  if (status === 'running') return 'bg-blue-50 text-blue-700'
  if (status === 'pending') return 'bg-yellow-50 text-yellow-700'
  return ''
}

function logLevelClass(level: string) {
  if (level === 'error') return 'border-red-400'
  if (level === 'warning' || level === 'warn') return 'border-yellow-400'
  return 'border-border'
}

function eventKindLabel(event: WorkflowRunEvent) {
  if (event.kind === 'agent_message') return event.agent_role === 'subagent' ? '子 Agent' : (event.agent_name || 'agent')
  if (event.kind === 'tool_call') return '工具调用'
  if (event.kind === 'tool_result') return event.status === 'failed' ? '工具失败' : '工具完成'
  if (event.kind === 'result') return event.status === 'failed' ? '运行失败' : '运行结果'
  if (event.kind === 'error') return '异常'
  if (event.kind === 'status') return '状态'
  if (event.kind === 'subagent_start') return '子 Agent 启动'
  if (event.kind === 'subagent_progress') return '子 Agent 进度'
  if (event.kind === 'subagent_end') return event.status === 'failed' ? '子 Agent 失败' : '子 Agent 完成'
  if (event.kind === 'subagent_updated') return '子 Agent 更新'
  return event.kind
}

function eventMessage(event: WorkflowRunEvent) {
  if (event.message) return event.message
  if (event.tool_name && event.kind === 'tool_call') return `调用工具 ${event.tool_name}`
  if (event.tool_name && event.kind === 'tool_result') return `工具 ${event.tool_name} 调用${event.status === 'failed' ? '失败' : '成功'}`
  return event.status || ''
}

function eventClass(event: WorkflowRunEvent) {
  if (event.kind === 'error' || event.status === 'failed') return 'border-red-400'
  if (event.kind === 'tool_call') return 'border-blue-400'
  if (event.kind === 'tool_result') return 'border-green-400'
  if (event.kind === 'result') return 'border-foreground/40'
  return 'border-border'
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}

async function openArtifact(item: WorkflowArtifact) {
  detailLoading.value = true
  showArtifact.value = true
  artifactDetail.value = null
  try {
    artifactDetail.value = await api.getWorkflowArtifact(
      item.artifact_id,
      selectedWorkflow.value?.profile_key || form.value.profile_key || undefined,
    )
  } catch (e: unknown) {
    artifactDetail.value = null
    showArtifact.value = false
    artifactError.value = errorMessage(e)
  } finally {
    detailLoading.value = false
  }
}

async function openArtifactHistory(item: WorkflowArtifact) {
  if (!item.task_key) return
  historyLoading.value = true
  showArtifactHistory.value = true
  artifactHistoryTarget.value = item
  artifactHistory.value = []
  try {
    const result = await api.getWorkflowArtifactHistory({
      profile_key: selectedWorkflow.value?.profile_key || form.value.profile_key || undefined,
      workflow_key: item.workflow_key,
      task_key: item.task_key,
      limit: 20,
    })
    artifactHistory.value = result.versions
  } catch (e: unknown) {
    artifactHistory.value = []
    showArtifactHistory.value = false
    artifactError.value = errorMessage(e)
  } finally {
    historyLoading.value = false
  }
}

async function loadRuns(workflowKey = selectedWorkflow.value?.workflow_key || '') {
  const key = workflowKey
  if (!key) {
    workflowRuns.value = { ...workflowRuns.value }
    return
  }
  runsLoading.value = true
  try {
    const nextRuns = await api.listWorkflowRuns(key, WORKFLOW_RUN_LIMIT)
    workflowRuns.value = { ...workflowRuns.value, [key]: nextRuns }
    if (key === selectedWorkflow.value?.workflow_key && !nextRuns.some(r => r.run_id === selectedRunId.value)) {
      selectedRunId.value = nextRuns[0]?.run_id || ''
      await loadLogs()
    }
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    runsLoading.value = false
  }
}

function mergeWorkflowRun(run: WorkflowRun) {
  const key = run.workflow_key
  const currentRuns = workflowRuns.value[key] || []
  const index = currentRuns.findIndex(item => item.run_id === run.run_id)
  const nextRuns = index >= 0
    ? currentRuns.map(item => item.run_id === run.run_id ? run : item)
    : [run, ...currentRuns]
  workflowRuns.value = { ...workflowRuns.value, [key]: nextRuns.slice(0, WORKFLOW_RUN_LIMIT) }
}

async function loadTasks(workflowKey = selectedWorkflow.value?.workflow_key || '') {
  const key = workflowKey
  if (!key) return
  tasksLoading.value = true
  taskError.value = ''
  try {
    const result = await api.listWorkflowTasks(key)
    workflowTasks.value = { ...workflowTasks.value, [key]: result.tasks }
  } catch (e: unknown) {
    taskError.value = errorMessage(e)
    workflowTasks.value = { ...workflowTasks.value, [key]: [] }
  } finally {
    tasksLoading.value = false
  }
}

function onTaskSearchInput() {
  if (taskSearchDebounce) clearTimeout(taskSearchDebounce)
  taskSearchDebounce = setTimeout(() => {
    taskSearch.value = taskSearchInput.value
  }, 250)
}

function taskArtifactKey(task: WorkflowTask) {
  return `${task.workflow_key}:${task.task_key}:${task.task_version}`
}

function taskArtifactsOf(task: WorkflowTask) {
  return taskArtifacts.value[taskArtifactKey(task)] || []
}

function isTaskArtifactLoading(task: WorkflowTask) {
  return taskArtifactLoading.value.has(taskArtifactKey(task))
}

function isTaskArtifactExpanded(task: WorkflowTask) {
  return expandedArtifactIds.value.has(taskArtifactKey(task))
}

function taskArtifactActiveId(task: WorkflowTask) {
  return taskArtifactActive.value[taskArtifactKey(task)] || ''
}

function selectTaskArtifact(task: WorkflowTask, artifactId: string) {
  taskArtifactActive.value = { ...taskArtifactActive.value, [taskArtifactKey(task)]: artifactId }
}

function activeTaskArtifact(task: WorkflowTask): WorkflowArtifact | null {
  const items = taskArtifactsOf(task)
  if (!items.length) return null
  const id = taskArtifactActiveId(task)
  return items.find(item => item.artifact_id === id) || items[0]
}

async function toggleTaskArtifacts(task: WorkflowTask) {
  const key = taskArtifactKey(task)
  const next = new Set(expandedArtifactIds.value)
  if (next.has(key)) {
    next.delete(key)
    expandedArtifactIds.value = next
    return
  }
  next.add(key)
  expandedArtifactIds.value = next
  if (taskArtifacts.value[key]) return
  const loading = new Set(taskArtifactLoading.value)
  loading.add(key)
  taskArtifactLoading.value = loading
  taskArtifactError.value = ''
  try {
    const result = await api.searchWorkflowArtifacts({
      workflow_key: task.workflow_key,
      task_key: task.task_key,
      include_history: true,
      full: true,
      limit: 50,
    })
    taskArtifacts.value = { ...taskArtifacts.value, [key]: result.items }
  } catch (e: unknown) {
    taskArtifactError.value = errorMessage(e)
    taskArtifacts.value = { ...taskArtifacts.value, [key]: [] }
  } finally {
    const done = new Set(taskArtifactLoading.value)
    done.delete(key)
    taskArtifactLoading.value = done
  }
}

/** Whether a task can be priority-executed right now (pending, or running
 *  with an expired lease). Mirrors the server-side leasability check. */
function canExecuteTask(task: WorkflowTask): boolean {
  if (task.status === 'pending') return true
  if (task.status === 'running' && task.lease_expires_at) {
    return new Date(task.lease_expires_at).getTime() < Date.now()
  }
  return false
}

function canResetTask(task: WorkflowTask): boolean {
  return task.status === 'completed'
    || task.status === 'failed'
    || task.status === 'abandoned'
    || (task.status === 'running' && canExecuteTask(task))
}

function taskActionKey(task: WorkflowTask) {
  return taskId(task)
}

function isTaskActionLoading(task: WorkflowTask) {
  return taskActionLoading.value.has(taskActionKey(task))
}

async function executeTask(task: WorkflowTask) {
  const key = taskActionKey(task)
  if (isTaskActionLoading(task)) return
  const loading = new Set(taskActionLoading.value)
  loading.add(key)
  taskActionLoading.value = loading
  taskActionError.value = ''
  try {
    await api.executeWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined)
    await loadTasks(task.workflow_key)
  } catch (e: unknown) {
    taskActionError.value = errorMessage(e)
  } finally {
    const done = new Set(taskActionLoading.value)
    done.delete(key)
    taskActionLoading.value = done
  }
}

function openResetConfirm(task: WorkflowTask) {
  resetTarget.value = task
}

function closeResetConfirm() {
  resetTarget.value = null
}

async function confirmResetTask() {
  const task = resetTarget.value
  if (!task || resetting.value) return
  resetting.value = true
  taskActionError.value = ''
  try {
    await api.resetWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined)
    resetTarget.value = null
    await loadTasks(task.workflow_key)
  } catch (e: unknown) {
    taskActionError.value = errorMessage(e)
  } finally {
    resetting.value = false
  }
}

async function loadLogs(options: { quiet?: boolean } = {}) {
  if (!selectedRunId.value) {
    runLogs.value = []
    runEvents.value = []
    return
  }
  if (!options.quiet) logsLoading.value = true
  try {
    const [logs, events] = await Promise.all([
      api.getWorkflowRunLogs(selectedRunId.value),
      api.getWorkflowRunEvents(selectedRunId.value),
    ])
    runLogs.value = logs
    runEvents.value = events
  } catch (e: unknown) {
    if (!options.quiet) {
      runLogs.value = []
      runEvents.value = []
    }
  } finally {
    if (!options.quiet) logsLoading.value = false
  }
}

function taskId(task: WorkflowTask) {
  return `${task.workflow_key}:${task.task_key}:${task.task_version}`
}

function taskRunLogKey(task: WorkflowTask) {
  return task.lease_run_id || taskId(task)
}

function taskLogs(task: WorkflowTask) {
  if (!task.lease_run_id) return []
  return (taskRunLogs.value[task.lease_run_id] || []).filter(log => !log.task_key || log.task_key === task.task_key)
}

function taskEvents(task: WorkflowTask) {
  return task.lease_run_id ? taskRunEvents.value[task.lease_run_id] || [] : []
}

/** Sub-agent actors present in a task's event stream (feature 5). */
function taskActors(task: WorkflowTask) {
  return distinctActors(taskEvents(task))
}

function taskActorFilterFor(task: WorkflowTask) {
  return taskActorFilter.value[taskId(task)] || ''
}

function setTaskActorFilter(task: WorkflowTask, actorId: string) {
  taskActorFilter.value = { ...taskActorFilter.value, [taskId(task)]: actorId }
}

/** Events for a task after the per-task actor filter is applied. */
function taskFilteredEvents(task: WorkflowTask) {
  return filterEventsByActor(taskEvents(task), taskActorFilterFor(task))
}

/** Filtered events grouped by actor (main + each sub-agent). */
function taskEventGroups(task: WorkflowTask) {
  return groupEventsByActor(taskFilteredEvents(task))
}

function taskUsageFor(task: WorkflowTask, taskIdStr: string) {
  return subagentUsage(taskEvents(task), taskIdStr)
}

function taskStatusFor(task: WorkflowTask, taskIdStr: string) {
  return subagentStatus(taskEvents(task), taskIdStr)
}

function isTaskLogLoading(task: WorkflowTask) {
  return task.lease_run_id ? taskLogLoading.value.has(task.lease_run_id) : false
}

async function openTasks(item: WorkflowDefinition) {
  window.location.hash = `workflow/${item.workflow_key}/tasks`
}

async function prepareTasks(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  await loadTasks(item.workflow_key)
}

async function toggleTaskLogs(task: WorkflowTask) {
  const id = taskId(task)
  const next = new Set(expandedTaskIds.value)
  if (next.has(id)) {
    next.delete(id)
    expandedTaskIds.value = next
    return
  }
  next.add(id)
  expandedTaskIds.value = next
  if (!task.lease_run_id || taskRunLogs.value[task.lease_run_id]) return
  const loading = new Set(taskLogLoading.value)
  loading.add(task.lease_run_id)
  taskLogLoading.value = loading
  try {
    const [logs, events] = await Promise.all([
      api.getWorkflowRunLogs(task.lease_run_id),
      api.getWorkflowRunEvents(task.lease_run_id),
    ])
    taskRunLogs.value = { ...taskRunLogs.value, [task.lease_run_id]: logs }
    taskRunEvents.value = { ...taskRunEvents.value, [task.lease_run_id]: events }
  } catch (e: unknown) {
    taskError.value = errorMessage(e)
  } finally {
    const done = new Set(taskLogLoading.value)
    done.delete(task.lease_run_id)
    taskLogLoading.value = done
  }
}

async function selectRun(runId: string) {
  selectedRunId.value = runId
  await loadLogs()
}

function runningRunFor(workflowKey: string) {
  return (workflowRuns.value[workflowKey] || []).find(run => run.status === 'running') || null
}

function stopTestPolling() {
  if (testPoll) {
    clearInterval(testPoll)
    testPoll = null
  }
}

async function runWorkflow(item: WorkflowDefinition) {
  const wf = item
  if (!wf || testing.value) return
  testError.value = ''
  testing.value = true
  try {
    const res = await api.runWorkflow(wf.workflow_key)
    if (res.status === 'started' && res.run_id) {
      testingRunId.value = res.run_id
      progressWorkflowKey.value = wf.workflow_key
      progressRunId.value = res.run_id
      selectedKey.value = wf.workflow_key
      selectedRunId.value = res.run_id
      await loadRuns(wf.workflow_key)
      await loadLogs()
      stopTestPolling()
      testPoll = setInterval(pollTestRun, 1500)
      window.location.hash = `workflow/${wf.workflow_key}/progress/${res.run_id}`
    } else {
      testing.value = false
    }
  } catch (e: unknown) {
    testing.value = false
    testError.value = errorMessage(e)
  }
}

async function openProgress(item: WorkflowDefinition, runId?: string) {
  const run = runId ? (workflowRuns.value[item.workflow_key] || []).find(r => r.run_id === runId) : runningRunFor(item.workflow_key)
  if (!run) return
  window.location.hash = `workflow/${item.workflow_key}/progress/${run.run_id}`
}

async function prepareProgress(item: WorkflowDefinition, runId?: string) {
  const run = runId ? (workflowRuns.value[item.workflow_key] || []).find(r => r.run_id === runId) : runningRunFor(item.workflow_key)
  if (!run) return
  selectedKey.value = item.workflow_key
  progressWorkflowKey.value = item.workflow_key
  progressRunId.value = run.run_id
  selectedRunId.value = run.run_id
  await loadLogs()
  if (run.status === 'running') {
    testing.value = true
    testingRunId.value = run.run_id
    stopTestPolling()
    testPoll = setInterval(pollTestRun, 1500)
  }
}

async function refreshProgress() {
  if (progressWorkflowKey.value) {
    await loadRuns(progressWorkflowKey.value)
  }
  await loadLogs()
}

async function pollTestRun() {
  const runId = testingRunId.value
  if (!runId) return
  try {
    const run = await api.getWorkflowRun(runId)
    mergeWorkflowRun(run)
    await loadLogs({ quiet: true })
    if (['completed', 'no_task', 'failed', 'stopped'].includes(run.status)) {
      stopTestPolling()
      testing.value = false
      testingRunId.value = ''
      const workflowKey = progressWorkflowKey.value || run.workflow_key
      await loadRuns(workflowKey)
      if (run.status === 'completed' || run.status === 'no_task') {
        await searchArtifacts()
      }
    }
  } catch {
    // transient poll error: keep polling
  }
}

async function deleteCurrent() {
  const wf = selectedWorkflow.value
  if (!wf) return
  if (!confirm(`确定删除工作流「${wf.name}」？其运行记录与产物将一并清除。`)) return
  try {
    await api.deleteWorkflow(wf.workflow_key)
    workflows.value = await api.listWorkflows()
    selectedKey.value = workflows.value[0]?.workflow_key || ''
    await loadAll()
    goList()
  } catch (e: unknown) {
    error.value = errorMessage(e)
  }
}

async function deleteWorkflow(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  await deleteCurrent()
}

function requestClearWorkflow(item: WorkflowDefinition) {
  clearTarget.value = item
  showClearConfirm.value = true
}

async function confirmClearWorkflow() {
  const wf = clearTarget.value
  if (!wf) return
  clearing.value = true
  error.value = ''
  try {
    await api.clearWorkflowExecutionData(wf.workflow_key)
    artifacts.value = []
    artifactDetail.value = null
    artifactHistory.value = []
    runEvents.value = []
    runLogs.value = []
    selectedRunId.value = ''
    progressRunId.value = ''
    expandedTaskIds.value = new Set()
    taskRunLogs.value = {}
    taskRunEvents.value = {}
    resetTaskFilters()
    expandedArtifactIds.value = new Set()
    taskArtifacts.value = {}
    taskArtifactActive.value = {}
    taskActionLoading.value = new Set()
    taskActionError.value = ''
    resetTarget.value = null
    taskActorFilter.value = {}
    await Promise.all([
      loadRunsForWorkflows(),
      loadTasks(wf.workflow_key),
    ])
    if (routeMode.value === 'detail' && selectedWorkflow.value?.workflow_key === wf.workflow_key) {
      await searchArtifacts()
    }
    showClearConfirm.value = false
    clearTarget.value = null
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    clearing.value = false
  }
}
</script>

<template>
  <div class="space-y-5">
    <div v-if="routeError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
      {{ routeError }}。请<a class="underline" href="#workflow" @click.prevent="goList">返回列表</a>。
    </div>

    <template v-if="routeMode === 'list'">
    <div class="flex flex-wrap items-center gap-2">
      <Button variant="outline" @click="showGuide = true">
        <HelpCircle class="mr-1.5 h-4 w-4" />
        使用指引
      </Button>
      <Button @click="openCreate">新建工作流</Button>
    </div>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {{ error }}
    </div>

    <Dialog v-model:open="showGuide">
      <DialogContent class="w-[96vw] max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>工作流使用指引</DialogTitle>
        </DialogHeader>
        <div class="max-h-[74vh] space-y-4 overflow-auto pr-1 text-sm leading-6 text-muted-foreground">
          <section class="rounded-md border p-4">
            <h3 class="mb-2 text-sm font-semibold text-foreground">workflow.js</h3>
            <p>脚本在一次独立运行目录中执行，负责领取任务、创建任务、记录日志，并在结束时写入 <span class="font-mono text-foreground">out/result.json</span>。</p>
            <div class="mt-3 grid gap-3 md:grid-cols-3">
              <div class="rounded-md bg-muted/50 p-3">
                <div class="font-mono text-xs text-foreground">workflow_get_task</div>
                <p class="mt-1 text-xs">领取当前运行的一条待处理任务。</p>
              </div>
              <div class="rounded-md bg-muted/50 p-3">
                <div class="font-mono text-xs text-foreground">workflow_set_task</div>
                <p class="mt-1 text-xs">写入任务列表，任务可带 <span class="font-mono">type</span> 供脚本分支。</p>
              </div>
              <div class="rounded-md bg-muted/50 p-3">
                <div class="font-mono text-xs text-foreground">workflow_run_log</div>
                <p class="mt-1 text-xs">记录运行过程中的业务日志。</p>
              </div>
            </div>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="mb-2 text-sm font-semibold text-foreground">返回格式</h3>
            <pre class="overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">{
  "status": "completed",
  "task_key": "page:a",
  "task_version": "v1",
  "artifacts": [
    {
      "title": "Page A",
      "path": "reports/page-a.md",
      "tags": ["page"],
      "format": "markdown",
      "file": "out/artifacts/page-a.md",
      "summary": "summary"
    }
  ]
}</pre>
            <p class="mt-2">没有任务时输出 <span class="font-mono text-foreground">{"status":"no_executable_task","reason":"..."}</span>。如果任务带 <span class="font-mono text-foreground">task_version</span>，结果里必须原样写回。</p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="mb-2 text-sm font-semibold text-foreground">让智能体协助编写</h3>
            <p>先让智能体读取内置技能，再基于用户需求生成 <span class="font-mono text-foreground">workflow.js</span>。</p>
            <pre class="mt-3 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">execute service='built-in' tool='load_skill' arguments={"skill_name":"design_workflow"}</pre>
            <p class="mt-2">随后要求智能体参照技能内容完成开发，并检查任务类型分支、日志、产物路径和 <span class="font-mono text-foreground">out/result.json</span>。</p>
          </section>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showGuide = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Card>
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-medium text-foreground">工作流</div>
            <div class="text-xs text-muted-foreground">{{ workflows.length }} 个定义</div>
          </div>
          <Button variant="outline" size="sm" :disabled="runsLoading" @click="loadRunsForWorkflows()">{{ runsLoading ? '刷新中' : '刷新运行状态' }}</Button>
        </div>
        <div v-if="loading" class="px-4 py-8 text-sm text-muted-foreground">加载中</div>
        <div v-else-if="!workflows.length" class="px-4 py-8 text-sm text-muted-foreground">暂无工作流</div>
        <div v-else class="divide-y">
          <div v-for="item in workflows" :key="item.workflow_key" class="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_220px_340px] lg:items-center">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-medium text-foreground">{{ item.name }}</span>
                <Badge variant="outline">{{ statusLabel(item.status) }}</Badge>
                <Badge v-if="runningRunFor(item.workflow_key)" class="bg-blue-50 text-blue-700">运行中</Badge>
              </div>
              <div class="mt-1 truncate font-mono text-xs text-muted-foreground">{{ item.workflow_key }}</div>
              <p class="mt-1 line-clamp-2 text-xs text-muted-foreground">{{ item.description || '无描述' }}</p>
            </div>
            <div class="text-xs text-muted-foreground">
              <div class="truncate">{{ profileName(item.profile_key) }}</div>
              <div class="mt-1">{{ (workflowRuns[item.workflow_key] || []).length }} 次运行记录</div>
            </div>
            <div class="flex flex-wrap justify-start gap-2 lg:justify-end">
              <Button
                v-if="runningRunFor(item.workflow_key)"
                variant="outline"
                size="sm"
                class="h-8 text-xs"
                @click="openProgress(item, runningRunFor(item.workflow_key)?.run_id)"
              >
                运行中...
              </Button>
              <Button
                v-else
                variant="outline"
                size="sm"
                class="h-8 text-xs"
                :disabled="hasAnyRunningRun"
                @click="runWorkflow(item)"
              >
                运行
              </Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openEdit(item)">编辑</Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openDetail(item)">详情</Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openTasks(item)">任务进度</Button>
              <Button variant="ghost" size="sm" class="h-8 text-xs text-destructive" @click="requestClearWorkflow(item)">清空</Button>
              <Button variant="ghost" size="sm" class="h-8 text-xs text-destructive" @click="deleteWorkflow(item)">删除</Button>
            </div>
          </div>
        </div>
        <div v-if="testError" class="border-t px-4 py-3 text-xs text-destructive">{{ testError }}</div>
      </CardContent>
    </Card>
    </template>

    <section v-if="routeMode === 'detail' && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-lg font-semibold text-foreground">{{ selectedWorkflow?.name || '工作流详情' }}</h2>
            <p class="font-mono text-xs text-muted-foreground">{{ selectedWorkflow?.workflow_key || routeWorkflowKey }}</p>
          </div>
        </div>
      </div>
      <div v-if="selectedWorkflow" class="space-y-5">
          <div class="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <Badge>{{ selectedWorkflow.workflow_key }}</Badge>
                <Badge variant="outline">{{ statusLabel(selectedWorkflow.status) }}</Badge>
              </div>
              <p class="mt-2 text-sm text-muted-foreground">{{ selectedWorkflow.description || '无描述' }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <Button
                v-if="runningRunFor(selectedWorkflow.workflow_key)"
                variant="outline"
                size="sm"
                @click="openProgress(selectedWorkflow, runningRunFor(selectedWorkflow.workflow_key)?.run_id)"
              >
                运行中...
              </Button>
              <Button
                v-else
                variant="outline"
                size="sm"
                :disabled="hasAnyRunningRun"
                @click="runWorkflow(selectedWorkflow)"
              >
                运行
              </Button>
              <Button variant="outline" size="sm" @click="openTasks(selectedWorkflow)">任务进度</Button>
              <Button variant="outline" size="sm" @click="openEdit(selectedWorkflow)">编辑</Button>
              <Button variant="ghost" size="sm" class="text-destructive" @click="requestClearWorkflow(selectedWorkflow)">清空</Button>
            </div>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <div class="rounded-md border px-3 py-2">
              <div class="text-xs text-muted-foreground">profile</div>
              <div class="mt-1 truncate text-sm font-medium">{{ selectedProfileName }}</div>
            </div>
            <div class="rounded-md border px-3 py-2">
              <div class="text-xs text-muted-foreground">产物工具</div>
              <div class="mt-1 text-sm font-medium">{{ artifactToolName }}</div>
            </div>
          </div>

          <WorkflowDagGraph :dag="workflowDag" />

          <section class="space-y-4 rounded-md border p-4">
            <div class="flex flex-wrap items-end gap-3">
              <div class="min-w-[220px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">检索</label>
                <Input v-model="artifactQuery" placeholder="标题、摘要、路径" @keyup.enter="searchArtifacts" />
              </div>
              <div class="min-w-[180px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">path</label>
                <Input v-model="artifactPath" placeholder="reports/page-a/" @keyup.enter="searchArtifacts" />
              </div>
              <div class="min-w-[180px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">tags</label>
                <Input v-model="artifactTags" placeholder="finance, report" @keyup.enter="searchArtifacts" />
              </div>
              <Button :disabled="artifactLoading" @click="searchArtifacts">{{ artifactLoading ? '检索中' : '检索产物' }}</Button>
            </div>
            <div v-if="artifactError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {{ artifactError }}
            </div>
            <div v-if="!artifactRows.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无产物</div>
            <div class="space-y-1.5">
              <template v-for="row in artifactRows" :key="row.type + ':' + row.path">
                <button
                  v-if="row.type === 'folder'"
                  class="flex w-full items-center gap-1.5 rounded-md py-1 text-left text-xs font-semibold uppercase text-muted-foreground transition hover:bg-muted/50 hover:text-foreground"
                  :style="{ paddingLeft: `${row.depth * 16 + 8}px` }"
                  @click="togglePath(row.path)"
                >
                  <span>{{ collapsedPaths.has(row.path) ? '▸' : '▾' }}</span>
                  <span>{{ row.segment }}/</span>
                  <span class="font-normal normal-case text-muted-foreground/70">({{ row.count }})</span>
                </button>
                <div v-else class="rounded-md border p-3" :style="{ marginLeft: `${row.depth * 16}px` }">
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div class="text-sm font-medium text-foreground">{{ row.artifact?.title }}</div>
                      <div class="mt-1 text-xs text-muted-foreground">{{ row.artifact?.path }}</div>
                      <div v-if="row.artifact?.task_version" class="mt-1 text-xs text-muted-foreground">
                        version: <span class="font-mono">{{ row.artifact.task_version }}</span>
                      </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-1">
                      <Badge v-if="row.artifact?.is_current" variant="outline">current</Badge>
                      <Badge v-for="tag in row.artifact?.tags || []" :key="tag" variant="outline">{{ tag }}</Badge>
                      <Button
                        v-if="row.artifact?.task_key"
                        variant="ghost"
                        size="sm"
                        class="h-7 text-xs"
                        @click="row.artifact && openArtifactHistory(row.artifact)"
                      >
                        历史
                      </Button>
                      <Button variant="ghost" size="sm" class="h-7 text-xs" @click="row.artifact && openArtifact(row.artifact)">查看</Button>
                    </div>
                  </div>
                  <p class="mt-2 text-sm text-muted-foreground">{{ row.artifact?.summary || row.artifact?.snippet }}</p>
                </div>
              </template>
            </div>
          </section>

          <section class="space-y-4 rounded-md border p-4">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold">运行记录</h3>
              <Button variant="outline" size="sm" :disabled="runsLoading" @click="loadRuns(selectedWorkflow.workflow_key)">{{ runsLoading ? '刷新中' : '刷新' }}</Button>
            </div>
            <div v-if="runsLoading" class="py-4 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runs.length" class="rounded-md border px-4 py-6 text-sm text-muted-foreground">暂无运行记录</div>
            <div v-else class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              <button
                v-for="run in runs"
                :key="run.run_id"
                class="rounded-md border px-3 py-2 text-left transition hover:bg-muted/50"
                @click="openProgress(selectedWorkflow, run.run_id)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate font-mono text-xs">{{ run.run_id }}</span>
                  <Badge :variant="run.status === 'completed' ? 'secondary' : 'outline'" :class="runBadgeClass(run.status)">{{ runStatusLabel(run.status) }}</Badge>
                </div>
                <div class="mt-1 text-xs text-muted-foreground">{{ run.started_at }}</div>
              </button>
            </div>
          </section>
      </div>
      <div v-else class="py-8 text-center text-sm text-muted-foreground">未选择工作流</div>
    </section>

    <Dialog v-model:open="showClearConfirm">
      <DialogContent class="max-w-[520px] sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>清空工作流执行数据</DialogTitle>
        </DialogHeader>
        <div class="space-y-3 text-sm text-muted-foreground">
          <p>
            确定清空工作流
            <span class="font-medium text-foreground">「{{ clearTarget?.name || clearTarget?.workflow_key }}」</span>
            的执行数据吗？
          </p>
          <div class="rounded-md border bg-muted/30 px-3 py-2 text-xs leading-5">
            将删除该工作流的任务清单、运行记录、业务日志和所有产出物；工作流定义、脚本和关联 profile 会保留。
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" :disabled="clearing" @click="showClearConfirm = false">取消</Button>
          <Button variant="destructive" :disabled="clearing" @click="confirmClearWorkflow">
            {{ clearing ? '清空中' : '确认清空' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="resetTarget !== null" @update:open="(v) => { if (!v) closeResetConfirm() }">
      <DialogContent class="max-w-[480px] sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>重置任务</DialogTitle>
        </DialogHeader>
        <div class="space-y-3 text-sm text-muted-foreground">
          <p>
            确定重置任务
            <span class="font-mono font-medium text-foreground">「{{ resetTarget?.task_key }}」</span>
            <span v-if="resetTarget?.task_version" class="text-foreground">（{{ resetTarget.task_version }}）</span>
            吗？
          </p>
          <div class="rounded-md border bg-muted/30 px-3 py-2 text-xs leading-5">
            重置后该任务会回到待处理状态，可被再次领取执行；不会立即触发执行，也不会改变其他任务的执行顺序。历史尝试次数和错误信息会保留。
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" :disabled="resetting" @click="closeResetConfirm">取消</Button>
          <Button variant="destructive" :disabled="resetting" @click="confirmResetTask">
            {{ resetting ? '重置中' : '确认重置' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <section v-if="routeMode === 'tasks' && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-lg font-semibold text-foreground">{{ taskWorkflow?.name || '任务进度' }}</h2>
            <p class="font-mono text-xs text-muted-foreground">{{ taskWorkflow?.workflow_key || routeWorkflowKey }}</p>
          </div>
        </div>
      </div>
      <div class="space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
            <div class="min-w-0 space-y-2">
              <div class="flex flex-wrap items-center gap-2">
                <Badge v-if="taskWorkflow" variant="outline">{{ taskWorkflow.workflow_key }}</Badge>
                <button type="button" class="cursor-pointer" @click="toggleStatusFilter('')">
                  <Badge :variant="taskStatusFilter === '' ? 'default' : 'outline'">全部 {{ tasks.length }}</Badge>
                </button>
                <button
                  v-for="status in taskStatuses"
                  :key="status"
                  type="button"
                  class="cursor-pointer"
                  @click="toggleStatusFilter(status)"
                >
                  <Badge :variant="taskStatusFilter === status ? 'default' : 'outline'" :class="taskBadgeClass(status)">
                    {{ taskStatusLabel(status) }} {{ taskStats[status] || 0 }}
                  </Badge>
                </button>
              </div>
              <div class="text-xs text-muted-foreground">展开任务可查看产出物与关联运行的日志；点击上方状态可按状态筛选。</div>
            </div>
            <Button
              variant="outline"
              size="sm"
              :disabled="tasksLoading || !taskWorkflow"
              @click="taskWorkflow && loadTasks(taskWorkflow.workflow_key)"
            >
              {{ tasksLoading ? '刷新中' : '刷新' }}
            </Button>
          </div>

          <!-- 筛选 / 搜索 / 排序 -->
          <div class="flex flex-wrap items-center gap-2">
            <Input
              v-model="taskSearchInput"
              type="search"
              placeholder="搜索 task_key / 类型"
              class="h-8 w-56 text-xs"
              @input="onTaskSearchInput"
            />
            <Select v-model="taskTypeFilter">
              <SelectTrigger class="h-8 w-[140px] text-xs">
                <SelectValue placeholder="全部类型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部类型</SelectItem>
                <SelectItem v-for="t in taskTypes" :key="t" :value="t">{{ t }}</SelectItem>
              </SelectContent>
            </Select>
            <Select v-model="taskSort">
              <SelectTrigger class="h-8 w-[150px] text-xs">
                <SelectValue placeholder="排序" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">默认（状态优先）</SelectItem>
                <SelectItem value="task_key_asc">task_key ↑</SelectItem>
                <SelectItem value="task_key_desc">task_key ↓</SelectItem>
                <SelectItem value="set_at_asc">设置时间 ↑</SelectItem>
                <SelectItem value="set_at_desc">设置时间 ↓</SelectItem>
                <SelectItem value="updated_at_desc">最近更新</SelectItem>
              </SelectContent>
            </Select>
            <Button
              v-if="taskStatusFilter || taskTypeFilter || taskSearch || taskSort !== 'default'"
              variant="ghost"
              size="sm"
              class="h-8 text-xs"
              @click="resetTaskFilters"
            >
              重置筛选
            </Button>
            <div class="ml-auto text-xs text-muted-foreground">
              {{ filteredTasks.length }} / {{ tasks.length }}
            </div>
          </div>

          <div v-if="taskError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {{ taskError }}
          </div>
          <div v-if="taskActionError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {{ taskActionError }}
          </div>
          <div v-if="tasksLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!tasks.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无任务</div>
          <div v-else-if="!filteredTasks.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">没有符合筛选条件的任务</div>
          <div v-else class="space-y-2">
            <div v-for="task in filteredTasks" :key="taskId(task)" class="rounded-md border">
              <div class="flex flex-wrap items-start justify-between gap-3 px-3 py-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-mono text-sm font-medium text-foreground">{{ task.task_key }}</span>
                    <Badge variant="outline" :class="taskBadgeClass(task.status)">{{ taskStatusLabel(task.status) }}</Badge>
                    <Badge v-if="task.priority_flag" variant="outline" class="bg-blue-50 text-blue-700">优先执行</Badge>
                    <Badge v-if="task.type" variant="outline">{{ task.type }}</Badge>
                    <Badge v-if="task.task_version" variant="outline">{{ task.task_version }}</Badge>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>尝试 {{ task.attempt_count }}</span>
                    <span v-if="task.lease_run_id" class="font-mono">run {{ task.lease_run_id }}</span>
                    <span>set {{ task.set_at }}</span>
                    <span>更新 {{ task.updated_at }}</span>
                    <span v-if="task.completed_at">完成 {{ task.completed_at }}</span>
                  </div>
                  <div v-if="task.last_error" class="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
                    {{ task.last_error }}
                  </div>
                </div>
                <div class="flex items-center gap-1">
                  <Button
                    v-if="canExecuteTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs text-blue-600"
                    :disabled="isTaskActionLoading(task)"
                    @click="executeTask(task)"
                  >
                    {{ isTaskActionLoading(task) ? '执行中' : '执行' }}
                  </Button>
                  <Button variant="ghost" size="sm" class="h-8 text-xs" @click="toggleTaskArtifacts(task)">
                    {{ isTaskArtifactExpanded(task) ? '收起产出物' : '产出物' }}
                    <Badge v-if="taskArtifactsOf(task).length" variant="outline" class="ml-1">{{ taskArtifactsOf(task).length }}</Badge>
                  </Button>
                  <Button variant="ghost" size="sm" class="h-8 text-xs" @click="toggleTaskLogs(task)">
                    {{ expandedTaskIds.has(taskId(task)) ? '收起日志' : '展开日志' }}
                  </Button>
                  <Button
                    v-if="canResetTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs text-amber-600"
                    :disabled="isTaskActionLoading(task)"
                    @click="openResetConfirm(task)"
                  >
                    重置
                  </Button>
                </div>
              </div>
              <!-- 产出物（feature 2） -->
              <div v-if="isTaskArtifactExpanded(task)" class="space-y-2 border-t bg-muted/20 px-3 py-3">
                <div v-if="taskArtifactError" class="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
                  {{ taskArtifactError }}
                </div>
                <div v-if="isTaskArtifactLoading(task)" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">
                  产出物加载中
                </div>
                <div v-else-if="!taskArtifactsOf(task).length" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">
                  该任务暂无产出物。
                </div>
                <div v-else class="space-y-3">
                  <div class="flex flex-wrap items-center gap-2">
                    <div class="text-xs font-semibold text-foreground">产出物版本</div>
                    <button
                      v-for="item in taskArtifactsOf(task)"
                      :key="item.artifact_id"
                      type="button"
                      class="cursor-pointer"
                      @click="selectTaskArtifact(task, item.artifact_id)"
                    >
                      <Badge :variant="taskArtifactActiveId(task) === item.artifact_id ? 'default' : 'outline'">
                        {{ item.task_version || '(无版本)' }}
                        <span v-if="item.is_current" class="ml-1">·当前</span>
                      </Badge>
                    </button>
                  </div>
                  <template v-if="activeTaskArtifact(task)">
                    <div class="rounded-md border bg-background p-3">
                      <div class="mb-1 flex flex-wrap items-center gap-2">
                        <span class="text-sm font-medium text-foreground">{{ activeTaskArtifact(task)?.title }}</span>
                        <Badge variant="outline">{{ activeTaskArtifact(task)?.path }}</Badge>
                        <span class="text-xs text-muted-foreground">更新 {{ activeTaskArtifact(task)?.updated_at }}</span>
                      </div>
                      <div v-if="activeTaskArtifact(task)?.summary" class="mb-2 text-xs text-muted-foreground">
                        {{ activeTaskArtifact(task)?.summary }}
                      </div>
                      <div
                        class="prose prose-sm max-w-none overflow-auto rounded bg-muted p-2 text-xs"
                        v-html="renderMarkdown(activeTaskArtifact(task)?.content || '')"
                      />
                    </div>
                  </template>
                </div>
              </div>
              <div v-if="expandedTaskIds.has(taskId(task))" class="space-y-3 border-t bg-muted/20 px-3 py-3">
                <div class="rounded-md border bg-background p-3">
                  <div class="mb-2 text-xs font-semibold text-foreground">任务参数</div>
                  <pre class="max-h-44 overflow-auto rounded bg-muted p-2 text-xs">{{ JSON.stringify(task.payload, null, 2) }}</pre>
                </div>
                <div v-if="!task.lease_run_id" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">
                  暂无运行日志：该任务还没有被领取执行。
                </div>
                <div v-else-if="isTaskLogLoading(task)" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">
                  日志加载中
                </div>
                <div v-else class="grid gap-3 lg:grid-cols-2">
                  <section class="space-y-2 rounded-md border bg-background p-3">
                    <div class="flex items-center justify-between">
                      <div class="text-xs font-semibold text-foreground">Agent 输出</div>
                      <Badge variant="outline">{{ taskEvents(task).length }}</Badge>
                    </div>
                    <!-- 子 Agent 筛选（feature 5） -->
                    <div v-if="taskActors(task).length > 1" class="flex flex-wrap items-center gap-1">
                      <button type="button" class="cursor-pointer" @click="setTaskActorFilter(task, '')">
                        <Badge :variant="taskActorFilterFor(task) === '' ? 'default' : 'outline'">全部</Badge>
                      </button>
                      <button
                        v-for="actor in taskActors(task)"
                        :key="actor.id"
                        type="button"
                        class="cursor-pointer"
                        @click="setTaskActorFilter(task, actor.id)"
                      >
                        <Badge :variant="taskActorFilterFor(task) === actor.id ? 'default' : 'outline'" :class="actor.role === 'subagent' ? 'bg-purple-50 text-purple-700' : ''">
                          {{ actor.label }}
                        </Badge>
                      </button>
                    </div>
                    <div v-if="!taskEvents(task).length" class="text-sm text-muted-foreground">暂无 agent 输出</div>
                    <div v-else class="max-h-80 space-y-3 overflow-auto text-xs">
                      <div v-for="group in taskEventGroups(task)" :key="taskRunLogKey(task) + ':actor:' + group.actor.id" class="space-y-1">
                        <div v-if="group.actor.role === 'subagent'" class="flex flex-wrap items-center gap-2 rounded bg-purple-50/60 px-2 py-1 dark:bg-purple-950/30">
                          <span class="font-medium text-purple-700 dark:text-purple-300">{{ group.actor.label }}</span>
                          <Badge variant="outline" class="text-[10px]">{{ group.events.length }}</Badge>
                          <span v-if="taskUsageFor(task, group.actor.id)" class="text-[10px] text-muted-foreground">
                            {{ taskUsageFor(task, group.actor.id)?.total_tokens ?? 0 }} tokens · {{ taskUsageFor(task, group.actor.id)?.tool_uses ?? 0 }} 工具
                          </span>
                          <Badge v-if="taskStatusFor(task, group.actor.id)" variant="outline" :class="taskStatusFor(task, group.actor.id) === 'completed' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'" class="text-[10px]">
                            {{ taskStatusFor(task, group.actor.id) }}
                          </Badge>
                        </div>
                        <div v-for="(event, idx) in group.events" :key="taskRunLogKey(task) + ':event:' + group.actor.id + ':' + idx" class="border-l-2 pl-2" :class="eventClass(event)">
                          <div class="flex flex-wrap items-center gap-2">
                            <span class="font-medium text-foreground">{{ eventKindLabel(event) }}</span>
                            <span v-if="event.tool_name" class="font-mono text-muted-foreground">{{ event.tool_name }}</span>
                            <span v-if="event.created_at" class="text-muted-foreground">{{ event.created_at }}</span>
                          </div>
                          <div class="mt-1 whitespace-pre-wrap text-foreground">{{ eventMessage(event) }}</div>
                        </div>
                      </div>
                    </div>
                  </section>

                  <section class="space-y-2 rounded-md border bg-background p-3">
                    <div class="flex items-center justify-between">
                      <div class="text-xs font-semibold text-foreground">业务日志</div>
                      <Badge variant="outline">{{ taskLogs(task).length }}</Badge>
                    </div>
                    <div v-if="!taskLogs(task).length" class="text-sm text-muted-foreground">暂无业务日志</div>
                    <div v-else class="max-h-72 space-y-2 overflow-auto font-mono text-xs">
                      <div v-for="(log, idx) in taskLogs(task)" :key="taskRunLogKey(task) + ':log:' + idx" class="border-l-2 pl-2" :class="logLevelClass(log.level)">
                        <span class="text-muted-foreground">[{{ log.level }}]{{ log.stage ? ' ' + log.stage : '' }}</span>
                        <span class="ml-1">{{ log.message }}</span>
                      </div>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          </div>
      </div>
    </section>

    <section v-if="routeMode === 'progress' && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-lg font-semibold text-foreground">{{ progressWorkflow?.name || '运行进度' }}</h2>
            <p class="font-mono text-xs text-muted-foreground">{{ progressRunId || '暂无运行 ID' }}</p>
          </div>
        </div>
      </div>
      <div class="space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
            <div class="min-w-0 space-y-1">
              <div class="flex flex-wrap items-center gap-2">
                <Badge v-if="progressWorkflow" variant="outline">{{ progressWorkflow.workflow_key }}</Badge>
                <Badge v-if="progressRun" :variant="progressRun.status === 'completed' ? 'secondary' : 'outline'" :class="runBadgeClass(progressRun.status)">
                  {{ runStatusLabel(progressRun.status) }}
                </Badge>
              </div>
              <div class="truncate font-mono text-xs text-muted-foreground">{{ progressRunId || '暂无运行 ID' }}</div>
              <div v-if="progressRun?.started_at" class="text-xs text-muted-foreground">{{ progressRun.started_at }}</div>
            </div>
            <Button variant="outline" size="sm" :disabled="logsLoading || runsLoading" @click="refreshProgress">
              {{ logsLoading || runsLoading ? '刷新中' : '刷新' }}
            </Button>
          </div>

          <div v-if="logsLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!selectedRunId" class="py-8 text-center text-sm text-muted-foreground">暂无运行记录</div>
          <div v-else class="grid gap-4 lg:grid-cols-2">
            <section class="space-y-3 rounded-md border bg-muted/20 p-3">
              <div class="flex items-center justify-between gap-2">
                <div class="text-xs font-semibold text-foreground">Agent 输出</div>
                <Badge variant="outline">{{ runEvents.length }}</Badge>
              </div>
              <div v-if="!runEvents.length" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">暂无 agent 输出</div>
              <div v-else class="max-h-[28rem] space-y-2 overflow-auto rounded-md border bg-background p-3 text-xs">
                <div v-for="(event, idx) in runEvents" :key="idx" class="border-l-2 pl-2" :class="eventClass(event)">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-medium text-foreground">{{ eventKindLabel(event) }}</span>
                    <span v-if="event.agent_name" class="font-mono text-muted-foreground">{{ event.agent_name }}</span>
                    <span v-if="event.tool_name" class="font-mono text-muted-foreground">{{ event.tool_name }}</span>
                    <span v-if="event.created_at" class="text-muted-foreground">{{ event.created_at }}</span>
                  </div>
                  <div class="mt-1 whitespace-pre-wrap text-foreground">{{ eventMessage(event) }}</div>
                </div>
              </div>
            </section>

            <section class="space-y-3 rounded-md border bg-muted/20 p-3">
              <div class="flex items-center justify-between gap-2">
                <div class="text-xs font-semibold text-foreground">业务日志</div>
                <Badge variant="outline">{{ runLogs.length }}</Badge>
              </div>
              <div v-if="!runLogs.length" class="rounded-md border bg-background px-3 py-4 text-sm text-muted-foreground">暂无业务日志</div>
              <div v-else class="max-h-[28rem] space-y-2 overflow-auto rounded-md border bg-background p-3 font-mono text-xs">
                <div v-for="(log, idx) in runLogs" :key="idx" class="border-l-2 pl-2" :class="logLevelClass(log.level)">
                  <span class="text-muted-foreground">[{{ log.level }}]{{ log.stage ? ' ' + log.stage : '' }}</span>
                  <span class="ml-1">{{ log.message }}</span>
                </div>
              </div>
            </section>
          </div>
      </div>
    </section>

    <section v-if="isWorkflowFormPage && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-lg font-semibold text-foreground">{{ form.workflow_key ? '编辑工作流' : '新建工作流' }}</h2>
            <p class="font-mono text-xs text-muted-foreground">{{ form.workflow_key || 'workflow/new' }}</p>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" :disabled="designing" @click="openWorkflowDesigner('modify')">
            <WandSparkles class="mr-1.5 h-4 w-4" />
            AI 设计
          </Button>
          <Button :disabled="saving" size="sm" @click="saveWorkflow">
            <Save class="mr-1.5 h-4 w-4" />
            {{ saving ? '保存中' : '保存' }}
          </Button>
        </div>
      </div>
      <Card>
        <CardContent class="space-y-5 p-4">
          <div class="grid gap-3 lg:grid-cols-[1.2fr_1.2fr_1fr_0.7fr]">
            <div class="lg:col-span-1">
              <label class="mb-1 block text-xs text-muted-foreground">workflow_id</label>
              <Input v-model="form.workflow_key" class="h-9" :disabled="Boolean(selectedWorkflow && form.workflow_key === selectedWorkflow.workflow_key)" />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">名称</label>
              <Input v-model="form.name" class="h-9" />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">关联 profile</label>
              <select v-model="form.profile_key" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option v-for="profile in profiles" :key="profile.profile_key" :value="profile.profile_key">
                  {{ profile.name }} / {{ profile.profile_key }}
                </option>
              </select>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">状态</label>
              <select v-model="form.status" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option value="active">启用</option>
                <option value="disabled">停用</option>
              </select>
            </div>
            <div class="lg:col-span-4">
              <label class="mb-1 block text-xs text-muted-foreground">描述</label>
              <Input v-model="form.description" class="h-9" />
            </div>
          </div>

          <div>
            <label class="mb-1 block text-xs text-muted-foreground">Claude Code 工作流</label>
            <textarea v-model="form.workflow_js" class="min-h-[34rem] w-full rounded-md border bg-background p-3 font-mono text-xs" />
          </div>

          <div class="rounded-md border bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
            <div class="font-medium text-foreground">输出验收要求</div>
            <div>Workflow 必须在运行目录写入 <span class="font-mono">out/result.json</span>。</div>
            <div class="mt-2 font-mono">
              {"status":"completed","task_key":"page:a","task_version":"sha256:...","artifacts":[{"title":"...","path":"reports/a.md","tags":[],"format":"markdown","file":"out/artifacts/a.md","summary":"..."}]}
            </div>
            <div class="mt-2">没有可执行任务时输出 <span class="font-mono">{"status":"no_executable_task","reason":"..."}</span>。如任务带 task_version，result.json 必须原样写回。artifact 文件必须在运行目录内，当前只接受 Markdown。</div>
          </div>
        <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {{ formError }}
        </div>
        </CardContent>
      </Card>

      <aside
        v-if="showDesigner"
        class="fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l bg-background shadow-xl"
      >
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-semibold text-foreground">工作流设计 Agent</div>
            <div class="font-mono text-xs text-muted-foreground">design_workflow</div>
          </div>
          <Button variant="ghost" size="sm" class="h-8 px-2" :disabled="designing" @click="showDesigner = false">关闭</Button>
        </div>
        <div class="flex-1 space-y-4 overflow-auto p-4">
          <div class="grid grid-cols-2 gap-2 rounded-md border bg-muted/20 p-1">
            <button
              class="rounded px-3 py-2 text-sm transition"
              :class="designMode === 'modify' ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground'"
              @click="designMode = 'modify'"
            >
              修改
            </button>
            <button
              class="rounded px-3 py-2 text-sm transition"
              :class="designMode === 'create' ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground'"
              @click="designMode = 'create'"
            >
              新建
            </button>
          </div>

          <div>
            <label class="mb-1 block text-xs text-muted-foreground">提示词</label>
            <textarea
              v-model="designPrompt"
              class="min-h-32 w-full rounded-md border bg-background p-3 text-sm"
              placeholder="描述希望 agent 设计或修改的工作流目标"
            />
          </div>
          <Button class="w-full" :disabled="designing" @click="runWorkflowDesigner">
            <WandSparkles class="mr-1.5 h-4 w-4" />
            {{ designing ? '生成中' : '生成方案' }}
          </Button>

          <div v-if="designError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {{ designError }}
          </div>

          <section v-if="designResponse?.result" class="space-y-3 rounded-md border p-3">
            <div class="flex items-center justify-between gap-2">
              <div class="text-sm font-semibold">生成结果</div>
              <Badge v-if="designResponse.run_key" variant="outline">{{ designResponse.run_key }}</Badge>
            </div>
            <p class="text-sm text-muted-foreground">{{ designResponse.result.summary }}</p>
            <div v-if="designResponse.result.notes?.length" class="space-y-1 text-xs text-muted-foreground">
              <div v-for="note in designResponse.result.notes" :key="note">· {{ note }}</div>
            </div>
            <div v-if="workflowDesignDraft" class="grid gap-2 text-xs">
              <div class="rounded-md border bg-muted/20 p-2">
                <div class="font-mono font-medium text-foreground">{{ workflowDesignDraft.workflow_key }}</div>
                <div class="mt-1 text-muted-foreground">{{ workflowDesignDraft.name }}</div>
              </div>
              <pre class="max-h-96 overflow-auto rounded-md border bg-muted/20 p-3 text-xs">{{ workflowDesignDraft.workflow_js }}</pre>
            </div>
          </section>
        </div>
        <div class="flex items-center justify-end gap-2 border-t p-4">
          <Button variant="outline" :disabled="designing" @click="showDesigner = false">取消</Button>
          <Button :disabled="!workflowDesignDraft || saving" @click="acceptWorkflowDesign">
            <Check class="mr-1.5 h-4 w-4" />
            {{ saving ? '保存中' : '采纳并保存' }}
          </Button>
        </div>
      </aside>
    </section>

    <Dialog v-model:open="showArtifact">
      <DialogContent class="max-w-[900px] sm:max-w-[900px]">
        <DialogHeader>
          <DialogTitle>{{ artifactDetail?.title || '产物详情' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[74vh] space-y-3 overflow-auto pr-1">
          <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <template v-else-if="artifactDetail">
            <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">{{ artifactDetail.path }}</Badge>
              <Badge v-for="tag in artifactDetail.tags" :key="tag" variant="outline">{{ tag }}</Badge>
            </div>
            <p v-if="artifactDetail.summary" class="text-sm text-muted-foreground">{{ artifactDetail.summary }}</p>
            <div class="prose prose-sm max-w-none rounded-md border bg-background p-4" v-html="artifactHtml"></div>
          </template>
          <div v-else class="py-8 text-center text-sm text-muted-foreground">无内容</div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showArtifact = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <Dialog v-model:open="showArtifactHistory">
      <DialogContent class="max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>{{ artifactHistoryTarget?.title || '历史版本' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[74vh] space-y-3 overflow-auto pr-1">
          <div v-if="historyLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!artifactHistory.length" class="py-8 text-center text-sm text-muted-foreground">暂无历史版本</div>
          <template v-else>
            <details
              v-for="version in artifactHistory"
              :key="version.task_version"
              class="rounded-md border p-3"
              :open="version.is_current"
            >
              <summary class="cursor-pointer text-sm font-medium">
                <span class="font-mono">{{ version.task_version || 'default' }}</span>
                <Badge v-if="version.is_current" variant="outline" class="ml-2">current</Badge>
                <span class="ml-2 text-xs font-normal text-muted-foreground">{{ version.updated_at }}</span>
              </summary>
              <div class="mt-3 space-y-3">
                <div v-for="item in version.artifacts" :key="item.artifact_id" class="space-y-2">
                  <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{{ item.path }}</Badge>
                    <Badge variant="outline">{{ item.run_id }}</Badge>
                    <span>{{ item.updated_at }}</span>
                    <Badge v-for="tag in item.tags" :key="tag" variant="outline">{{ tag }}</Badge>
                  </div>
                  <div class="text-sm font-medium">{{ item.title }}</div>
                  <p v-if="item.summary" class="text-sm text-muted-foreground">{{ item.summary }}</p>
                  <div class="prose prose-sm max-w-none rounded-md border bg-background p-4" v-html="renderMarkdown(item.content)"></div>
                </div>
              </div>
            </details>
          </template>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showArtifactHistory = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
