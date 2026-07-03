<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { ArrowLeft, Bot, Check, ChevronDown, ChevronRight, HelpCircle, Maximize2, Minimize2, Save, WandSparkles } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { ProjectProfile, ArtifactTreeNode, DesignAgentResponse, WorkflowArtifact, WorkflowArtifactDetail, WorkflowArtifactHistoryVersion, WorkflowDefinition, WorkflowDesignResult, WorkflowRun, WorkflowRunEvent, WorkflowRunLog, WorkflowSubagentDetail, WorkflowTask } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { confirm } from '../../composables/useConfirm'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import WorkflowDagGraph from './WorkflowDagGraph.vue'
import WorkflowSubagentDetailPanel from './WorkflowSubagentDetailPanel.vue'
import { parseWorkflowDag } from './workflowDag'
import {
  ALL_STATUS_SENTINEL,
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
  subagentStatusLabel,
  subagentTaskIds,
} from '../../lib/workflowEvents'
import { buildWorkflowTaskProgressHash } from '../../lib/navigation'
import { formatLocalDatetime } from '../../lib/time'

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
const fullscreenArtifact = ref<{ title: string; path: string; summary: string; tags: string[]; content: string } | null>(null)
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
const expandedRunSubagents = ref<Record<string, Set<string>>>({})
const subagentDetails = ref<Record<string, WorkflowSubagentDetail>>({})
const subagentDetailLoading = ref<Set<string>>(new Set())
const subagentDetailErrors = ref<Record<string, string>>({})
const taskError = ref('')
const clearing = ref(false)
const clearTarget = ref<WorkflowDefinition | null>(null)
const expandedTaskIds = ref<Set<string>>(new Set())
const taskRunLogs = ref<Record<string, WorkflowRunLog[]>>({})
const taskRunEvents = ref<Record<string, WorkflowRunEvent[]>>({})
const taskLogLoading = ref<Set<string>>(new Set())
const collapsedTaskSubagents = ref<Record<string, Set<string>>>({})
// Task progress page: client-side filter / search / sort (feature 1).
const taskStatusFilter = ref(ALL_STATUS_SENTINEL)
const taskTypeFilter = ref('__all__')
const taskSearchInput = ref('')
const taskSearch = ref('')
const taskSort = ref('default')
// Per-task artifacts fetched on demand (feature 2, view outputs from tasks page).
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

function openArtifactFullscreen(artifact: WorkflowArtifact | WorkflowArtifactDetail) {
  fullscreenArtifact.value = {
    title: artifact.title,
    path: artifact.path,
    summary: artifact.summary,
    tags: artifact.tags,
    content: artifact.content || '',
  }
}

function openTaskArtifactFullscreen(task: WorkflowTask) {
  const artifact = activeTaskArtifact(task)
  if (artifact) openArtifactFullscreen(artifact)
}

function closeArtifactFullscreen() {
  fullscreenArtifact.value = null
}

const fullscreenArtifactHtml = computed(() =>
  fullscreenArtifact.value ? marked.parse(fullscreenArtifact.value.content, { async: false }) as string : '',
)

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

/** Whether the edit form has unsaved changes (any field touched since load). */
const formDirty = ref(false)
/** Suppress the dirty watcher while the form is being programmatically reset. */
let suppressDirty = false
watch(
  form,
  () => {
    if (suppressDirty) return
    formDirty.value = true
  },
  { deep: true },
)
function resetForm(next: typeof form.value) {
  suppressDirty = true
  form.value = { ...next }
  formDirty.value = false
  // let the deep watcher's synchronous flush pass before re-enabling
  void nextTick(() => {
    suppressDirty = false
  })
}
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
function resetTaskFilters() {
  taskStatusFilter.value = ALL_STATUS_SENTINEL
  taskTypeFilter.value = ALL_TYPE_SENTINEL
  taskSearchInput.value = ''
  taskSearch.value = ''
  taskSort.value = 'default'
}
const progressRun = computed(() =>
  (workflowRuns.value[progressWorkflowKey.value] || []).find(run => run.run_id === progressRunId.value) || null,
)
const runEventGroups = computed(() => groupEventsByActor(runEvents.value))
/** Single-column interleaved timeline for the run-progress view. */
const runTimeline = computed(() => buildTimeline(runEventGroups.value))
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
  resetForm({
    workflow_key: '',
    name: '',
    description: '',
    profile_key: profiles.value[0]?.profile_key || '',
    status: 'active',
    workflow_js: '',
  })
  formError.value = ''
}

function openEdit(item: WorkflowDefinition) {
  window.location.hash = `workflow/${item.workflow_key}/edit`
}

function prepareEditForm(item: WorkflowDefinition) {
  resetForm({
    workflow_key: item.workflow_key,
    name: item.name,
    description: item.description,
    profile_key: item.profile_key,
    status: item.status,
    workflow_js: item.workflow_js,
  })
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

/** Navigate back to the detail page of the current workflow, the hub for
 *  tasks / progress / edit so those sub-pages return to detail, not the list. */
function goDetail() {
  const key = routeWorkflowKey.value
  if (!key || routeMode.value === 'new') {
    // No parent detail (e.g. an orphaned entry): fall back to the list.
    goList()
    return
  }
  window.location.hash = `workflow/${key}/detail`
}

/** Return from the edit/new page: confirm if there are unsaved edits. */
async function backFromForm() {
  // 'new' has no parent detail → list. 'edit' returns to detail.
  if (routeMode.value === 'new') {
    if (formDirty.value) {
      const ok = await confirm({
        title: '放弃新建',
        description: '当前表单有未保存内容，确认离开？',
        destructive: true,
        confirmText: '离开',
      })
      if (!ok) return
    }
    goList()
    return
  }
  if (formDirty.value) {
    const ok = await confirm({
      title: '放弃修改',
      description: '当前工作流有未保存的改动，确认离开？',
      destructive: true,
      confirmText: '离开',
    })
    if (!ok) return
  }
  goDetail()
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
  if (event.kind === 'subagent_progress') {
    const parts: string[] = []
    if (event.last_tool_name) parts.push(`当前工具: ${event.last_tool_name}`)
    if (event.usage) {
      const usageParts: string[] = []
      if (event.usage.total_tokens != null) usageParts.push(`${event.usage.total_tokens} tokens`)
      if (event.usage.tool_uses != null) usageParts.push(`${event.usage.tool_uses} 次工具`)
      if (usageParts.length) parts.push(usageParts.join(' · '))
    }
    if (parts.length) return parts.join(' · ')
  }
  return event.status || ''
}

function eventClass(event: WorkflowRunEvent) {
  if (event.kind === 'error' || event.status === 'failed') return 'border-red-400'
  if (event.kind === 'tool_call') return 'border-blue-400'
  if (event.kind === 'tool_result') return event.status === 'failed' ? 'border-red-400' : 'border-green-400'
  if (event.kind === 'result') return 'border-foreground/40'
  return 'border-border'
}

/** Semantic kind for timeline styling. Groups the many event.kind values into
 *  a small visual family: message / think / tool / result / error / status. */
type TimelineKind = 'message' | 'think' | 'tool' | 'result' | 'error' | 'status'

function timelineKind(event: WorkflowRunEvent): TimelineKind {
  if (event.kind === 'error' || event.status === 'failed') return 'error'
  if (event.kind === 'tool_call') return 'tool'
  if (event.kind === 'tool_result') return event.status === 'failed' ? 'error' : 'result'
  if (event.kind === 'result') return event.status === 'failed' ? 'error' : 'result'
  if (event.kind === 'status') return 'status'
  return 'message'
}

/** Interleave main-agent and subagent events into a single reading order.
 *  - Main events stay as individual timeline nodes (in order).
 *  - For each subagent, only the FIRST lifecycle event (usually subagent_start)
 *    becomes a timeline node; it carries the actor so the UI can render the
 *    whole subagent thread inline. Later subagent lifecycle events are dropped
 *    from the top-level list (they live inside the thread card). */
interface TimelineEntry {
  actor: { id: string; role: 'main' | 'subagent'; label: string }
  event: WorkflowRunEvent
}

function buildTimeline(groups: { actor: { id: string; role: 'main' | 'subagent'; label: string }; events: WorkflowRunEvent[] }[]): TimelineEntry[] {
  const subFirstSeen = new Set<string>()
  const entries: TimelineEntry[] = []
  // Re-walk all events in original order. We need the raw stream; rebuild it
  // from groups by stable merge is complex, so we rely on each group preserving
  // its own order and interleave by created_at when available.
  const all: { actor: TimelineEntry['actor']; event: WorkflowRunEvent }[] = []
  for (const g of groups) {
    for (const e of g.events) all.push({ actor: g.actor, event: e })
  }
  all.sort((a, b) => {
    const ta = a.event.created_at ? Date.parse(a.event.created_at) : NaN
    const tb = b.event.created_at ? Date.parse(b.event.created_at) : NaN
    if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb
    return 0 // keep original order when timestamps missing
  })
  for (const item of all) {
    if (item.actor.role === 'subagent') {
      if (subFirstSeen.has(item.actor.id)) continue // only the first becomes a node
      subFirstSeen.add(item.actor.id)
    }
    entries.push(item)
  }
  return entries
}

function hasDetailContent(detail: WorkflowSubagentDetail | null) {
  return !!detail && (detail.agents.length > 0 || !!detail.task_output)
}

function runSubagentStatus(taskIdStr: string) {
  return subagentStatus(runEvents.value, taskIdStr)
}

function runSubagentStatusLabel(taskIdStr: string) {
  return subagentStatusLabel(runEvents.value, taskIdStr)
}

function runSubagentUsage(taskIdStr: string) {
  return subagentUsage(runEvents.value, taskIdStr)
}

function subagentDetailKey(runId: string, taskIdStr: string) {
  return `${runId}:${taskIdStr}`
}

async function ensureSubagentDetail(runId: string | null | undefined, taskIdStr: string) {
  if (!runId) return
  const key = subagentDetailKey(runId, taskIdStr)
  if (subagentDetails.value[key] || subagentDetailLoading.value.has(key)) return
  const loading = new Set(subagentDetailLoading.value)
  loading.add(key)
  subagentDetailLoading.value = loading
  const nextErrors = { ...subagentDetailErrors.value }
  delete nextErrors[key]
  subagentDetailErrors.value = nextErrors
  try {
    const detail = await api.getWorkflowRunSubagentDetail(runId, taskIdStr)
    subagentDetails.value = { ...subagentDetails.value, [key]: detail }
  } catch (e: unknown) {
    subagentDetailErrors.value = { ...subagentDetailErrors.value, [key]: errorMessage(e) }
  } finally {
    const done = new Set(subagentDetailLoading.value)
    done.delete(key)
    subagentDetailLoading.value = done
  }
}

function subagentDetail(runId: string | null | undefined, taskIdStr: string) {
  return runId ? subagentDetails.value[subagentDetailKey(runId, taskIdStr)] || null : null
}

function subagentDetailLoadingFor(runId: string | null | undefined, taskIdStr: string) {
  return runId ? subagentDetailLoading.value.has(subagentDetailKey(runId, taskIdStr)) : false
}

function subagentDetailErrorFor(runId: string | null | undefined, taskIdStr: string) {
  return runId ? subagentDetailErrors.value[subagentDetailKey(runId, taskIdStr)] || '' : ''
}

function isRunSubagentCollapsed(taskIdStr: string): boolean {
  return !expandedRunSubagents.value[selectedRunId.value]?.has(taskIdStr)
}

function toggleRunSubagent(taskIdStr: string) {
  const key = selectedRunId.value
  if (!key) return
  const set = new Set(expandedRunSubagents.value[key] ?? [])
  if (set.has(taskIdStr)) set.delete(taskIdStr)
  else {
    set.add(taskIdStr)
    void ensureSubagentDetail(key, taskIdStr)
  }
  expandedRunSubagents.value = { ...expandedRunSubagents.value, [key]: set }
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}

async function openArtifact(item: WorkflowArtifact) {
  detailLoading.value = true
  showArtifact.value = true
  artifactDetail.value = null
  fullscreenArtifact.value = null
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
    const result = await api.executeWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined)
    if (result.run_id) {
      window.location.hash = buildWorkflowTaskProgressHash(task.workflow_key, result.run_id)
      return
    }
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
    expandedRunSubagents.value = {
      ...expandedRunSubagents.value,
      [selectedRunId.value]: expandedRunSubagents.value[selectedRunId.value] ?? new Set(),
    }
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

function taskSubagentCollapseKey(task: WorkflowTask) {
  return `${taskId(task)}:${task.lease_run_id || ''}`
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

/** Single-column interleaved timeline for a task's expanded panel. */
function taskTimeline(task: WorkflowTask) {
  return buildTimeline(taskEventGroups(task))
}

function taskUsageFor(task: WorkflowTask, taskIdStr: string) {
  return subagentUsage(taskEvents(task), taskIdStr)
}

function taskStatusFor(task: WorkflowTask, taskIdStr: string) {
  return subagentStatus(taskEvents(task), taskIdStr)
}

function taskStatusLabelFor(task: WorkflowTask, taskIdStr: string) {
  return subagentStatusLabel(taskEvents(task), taskIdStr)
}

function isTaskSubagentCollapsed(task: WorkflowTask, taskIdStr: string): boolean {
  return !!collapsedTaskSubagents.value[taskSubagentCollapseKey(task)]?.has(taskIdStr)
}

function toggleTaskSubagent(task: WorkflowTask, taskIdStr: string) {
  const key = taskSubagentCollapseKey(task)
  const set = new Set(collapsedTaskSubagents.value[key] ?? [])
  if (set.has(taskIdStr)) set.delete(taskIdStr)
  else {
    set.add(taskIdStr)
    void ensureSubagentDetail(task.lease_run_id, taskIdStr)
  }
  collapsedTaskSubagents.value = { ...collapsedTaskSubagents.value, [key]: set }
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
    collapsedTaskSubagents.value = {
      ...collapsedTaskSubagents.value,
      [taskSubagentCollapseKey(task)]: new Set(subagentTaskIds(events)),
    }
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
  if (!await confirm({ title: '删除工作流', description: `确定删除工作流「${wf.name}」？其运行记录与产物将一并清除。`, destructive: true, confirmText: '删除' })) return
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
    expandedRunSubagents.value = {}
    runLogs.value = []
    selectedRunId.value = ''
    progressRunId.value = ''
    expandedTaskIds.value = new Set()
    taskRunLogs.value = {}
    taskRunEvents.value = {}
    collapsedTaskSubagents.value = {}
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
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openDetail(item)">详情</Button>
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
        <div v-if="selectedWorkflow" class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" @click="openTasks(selectedWorkflow)">任务进度</Button>
          <Button variant="outline" size="sm" @click="openEdit(selectedWorkflow)">编辑</Button>
          <Button
            v-if="runningRunFor(selectedWorkflow.workflow_key)"
            variant="default"
            size="sm"
            @click="openProgress(selectedWorkflow, runningRunFor(selectedWorkflow.workflow_key)?.run_id)"
          >
            运行中...
          </Button>
          <Button
            v-else
            size="sm"
            :disabled="hasAnyRunningRun"
            @click="runWorkflow(selectedWorkflow)"
          >
            运行
          </Button>
          <Button variant="ghost" size="sm" class="text-destructive" @click="requestClearWorkflow(selectedWorkflow)">清空</Button>
          <Button variant="ghost" size="sm" class="text-destructive" @click="deleteWorkflow(selectedWorkflow)">删除</Button>
        </div>
      </div>
      <div v-if="selectedWorkflow" class="space-y-5">
          <div class="flex flex-wrap items-start gap-3 border-b pb-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <Badge>{{ selectedWorkflow.workflow_key }}</Badge>
                <Badge variant="outline">{{ statusLabel(selectedWorkflow.status) }}</Badge>
              </div>
              <p class="mt-2 text-sm text-muted-foreground">{{ selectedWorkflow.description || '无描述' }}</p>
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
                <div class="mt-1 text-xs text-muted-foreground">{{ formatLocalDatetime(run.started_at) }}</div>
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
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goDetail">
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
          <!-- 筛选 / 搜索 / 排序 -->
          <div class="flex flex-wrap items-center gap-2">
            <Input
              v-model="taskSearchInput"
              type="search"
              placeholder="搜索 task_key / 类型"
              class="h-8 w-56 text-xs"
              @input="onTaskSearchInput"
            />
            <Select v-model="taskStatusFilter">
              <SelectTrigger class="h-8 w-[140px] text-xs">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all_status__">全部状态</SelectItem>
                <SelectItem v-for="status in taskStatuses" :key="status" :value="status">
                  {{ taskStatusLabel(status) }} {{ taskStats[status] || 0 }}
                </SelectItem>
              </SelectContent>
            </Select>
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
              v-if="taskStatusFilter !== ALL_STATUS_SENTINEL || taskTypeFilter !== ALL_TYPE_SENTINEL || taskSearch || taskSort !== 'default'"
              variant="ghost"
              size="sm"
              class="h-8 text-xs"
              @click="resetTaskFilters"
            >
              重置筛选
            </Button>
            <div class="ml-auto flex items-center gap-3">
              <span class="text-xs text-muted-foreground">
                {{ filteredTasks.length }} / {{ tasks.length }}
              </span>
              <Button
                variant="outline"
                size="sm"
                class="h-8 text-xs"
                :disabled="tasksLoading || !taskWorkflow"
                @click="taskWorkflow && loadTasks(taskWorkflow.workflow_key)"
              >
                {{ tasksLoading ? '刷新中' : '刷新' }}
              </Button>
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
                    <span>set {{ formatLocalDatetime(task.set_at) }}</span>
                    <span>更新 {{ formatLocalDatetime(task.updated_at) }}</span>
                    <span v-if="task.completed_at">完成 {{ formatLocalDatetime(task.completed_at) }}</span>
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
                        <span class="text-xs text-muted-foreground">更新 {{ formatLocalDatetime(activeTaskArtifact(task)?.updated_at ?? null) }}</span>
                        <Button
                          variant="ghost"
                          size="sm"
                          class="ml-auto h-6 gap-1 px-2 text-xs"
                          title="全屏查看"
                          @click="openTaskArtifactFullscreen(task)"
                        >
                          <Maximize2 :size="12" />
                          全屏
                        </Button>
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
                <div v-else class="space-y-2">
                  <div class="flex items-center justify-between gap-2">
                    <div class="text-xs font-semibold text-foreground">Agent 输出</div>
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
                        <Badge :variant="taskActorFilterFor(task) === actor.id ? 'default' : 'outline'">
                          {{ actor.label }}
                        </Badge>
                      </button>
                    </div>
                  </div>
                  <div v-if="!taskEvents(task).length" class="rounded-md border bg-background px-3 py-6 text-center text-sm text-muted-foreground">
                    还没有 Agent 输出，任务被领取执行后这里会按时间顺序显示对话流。
                  </div>
                  <div v-else class="tl-timeline max-h-[30rem] overflow-auto pr-1">
                    <template v-for="(entry, idx) in taskTimeline(task)" :key="taskRunLogKey(task) + ':tl:' + idx">
                      <!-- 主 Agent 事件 -->
                      <div v-if="entry.actor.role === 'main'" class="tl-event" :class="'k-' + timelineKind(entry.event)">
                        <div class="tl-avatar" />
                        <div class="tl-body">
                          <div class="tl-head">
                            <span class="tl-kind">{{ eventKindLabel(entry.event) }}</span>
                            <span v-if="entry.event.tool_name" class="tl-target"><b>{{ entry.event.tool_name }}</b></span>
                            <span v-if="entry.event.created_at" class="tl-time">{{ formatLocalDatetime(entry.event.created_at) }}</span>
                          </div>
                          <div v-if="eventMessage(entry.event)" class="tl-content">
                            <div
                              v-if="entry.event.message"
                              class="tl-md"
                              v-html="renderMarkdown(entry.event.message)"
                            />
                            <p v-else>{{ eventMessage(entry.event) }}</p>
                          </div>
                        </div>
                      </div>
                      <!-- 子 Agent 线程卡片（默认折叠） -->
                      <div v-else class="tl-event">
                        <div class="tl-avatar" style="border-color:#7c3aed" />
                        <div
                          class="tl-sub"
                          :class="{
                            open: !isTaskSubagentCollapsed(task, entry.actor.id),
                            'is-failed': taskStatusFor(task, entry.actor.id) === 'failed' || taskStatusFor(task, entry.actor.id) === 'error',
                          }"
                        >
                          <button type="button" class="tl-sub-head" @click="toggleTaskSubagent(task, entry.actor.id)">
                            <span class="tl-bot">
                              <Bot :size="14" />
                            </span>
                            <span>
                              <span class="tl-sub-id block leading-tight">{{ entry.actor.label }}</span>
                              <span class="tl-sub-desc block">task {{ entry.actor.id.slice(0, 8) }}</span>
                            </span>
                            <span class="tl-sub-stats">
                              <span v-if="taskUsageFor(task, entry.actor.id)">
                                <b>{{ taskUsageFor(task, entry.actor.id)?.total_tokens ?? 0 }}</b> tokens · <b>{{ taskUsageFor(task, entry.actor.id)?.tool_uses ?? 0 }}</b> 工具
                              </span>
                              <Badge
                                variant="outline"
                                :class="taskStatusFor(task, entry.actor.id) === 'completed'
                                  ? 'bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-300'
                                  : (taskStatusFor(task, entry.actor.id) === 'failed' || taskStatusFor(task, entry.actor.id) === 'error')
                                    ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'
                                    : 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'"
                                class="text-[10px]"
                              >
                                {{ taskStatusLabelFor(task, entry.actor.id) }}
                              </Badge>
                              <ChevronRight :size="14" class="tl-chevron" />
                            </span>
                          </button>
                          <div v-if="!isTaskSubagentCollapsed(task, entry.actor.id)" class="tl-sub-body">
                            <WorkflowSubagentDetailPanel
                              :detail="subagentDetail(task.lease_run_id, entry.actor.id)"
                              :loading="subagentDetailLoadingFor(task.lease_run_id, entry.actor.id)"
                              :error="subagentDetailErrorFor(task.lease_run_id, entry.actor.id)"
                            />
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
      </div>
    </section>

    <section v-if="routeMode === 'progress' && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goDetail">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-lg font-semibold text-foreground">{{ progressWorkflow?.name || '运行进度' }}</h2>
            <p class="font-mono text-xs text-muted-foreground">{{ progressRunId || '暂无运行 ID' }}</p>
          </div>
        </div>
        <Button v-if="progressWorkflow" variant="outline" size="sm" @click="progressWorkflow && openTasks(progressWorkflow)">
          任务进度
        </Button>
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
              <div v-if="progressRun?.started_at" class="text-xs text-muted-foreground">{{ formatLocalDatetime(progressRun.started_at) }}</div>
            </div>
            <Button variant="outline" size="sm" :disabled="logsLoading || runsLoading" @click="refreshProgress">
              {{ logsLoading || runsLoading ? '刷新中' : '刷新' }}
            </Button>
          </div>

          <div v-if="logsLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!selectedRunId" class="py-8 text-center text-sm text-muted-foreground">暂无运行记录</div>
          <div v-else class="space-y-2">
            <div class="flex items-center justify-between gap-2">
              <div class="text-xs font-semibold text-foreground">Agent 输出</div>
              <Badge variant="outline">{{ runEvents.length }}</Badge>
            </div>
            <div v-if="!runEvents.length" class="rounded-md border bg-background px-3 py-8 text-center text-sm text-muted-foreground">
              还没有 Agent 输出，任务被领取执行后这里会按时间顺序显示对话流。
            </div>
            <div v-else class="tl-timeline max-h-[32rem] overflow-auto pr-1">
              <template v-for="(entry, idx) in runTimeline" :key="'run-tl:' + idx">
                <!-- 主 Agent 事件 -->
                <div v-if="entry.actor.role === 'main'" class="tl-event" :class="'k-' + timelineKind(entry.event)">
                  <div class="tl-avatar" />
                  <div class="tl-body">
                    <div class="tl-head">
                      <span class="tl-kind">{{ eventKindLabel(entry.event) }}</span>
                      <span v-if="entry.event.agent_name" class="tl-target">{{ entry.event.agent_name }}</span>
                      <span v-if="entry.event.tool_name" class="tl-target"><b>{{ entry.event.tool_name }}</b></span>
                      <span v-if="entry.event.created_at" class="tl-time">{{ formatLocalDatetime(entry.event.created_at) }}</span>
                    </div>
                    <div v-if="eventMessage(entry.event)" class="tl-content">
                      <div
                        v-if="entry.event.message"
                        class="tl-md"
                        v-html="renderMarkdown(entry.event.message)"
                      />
                      <p v-else>{{ eventMessage(entry.event) }}</p>
                    </div>
                  </div>
                </div>
                <!-- 子 Agent 线程卡片（默认折叠） -->
                <div v-else class="tl-event">
                  <div class="tl-avatar" style="border-color:#7c3aed" />
                  <div
                    class="tl-sub"
                    :class="{
                      open: !isRunSubagentCollapsed(entry.actor.id),
                      'is-failed': runSubagentStatus(entry.actor.id) === 'failed' || runSubagentStatus(entry.actor.id) === 'error',
                    }"
                  >
                    <button type="button" class="tl-sub-head" @click="toggleRunSubagent(entry.actor.id)">
                      <span class="tl-bot">
                        <Bot :size="14" />
                      </span>
                      <span>
                        <span class="tl-sub-id block leading-tight">{{ entry.actor.label }}</span>
                        <span class="tl-sub-desc block">task {{ entry.actor.id.slice(0, 8) }}</span>
                      </span>
                      <span class="tl-sub-stats">
                        <span v-if="runSubagentUsage(entry.actor.id)">
                          <b>{{ runSubagentUsage(entry.actor.id)?.total_tokens ?? 0 }}</b> tokens · <b>{{ runSubagentUsage(entry.actor.id)?.tool_uses ?? 0 }}</b> 工具
                        </span>
                        <Badge
                          variant="outline"
                          :class="runSubagentStatus(entry.actor.id) === 'completed'
                            ? 'bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-300'
                            : (runSubagentStatus(entry.actor.id) === 'failed' || runSubagentStatus(entry.actor.id) === 'error')
                              ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'
                              : 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'"
                          class="text-[10px]"
                        >
                          {{ runSubagentStatusLabel(entry.actor.id) }}
                        </Badge>
                        <ChevronRight :size="14" class="tl-chevron" />
                      </span>
                    </button>
                    <div v-if="!isRunSubagentCollapsed(entry.actor.id)" class="tl-sub-body">
                      <WorkflowSubagentDetailPanel
                        :detail="subagentDetail(selectedRunId, entry.actor.id)"
                        :loading="subagentDetailLoadingFor(selectedRunId, entry.actor.id)"
                        :error="subagentDetailErrorFor(selectedRunId, entry.actor.id)"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
      </div>
    </section>

    <section v-if="isWorkflowFormPage && !routeError" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="backFromForm">
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

    <Dialog v-model:open="showArtifact" @update:open="(v: boolean) => { if (!v) closeArtifactFullscreen() }">
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
          <Button
            v-if="artifactDetail"
            variant="outline"
            class="mr-auto"
            title="全屏查看"
            @click="openArtifactFullscreen(artifactDetail)"
          >
            <Maximize2 :size="14" />
            全屏
          </Button>
          <Button variant="outline" @click="showArtifact = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <Teleport to="body">
      <div v-if="fullscreenArtifact" class="fixed inset-0 z-[10000] flex flex-col bg-background pointer-events-auto">
        <div class="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
          <div class="flex min-w-0 items-center gap-2">
            <span class="truncate text-sm font-medium">{{ fullscreenArtifact.title || '产物详情' }}</span>
            <Badge variant="outline" class="shrink-0 font-mono text-xs">{{ fullscreenArtifact.path }}</Badge>
            <Badge v-for="tag in fullscreenArtifact.tags" :key="tag" variant="outline" class="shrink-0 text-xs">{{ tag }}</Badge>
          </div>
          <Button variant="ghost" size="sm" class="h-8 w-8 shrink-0 p-0" title="退出全屏" @click="closeArtifactFullscreen()">
            <Minimize2 :size="16" />
          </Button>
        </div>
        <div class="flex-1 overflow-auto px-6 py-4">
          <div class="mx-auto max-w-4xl space-y-4">
            <div v-if="fullscreenArtifact.summary" class="text-sm text-muted-foreground">{{ fullscreenArtifact.summary }}</div>
            <div class="prose prose-sm max-w-none" v-html="fullscreenArtifactHtml"></div>
          </div>
        </div>
      </div>
    </Teleport>
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
                <span class="ml-2 text-xs font-normal text-muted-foreground">{{ formatLocalDatetime(version.updated_at) }}</span>
              </summary>
              <div class="mt-3 space-y-3">
                <div v-for="item in version.artifacts" :key="item.artifact_id" class="space-y-2">
                  <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{{ item.path }}</Badge>
                    <Badge variant="outline">{{ item.run_id }}</Badge>
                    <span>{{ formatLocalDatetime(item.updated_at) }}</span>
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

<style>
/* ============================================================
   Agent 输出 时间轴 (timeline)
   - 用主题 token 变量 (var(--*)) 兼容明暗模式
   - 命名空间 tl- 避免与其它组件冲突
   - 一个强调色 (primary 蓝), 子 agent 紫色仅作"角色"语义区分
   ============================================================ */
.tl-timeline{position:relative;padding:2px 0 0}
.tl-timeline::before{content:"";position:absolute;left:18px;top:6px;bottom:6px;width:2px;background:var(--border);border-radius:1px}
.tl-event{position:relative;padding:0 0 12px 46px}
.tl-event:last-child{padding-bottom:0}
.tl-avatar{position:absolute;left:10px;top:2px;width:18px;height:18px;border-radius:50%;background:var(--card);border:2px solid var(--primary);display:flex;align-items:center;justify-content:center;z-index:2}
.tl-avatar::after{content:"";width:8px;height:8px;border-radius:50%;background:var(--primary)}
.tl-event.k-think .tl-avatar{border-color:var(--warning)}
.tl-event.k-think .tl-avatar::after{background:var(--warning)}
.tl-event.k-tool .tl-avatar{border-color:var(--info)}
.tl-event.k-tool .tl-avatar::after{background:var(--info)}
.tl-event.k-result .tl-avatar{border-color:var(--success)}
.tl-event.k-result .tl-avatar::after{background:var(--success)}
.tl-event.k-error .tl-avatar{border-color:var(--destructive)}
.tl-event.k-error .tl-avatar::after{background:var(--destructive)}
.tl-body{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;transition:border-color .12s ease}
.tl-body:hover{border-color:var(--input)}
.tl-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 14px;border-bottom:1px solid var(--border)}
.tl-kind{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px}
.k-message .tl-kind{background:var(--accent);color:var(--accent-foreground)}
.k-think .tl-kind{background:color-mix(in oklch,var(--warning) 16%,transparent);color:var(--warning)}
.k-tool .tl-kind{background:color-mix(in oklch,var(--info) 16%,transparent);color:var(--info)}
.k-result .tl-kind{background:color-mix(in oklch,var(--success) 16%,transparent);color:var(--success)}
.k-error .tl-kind{background:color-mix(in oklch,var(--destructive) 16%,transparent);color:var(--destructive)}
.tl-target{font-family:var(--font-mono);font-size:12px;color:var(--muted-foreground)}
.tl-target b{color:var(--foreground);font-weight:600}
.tl-time{margin-left:auto;font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);flex-shrink:0;opacity:.85}
.tl-content{padding:11px 14px;font-size:13.5px;color:var(--foreground);line-height:1.6}
.tl-content p{margin:0 0 6px}
.tl-content p:last-child{margin-bottom:0}
.k-message .tl-content{font-size:14px;line-height:1.65}
.tl-content pre{margin:8px 0 0;background:var(--muted);border-radius:6px;padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;line-height:1.6;color:var(--foreground);overflow:auto;max-height:220px;white-space:pre-wrap}

/* ===== Markdown rendered inside timeline (.tl-md) ===== */
/* Scoped so it blends with the card instead of behaving like a heavy prose block. */
.tl-md{font-size:inherit;line-height:inherit;color:inherit;word-break:break-word}
.tl-md>:first-child{margin-top:0}
.tl-md>:last-child{margin-bottom:0}
.tl-md p{margin:0 0 6px}
.tl-md p:last-child{margin-bottom:0}
.tl-md h1,.tl-md h2,.tl-md h3,.tl-md h4{font-weight:600;line-height:1.3;margin:14px 0 6px;color:var(--foreground)}
.tl-md h1{font-size:1.18em}
.tl-md h2{font-size:1.1em}
.tl-md h3{font-size:1.02em}
.tl-md h4{font-size:.96em}
.tl-md ul,.tl-md ol{margin:4px 0 6px;padding-left:20px}
.tl-md li{margin:2px 0}
.tl-md li::marker{color:var(--muted-foreground)}
.tl-md a{color:var(--primary);text-decoration:underline;text-underline-offset:2px}
.tl-md a:hover{opacity:.8}
.tl-md blockquote{margin:6px 0;padding:4px 12px;border-left:3px solid var(--border);color:var(--muted-foreground)}
.tl-md blockquote p{margin:2px 0}
.tl-md hr{border:none;border-top:1px solid var(--border);margin:10px 0}
.tl-md code{font-family:var(--font-mono);font-size:.88em;background:var(--muted);padding:1px 5px;border-radius:4px;color:var(--foreground)}
.tl-md pre{margin:8px 0;background:var(--muted);border-radius:6px;padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;line-height:1.6;color:var(--foreground);overflow:auto;max-height:260px;white-space:pre-wrap}
.tl-md pre code{background:transparent;padding:0;font-size:inherit;border-radius:0}
.tl-md table{width:100%;border-collapse:collapse;margin:8px 0;font-size:.95em}
.tl-md th,.tl-md td{border:1px solid var(--border);padding:5px 8px;text-align:left}
.tl-md th{background:var(--muted);font-weight:600}

/* ===== Subagent thread card ===== */
.tl-sub{position:relative;background:color-mix(in oklch,#7c3aed 7%,var(--card));border:1px solid color-mix(in oklch,#7c3aed 24%,var(--border));border-radius:10px;overflow:hidden;transition:border-color .12s ease}
:root.dark .tl-sub{background:color-mix(in oklch,#7c3aed 14%,var(--card))}
.tl-sub-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;cursor:pointer;width:100%;text-align:left;background:transparent;border:none}
.tl-sub-head:hover{background:color-mix(in oklch,#7c3aed 6%,transparent)}
.tl-bot{width:26px;height:26px;border-radius:7px;flex-shrink:0;background:linear-gradient(135deg,#7c3aed,#9b6cf0);display:flex;align-items:center;justify-content:center;color:#fff}
.tl-sub.is-failed .tl-bot{background:linear-gradient(135deg,var(--destructive),#e85a5a)}
.tl-sub-id{font-weight:600;color:#5b21b6;font-size:13.5px}
:root.dark .tl-sub-id{color:#c4b0fd}
.tl-sub-desc{font-size:12px;color:var(--muted-foreground)}
.tl-sub-stats{margin-left:auto;display:flex;align-items:center;gap:10px;font-size:11.5px;color:var(--muted-foreground);font-family:var(--font-mono);flex-wrap:wrap}
.tl-sub-stats b{color:var(--foreground);font-weight:600}
.tl-chevron{color:#7c3aed;transition:transform .15s ease;flex-shrink:0}
:root.dark .tl-chevron{color:#a78bfa}
.tl-sub.open .tl-chevron{transform:rotate(90deg)}
.tl-sub-body{padding:0 14px 14px;background:var(--card);border-top:1px solid color-mix(in oklch,#7c3aed 18%,var(--border))}
.tl-result{margin:12px 0 4px;padding:10px 12px;background:color-mix(in oklch,#7c3aed 5%,var(--card));border:1px solid color-mix(in oklch,#7c3aed 22%,var(--border));border-left:3px solid #7c3aed;border-radius:6px}
.tl-sub.is-failed .tl-result{border-left-color:var(--destructive);background:color-mix(in oklch,var(--destructive) 5%,var(--card));border-color:color-mix(in oklch,var(--destructive) 22%,var(--border))}
.tl-result-label{font-size:11px;font-weight:600;color:#5b21b6;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px;display:flex;align-items:center;gap:5px}
:root.dark .tl-result-label{color:#c4b0fd}
.tl-sub.is-failed .tl-result-label{color:var(--destructive)}
.tl-result-text{font-size:13px;color:var(--foreground);line-height:1.6}
.tl-result-text pre{margin:6px 0 0;background:var(--muted);border-radius:6px;padding:8px 10px;font-family:var(--font-mono);font-size:11.5px;color:var(--foreground);overflow:auto;max-height:200px;white-space:pre-wrap}

/* ===== Mini timeline inside subagent ===== */
.tl-mini{position:relative;padding:6px 0 0 4px;margin-top:10px}
.tl-mini::before{content:"";position:absolute;left:5px;top:10px;bottom:10px;width:2px;background:color-mix(in oklch,#7c3aed 18%,var(--border));border-radius:1px}
.tl-mini-event{position:relative;padding:0 0 10px 22px}
.tl-mini-event:last-child{padding-bottom:0}
.tl-mavatar{position:absolute;left:0;top:1px;width:12px;height:12px;border-radius:50%;background:var(--card);border:2px solid #7c3aed;z-index:2}
.tl-mini-event.k-think .tl-mavatar{border-color:var(--warning)}
.tl-mini-event.k-tool .tl-mavatar{border-color:var(--info)}
.tl-mini-event.k-result .tl-mavatar{border-color:var(--success)}
.tl-mini-event.k-error .tl-mavatar{border-color:var(--destructive)}
.tl-mini-head{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:3px}
.tl-mini-kind{font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:999px}
.tl-mini-event.k-think .tl-mini-kind{background:color-mix(in oklch,var(--warning) 16%,transparent);color:var(--warning)}
.tl-mini-event.k-tool .tl-mini-kind{background:color-mix(in oklch,var(--info) 16%,transparent);color:var(--info)}
.tl-mini-event.k-result .tl-mini-kind{background:color-mix(in oklch,var(--success) 16%,transparent);color:var(--success)}
.tl-mini-event.k-error .tl-mini-kind{background:color-mix(in oklch,var(--destructive) 16%,transparent);color:var(--destructive)}
.tl-mini-event.k-message .tl-mini-kind{background:color-mix(in oklch,#7c3aed 14%,transparent);color:#5b21b6}
:root.dark .tl-mini-event.k-message .tl-mini-kind{color:#c4b0fd}
.tl-mini-target{font-family:var(--font-mono);font-size:11.5px;color:var(--muted-foreground)}
.tl-mini-target b{color:var(--foreground);font-weight:600}
.tl-mini-time{margin-left:auto;font-family:var(--font-mono);font-size:10.5px;color:var(--muted-foreground);opacity:.8}
.tl-mini-content{font-size:12.5px;color:var(--muted-foreground);line-height:1.55;white-space:pre-wrap}
.tl-mini-content.tl-dump{font-family:var(--font-mono);font-size:11.5px;color:var(--foreground);background:var(--muted);padding:6px 9px;border-radius:4px;margin-top:4px;white-space:pre-wrap;overflow:auto;max-height:160px}
/* Markdown output inside mini timeline: free-form agent text renders as solid
   prose (not muted, not pre-wrapped) so headings/lists/code read correctly. */
.tl-mini-content > .tl-md{color:var(--foreground);white-space:normal;word-break:break-word}

@media (prefers-reduced-motion: reduce){
  .tl-body,.tl-sub,.tl-chevron{transition:none}
}
</style>
