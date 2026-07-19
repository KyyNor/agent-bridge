<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowLeft, Check, Download, FolderOutput, GitBranch, HelpCircle, ListTodo, Maximize2, Minimize2, MoreHorizontal, Play, Plus, Save, Upload, WandSparkles, X } from 'lucide-vue-next'
import { api, beginWorkflowValidationRun, finishWorkflowValidationRun, hasBlockingWorkflowValidationErrors, invalidateWorkflowValidationRun, isCurrentWorkflowValidationRun, workflowValidationErrorMessage, workflowValidationIssuesFor } from '../../api/client'
import type { ProjectProfile, ArtifactTreeNode, DesignAgentResponse, WorkflowArtifact, WorkflowArtifactDetail, WorkflowArtifactHistoryVersion, WorkflowDefinition, WorkflowDesignResult, WorkflowDraft, WorkflowRun, WorkflowRunEvent, WorkflowRunLog, WorkflowSubagentDetail, WorkflowTask, WorkflowTaskImportPreview, WorkflowImportPreview, WorkflowImportTargetMode, AgentRun, AgentRuntimeConfig, ManagedScript, SkillPrompt, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowNodeRun, WorkflowNodeType, WorkflowValidationError, WorkflowType, WorkflowExecutionMode, WorkflowExecutionPlan } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { confirm, alert } from '../../composables/useConfirm'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import WorkflowTaskImportDialog from './WorkflowTaskImportDialog.vue'
import WorkflowImportDialog from '../../components/workflow/WorkflowImportDialog.vue'
import WorkflowEditorCanvas from './WorkflowEditorCanvas.vue'
import WorkflowNodePalette from './WorkflowNodePalette.vue'
import WorkflowConfigDrawer from './WorkflowConfigDrawer.vue'
import WorkflowNodeConfigPanel from './WorkflowNodeConfigPanel.vue'
import WorkflowEdgeConfigPanel from './WorkflowEdgeConfigPanel.vue'
import WorkflowRunGraph from './WorkflowRunGraph.vue'
import SubagentDetailPanel from '../../components/SubagentDetailPanel.vue'
import RunEventTimeline from '../../components/RunEventTimeline.vue'
import AgentRunTabs from '../../components/AgentRunTabs.vue'
import WorkflowRunDetailPanel from '../../components/WorkflowRunDetailPanel.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import StatCard from '../../components/StatCard.vue'
import RevisionHistoryPanel from '../../components/version/RevisionHistoryPanel.vue'
import { createDefaultGraph, deriveManualInputFields, deriveWorkflowBackendKeys, isProtectedSummaryEdge, migrateWorkflowGraph } from './workflowDefinition'
import { workflowNodeToneClass, workflowNodeTypeText } from './workflowNodeVisuals'
import { deriveAvailableData } from '../../lib/workflowReferences'
import {
  ALL_STATUS_SENTINEL,
  ALL_TYPE_SENTINEL,
  filterAndSortTasks,
  distinctStatuses,
  distinctTypes,
  taskStats as computeTaskStats,
  taskStatusLabel as labelTaskStatus,
  taskId,
  toggleTaskSelection,
  togglePageTaskSelection,
  runWorkflowTaskQueue,
  runWorkflowTaskResetQueue,
} from '../../lib/workflowTasks'
import {
  distinctActors,
  filterEventsByActor,
} from '../../lib/workflowEvents'
import { renderMarkdown } from '../../lib/markdown'
import { buildWorkflowTaskProgressHash } from '../../lib/navigation'
import { formatLocalDatetime } from '../../lib/time'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

const WORKFLOW_RUN_LIMIT = 200
const ARTIFACT_PAGE_SIZE_OPTIONS = [10, 20, 50] as const
const props = defineProps<{ routeKey: string }>()

const workflows = ref<WorkflowDefinition[]>([])
const profiles = ref<ProjectProfile[]>([])
const scripts = ref<ManagedScript[]>([])
const skills = ref<SkillPrompt[]>([])
const defaultBackend = ref('codex')
const agentRuntimeConfig = ref<AgentRuntimeConfig>({ default_backend: 'claude', backends: [] })
const artifacts = ref<WorkflowArtifact[]>([])
const selectedKey = ref('')
const loading = ref(true)
const workflowPage = ref(1)
const workflowPageSize = ref(10)
const artifactLoading = ref(false)
const artifactTotal = ref(0)
const artifactPage = ref(1)
const artifactPageSize = ref(50)
const error = ref('')
const workflowDetailError = ref('')
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
const fullscreenArtifact = ref<{ title: string; path: string; summary: string; tags: string[]; content: string; format: string } | null>(null)
const showGuide = ref(false)
const showClearConfirm = ref(false)
const detailTab = ref<'overview' | 'tasks' | 'artifacts' | 'runs' | 'versions'>('overview')
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
const progressRunArtifacts = ref<Record<string, WorkflowArtifact[]>>({})
const progressArtifactsLoading = ref(false)
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
const taskPage = ref(1)
const taskPageSize = ref(10)
const runPage = ref(1)
const runPageSize = ref(10)
// Progress page: multiple agent runs (e.g. workflow + html_reporter).
const progressAgentRuns = ref<AgentRun[]>([])
const progressAgentRunKey = ref('')
const progressAgentRunsLoading = ref(false)
const progressDetailError = ref('')
/** Maps a workflow_run_id to its agent_runs.run_key, so subagent-detail (which is
 *  keyed by run_key under /agent-runs) can be resolved from the workflow view. */
const runIdToAgentRunKey = ref<Record<string, string>>({})
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
const taskPreviews = ref<Record<string, WorkflowExecutionPlan>>({})
const taskPreviewLoading = ref<Set<string>>(new Set())
const resetTarget = ref<WorkflowTask | null>(null)
const resetting = ref(false)
const selectedTaskIds = ref<Set<string>>(new Set())
const batchAction = ref<'reset' | 'run' | ''>('')
const batchProgress = ref({ current: 0, total: 0, completed: 0, success: 0, failed: 0, skipped: 0, stopped: 0 })
const batchCurrentTask = ref<WorkflowTask | null>(null)
const batchCurrentTaskId = ref('')
const batchCurrentRunId = ref('')
const batchRunDetailError = ref('')
const batchSummary = ref('')
const showTaskImport = ref(false)
const taskImportPreview = ref<WorkflowTaskImportPreview | null>(null)
const taskImportLoading = ref(false)
const taskImportConfirming = ref(false)
const taskImportError = ref('')
let taskImportRequestToken = 0
const showWorkflowImport = ref(false)
const workflowImportPreview = ref<WorkflowImportPreview | null>(null)
const workflowImportLoading = ref(false)
const workflowImportConfirming = ref(false)
const workflowImportError = ref('')
const workflowImportTargetKey = ref('')
const workflowImportTargetMode = ref<WorkflowImportTargetMode>('auto')
const workflowImportFile = ref<File | null>(null)
let workflowImportRequestToken = 0
let batchToken = 0
const batchStopRequested = ref(false)
// Per-task sub-agent event filter (feature 5). Keyed by task id; "" = all.
const taskActorFilter = ref<Record<string, string>>({})
const taskArtifactActive = ref<Record<string, string>>({})
let taskSearchDebounce: ReturnType<typeof setTimeout> | null = null
let artifactRequestToken = 0
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
const designRunKey = ref('')
const designStopRequested = ref(false)
const selectedNodeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const graphErrors = ref<WorkflowValidationError[]>([])
const schemaEditorErrors = ref<Record<string, string>>({})
const runValidationGuard = ref({ validating: false, token: 0 })
const editedWorkflowRunBusy = computed(() => runValidationGuard.value.validating || testing.value)
type WorkflowConfigDrawerMode = 'overlay' | 'fullscreen'
const configDrawerOpen = ref(false)
const configDrawerMode = ref<WorkflowConfigDrawerMode>('overlay')
const manualInputValues = ref<Record<string, string>>({})
const advancedInput = ref('{}')
const progressRunDetail = ref<WorkflowRun | null>(null)
let testPoll: ReturnType<typeof setInterval> | null = null

const artifactHtml = computed(() =>
  artifactDetail.value ? renderMarkdown(artifactDetail.value.content) : '',
)

function openArtifactFullscreen(artifact: WorkflowArtifact | WorkflowArtifactDetail) {
  fullscreenArtifact.value = {
    title: artifact.title,
    path: artifact.path,
    summary: artifact.summary,
    tags: artifact.tags,
    content: artifact.content || '',
    format: artifact.format,
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
  fullscreenArtifact.value ? renderMarkdown(fullscreenArtifact.value.content) : '',
)

const form = ref({
  workflow_key: '',
  name: '',
  description: '',
  profile_key: '',
  status: 'active',
  workflow_type: 'operation' as WorkflowType,
  definition: createDefaultGraph('operation', defaultBackend.value) as WorkflowGraph,
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
    invalidateWorkflowValidationRun(runValidationGuard.value)
  },
  { deep: true },
)
watch(
  () => form.value.definition.nodes.map(node => node.id),
  (nodeIds) => {
    const activeIds = new Set(nodeIds)
    const next = Object.fromEntries(
      Object.entries(schemaEditorErrors.value).filter(([nodeId]) => activeIds.has(nodeId)),
    )
    if (Object.keys(next).length !== Object.keys(schemaEditorErrors.value).length) {
      schemaEditorErrors.value = next
    }
  },
)
function resetForm(next: typeof form.value) {
  invalidateWorkflowValidationRun(runValidationGuard.value)
  suppressDirty = true
  form.value = { ...next }
  schemaEditorErrors.value = {}
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

const selectedNode = computed(() => form.value.definition.nodes.find(node => node.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => form.value.definition.edges.find(edge => edge.id === selectedEdgeId.value) || null)
const selectedNodeIssues = computed(() => scopedGraphIssues('node', selectedNode.value?.id || null))
const selectedEdgeIssues = computed(() => scopedGraphIssues('edge', selectedEdge.value?.id || null))
const configDrawerTitle = computed(() => {
  if (selectedNode.value) return `节点配置 / ${selectedNode.value.name || selectedNode.value.id}`
  if (selectedEdge.value) return `连线条件 / ${selectedEdge.value.id}`
  return '配置'
})
const selectedNodeReferenceItems = computed(() => selectedNode.value ? deriveAvailableData(form.value.definition, { kind: 'node', id: selectedNode.value.id }, scripts.value) : [])
const selectedEdgeReferenceItems = computed(() => selectedEdge.value ? deriveAvailableData(form.value.definition, { kind: 'edge', id: selectedEdge.value.id }, scripts.value) : [])
const hasTaskNode = computed(() => form.value.definition.nodes.some(node => node.type === 'get_task'))
const manualInputFields = computed(() => deriveManualInputFields(form.value.definition, scripts.value))
const backendKeys = computed(() => deriveWorkflowBackendKeys(agentRuntimeConfig.value))
const selectedProfileName = computed(() => profileName(selectedWorkflow.value?.profile_key || ''))
const runs = computed(() => workflowRuns.value[selectedWorkflow.value?.workflow_key || ''] || [])
const latestRun = computed(() => runs.value[0] || null)
const latestRunTone = computed<'neutral' | 'ok' | 'err' | 'info'>(() => {
  if (latestRun.value?.status === 'completed') return 'ok'
  if (latestRun.value?.status === 'failed' || latestRun.value?.status === 'stopped') return 'err'
  if (latestRun.value?.status === 'running') return 'info'
  return 'neutral'
})
const latestRunValue = computed(() => {
  if (!latestRun.value) return '暂无运行'
  const duration = formatRunDuration(latestRun.value.duration_ms)
  return duration ? `${runStatusLabel(latestRun.value.status)} · ${duration}` : runStatusLabel(latestRun.value.status)
})
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
const pendingTaskCount = computed(() => (taskStats.value.pending || 0) + (taskStats.value.failed || 0) + (taskStats.value.abandoned || 0))
const workflowNodeCount = computed(() => selectedWorkflow.value?.definition?.nodes.length || 0)
const humanReadableArtifactCount = computed(() => artifacts.value.filter(item => item.format === 'html').length)
const recentArtifacts = computed(() => artifacts.value.slice(0, 3))
const detailTabs = computed(() => [
  { key: 'overview', label: '概览' },
  { key: 'tasks', label: '任务队列', count: pendingTaskCount.value || undefined },
  { key: 'artifacts', label: '工作流产物', count: artifactTotal.value || undefined },
  { key: 'runs', label: '运行记录', count: runs.value.length || undefined },
  { key: 'versions', label: '版本历史' },
])
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
const pagedWorkflows = computed(() => paginate(workflows.value, workflowPage.value, workflowPageSize.value))
const pagedTasks = computed(() => paginate(filteredTasks.value, taskPage.value, taskPageSize.value))
const selectedTasks = computed(() => filteredTasks.value.filter(task => selectedTaskIds.value.has(taskId(task))))
const allVisibleTasksSelected = computed(() =>
  pagedTasks.value.length > 0 && pagedTasks.value.every(task => selectedTaskIds.value.has(taskId(task))),
)
const someVisibleTasksSelected = computed(() =>
  pagedTasks.value.some(task => selectedTaskIds.value.has(taskId(task))),
)
const batchBusy = computed(() => batchAction.value !== '')
const batchProgressPercent = computed(() => batchProgress.value.total
  ? Math.min(100, Math.round((batchProgress.value.completed / batchProgress.value.total) * 100))
  : 0)
const batchPendingCount = computed(() => Math.max(
  0,
  batchProgress.value.total
    - batchProgress.value.completed
    - (batchCurrentTask.value ? 1 : 0),
))
const batchRunDetailVisible = computed(() => (routeMode.value === 'tasks' || (routeMode.value === 'detail' && detailTab.value === 'tasks'))
  && !!batchCurrentRunId.value
  && (batchAction.value === 'run' || !!batchSummary.value))
const pagedRuns = computed(() => paginate(runs.value, runPage.value, runPageSize.value))
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
const progressArtifacts = computed(() => progressRunArtifacts.value[progressRunId.value] || [])
const progressFinished = computed(() =>
  !!progressRun.value && ['completed', 'no_task', 'failed', 'stopped'].includes(progressRun.value.status),
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
  async () => {
    closeTaskImport()
    closeWorkflowImport()
    batchToken += 1
    batchStopRequested.value = false
    if (batchAction.value) batchAction.value = ''
    resetBatchRunDetail()
    await applyRoute()
  },
)

onUnmounted(() => {
  stopTestPolling()
  batchToken += 1
  batchStopRequested.value = false
  resetBatchRunDetail()
  closeWorkflowImport()
})

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [workflowList, profileList, scriptList, skillList, runtimeConfig] = await Promise.all([
      api.listWorkflows(),
      api.listProfiles(),
      api.listScripts(),
      api.listSkills(),
      api.getAgentRuntimeConfig(),
    ])
    workflows.value = workflowList
    profiles.value = profileList
    scripts.value = scriptList
    skills.value = skillList
    agentRuntimeConfig.value = runtimeConfig
    defaultBackend.value = runtimeConfig.default_backend || defaultBackend.value
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
  const token = ++artifactRequestToken
  artifactLoading.value = true
  artifactError.value = ''
  try {
    const result = await api.searchWorkflowArtifacts({
      profile_key: selectedWorkflow.value?.profile_key || form.value.profile_key || undefined,
      workflow_key: selectedWorkflow.value?.workflow_key || undefined,
      query: artifactQuery.value || undefined,
      path: artifactPath.value || undefined,
      tags: artifactTags.value.split(',').map(tag => tag.trim()).filter(Boolean),
      format: 'all',
      limit: artifactPageSize.value,
      offset: (artifactPage.value - 1) * artifactPageSize.value,
    })
    if (token !== artifactRequestToken) return
    artifacts.value = result.items
    artifactTotal.value = result.total ?? result.items.length
  } catch (e: unknown) {
    if (token !== artifactRequestToken) return
    artifactError.value = errorMessage(e)
    artifactTotal.value = 0
  } finally {
    if (token === artifactRequestToken) artifactLoading.value = false
  }
}

function resetArtifactPage() {
  artifactPage.value = 1
}

watch([artifactPage, artifactPageSize], () => {
  if (selectedWorkflow.value) searchArtifacts()
})

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
    workflow_type: 'operation',
    definition: createDefaultGraph('operation', defaultBackend.value),
  })
  formError.value = ''
  graphErrors.value = []
  selectedNodeId.value = null
  selectedEdgeId.value = null
  configDrawerOpen.value = false
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
    workflow_type: item.workflow_type === 'summary' ? 'summary' : 'operation',
    definition: item.definition || createDefaultGraph(item.workflow_type === 'summary' ? 'summary' : 'operation', defaultBackend.value),
  })
  formError.value = ''
  graphErrors.value = []
  selectedNodeId.value = null
  selectedEdgeId.value = null
  configDrawerOpen.value = false
}

function normalizeWorkflowIssue(value: unknown): WorkflowValidationError | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const scope = raw.scope
  if (scope !== 'workflow' && scope !== 'node' && scope !== 'edge') return null
  const id = typeof raw.id === 'string' ? raw.id : null
  const field = typeof raw.field === 'string' ? raw.field : null
  const message = typeof raw.message === 'string' ? raw.message : ''
  if (!message) return null
  const code = typeof raw.code === 'string' ? raw.code : 'invalid_definition'
  return { scope, id, field, message, code }
}

function collectWorkflowIssues(value: unknown): WorkflowValidationError[] {
  if (Array.isArray(value)) return value.map(normalizeWorkflowIssue).filter((issue): issue is WorkflowValidationError => Boolean(issue))
  if (!value || typeof value !== 'object') return []
  const raw = value as Record<string, unknown>
  for (const key of ['issues', 'errors']) {
    const found = collectWorkflowIssues(raw[key])
    if (found.length) return found
  }
  return collectWorkflowIssues(raw.detail)
}

function parseWorkflowIssues(message: string): WorkflowValidationError[] {
  const body = message.replace(/^\d+:\s*/, '').trim()
  try {
    const parsed = JSON.parse(body)
    const issues = collectWorkflowIssues(parsed)
    if (issues.length) return issues
  } catch {
    // Some server errors are plain text containing a JSON issues array.
  }
  const match = message.match(/"(?:errors|issues)"\s*:\s*(\[[\s\S]*?\])\s*[},]?/)
  if (!match) return []
  try {
    return collectWorkflowIssues(JSON.parse(match[1]))
  } catch {
    return []
  }
}

function scopedGraphIssues(scope: WorkflowValidationError['scope'], id: string | null) {
  return workflowValidationIssuesFor(graphErrors.value, scope, id)
}

function workflowDraft(): WorkflowDraft {
  return {
    workflow_key: form.value.workflow_key,
    name: form.value.name,
    description: form.value.description,
    profile_key: form.value.profile_key,
    status: form.value.status,
    workflow_type: form.value.workflow_type,
    definition: form.value.definition,
  }
}

async function validateWorkflowDraft(options: { isCurrent?: () => boolean } = {}): Promise<boolean | null> {
  graphErrors.value = []
  const schemaError = Object.values(schemaEditorErrors.value).find(Boolean)
  if (schemaError) {
    formError.value = `保存前请修正 Schema：${schemaError}`
    return false
  }
  const validation = await api.validateWorkflow(workflowDraft())
  if (options.isCurrent && !options.isCurrent()) return null
  if (!hasBlockingWorkflowValidationErrors(validation)) return true
  graphErrors.value = validation.errors
  formError.value = workflowValidationErrorMessage(validation)
  return false
}

async function saveWorkflow(): Promise<WorkflowDefinition | null> {
  formError.value = ''
  if (!form.value.workflow_key || !form.value.name || !form.value.profile_key) {
    formError.value = '请填写工作流标识、名称，并选择关联的能力平面'
    return null
  }
  saving.value = true
  try {
    if (!await validateWorkflowDraft()) return null
    const saved = await api.upsertWorkflow({
      workflow_key: form.value.workflow_key,
      name: form.value.name,
      description: form.value.description,
      profile_key: form.value.profile_key,
      status: form.value.status,
      workflow_type: form.value.workflow_type,
      definition: form.value.definition,
    })
    selectedKey.value = saved.workflow_key
    graphErrors.value = []
    workflows.value = await api.listWorkflows()
    await loadRunsForWorkflows()
    window.location.hash = `workflow/${saved.workflow_key}/detail`
    return saved
  } catch (e: unknown) {
    formError.value = errorMessage(e)
    graphErrors.value = parseWorkflowIssues(formError.value)
    return null
  } finally {
    saving.value = false
  }
}

function changeWorkflowType(value: WorkflowType) {
  const previous = form.value.workflow_type
  form.value.workflow_type = value
  form.value.definition = migrateWorkflowGraph(form.value.definition, previous, value, defaultBackend.value)
  selectedNodeId.value = null
  selectedEdgeId.value = null
  configDrawerOpen.value = false
}

function openWorkflowDesigner(mode: 'create' | 'modify' = 'modify') {
  designMode.value = mode
  showDesigner.value = true
  designError.value = ''
}

function createDesignRunKey() {
  const suffix = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `design-workflow-${suffix}`
}

function workflowDesignerCurrent(): Record<string, unknown> {
  if (designMode.value === 'modify') return { ...workflowDraft() }
  return {
    profile_key: form.value.profile_key,
    status: 'active',
    workflow_type: form.value.workflow_type,
    definition: createDefaultGraph(form.value.workflow_type, defaultBackend.value),
  }
}

function createNode(type: WorkflowNodeType, position = { x: 120 + form.value.definition.nodes.length * 36, y: 160 + form.value.definition.nodes.length * 32 }): WorkflowNode {
  const id = `${type}-${Date.now()}`
  if (type === 'get_task') return { id, type, name: '获取任务', position, config: { on_empty: 'terminate' } }
  if (type === 'agent') return { id, type, name: 'Agent', position, config: { prompt: '', backend_key: defaultBackend.value, mcp_enabled: true, skill_names: [], result_mode: 'text', output_schema: null } }
  if (type === 'script') return { id, type, name: '托管脚本', position, config: { script_key: '', params: {}, timeout_seconds: 60 } }
  return { id, type, name: '输出结果', position, config: { format: 'markdown', title: '输出结果', path: 'reports/output.md', tags: [], prompt: '', backend_key: defaultBackend.value, mcp_enabled: false, skill_names: [] } }
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

function setConfigDrawerMode(mode: WorkflowConfigDrawerMode) {
  configDrawerMode.value = mode
}

function replaceNode(node: WorkflowNode) {
  form.value.definition = { ...form.value.definition, nodes: form.value.definition.nodes.map(item => item.id === node.id ? node : item) }
}

function setNodeSchemaValidity(nodeId: string, valid: boolean, message: string) {
  const next = { ...schemaEditorErrors.value }
  if (valid) delete next[nodeId]
  else next[nodeId] = message || 'Schema 不合法'
  schemaEditorErrors.value = next
}

function replaceEdge(edge: WorkflowEdge) {
  form.value.definition = { ...form.value.definition, edges: form.value.definition.edges.map(item => item.id === edge.id ? edge : item) }
}

function manualInput(): Record<string, unknown> | null {
  let input: Record<string, unknown>
  try {
    const parsed = advancedInput.value.trim() ? JSON.parse(advancedInput.value) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
    input = parsed as Record<string, unknown>
  } catch {
    testError.value = '高级 JSON 必须是对象'
    return null
  }
  for (const field of manualInputFields.value) {
    const raw = manualInputValues.value[field.path] || ''
    if (field.required && !raw.trim()) {
      testError.value = `${field.path} 为必填项`
      return null
    }
    if (!raw.trim()) continue
    const value: unknown = field.type === 'integer' || field.type === 'number' ? Number(raw) : field.type === 'boolean' ? raw === 'true' : raw
    const parts = field.path.replace(/^input\./, '').split('.')
    let current = input
    parts.forEach((part, index) => {
      if (index === parts.length - 1) current[part] = value
      else current = (current[part] ||= {}) as Record<string, unknown>
    })
  }
  return input
}

async function runEditedWorkflow() {
  if (editedWorkflowRunBusy.value) return
  const validationToken = beginWorkflowValidationRun(runValidationGuard.value)
  if (validationToken === null) return
  const isCurrentValidation = () => isCurrentWorkflowValidationRun(runValidationGuard.value, validationToken)
  formError.value = ''
  try {
    const valid = await validateWorkflowDraft({ isCurrent: isCurrentValidation })
    if (!isCurrentValidation() || valid !== true) return
    if (formDirty.value) {
      formError.value = '有未保存的修改，请先保存后再测试运行'
      return
    }
    const workflow = selectedWorkflow.value
    if (!workflow) return
    const input = hasTaskNode.value ? {} : manualInput()
    if (input === null) return
    await runWorkflow(workflow, input)
  } catch (e: unknown) {
    if (!isCurrentValidation()) return
    formError.value = errorMessage(e)
    graphErrors.value = parseWorkflowIssues(formError.value)
  } finally {
    finishWorkflowValidationRun(runValidationGuard.value, validationToken)
  }
}

async function runWorkflowDesigner() {
  designError.value = ''
  if (!designPrompt.value.trim()) {
    designError.value = '请输入提示词'
    return
  }
  const runKey = createDesignRunKey()
  designRunKey.value = runKey
  designStopRequested.value = false
  designResponse.value = null
  designing.value = true
  try {
    const response = await api.designWorkflow({
      run_key: runKey,
      mode: designMode.value,
      prompt: designPrompt.value,
      current: workflowDesignerCurrent(),
      profile_key: form.value.profile_key || undefined,
    })
    if (designRunKey.value !== runKey || designStopRequested.value) return
    designResponse.value = response
    if (!response.ok) designError.value = response.error || '设计 agent 执行失败'
  } catch (e: unknown) {
    if (designRunKey.value !== runKey || designStopRequested.value) return
    designError.value = errorMessage(e)
  } finally {
    if (designRunKey.value === runKey) {
      if (designStopRequested.value && !designError.value) designError.value = '已停止'
      designing.value = false
      designStopRequested.value = false
    }
  }
}

async function stopWorkflowDesigner() {
  const runKey = designRunKey.value
  if (!designing.value || !runKey || designStopRequested.value) return
  designStopRequested.value = true
  designError.value = ''
  try {
    await api.stopAgentRun(runKey)
  } catch (e: unknown) {
    designError.value = errorMessage(e)
  }
}

async function acceptWorkflowDesign() {
  if (designing.value || designStopRequested.value) return
  const draft = workflowDesignDraft.value
  if (!draft) return
  const workflowType = draft.workflow_type === 'summary' ? 'summary' : 'operation'
  resetForm({
    workflow_key: draft.workflow_key,
    name: draft.name,
    description: draft.description,
    profile_key: draft.profile_key,
    status: draft.status,
    workflow_type: workflowType,
    definition: draft.definition || createDefaultGraph(workflowType, defaultBackend.value),
  })
  const saved = await saveWorkflow()
  if (saved) showDesigner.value = false
}

async function openDetail(item: WorkflowDefinition) {
  window.location.hash = `workflow/${item.workflow_key}/detail`
}

async function prepareDetail(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  detailTab.value = 'overview'
  resetArtifactPage()
  await Promise.all([searchArtifacts(), loadRuns(item.workflow_key), loadTasks(item.workflow_key)])
}

async function selectDetailTab(value: string) {
  if (value !== 'overview' && value !== 'tasks' && value !== 'artifacts' && value !== 'runs' && value !== 'versions') return
  detailTab.value = value
  if (value === 'tasks' && taskWorkflow.value) await loadTasks(taskWorkflow.value.workflow_key)
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
    progressAgentRunKey.value = ''
    await loadRuns(workflow.workflow_key)
    if (runId) {
      progressRunDetail.value = await api.getWorkflowRun(runId)
      await loadProgressAgentRuns()
      await loadProgressAgentEvents()
    }
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

function workflowTypeLabel(workflowType?: string) {
  if (workflowType === 'summary') return '总结（Markdown + HTML 输出节点）'
  return '操作'
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

function formatRunDuration(durationMs: number | null | undefined) {
  if (durationMs == null || durationMs < 0) return ''
  if (durationMs < 1000) return `${durationMs}ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 1 : 0)}s`
  return `${Math.floor(durationMs / 60_000)}m ${Math.round((durationMs % 60_000) / 1000)}s`
}

function runBadgeClass(status: string) {
  if (status === 'completed') return 'bg-success-soft text-success-soft-fg'
  if (status === 'failed') return 'bg-destructive-soft text-destructive-soft-fg'
  if (status === 'running') return 'bg-info-soft text-info-soft-fg'
  return ''
}

function taskStatusLabel(status: string) {
  return labelTaskStatus(status)
}

function taskBadgeClass(status: string) {
  if (status === 'completed') return 'bg-success-soft text-success-soft-fg'
  if (status === 'failed' || status === 'abandoned') return 'bg-destructive-soft text-destructive-soft-fg'
  if (status === 'running') return 'bg-info-soft text-info-soft-fg'
  if (status === 'pending') return 'bg-warning-soft text-warning-soft-fg'
  if (status === 'stale') return 'bg-warning-soft text-warning-soft-fg'
  return ''
}

function logLevelClass(level: string) {
  if (level === 'error') return 'border-destructive'
  if (level === 'warning' || level === 'warn') return 'border-warning'
  return 'border-border'
}

function hasDetailContent(detail: WorkflowSubagentDetail | null) {
  return !!detail && (detail.agents.length > 0 || !!detail.task_output)
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
    // Subagent detail is unified under /agent-runs/{run_key}; resolve the
    // workflow_run_id to its agent run_key first (cached when events loaded).
    let runKey = runIdToAgentRunKey.value[runId]
    if (!runKey) {
      const agentRun = await api.getAgentRunForWorkflowRun(runId)
      if (!agentRun) throw new Error('未找到该运行对应的 agent 记录')
      runKey = agentRun.run_key
      runIdToAgentRunKey.value = { ...runIdToAgentRunKey.value, [runId]: runKey }
    }
    const detail = await api.getAgentRunSubagentDetail(runKey, taskIdStr)
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

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}

function createBatchProgress(total = 0) {
  return { current: 0, total, completed: 0, success: 0, failed: 0, skipped: 0, stopped: 0 }
}

function resetBatchRunDetail() {
  batchCurrentTask.value = null
  batchCurrentTaskId.value = ''
  batchCurrentRunId.value = ''
  batchRunDetailError.value = ''
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

function resetTaskImportState() {
  taskImportRequestToken += 1
  taskImportPreview.value = null
  taskImportLoading.value = false
  taskImportConfirming.value = false
  taskImportError.value = ''
}

function taskImportWorkflowKey() {
  return taskWorkflow.value?.workflow_key || (routeMode.value === 'tasks' ? routeWorkflowKey.value : '')
}

function openTaskImport() {
  if (!taskImportWorkflowKey() || !taskWorkflow.value || batchBusy.value) return
  resetTaskImportState()
  showTaskImport.value = true
}

function closeTaskImport() {
  showTaskImport.value = false
  resetTaskImportState()
}

function isCurrentTaskImportRequest(token: number, workflowKey: string) {
  return token === taskImportRequestToken
    && showTaskImport.value
    && taskImportWorkflowKey() === workflowKey
}

async function previewTaskImport(file: File) {
  const workflowKey = taskImportWorkflowKey()
  if (!workflowKey || !taskWorkflow.value || batchBusy.value) return
  const requestToken = ++taskImportRequestToken
  taskImportPreview.value = null
  taskImportError.value = ''
  taskImportLoading.value = true
  try {
    const preview = await api.previewWorkflowTaskImport(workflowKey, file)
    if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportPreview.value = preview
  } catch (e: unknown) {
    if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportError.value = errorMessage(e)
  } finally {
    if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportLoading.value = false
  }
}

async function downloadTaskImportTemplate() {
  const workflowKey = taskImportWorkflowKey()
  if (!workflowKey || !taskWorkflow.value || batchBusy.value) return
  let objectUrl = ''
  let anchor: HTMLAnchorElement | null = null
  try {
    const blob = await api.downloadWorkflowTaskTemplate(workflowKey)
    objectUrl = URL.createObjectURL(blob)
    anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = `${workflowKey}-tasks-template.xlsx`
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
  } catch (e: unknown) {
    taskImportError.value = errorMessage(e)
    if (taskImportWorkflowKey() === workflowKey) showTaskImport.value = true
  } finally {
    anchor?.remove()
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

async function confirmTaskImport() {
  const workflowKey = taskImportWorkflowKey()
  const preview = taskImportPreview.value
  if (
    !workflowKey
    || !taskWorkflow.value
    || !preview?.import_id
    || !preview.can_confirm
    || taskImportConfirming.value
    || batchBusy.value
  ) return
  const requestToken = ++taskImportRequestToken
  taskImportConfirming.value = true
  taskImportError.value = ''
  try {
    const result = await api.confirmWorkflowTaskImport(workflowKey, preview.import_id)
    if (!isCurrentTaskImportRequest(requestToken, workflowKey)) return
    closeTaskImport()
    selectedTaskIds.value = new Set()
    batchSummary.value = `导入完成：新增 ${result.created}，更新 ${result.updated}，跳过（运行中） ${result.skipped_running}，跳过（已完成） ${result.skipped_completed}，重开（已过期） ${result.reopened_expired}`
    await loadTasks(workflowKey)
  } catch (e: unknown) {
    if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportError.value = errorMessage(e)
  } finally {
    if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportConfirming.value = false
  }
}

function resetWorkflowImportState() {
  workflowImportRequestToken += 1
  workflowImportPreview.value = null
  workflowImportLoading.value = false
  workflowImportConfirming.value = false
  workflowImportError.value = ''
  workflowImportTargetKey.value = ''
  workflowImportTargetMode.value = 'auto'
  workflowImportFile.value = null
}

function openWorkflowImport(preferredTargetKey = '') {
  resetWorkflowImportState()
  workflowImportTargetKey.value = preferredTargetKey
  workflowImportTargetMode.value = preferredTargetKey ? 'overwrite' : 'auto'
  showWorkflowImport.value = true
}

function closeWorkflowImport() {
  showWorkflowImport.value = false
  resetWorkflowImportState()
}

function isCurrentWorkflowImportRequest(token: number) {
  return token === workflowImportRequestToken && showWorkflowImport.value
}

function updateWorkflowImportTargetKey(value: string) {
  workflowImportTargetKey.value = value
  workflowImportPreview.value = null
  workflowImportError.value = ''
}

function updateWorkflowImportTargetMode(value: WorkflowImportTargetMode) {
  workflowImportTargetMode.value = value
  workflowImportPreview.value = null
  workflowImportError.value = ''
}

function selectWorkflowImportFile(file: File) {
  workflowImportFile.value = file
  void previewWorkflowImport(file)
}

async function previewWorkflowImport(file: File | null = workflowImportFile.value) {
  if (!file || !showWorkflowImport.value || workflowImportLoading.value || workflowImportConfirming.value) return
  workflowImportFile.value = file
  const requestToken = ++workflowImportRequestToken
  workflowImportPreview.value = null
  workflowImportError.value = ''
  workflowImportLoading.value = true
  try {
    const preview = await api.previewWorkflowImport(
      file,
      workflowImportTargetKey.value.trim() || undefined,
      workflowImportTargetMode.value,
    )
    if (isCurrentWorkflowImportRequest(requestToken)) workflowImportPreview.value = preview
  } catch (e: unknown) {
    if (isCurrentWorkflowImportRequest(requestToken)) workflowImportError.value = errorMessage(e)
  } finally {
    if (isCurrentWorkflowImportRequest(requestToken)) workflowImportLoading.value = false
  }
}

async function downloadWorkflowDefinition() {
  const workflowKey = selectedWorkflow.value?.workflow_key
  if (!workflowKey) return
  workflowDetailError.value = ''
  let objectUrl = ''
  let anchor: HTMLAnchorElement | null = null
  try {
    const blob = await api.exportWorkflow(workflowKey)
    objectUrl = URL.createObjectURL(blob)
    anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = `${workflowKey}.workflow.json`
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
  } catch (e: unknown) {
    workflowDetailError.value = errorMessage(e)
  } finally {
    anchor?.remove()
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

async function confirmWorkflowImport() {
  const preview = workflowImportPreview.value
  if (!preview?.import_id || !preview.can_confirm || workflowImportConfirming.value) return
  const requestToken = ++workflowImportRequestToken
  workflowImportConfirming.value = true
  workflowImportError.value = ''
  try {
    const result = await api.confirmWorkflowImport(preview.import_id)
    if (!isCurrentWorkflowImportRequest(requestToken)) return
    closeWorkflowImport()
    await loadAll()
    if (error.value) {
      workflowDetailError.value = `导入已完成，但刷新工作流列表失败：${error.value}`
      error.value = ''
      return
    }
    selectedKey.value = result.workflow_key
    window.location.hash = `workflow/${result.workflow_key}/detail`
  } catch (e: unknown) {
    if (isCurrentWorkflowImportRequest(requestToken)) workflowImportError.value = errorMessage(e)
  } finally {
    if (isCurrentWorkflowImportRequest(requestToken)) workflowImportConfirming.value = false
  }
}

async function handleWorkflowRestored() {
  await loadAll()
  const workflowKey = selectedWorkflow.value?.workflow_key
  if (workflowKey) await Promise.all([loadRuns(workflowKey), loadTasks(workflowKey), searchArtifacts()])
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
      format: 'all',
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

/** Whether the task row has a user-triggerable run action. */
function canExecuteTask(task: WorkflowTask): boolean {
  if (task.status === 'pending' || task.status === 'stale' || task.status === 'completed') return true
  if (task.status === 'running' && task.lease_expires_at) {
    return new Date(task.lease_expires_at).getTime() < Date.now()
  }
  return false
}

function taskExecutionMode(task: WorkflowTask): WorkflowExecutionMode {
  // Preview requests use execution_mode: 'incremental' for stale tasks and
  // execution_mode: 'force_full' for completed tasks with existing output.
  if (task.status === 'stale') return 'incremental'
  if (task.status === 'completed') return 'force_full'
  return 'normal'
}

function canPreviewTask(task: WorkflowTask): boolean {
  return task.status === 'stale' || task.status === 'completed' || task.status === 'pending'
}

function taskPreview(task: WorkflowTask): WorkflowExecutionPlan | undefined {
  return taskPreviews.value[taskId(task)]
}

async function previewTask(task: WorkflowTask) {
  const key = taskId(task)
  const loading = new Set(taskPreviewLoading.value)
  loading.add(key)
  taskPreviewLoading.value = loading
  try {
    const plan = await api.previewWorkflowRun(task.workflow_key, {
      task_key: task.task_key,
      task_version: task.task_version || undefined,
      execution_mode: task.status === 'stale' ? 'incremental' : task.status === 'completed' ? 'force_full' : 'normal',
    })
    taskPreviews.value = { ...taskPreviews.value, [key]: plan }
  } catch (e: unknown) {
    taskActionError.value = errorMessage(e)
  } finally {
    const done = new Set(taskPreviewLoading.value)
    done.delete(key)
    taskPreviewLoading.value = done
  }
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
  if (batchBusy.value || isTaskActionLoading(task)) return
  const loading = new Set(taskActionLoading.value)
  loading.add(key)
  taskActionLoading.value = loading
  taskActionError.value = ''
  try {
    const result = await api.executeWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined, taskExecutionMode(task))
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
  if (!task || resetting.value || batchBusy.value) return
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

function setTaskSelectedFromEvent(task: WorkflowTask, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedTaskIds.value = toggleTaskSelection(selectedTaskIds.value, task, checked)
}

function setVisibleTasksSelectedFromEvent(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedTaskIds.value = togglePageTaskSelection(selectedTaskIds.value, pagedTasks.value, checked)
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

function shouldStopBatchError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return /(?:^|\s)(?:4\d\d|5\d\d):/.test(message)
    || /already running|conflict|network|failed to fetch|fetch failed|页面队列已停止/i.test(message)
}

async function loadBatchRunDetail(task: WorkflowTask | null, runId: string, quiet: boolean) {
  if (!task || !runId) return
  const runChanged = batchCurrentRunId.value !== runId
  batchCurrentTask.value = task
  batchCurrentTaskId.value = taskId(task)
  batchCurrentRunId.value = runId
  progressWorkflowKey.value = task.workflow_key
  progressRunId.value = runId
  selectedRunId.value = runId
  if (runChanged) {
    progressAgentRunKey.value = ''
    progressDetailError.value = ''
  }
  try {
    await loadProgressAgentRuns()
    await loadProgressAgentEvents({ quiet })
  } catch (e: unknown) {
    progressDetailError.value = errorMessage(e)
  }
  batchRunDetailError.value = progressDetailError.value
}

async function waitForBatchRun(
  runId: string,
  token: number,
  onUpdate?: (run: WorkflowRun) => void | Promise<void>,
): Promise<WorkflowRun> {
  while (true) {
    if (token !== batchToken && !batchStopRequested.value) throw new Error('页面队列已停止')
    const run = await api.getWorkflowRun(runId)
    mergeWorkflowRun(run)
    await onUpdate?.(run)
    if (['completed', 'no_task', 'failed', 'stopped'].includes(run.status)) return run
    await sleep(1500)
  }
}

async function resetSelectedTasks() {
  if (batchBusy.value || !selectedTasks.value.length) return
  const queue = [...selectedTasks.value]
  const token = ++batchToken
  batchStopRequested.value = false
  batchAction.value = 'reset'
  batchProgress.value = createBatchProgress(queue.length)
  resetBatchRunDetail()
  batchSummary.value = ''
  taskActionError.value = ''
  let success = 0
  let skipped = 0
  let failed = 0
  try {
    const result = await runWorkflowTaskResetQueue(queue, {
      canReset: canResetTask,
      reset: task => api.resetWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined).then(() => undefined),
      isCancelled: () => token !== batchToken,
      shouldStopOnError: shouldStopBatchError,
      onTaskStart: (_task, index, total) => {
        batchProgress.value = { ...batchProgress.value, current: index + 1, total }
      },
    })
    if (token !== batchToken) return
    success = result.outcomes.filter(item => item.status === 'success').length
    skipped = result.outcomes.filter(item => item.status === 'skipped').length
    failed = result.outcomes.filter(item => item.status === 'failed').length
    const stoppedText = result.stopped ? `，队列已停止，剩余 ${result.remaining.length} 个任务未执行` : ''
    batchSummary.value = `批量重置完成：成功 ${success}，跳过 ${skipped}，失败 ${failed}${stoppedText}`
    const firstError = result.outcomes.find(item => item.error)?.error
    if (firstError) taskActionError.value = firstError
    selectedTaskIds.value = result.stopped
      ? new Set(result.remaining.map(taskId))
      : new Set()
    if (token === batchToken && taskWorkflow.value) await loadTasks(taskWorkflow.value.workflow_key)
  } finally {
    if (token === batchToken) batchAction.value = ''
  }
}

async function runSelectedTasks() {
  if (batchBusy.value || !selectedTasks.value.length) return
  const queue = [...selectedTasks.value]
  const token = ++batchToken
  batchStopRequested.value = false
  batchAction.value = 'run'
  batchProgress.value = createBatchProgress(queue.length)
  resetBatchRunDetail()
  batchSummary.value = ''
  taskActionError.value = ''
  try {
    const result = await runWorkflowTaskQueue(queue, {
      canExecute: canExecuteTask,
      execute: task => api.executeWorkflowTask(task.workflow_key, task.task_key, task.task_version || undefined, taskExecutionMode(task)),
      waitForRun: (runId, onUpdate) => waitForBatchRun(runId, token, onUpdate),
      isCancelled: () => token !== batchToken,
      stopRun: async runId => {
        try {
          await api.stopWorkflowRun(runId)
        } catch (error: unknown) {
          taskActionError.value = errorMessage(error)
          throw error
        }
      },
      shouldStopOnError: shouldStopBatchError,
      onTaskStart: (task, index, total) => {
        batchCurrentTask.value = task
        batchCurrentTaskId.value = taskId(task)
        batchProgress.value = { ...batchProgress.value, current: index + 1, total }
      },
      onRunStart: (task, runId) => loadBatchRunDetail(task, runId, false),
      onRunUpdate: (task, run) => loadBatchRunDetail(task, run.run_id, true),
      onTaskFinish: outcome => {
        batchProgress.value = {
          ...batchProgress.value,
          completed: batchProgress.value.completed + 1,
          success: batchProgress.value.success + (outcome.status === 'success' ? 1 : 0),
          failed: batchProgress.value.failed + (outcome.status === 'failed' ? 1 : 0),
          skipped: batchProgress.value.skipped + (outcome.status === 'skipped' ? 1 : 0),
          stopped: batchProgress.value.stopped + (outcome.status === 'stopped' ? 1 : 0),
        }
        batchCurrentTask.value = null
        batchCurrentTaskId.value = ''
      },
    })
    if (token !== batchToken && !batchStopRequested.value) return
    const success = result.outcomes.filter(item => item.status === 'success').length
    const failed = result.outcomes.filter(item => item.status === 'failed').length
    const skipped = result.outcomes.filter(item => item.status === 'skipped').length
    const stoppedText = result.stopped ? `，队列已停止，剩余 ${result.remaining.length} 个任务未执行` : ''
    const stopped = result.outcomes.filter(item => item.status === 'stopped').length
    batchSummary.value = `批量运行完成：成功 ${success}，跳过 ${skipped}，失败 ${failed}，停止 ${stopped}${stoppedText}`
    const firstError = result.outcomes.find(item => item.error)?.error
    if (firstError) taskActionError.value = firstError
    selectedTaskIds.value = result.stopped
      ? new Set(result.remaining.map(taskId))
      : new Set()
    if (token === batchToken && taskWorkflow.value) await loadTasks(taskWorkflow.value.workflow_key)
  } finally {
    if (token === batchToken || batchStopRequested.value) {
      batchAction.value = ''
      batchStopRequested.value = false
    }
  }
}

async function stopBatchRun() {
  if (batchAction.value !== 'run' || batchStopRequested.value) return
  batchStopRequested.value = true
  batchToken += 1
  const runId = batchCurrentRunId.value
  if (!runId) return
  try {
    await api.stopWorkflowRun(runId)
  } catch (error: unknown) {
    taskActionError.value = errorMessage(error)
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
    // Logs are workflow-scheduling-specific; agent execution events live under
    // /agent-runs (unified). Resolve the run_key once, then stream events from
    // the live events.jsonl via /agent-runs/{run_key}/events (real time, not
    // just the DB copy flushed at completion).
    let runKey = runIdToAgentRunKey.value[selectedRunId.value]
    const logsPromise = api.getWorkflowRunLogs(selectedRunId.value)
    if (!runKey) {
      const agentRun = await api.getAgentRunForWorkflowRun(selectedRunId.value)
      if (agentRun) {
        runKey = agentRun.run_key
        runIdToAgentRunKey.value = { ...runIdToAgentRunKey.value, [selectedRunId.value]: runKey }
      }
    }
    const [logs, events] = await Promise.all([
      logsPromise,
      runKey ? api.getAgentRunEvents(runKey) : Promise.resolve([] as WorkflowRunEvent[]),
    ])
    runLogs.value = logs
    runEvents.value = events
    if (options.quiet) {
      await refreshLoadedSubagentDetailsForRun(selectedRunId.value)
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

async function refreshLoadedSubagentDetailsForRun(runId: string) {
  if (!runId) return
  const runKey = runIdToAgentRunKey.value[runId]
  if (!runKey) return
  const taskIds = Object.keys(subagentDetails.value)
    .filter(key => key.startsWith(`${runId}:`))
    .map(key => key.slice(runId.length + 1))
  if (!taskIds.length) return
  const entries = await Promise.all(
    taskIds.map(async taskIdStr => {
      try {
        return [subagentDetailKey(runId, taskIdStr), await api.getAgentRunSubagentDetail(runKey, taskIdStr)] as const
      } catch {
        return null
      }
    }),
  )
  const next = { ...subagentDetails.value }
  for (const entry of entries) {
    if (entry) next[entry[0]] = entry[1]
  }
  subagentDetails.value = next
}

// ===== Progress page: multi agent-run support =====

/** Key used to index subagent details by agent run_key (progress page only). */
function progressSubagentDetailKey(agentRunKey: string, taskIdStr: string) {
  return `progress:${agentRunKey}:${taskIdStr}`
}

/** Load all agent runs associated with the current progress workflow run. */
async function loadProgressAgentRuns() {
  const workflowRunId = progressRunId.value
  if (!workflowRunId) {
    progressAgentRuns.value = []
    progressAgentRunKey.value = ''
    return
  }
  progressAgentRunsLoading.value = true
  try {
    const runs = await api.listAgentRunsForWorkflowRun(workflowRunId)
    progressAgentRuns.value = runs
    progressDetailError.value = ''
    // Default to the first agent run (oldest = main workflow agent).
    if (!progressAgentRunKey.value || !runs.some(r => r.run_key === progressAgentRunKey.value)) {
      progressAgentRunKey.value = runs[0]?.run_key || ''
    }
  } catch (e: unknown) {
    progressAgentRuns.value = []
    progressAgentRunKey.value = ''
    progressDetailError.value = errorMessage(e)
  } finally {
    progressAgentRunsLoading.value = false
  }
}

/** Load events for the currently selected progress agent run. */
async function loadProgressAgentEvents(options: { quiet?: boolean } = {}) {
  const agentRunKey = progressAgentRunKey.value
  if (!agentRunKey) {
    runEvents.value = []
    return
  }
  if (!options.quiet) logsLoading.value = true
  try {
    const events = await api.getAgentRunEvents(agentRunKey)
    runEvents.value = events
    progressDetailError.value = ''
    if (options.quiet) {
      await refreshProgressSubagentDetails(agentRunKey)
    }
  } catch (e: unknown) {
    progressDetailError.value = errorMessage(e)
    if (!options.quiet) {
      runEvents.value = []
    }
  } finally {
    if (!options.quiet) logsLoading.value = false
  }
}

async function refreshProgressSubagentDetails(agentRunKey: string) {
  if (!agentRunKey) return
  const prefix = progressSubagentDetailKey(agentRunKey, '')
  const taskIds = Object.keys(subagentDetails.value)
    .filter(key => key.startsWith(prefix))
    .map(key => key.slice(prefix.length))
  if (!taskIds.length) return
  const entries = await Promise.all(
    taskIds.map(async taskIdStr => {
      try {
        return [progressSubagentDetailKey(agentRunKey, taskIdStr), await api.getAgentRunSubagentDetail(agentRunKey, taskIdStr)] as const
      } catch {
        return null
      }
    }),
  )
  const next = { ...subagentDetails.value }
  for (const entry of entries) {
    if (entry) next[entry[0]] = entry[1]
  }
  subagentDetails.value = next
}

/** Switch to a different agent run in the progress page. */
async function selectProgressAgentRun(agentRunKey: string) {
  if (agentRunKey === progressAgentRunKey.value) return
  progressAgentRunKey.value = agentRunKey
  await loadProgressAgentEvents()
}

/** Subagent detail for the currently selected progress agent run. */
function progressSubagentDetail(taskIdStr: string) {
  const key = progressAgentRunKey.value ? progressSubagentDetailKey(progressAgentRunKey.value, taskIdStr) : ''
  return key ? subagentDetails.value[key] || null : null
}

function progressSubagentDetailLoading(taskIdStr: string) {
  const key = progressAgentRunKey.value ? progressSubagentDetailKey(progressAgentRunKey.value, taskIdStr) : ''
  return key ? subagentDetailLoading.value.has(key) : false
}

function progressSubagentDetailError(taskIdStr: string) {
  const key = progressAgentRunKey.value ? progressSubagentDetailKey(progressAgentRunKey.value, taskIdStr) : ''
  return key ? subagentDetailErrors.value[key] || '' : ''
}

async function ensureProgressSubagentDetail(taskIdStr: string) {
  const agentRunKey = progressAgentRunKey.value
  if (!agentRunKey) return
  const key = progressSubagentDetailKey(agentRunKey, taskIdStr)
  if (subagentDetails.value[key] || subagentDetailLoading.value.has(key)) return
  const loading = new Set(subagentDetailLoading.value)
  loading.add(key)
  subagentDetailLoading.value = loading
  const nextErrors = { ...subagentDetailErrors.value }
  delete nextErrors[key]
  subagentDetailErrors.value = nextErrors
  try {
    const detail = await api.getAgentRunSubagentDetail(agentRunKey, taskIdStr)
    subagentDetails.value = { ...subagentDetails.value, [key]: detail }
  } catch (e: unknown) {
    subagentDetailErrors.value = { ...subagentDetailErrors.value, [key]: errorMessage(e) }
  } finally {
    const done = new Set(subagentDetailLoading.value)
    done.delete(key)
    subagentDetailLoading.value = done
  }
}

async function loadProgressArtifacts() {
  const runId = progressRunId.value
  const workflowKey = progressWorkflowKey.value
  if (!runId || !workflowKey) return
  if (progressRunArtifacts.value[runId]) return
  progressArtifactsLoading.value = true
  try {
    const result = await api.searchWorkflowArtifacts({
      workflow_key: workflowKey,
      run_id: runId,
      include_history: true,
      full: true,
      format: 'all',
      limit: 50,
    })
    progressRunArtifacts.value = { ...progressRunArtifacts.value, [runId]: result.items }
  } catch (e: unknown) {
    artifactError.value = errorMessage(e)
    progressRunArtifacts.value = { ...progressRunArtifacts.value, [runId]: [] }
  } finally {
    progressArtifactsLoading.value = false
  }
}

async function openProgressArtifact() {
  await loadProgressArtifacts()
  const artifact = progressArtifacts.value.find(item => item.format === 'markdown') || progressArtifacts.value[0]
  if (!artifact) {
    await alert({ title: '暂无产物', description: '本次运行没有可查看的 Markdown 产物。' })
    return
  }
  openArtifactFullscreen(artifact)
}

async function openProgressHtmlReport() {
  await loadProgressArtifacts()
  const artifact = progressArtifacts.value.find(item => item.format === 'html')
  if (!artifact) {
    await alert({ title: '暂无 HTML 报告', description: '本次运行没有生成 HTML 报告，或报告仍在生成中。' })
    return
  }
  openArtifactFullscreen(artifact)
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

function isTaskLogLoading(task: WorkflowTask) {
  return task.lease_run_id ? taskLogLoading.value.has(task.lease_run_id) : false
}

async function openTasks(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  detailTab.value = 'tasks'
  if (routeMode.value === 'detail' && routeWorkflowKey.value === item.workflow_key) {
    await loadTasks(item.workflow_key)
    return
  }
  window.location.hash = `workflow/${item.workflow_key}/tasks`
}

async function prepareTasks(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  selectedTaskIds.value = new Set()
  resetBatchRunDetail()
  batchProgress.value = createBatchProgress()
  batchSummary.value = ''
  batchAction.value = ''
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
    const logsPromise = api.getWorkflowRunLogs(task.lease_run_id)
    let runKey = runIdToAgentRunKey.value[task.lease_run_id]
    if (!runKey) {
      const agentRun = await api.getAgentRunForWorkflowRun(task.lease_run_id)
      if (agentRun) {
        runKey = agentRun.run_key
        runIdToAgentRunKey.value = { ...runIdToAgentRunKey.value, [task.lease_run_id]: runKey }
      }
    }
    const [logs, events] = await Promise.all([
      logsPromise,
      runKey ? api.getAgentRunEvents(runKey) : Promise.resolve([] as WorkflowRunEvent[]),
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

async function runWorkflow(item: WorkflowDefinition, input: Record<string, unknown> = {}) {
  const wf = item
  if (!wf || testing.value) return
  testError.value = ''
  testing.value = true
  try {
    const res = await api.runWorkflow(wf.workflow_key, input)
    if (res.status === 'started' && res.run_id) {
      testingRunId.value = res.run_id
      progressWorkflowKey.value = wf.workflow_key
      progressRunId.value = res.run_id
      selectedKey.value = wf.workflow_key
      selectedRunId.value = res.run_id
      progressAgentRunKey.value = ''
      await loadRuns(wf.workflow_key)
      await loadProgressAgentRuns()
      await loadProgressAgentEvents()
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
  progressAgentRunKey.value = ''
  progressRunDetail.value = await api.getWorkflowRun(run.run_id)
  await loadProgressAgentRuns()
  await loadProgressAgentEvents()
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
  await loadProgressAgentRuns()
  await loadProgressAgentEvents()
  if (progressRunId.value) progressRunDetail.value = await api.getWorkflowRun(progressRunId.value)
}

async function openScriptRun(runId: string) {
  try {
    const scriptRun = await api.getScriptRun(runId)
    window.location.hash = `scripts/${scriptRun.script_key}/run/${runId}`
  } catch (e: unknown) {
    testError.value = errorMessage(e)
  }
}

function openAgentRun(runKey: string) {
  window.location.hash = `agent-runs/${runKey}`
}

async function pollTestRun() {
  const runId = testingRunId.value
  if (!runId) return
  try {
    const run = await api.getWorkflowRun(runId)
    progressRunDetail.value = run
    mergeWorkflowRun(run)
    // Refresh agent runs list (to pick up html reporter when it starts) and
    // refresh events for the currently selected agent run.
    await loadProgressAgentRuns()
    await loadProgressAgentEvents({ quiet: true })
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
    <div v-if="routeError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-3 text-sm text-destructive-soft-fg">
      {{ routeError }}。请<a class="underline" href="#workflow" @click.prevent="goList">返回列表</a>。
    </div>

    <template v-if="routeMode === 'list'">
    <!-- 页头操作：使用指引 + 新建工作流进 #ph-actions（仅列表态） -->
    <Teleport v-if="routeMode === 'list'" to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="showGuide = true">
        <HelpCircle :size="14" />
        使用指引
      </Button>
      <Button variant="outline" size="lg" @click="openWorkflowImport()">
        <Upload :size="14" />
        导入工作流
      </Button>
      <Button size="lg" class="shadow-btn" @click="openCreate">
        <Plus :size="14" />
        新建工作流
      </Button>
    </Teleport>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
      {{ error }}
    </div>

    <Dialog v-model:open="showGuide">
      <DialogContent class="w-[96vw] max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>工作流使用指引</DialogTitle>
        </DialogHeader>
        <div class="max-h-[74vh] space-y-4 overflow-auto pr-1 text-sm leading-6 text-muted-foreground">
          <section class="rounded-md border p-4">
            <h3 class="mb-2 text-sm font-semibold text-foreground">结构化 DAG</h3>
            <p>画布由获取任务、Agent、托管脚本和输出结果四类节点组成；节点输出通过显式引用和条件连线传递。</p>
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
            <h3 class="mb-2 text-sm font-semibold text-foreground">节点输出</h3>
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
            <p class="mt-2">获取任务节点没有领取到任务时，运行以 <span class="font-mono text-foreground">no_task</span> 结束；HTML 输出失败则记录为 warning，不影响 Markdown 主产物。</p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="mb-2 text-sm font-semibold text-foreground">测试运行</h3>
            <p>含获取任务节点时使用任务队列；其他工作流可由脚本参数中的 <span class="font-mono text-foreground" v-pre>{{ input.path }}</span> 引用推导输入字段，并用高级 JSON 补充值。</p>
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
          <div v-for="item in pagedWorkflows" :key="item.workflow_key" class="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_220px_340px] lg:items-center">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-medium text-foreground">{{ item.name }}</span>
                <StatusBadge v-if="item.status === 'active'" status="enabled" />
                <StatusBadge v-else status="disabled" />
                <Badge v-if="runningRunFor(item.workflow_key)" class="bg-info-soft text-info-soft-fg">运行中</Badge>
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
    <PaginationBar
      v-if="workflows.length"
      v-model:page="workflowPage"
      v-model:page-size="workflowPageSize"
      :total="workflows.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />
    </template>

    <section v-if="routeMode === 'detail' && !routeError" class="space-y-5">
      <div v-if="workflowDetailError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
        {{ workflowDetailError }}
      </div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-xl font-semibold tracking-tight text-foreground">{{ selectedWorkflow?.name || '工作流详情' }}</h2>
            <p class="mt-0.5 text-xs text-muted-foreground">{{ selectedWorkflow?.workflow_key || routeWorkflowKey }} · {{ selectedProfileName }}</p>
          </div>
        </div>
        <div v-if="selectedWorkflow" class="flex flex-wrap gap-2">
          <Button variant="outline" size="lg" @click="downloadWorkflowDefinition">
            <Download :size="14" />
            导出工作流
          </Button>
          <Button variant="outline" size="lg" @click="openWorkflowImport(selectedWorkflow.workflow_key)">
            <Upload :size="14" />
            导入工作流
          </Button>
          <details class="relative">
            <summary class="flex h-9 cursor-pointer list-none items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-sm font-medium text-foreground transition-colors hover:bg-muted [&::-webkit-details-marker]:hidden">
              <MoreHorizontal class="h-4 w-4" />
              更多
            </summary>
            <div class="absolute right-0 z-20 mt-1 grid min-w-28 gap-1 rounded-lg border border-border bg-popover p-1 shadow-pop">
              <button type="button" class="rounded-sm px-2.5 py-2 text-left text-sm text-popover-foreground transition-colors hover:bg-muted" @click="openEdit(selectedWorkflow)">编辑</button>
              <button type="button" class="rounded-sm px-2.5 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive-soft" @click="requestClearWorkflow(selectedWorkflow)">清空数据</button>
              <button type="button" class="rounded-sm px-2.5 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive-soft" @click="deleteWorkflow(selectedWorkflow)">删除</button>
            </div>
          </details>
          <Button
            v-if="runningRunFor(selectedWorkflow.workflow_key)"
            variant="default"
            size="lg"
            @click="openProgress(selectedWorkflow, runningRunFor(selectedWorkflow.workflow_key)?.run_id)"
          >
            运行中...
          </Button>
          <Button
            v-else
            size="lg"
            :disabled="hasAnyRunningRun"
            @click="runWorkflow(selectedWorkflow)"
          >
            运行
          </Button>
        </div>
      </div>
      <div v-if="selectedWorkflow" class="space-y-5">
          <div class="flex flex-wrap items-center gap-2 border-b border-border pb-4">
            <StatusBadge v-if="selectedWorkflow.status === 'active'" status="enabled" />
            <StatusBadge v-else status="disabled" />
            <Badge v-if="selectedWorkflow.workflow_type === 'summary'" variant="outline">总结类</Badge>
            <span class="text-xs text-muted-foreground">{{ workflowTypeLabel(selectedWorkflow.workflow_type) }}</span>
            <p class="basis-full pt-1 text-sm text-muted-foreground">{{ selectedWorkflow.description || '无描述' }}</p>
          </div>

          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="最近运行" :value="latestRunValue" :tone="latestRunTone">
              <template #icon><Play class="h-4 w-4" /></template>
              <template #sub>{{ latestRun ? `${formatLocalDatetime(latestRun.started_at)} · ${latestRun.run_id}` : '运行后在此查看状态' }}</template>
            </StatCard>
            <StatCard label="任务" :value="tasks.length">
              <template #icon><ListTodo class="h-4 w-4" /></template>
              <template #sub>{{ pendingTaskCount ? `${pendingTaskCount} 条需要处理` : '当前没有待处理项' }}</template>
            </StatCard>
            <StatCard label="当前产物" :value="artifactTotal">
              <template #icon><FolderOutput class="h-4 w-4" /></template>
              <template #sub>{{ humanReadableArtifactCount ? `${humanReadableArtifactCount} 个可读报告` : '按需检索产物' }}</template>
            </StatCard>
            <StatCard label="依赖节点" :value="workflowNodeCount">
              <template #icon><GitBranch class="h-4 w-4" /></template>
              <template #sub>{{ selectedWorkflow.definition?.edges.length || 0 }} 条连线</template>
            </StatCard>
          </div>

          <SegmentedTabs v-model="detailTab" :tabs="detailTabs" @update:model-value="selectDetailTab" />

          <div v-if="detailTab === 'overview'" class="space-y-4">
            <div class="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(280px,0.7fr)]">
              <Card v-if="selectedWorkflow.definition" class="shadow-card">
                <CardContent class="p-4">
                  <div class="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 class="text-sm font-semibold text-foreground">工作流图</h3>
                      <p class="mt-0.5 text-xs text-muted-foreground">{{ workflowNodeCount }} 个节点 · {{ selectedWorkflow.definition.edges.length }} 条连线</p>
                    </div>
                    <Button variant="outline" size="sm" @click="openEdit(selectedWorkflow)">编辑图</Button>
                  </div>
                  <WorkflowRunGraph :definition-snapshot="selectedWorkflow.definition" :node-runs="latestRun?.node_runs || []" @open-agent-run="openAgentRun" @open-script-run="openScriptRun" />
                </CardContent>
              </Card>
              <div v-else class="rounded-lg border border-warning/30 bg-warning-soft p-4 text-sm text-warning-soft-fg">该历史工作流需要迁移。进入编辑页并显式保存后才会写入结构化定义。</div>
              <div class="space-y-4">
                <Card size="sm" class="shadow-card">
                  <CardContent class="space-y-3">
                    <div class="flex items-center justify-between gap-2"><h3 class="text-sm font-semibold">最近运行</h3><StatusBadge v-if="latestRun" :status="latestRun.status === 'completed' ? 'success' : latestRun.status === 'running' ? 'running' : latestRun.status === 'failed' || latestRun.status === 'stopped' ? 'error' : 'blocked'" :label="runStatusLabel(latestRun.status)" /></div>
                    <template v-if="latestRun"><div class="font-mono text-xs text-muted-foreground">{{ latestRun.run_id }}</div><div class="grid grid-cols-2 gap-x-3 gap-y-2 text-xs"><span class="text-muted-foreground">开始时间</span><span class="text-right tabular-nums">{{ formatLocalDatetime(latestRun.started_at) }}</span><span class="text-muted-foreground">耗时</span><span class="text-right font-medium tabular-nums">{{ formatRunDuration(latestRun.duration_ms) || '—' }}</span></div><Button class="w-full" size="sm" variant="outline" @click="openProgress(selectedWorkflow, latestRun.run_id)">查看运行详情</Button></template>
                    <p v-else class="py-5 text-center text-sm text-muted-foreground">还没有运行记录</p>
                  </CardContent>
                </Card>
                <Card size="sm" class="shadow-card">
                  <CardContent class="flex items-start gap-3">
                    <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-warning-soft text-warning-soft-fg"><ListTodo class="h-4 w-4" /></div>
                    <div class="min-w-0 flex-1"><h3 class="text-sm font-semibold">下一步</h3><p class="mt-1 text-xs leading-5 text-muted-foreground">{{ pendingTaskCount ? `处理剩余 ${pendingTaskCount} 条任务，或批量运行可执行项。` : '检查工作流图与最新产物，确认下一次运行输入。' }}</p><Button class="mt-3" size="sm" variant="outline" @click="selectDetailTab('tasks')">进入任务队列</Button></div>
                  </CardContent>
                </Card>
              </div>
            </div>
            <div class="grid gap-4 xl:grid-cols-2">
              <Card size="sm" class="shadow-card"><CardContent><div class="mb-2 flex items-center justify-between"><div><h3 class="text-sm font-semibold">最新产物</h3><p class="mt-0.5 text-xs text-muted-foreground">最近更新的工作流输出</p></div><Button size="sm" variant="ghost" @click="selectDetailTab('artifacts')">查看全部</Button></div><div v-if="recentArtifacts.length" class="divide-y"><button v-for="item in recentArtifacts" :key="item.artifact_id" type="button" class="list-row-interactive flex w-full items-center justify-between gap-3 px-1 py-2.5 text-left" @click="openArtifact(item)"><div class="min-w-0"><div class="truncate text-sm font-medium">{{ item.title }}</div><div class="mt-0.5 truncate text-xs text-muted-foreground">{{ item.path }} · {{ formatLocalDatetime(item.updated_at) }}</div></div><Badge variant="outline" class="shrink-0">{{ item.format }}</Badge></button></div><p v-else class="py-5 text-center text-sm text-muted-foreground">暂无产物</p></CardContent></Card>
              <Card size="sm" class="shadow-card"><CardContent><div class="mb-2"><h3 class="text-sm font-semibold">活动</h3><p class="mt-0.5 text-xs text-muted-foreground">最近运行与产物更新</p></div><div v-if="latestRun || recentArtifacts.length" class="divide-y"><button v-if="latestRun" type="button" class="list-row-interactive flex w-full items-start justify-between gap-3 px-1 py-2.5 text-left" @click="openProgress(selectedWorkflow, latestRun.run_id)"><div><div class="text-sm font-medium">工作流{{ runStatusLabel(latestRun.status) }}</div><div class="mt-0.5 font-mono text-xs text-muted-foreground">{{ latestRun.run_id }}</div></div><span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(latestRun.started_at) }}</span></button><div v-for="item in recentArtifacts.slice(0, 2)" :key="`activity:${item.artifact_id}`" class="flex items-start justify-between gap-3 px-1 py-2.5"><div><div class="text-sm font-medium">产物已更新</div><div class="mt-0.5 truncate text-xs text-muted-foreground">{{ item.path }}</div></div><span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(item.updated_at) }}</span></div></div><p v-else class="py-5 text-center text-sm text-muted-foreground">还没有活动记录</p></CardContent></Card>
            </div>
          </div>

          <section v-if="detailTab === 'artifacts'" class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
            <div class="flex flex-wrap items-end gap-3">
              <div class="min-w-[220px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">检索</label>
                <Input v-model="artifactQuery" placeholder="标题、摘要、路径" @keyup.enter="resetArtifactPage(); searchArtifacts()" />
              </div>
              <div class="min-w-[180px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">path</label>
                <Input v-model="artifactPath" placeholder="reports/page-a/" @keyup.enter="resetArtifactPage(); searchArtifacts()" />
              </div>
              <div class="min-w-[180px] flex-1">
                <label class="mb-1 block text-xs text-muted-foreground">tags</label>
                <Input v-model="artifactTags" placeholder="finance, report" @keyup.enter="resetArtifactPage(); searchArtifacts()" />
              </div>
              <Button :disabled="artifactLoading" @click="resetArtifactPage(); searchArtifacts()">{{ artifactLoading ? '检索中' : '检索产物' }}</Button>
            </div>
            <div v-if="artifactError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
              {{ artifactError }}
            </div>
            <div v-if="!artifactRows.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无产物</div>
            <div class="space-y-1.5">
              <template v-for="row in artifactRows" :key="row.type + ':' + row.path">
                <button
                  v-if="row.type === 'folder'"
                  class="list-row-interactive flex w-full items-center gap-1.5 rounded-md py-1 text-left text-xs font-semibold uppercase text-muted-foreground hover:text-foreground"
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
                      <Badge v-if="row.artifact?.format === 'html'" variant="secondary" class="text-xs">人类阅读</Badge>
                      <Badge v-else variant="secondary" class="text-xs">AI 检索</Badge>
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
            <PaginationBar
              v-if="artifactTotal"
              v-model:page="artifactPage"
              v-model:page-size="artifactPageSize"
              :total="artifactTotal"
              :page-size-options="ARTIFACT_PAGE_SIZE_OPTIONS"
            />
          </section>

          <section v-if="detailTab === 'runs'" class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold">运行记录</h3>
              <Button variant="outline" size="sm" :disabled="runsLoading" @click="loadRuns(selectedWorkflow.workflow_key)">{{ runsLoading ? '刷新中' : '刷新' }}</Button>
            </div>
            <div v-if="runsLoading" class="py-4 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runs.length" class="rounded-md border px-4 py-6 text-sm text-muted-foreground">暂无运行记录</div>
            <div v-else class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              <button
                v-for="run in pagedRuns"
                :key="run.run_id"
                class="list-row-interactive rounded-md border px-3 py-2 text-left"
                @click="openProgress(selectedWorkflow, run.run_id)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate font-mono text-xs">{{ run.run_id }}</span>
                  <Badge :variant="run.status === 'completed' ? 'secondary' : 'outline'" :class="runBadgeClass(run.status)">{{ runStatusLabel(run.status) }}</Badge>
                </div>
                <div class="mt-1 text-xs text-muted-foreground">{{ formatLocalDatetime(run.started_at) }}</div>
              </button>
            </div>
            <PaginationBar
              v-if="runs.length"
              v-model:page="runPage"
              v-model:page-size="runPageSize"
              :total="runs.length"
              :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
              />
          </section>
          <section v-if="detailTab === 'versions'" class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold">版本历史</h3>
              <span v-if="selectedWorkflow?.revision_no" class="rounded bg-secondary px-2 py-0.5 font-mono text-xs text-muted-foreground">当前 v{{ selectedWorkflow.revision_no }}</span>
            </div>
            <RevisionHistoryPanel
              v-if="selectedWorkflow"
              :key="`wf-${selectedWorkflow.workflow_key}`"
              entity-type="workflow"
              :entity-key="selectedWorkflow.workflow_key"
              @restored="handleWorkflowRestored"
            />
            <p v-else class="py-8 text-center text-sm text-muted-foreground">未选择工作流</p>
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
      <DialogContent class="w-[min(560px,calc(100vw-2rem))] max-w-[560px] sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>重置任务</DialogTitle>
        </DialogHeader>
        <div class="min-w-0 space-y-3 text-sm text-muted-foreground">
          <p>
            确定重置任务
            <span class="break-all font-mono font-medium text-foreground">「{{ resetTarget?.task_key }}」</span>
            <span v-if="resetTarget?.task_version" class="break-all text-foreground">（{{ resetTarget.task_version }}）</span>
            吗？
          </p>
          <div class="rounded-md border bg-muted/30 px-3 py-2 text-xs leading-5">
            重置后该任务会回到待处理状态，可被再次领取执行；不会立即触发执行，也不会改变其他任务的执行顺序。历史尝试次数和错误信息会保留。
          </div>
        </div>
        <DialogFooter class="flex-wrap gap-2">
          <Button variant="outline" :disabled="resetting" @click="closeResetConfirm">取消</Button>
          <Button variant="destructive" :disabled="resetting" @click="confirmResetTask">
            {{ resetting ? '重置中' : '确认重置' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <WorkflowImportDialog
      v-model:open="showWorkflowImport"
      :preview="workflowImportPreview"
      :loading="workflowImportLoading"
      :confirming="workflowImportConfirming"
      :error="workflowImportError || null"
      :target-workflow-key="workflowImportTargetKey"
      :target-mode="workflowImportTargetMode"
      :has-file="workflowImportFile !== null"
      @update:open="(open: boolean) => { if (!open) closeWorkflowImport() }"
      @select-file="selectWorkflowImportFile"
      @update-target-key="updateWorkflowImportTargetKey"
      @update-target-mode="updateWorkflowImportTargetMode"
      @preview="previewWorkflowImport()"
      @confirm="confirmWorkflowImport"
    />

    <WorkflowTaskImportDialog
      v-if="routeMode === 'tasks' || (routeMode === 'detail' && detailTab === 'tasks')"
      v-model:open="showTaskImport"
      :preview="taskImportPreview"
      :loading="taskImportLoading"
      :confirming="taskImportConfirming"
      :error="taskImportError || null"
      @update:open="(open: boolean) => { if (!open) closeTaskImport() }"
      @select-file="previewTaskImport"
      @download-template="downloadTaskImportTemplate"
      @confirm="confirmTaskImport"
    />

    <section v-if="(routeMode === 'tasks' || (routeMode === 'detail' && detailTab === 'tasks')) && !routeError" class="space-y-4">
      <div v-if="routeMode === 'tasks'" class="flex flex-wrap items-center justify-between gap-3">
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
              <label v-if="pagedTasks.length" class="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  :checked="allVisibleTasksSelected"
                  :indeterminate.prop="someVisibleTasksSelected && !allVisibleTasksSelected"
                  :disabled="batchBusy"
                  @change="setVisibleTasksSelectedFromEvent"
                />
                本页全选
              </label>
              <span v-if="selectedTasks.length" class="text-xs text-primary">已选 {{ selectedTasks.length }}</span>
              <Button
                v-if="selectedTasks.length"
                variant="outline"
                size="sm"
                class="h-8 text-xs text-warning"
                :disabled="batchBusy"
                @click="resetSelectedTasks"
              >
                {{ batchAction === 'reset' ? `重置中 ${batchProgress.current}/${batchProgress.total}` : '批量重置' }}
              </Button>
              <Button
                v-if="selectedTasks.length"
                variant="outline"
                size="sm"
                class="h-8 text-xs text-primary"
                :disabled="batchBusy"
                @click="runSelectedTasks"
              >
                {{ batchAction === 'run' ? `运行中 ${batchProgress.current}/${batchProgress.total}` : '批量运行' }}
              </Button>
              <Button
                v-if="batchAction === 'run'"
                variant="outline"
                size="sm"
                class="h-8 text-xs text-destructive"
                :disabled="batchStopRequested"
                @click="stopBatchRun"
              >
                {{ batchStopRequested ? '停止中' : '停止批量' }}
              </Button>
              <span class="text-xs text-muted-foreground">
                {{ filteredTasks.length }} / {{ tasks.length }}
              </span>
              <Button
                variant="outline"
                size="sm"
                class="h-8 text-xs"
                :disabled="!taskWorkflow || batchBusy"
                @click="downloadTaskImportTemplate"
              >
                下载模板
              </Button>
              <Button
                size="sm"
                class="h-8 text-xs"
                :disabled="!taskWorkflow || batchBusy"
                @click="openTaskImport"
              >
                导入 Excel
              </Button>
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

          <div v-if="taskError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
            {{ taskError }}
          </div>
          <div v-if="taskActionError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
            {{ taskActionError }}
          </div>
          <div
            v-if="batchAction === 'run' || batchSummary"
            class="workflow-batch-run-context sticky top-0 z-30 space-y-3 bg-background/95 pb-2 pt-1 backdrop-blur"
          >
            <div v-if="batchSummary" class="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">
              {{ batchSummary }}
            </div>
            <div
              v-if="batchAction === 'run' || (batchSummary && batchCurrentRunId)"
              class="space-y-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-3"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0 space-y-1">
                  <div class="text-sm font-semibold text-primary">
                    {{ batchAction === 'run' ? '批量运行中' : '批量运行完成' }}
                    · 当前第 {{ batchProgress.current }} / {{ batchProgress.total }} 项
                  </div>
                  <div class="truncate text-xs text-primary">
                    当前任务：{{ batchCurrentTask?.task_key || progressRun?.task_key || '等待启动' }}
                    <span v-if="batchCurrentRunId" class="font-mono"> · {{ batchCurrentRunId }}</span>
                  </div>
                </div>
                <Badge v-if="progressRun" variant="outline" :class="runBadgeClass(progressRun.status)">
                  {{ runStatusLabel(progressRun.status) }}
                </Badge>
              </div>
              <div class="h-2 overflow-hidden rounded-full bg-primary/10">
                <div
                  class="h-full rounded-full bg-primary transition-[width] duration-300"
                  :style="{ width: batchProgressPercent + '%' }"
                />
              </div>
              <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-primary">
                <span>已完成 {{ batchProgress.completed }} / {{ batchProgress.total }}</span>
                <span>成功 {{ batchProgress.success }}</span>
                <span>失败 {{ batchProgress.failed }}</span>
                <span>跳过 {{ batchProgress.skipped }}</span>
                <span>停止 {{ batchProgress.stopped }}</span>
                <span>待执行 {{ batchPendingCount }}</span>
              </div>
            </div>
            <AgentRunTabs
              v-if="batchRunDetailVisible"
              :agent-runs="progressAgentRuns"
              :selected-agent-run-key="progressAgentRunKey"
              :event-count="runEvents.length"
              :events-loading="logsLoading"
              :agent-runs-loading="progressAgentRunsLoading"
              :detail-error="batchRunDetailError || progressDetailError"
              :sticky="false"
              @select-agent-run="selectProgressAgentRun"
              @refresh="refreshProgress"
            />
          </div>
          <div v-if="batchRunDetailVisible" class="space-y-3 rounded-md border bg-card px-3 py-3">
            <div class="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
              <div>
                <div class="text-sm font-semibold text-foreground">当前运行详情</div>
                <div class="text-xs text-muted-foreground">
                  {{ batchCurrentTask?.task_key || progressRun?.task_key || '当前 Run' }}
                </div>
              </div>
              <div class="truncate font-mono text-xs text-muted-foreground">{{ batchCurrentRunId }}</div>
            </div>
            <WorkflowRunDetailPanel
              :events="runEvents"
              :agent-runs="progressAgentRuns"
              :selected-agent-run-key="progressAgentRunKey"
              :events-loading="logsLoading"
              :agent-runs-loading="progressAgentRunsLoading"
              :context-key="'batch-run:' + progressAgentRunKey"
              :detail-error="batchRunDetailError || progressDetailError"
              :subagent-detail="progressSubagentDetail"
              :subagent-detail-loading="progressSubagentDetailLoading"
              :subagent-detail-error="progressSubagentDetailError"
              :show-header="false"
              @select-agent-run="selectProgressAgentRun"
              @refresh="refreshProgress"
              @expand-subagent="ensureProgressSubagentDetail"
            />
          </div>
          <div v-if="tasksLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!tasks.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无任务</div>
          <div v-else-if="!filteredTasks.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">没有符合筛选条件的任务</div>
          <div v-else class="space-y-2">
            <div
              v-for="task in pagedTasks"
              :key="taskId(task)"
              class="rounded-md border"
              :class="batchAction === 'run' && batchCurrentTaskId === taskId(task) ? 'border-primary/30 bg-primary/5' : ''"
            >
              <div class="flex flex-wrap items-start justify-between gap-3 px-3 py-3">
                <div class="flex min-w-0 items-start gap-2">
                  <input
                    type="checkbox"
                    class="mt-1"
                    :checked="selectedTaskIds.has(taskId(task))"
                    :disabled="batchBusy"
                    @change="setTaskSelectedFromEvent(task, $event)"
                  />
                  <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-mono text-sm font-medium text-foreground">{{ task.task_key }}</span>
                    <Badge variant="outline" :class="taskBadgeClass(task.status)">{{ taskStatusLabel(task.status) }}</Badge>
                    <Badge v-if="task.priority_flag" variant="outline" class="bg-accent text-accent-foreground">优先执行</Badge>
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
                  <div v-if="task.last_error" class="mt-2 rounded-md border border-destructive/30 bg-destructive-soft px-2 py-1 text-xs text-destructive-soft-fg">
                    {{ task.last_error }}
                  </div>
                  </div>
                </div>
                <div class="flex items-center gap-1">
                  <Button
                    v-if="canExecuteTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs text-primary"
                    :disabled="batchBusy || isTaskActionLoading(task)"
                    @click="executeTask(task)"
                  >
                    {{ isTaskActionLoading(task) ? '执行中' : task.status === 'completed' ? '全量运行' : task.status === 'stale' ? '增量运行' : '执行' }}
                  </Button>
                  <Button
                    v-if="canPreviewTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs"
                    :disabled="taskPreviewLoading.has(taskId(task))"
                    @click="previewTask(task)"
                  >
                    {{ taskPreviewLoading.has(taskId(task)) ? '分析中' : '预览' }}
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
                    class="h-8 text-xs text-warning"
                    :disabled="batchBusy || isTaskActionLoading(task)"
                    @click="openResetConfirm(task)"
                  >
                    重置
                  </Button>
                </div>
              </div>
              <div v-if="taskPreview(task)" class="border-t bg-background px-3 py-3">
                <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <span class="font-semibold text-foreground">执行预览</span>
                  <Badge variant="outline">{{ taskPreview(task)?.mode }}</Badge>
                  <span class="text-muted-foreground">baseline: {{ taskPreview(task)?.baseline_run_id || '无可复用运行' }}</span>
                </div>
                <div class="mb-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span>复用节点 {{ taskPreview(task)?.reusable_node_ids.length || 0 }}</span>
                  <span>重新执行节点 {{ taskPreview(task)?.affected_node_ids.length || 0 }}</span>
                </div>
                <div class="flex flex-wrap gap-1.5">
                  <Badge v-for="node in taskPreview(task)?.nodes || []" :key="node.node_id" variant="outline">
                    {{ node.node_id }} · {{ node.action === 'reuse' ? '复用' : '执行' }} · {{ node.reason }}
                  </Badge>
                </div>
              </div>
              <!-- 产出物（feature 2） -->
              <div v-if="isTaskArtifactExpanded(task)" class="space-y-2 border-t bg-muted/20 px-3 py-3">
                <div v-if="taskArtifactError" class="rounded-md border border-destructive/30 bg-destructive-soft px-2 py-1 text-xs text-destructive-soft-fg">
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
                      <iframe
                        v-if="activeTaskArtifact(task)?.format === 'html'"
                        :srcdoc="activeTaskArtifact(task)?.content || ''"
                        sandbox="allow-same-origin"
                        class="min-h-[40vh] w-full rounded-lg border bg-card text-xs"
                        :title="activeTaskArtifact(task)?.title || 'HTML 报告'"
                      />
                      <div
                        v-else
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
                  <JsonViewer :value="task.payload" max-height="176px" />
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
                  <div v-else class="max-h-[30rem] overflow-auto pr-1">
                    <RunEventTimeline
                      :events="taskFilteredEvents(task)"
                      :context-key="'task:' + taskRunLogKey(task)"
                      @expand="(taskId: string) => ensureSubagentDetail(task.lease_run_id, taskId)"
                    >
                      <template #subagent-body="{ taskId }">
                        <SubagentDetailPanel
                          :detail="subagentDetail(task.lease_run_id, taskId)"
                          :loading="subagentDetailLoadingFor(task.lease_run_id, taskId)"
                          :error="subagentDetailErrorFor(task.lease_run_id, taskId)"
                        />
                      </template>
                    </RunEventTimeline>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <PaginationBar
            v-if="filteredTasks.length"
            v-model:page="taskPage"
            v-model:page-size="taskPageSize"
            :total="filteredTasks.length"
            :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
          />
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
        <div class="flex flex-wrap gap-2">
          <Button
            v-if="progressFinished"
            variant="outline"
            size="sm"
            :disabled="progressArtifactsLoading"
            @click="openProgressArtifact"
          >
            {{ progressArtifactsLoading ? '加载产物中' : '查看产物' }}
          </Button>
          <Button
            v-if="progressFinished && progressWorkflow?.workflow_type === 'summary'"
            variant="outline"
            size="sm"
            :disabled="progressArtifactsLoading"
            @click="openProgressHtmlReport"
          >
            {{ progressArtifactsLoading ? '加载中' : '查看 HTML 报告' }}
          </Button>
        </div>
      </div>
      <div class="space-y-4">
          <WorkflowRunGraph
            v-if="progressRunDetail"
            :definition-snapshot="progressRunDetail.definition_snapshot"
            :node-runs="progressRunDetail.node_runs || []"
            @open-agent-run="openAgentRun"
            @open-script-run="openScriptRun"
          />
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
          </div>

          <div class="workflow-progress-agent-context sticky top-0 z-30 bg-background/95 pb-2 pt-1 backdrop-blur">
            <AgentRunTabs
              :agent-runs="progressAgentRuns"
              :selected-agent-run-key="progressAgentRunKey"
              :event-count="runEvents.length"
              :events-loading="logsLoading"
              :agent-runs-loading="runsLoading || progressAgentRunsLoading"
              :detail-error="progressDetailError"
              :sticky="false"
              :workflow-run-id="selectedRunId"
              :workflow-run-status="progressRun?.status || ''"
              @select-agent-run="selectProgressAgentRun"
              @refresh="refreshProgress"
            />
          </div>
          <div v-if="logsLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!selectedRunId" class="py-8 text-center text-sm text-muted-foreground">暂无运行记录</div>
          <div v-else>
            <WorkflowRunDetailPanel
              :events="runEvents"
              :agent-runs="progressAgentRuns"
              :selected-agent-run-key="progressAgentRunKey"
              :events-loading="logsLoading"
              :agent-runs-loading="runsLoading || progressAgentRunsLoading"
              :context-key="'run:' + progressAgentRunKey"
              :detail-error="progressDetailError"
              :subagent-detail="progressSubagentDetail"
              :subagent-detail-loading="progressSubagentDetailLoading"
              :subagent-detail-error="progressSubagentDetailError"
              :show-header="false"
              @select-agent-run="selectProgressAgentRun"
              @refresh="refreshProgress"
              @expand-subagent="ensureProgressSubagentDetail"
            />
          </div>
      </div>
    </section>

    <section v-if="isWorkflowFormPage && !routeError" class="space-y-5">
      <!-- Header：对齐 detail 页 text-xl font-semibold tracking-tight -->
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <Button variant="ghost" size="sm" class="h-8 px-2" @click="backFromForm">
            <ArrowLeft class="mr-1 h-4 w-4" />
            返回
          </Button>
          <div>
            <h2 class="text-xl font-semibold tracking-tight text-foreground">{{ form.workflow_key ? '编辑工作流' : '新建工作流' }}</h2>
            <p class="mt-0.5 text-xs text-muted-foreground">
              <span class="font-mono">{{ form.workflow_key || 'workflow/new' }}</span>
              <span v-if="form.profile_key"> · {{ profileName(form.profile_key) }}</span>
            </p>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" :disabled="designing" @click="openWorkflowDesigner('modify')">
            <WandSparkles class="mr-1.5 h-4 w-4" />
            AI 设计
          </Button>
          <Button variant="outline" size="sm" :disabled="editedWorkflowRunBusy" @click="runEditedWorkflow">
            <Play class="mr-1.5 h-4 w-4" />
            测试运行
          </Button>
          <Button :disabled="saving" size="sm" @click="saveWorkflow">
            <Save class="mr-1.5 h-4 w-4" />
            {{ saving ? '保存中' : '保存' }}
          </Button>
        </div>
      </div>

      <!-- 错误条 -->
      <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
        {{ formError }}
      </div>

      <!-- 主体：左大画布 + 右栏(420px)。整个 grid 作为 relative 容器锚定 fullscreen drawer -->
      <div class="relative grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <!-- ============ 左：画布卡（palette 上移到顶部，画布横向占满） ============ -->
        <Card class="shadow-card">
          <CardContent class="space-y-3 p-4">
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="flex items-center gap-2">
                  <h3 class="text-sm font-semibold text-foreground">工作流图</h3>
                  <Badge variant="outline">{{ form.definition.nodes.length }} 节点</Badge>
                  <Badge variant="outline">{{ form.definition.edges.length }} 连线</Badge>
                </div>
                <p class="mt-0.5 text-xs text-muted-foreground">拖拽上方节点到画布，点击节点/连线在右侧编辑配置</p>
              </div>
              <div class="flex items-center gap-2">
                <StatusBadge :status="form.status === 'active' ? 'enabled' : 'disabled'" />
                <Badge v-if="form.workflow_type === 'summary'" variant="outline">总结类</Badge>
              </div>
            </div>

            <div class="workflow-editor-region flex flex-col overflow-hidden rounded-md border bg-muted/10" style="height: clamp(480px, calc(100vh - 240px), 820px);">
              <!-- 调色板：横排置顶，自然高度 -->
              <WorkflowNodePalette orientation="horizontal" @add-node="addNode" />
              <!-- 画布：flex-1 撑满剩余高度，min-h-0 让 VueFlow 正确测量 -->
              <div class="min-h-0 flex-1">
                <WorkflowEditorCanvas
                  v-model:graph="form.definition"
                  :workflow-type="form.workflow_type"
                  :errors="graphErrors"
                  class="h-full border-t !min-h-0"
                  @select-node="selectWorkflowNode"
                  @select-edge="selectWorkflowEdge"
                  @add-node="addNode"
                  @deselect="setConfigDrawerOpen(false)"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <!-- ============ 右栏：选中节点/边时显示配置，否则显示工作流信息（互斥） ============ -->
        <div class="space-y-4">
          <!-- 配置态：选中节点/边且 overlay 模式时，整栏只显示配置（元数据隐藏） -->
          <Card v-if="(selectedNode || selectedEdge) && configDrawerOpen && configDrawerMode === 'overlay'" class="shadow-card">
            <CardContent class="space-y-3 p-4">
              <div class="flex items-center justify-between gap-2 border-b pb-2">
                <div class="min-w-0">
                  <!-- 节点类型 tag：颜色与画布一致；边则显示「连线」中性 tag -->
                  <span
                    v-if="selectedNode"
                    class="mb-1 inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[11px] font-medium"
                    :class="workflowNodeToneClass(selectedNode.type, 'badge')"
                  >
                    <span class="h-1.5 w-1.5 rounded-full" :class="workflowNodeToneClass(selectedNode.type, 'rail')" />
                    {{ workflowNodeTypeText(selectedNode.type) }}
                  </span>
                  <span v-else class="mb-1 inline-flex items-center gap-1.5 rounded-sm bg-secondary px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                    <span class="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
                    连线
                  </span>
                  <div class="truncate text-sm font-semibold text-foreground">{{ configDrawerTitle }}</div>
                  <div class="mt-0.5 text-xs text-muted-foreground">点击画布空白处或 ✕ 返回工作流信息</div>
                </div>
                <div class="flex shrink-0 items-center gap-1">
                  <Button variant="ghost" size="sm" class="h-7 px-2" title="全屏编辑" @click="setConfigDrawerMode('fullscreen')">
                    <Maximize2 class="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="sm" class="h-7 px-2" title="关闭配置" @click="setConfigDrawerOpen(false)">
                    <X class="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              <WorkflowNodeConfigPanel
                v-if="selectedNode"
                :node="selectedNode"
                :scripts="scripts"
                :skills="skills"
                :backends="backendKeys"
                :reference-items="selectedNodeReferenceItems"
                :issues="selectedNodeIssues"
                @replace="replaceNode"
                @schema-validity="setNodeSchemaValidity"
              />
              <WorkflowEdgeConfigPanel
                v-else-if="selectedEdge"
                :edge="selectedEdge"
                :locked="isProtectedSummaryEdge(selectedEdge, form.workflow_type)"
                :reference-items="selectedEdgeReferenceItems"
                :issues="selectedEdgeIssues"
                @replace="replaceEdge"
              />
            </CardContent>
          </Card>

          <!-- 工作流信息态：未选中节点/边时显示（基础信息 + 运行配置合并为一张卡） -->
          <template v-else>
            <Card class="shadow-card">
              <CardContent class="space-y-3 p-4">
                <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/70">工作流信息</div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">workflow_id</label>
                  <Input v-model="form.workflow_key" class="h-9 font-mono text-xs" :disabled="Boolean(selectedWorkflow && form.workflow_key === selectedWorkflow.workflow_key)" />
                </div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">名称</label>
                  <Input v-model="form.name" class="h-9" />
                </div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">关联 profile</label>
                  <Select v-model="form.profile_key">
                    <SelectTrigger class="h-9">
                      <SelectValue placeholder="选择 profile" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="profile in profiles" :key="profile.profile_key" :value="profile.profile_key">
                        {{ profile.name }} / {{ profile.profile_key }}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">描述</label>
                  <Input v-model="form.description" class="h-9" />
                </div>
                <div class="border-t pt-3">
                  <div class="flex items-center justify-between gap-3">
                    <label class="text-xs text-muted-foreground">状态</label>
                    <div class="flex items-center gap-2">
                      <StatusBadge :status="form.status === 'active' ? 'enabled' : 'disabled'" />
                      <Select v-model="form.status">
                        <SelectTrigger class="h-8 w-24 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="active">启用</SelectItem>
                          <SelectItem value="disabled">停用</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div class="mt-3">
                    <label class="mb-1 flex items-center gap-1 text-xs text-muted-foreground">
                      类型
                      <span class="group relative inline-flex cursor-help items-center text-muted-foreground/70" tabindex="0">
                        <HelpCircle class="h-3.5 w-3.5" />
                        <span
                          class="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-64 -translate-x-1/2 translate-y-1 rounded-md border bg-popover px-3 py-2 text-xs leading-relaxed text-popover-foreground opacity-0 shadow-md transition-all duration-150 group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100"
                        >
                          总结型工作流会固定 Markdown 与 HTML 输出节点；HTML 失败仅记为 warning，Markdown 主产物仍可保留。
                        </span>
                      </span>
                    </label>
                    <Select :model-value="form.workflow_type" @update:model-value="(v) => changeWorkflowType(v as WorkflowType)">
                      <SelectTrigger class="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="operation">操作</SelectItem>
                        <SelectItem value="summary">总结</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </CardContent>
            </Card>

            <!-- 测试输入：折叠块，仅 !hasTaskNode 显示 -->
            <details v-if="!hasTaskNode" class="shadow-card rounded-lg bg-card ring-1 ring-foreground/10">
              <summary class="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-sm font-semibold text-foreground [&::-webkit-details-marker]:hidden">
                <span>测试输入</span>
                <span class="text-xs font-normal text-muted-foreground">{{ manualInputFields.length }} 个字段 · 点击展开</span>
              </summary>
              <div class="space-y-3 border-t px-4 py-3">
                <div>
                  <div class="mb-2 text-xs font-medium text-muted-foreground">逐字段输入</div>
                  <div class="space-y-2">
                    <div v-for="field in manualInputFields" :key="field.path">
                      <label class="mb-1 block text-xs text-muted-foreground">{{ field.path }}<span v-if="field.required" class="text-destructive"> *</span></label>
                      <Input v-model="manualInputValues[field.path]" :placeholder="field.description || field.type" class="h-9" />
                    </div>
                    <p v-if="!manualInputFields.length" class="text-xs text-muted-foreground">当前脚本参数没有可推导输入字段。</p>
                  </div>
                </div>
                <div>
                  <label class="mb-1 block text-xs font-medium text-muted-foreground">高级 JSON</label>
                  <textarea v-model="advancedInput" class="min-h-32 w-full rounded-md border bg-background p-2 font-mono text-xs" />
                </div>
              </div>
            </details>
          </template>
        </div>

        <!-- fullscreen drawer：锚定到此 relative grid，inset:0 覆盖画布+右栏 -->
        <WorkflowConfigDrawer
          v-if="configDrawerOpen && (selectedNode || selectedEdge) && configDrawerMode === 'fullscreen'"
          :open="true"
          :mode="'fullscreen'"
          :title="configDrawerTitle"
          @update:open="setConfigDrawerOpen"
          @update:mode="setConfigDrawerMode"
        >
          <WorkflowNodeConfigPanel
            v-if="selectedNode"
            :node="selectedNode"
            :scripts="scripts"
            :skills="skills"
            :backends="backendKeys"
            :reference-items="selectedNodeReferenceItems"
            :issues="selectedNodeIssues"
            @replace="replaceNode"
            @schema-validity="setNodeSchemaValidity"
          />
          <WorkflowEdgeConfigPanel
            v-else-if="selectedEdge"
            :edge="selectedEdge"
            :locked="isProtectedSummaryEdge(selectedEdge, form.workflow_type)"
            :reference-items="selectedEdgeReferenceItems"
            :issues="selectedEdgeIssues"
            @replace="replaceEdge"
          />
        </WorkflowConfigDrawer>
      </div>

      <!-- AI 设计抽屉：fixed 全屏，仅 tab → SegmentedTabs + bg-muted/10 对齐 -->
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
          <SegmentedTabs
            :model-value="designMode"
            :tabs="[{ key: 'modify', label: '修改' }, { key: 'create', label: '新建' }]"
            @update:model-value="(v) => designMode = v as 'create' | 'modify'"
          />

          <div>
            <label class="mb-1 block text-xs text-muted-foreground">提示词</label>
            <textarea
              v-model="designPrompt"
              class="min-h-32 w-full rounded-md border bg-background p-3 text-sm"
              placeholder="描述希望 agent 设计或修改的工作流目标"
            />
          </div>
          <Button class="w-full" :disabled="designStopRequested" @click="designing ? stopWorkflowDesigner() : runWorkflowDesigner()">
            <WandSparkles class="mr-1.5 h-4 w-4" />
            {{ designing ? (designStopRequested ? '停止中' : '立即停止') : '生成方案' }}
          </Button>

          <div v-if="designError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
            {{ designError }}
          </div>

          <section v-if="designResponse?.result" class="space-y-3 rounded-md border bg-muted/10 p-3">
            <div class="flex items-center justify-between gap-2">
              <div class="text-sm font-semibold">生成结果</div>
              <Badge v-if="designResponse.run_key" variant="outline">{{ designResponse.run_key }}</Badge>
            </div>
            <p class="text-sm text-muted-foreground">{{ designResponse.result.summary }}</p>
            <div v-if="designResponse.result.notes?.length" class="space-y-1 text-xs text-muted-foreground">
              <div v-for="note in designResponse.result.notes" :key="note">· {{ note }}</div>
            </div>
            <div v-if="workflowDesignDraft" class="grid gap-2 text-xs">
              <div class="rounded-md border bg-muted/10 p-2">
                <div class="font-mono font-medium text-foreground">{{ workflowDesignDraft.workflow_key }}</div>
                <div class="mt-1 text-muted-foreground">{{ workflowDesignDraft.name }}</div>
              </div>
              <pre class="max-h-96 overflow-auto rounded-md border bg-muted/10 p-3 text-xs">{{ JSON.stringify(workflowDesignDraft.definition, null, 2) }}</pre>
            </div>
          </section>
        </div>
        <div class="flex items-center justify-end gap-2 border-t p-4">
          <Button variant="outline" :disabled="designing" @click="showDesigner = false">取消</Button>
          <Button :disabled="designing || !workflowDesignDraft || saving" @click="acceptWorkflowDesign">
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
            <iframe
              v-if="artifactDetail.format === 'html'"
              :srcdoc="artifactDetail.content"
              sandbox="allow-same-origin"
              class="min-h-[60vh] w-full rounded-lg border bg-card"
              :title="artifactDetail.title || 'HTML 报告'"
            />
            <div v-else class="prose prose-sm max-w-none rounded-md border bg-background p-4" v-html="artifactHtml"></div>
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
          <div v-if="fullscreenArtifact.format === 'html'" class="mx-auto h-full max-w-5xl">
            <iframe
              :srcdoc="fullscreenArtifact.content"
              sandbox="allow-same-origin"
              class="h-full min-h-[70vh] w-full rounded-lg border bg-card"
              :title="fullscreenArtifact.title || 'HTML 报告'"
            />
          </div>
          <div v-else class="mx-auto max-w-4xl space-y-4">
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
                  <iframe
                    v-if="item.format === 'html'"
                    :srcdoc="item.content"
                    sandbox="allow-same-origin"
                    class="min-h-[50vh] w-full rounded-lg border bg-card"
                    :title="item.title || 'HTML 报告'"
                  />
                  <div v-else class="prose prose-sm max-w-none rounded-md border bg-background p-4" v-html="renderMarkdown(item.content)"></div>
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
