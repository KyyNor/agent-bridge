<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Download, FolderOutput, GitBranch, HelpCircle, ListTodo, Maximize2, MoreHorizontal, Play, Plus, Save, Upload, WandSparkles, X } from '@lucide/vue'
import { api, beginWorkflowValidationRun, finishWorkflowValidationRun, hasBlockingWorkflowValidationErrors, invalidateWorkflowValidationRun, isCurrentWorkflowValidationRun, workflowValidationErrorMessage, workflowValidationIssuesFor } from '../../api/client'
import type { AccessActorContext, ProjectProfile, WorkflowArtifact, WorkflowDefinition, WorkflowDraft, WorkflowRun, WorkflowRunEvent, WorkflowRunLog, WorkflowRunSummary, WorkflowSubagentDetail, WorkflowTask, WorkflowTaskImportPreview, WorkflowImportPreview, WorkflowImportTargetMode, AgentRun, AgentRuntimeConfig, ManagedScript, SkillPrompt, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowNodeRun, WorkflowNodeType, WorkflowValidationError, WorkflowType, WorkflowExecutionMode, WorkflowExecutionPlan } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import EditorActionBar from '../../components/EditorActionBar.vue'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { confirm, alert } from '../../composables/useConfirm'
import { useToast } from '../../composables/useToast'
import { useSubagentDetails } from '../../composables/useSubagentDetails'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import WorkflowTaskImportDialog from './WorkflowTaskImportDialog.vue'
import WorkflowImportDialog from '../../components/workflow/WorkflowImportDialog.vue'
import WorkflowTaskExecutionPreview from '../../components/workflow/WorkflowTaskExecutionPreview.vue'
import WorkflowArtifactBrowser from '../../components/workflow/WorkflowArtifactBrowser.vue'
import WorkflowArtifactDialogs from '../../components/workflow/WorkflowArtifactDialogs.vue'
import WorkflowDesignerDrawer from '../../components/workflow/WorkflowDesignerDrawer.vue'
import WorkflowTaskToolbar from '../../components/workflow/WorkflowTaskToolbar.vue'
import WorkflowRunHistory from '../../components/workflow/WorkflowRunHistory.vue'
import WorkflowDetailTourPreview from './WorkflowDetailTourPreview.vue'
import WorkflowEditorCanvas from './WorkflowEditorCanvas.vue'
import WorkflowNodePalette from './WorkflowNodePalette.vue'
import WorkflowConfigDrawer from './WorkflowConfigDrawer.vue'
import WorkflowNodeConfigPanel from './WorkflowNodeConfigPanel.vue'
import WorkflowEdgeConfigPanel from './WorkflowEdgeConfigPanel.vue'
import WorkflowRunGraph from './WorkflowRunGraph.vue'
import SubagentDetailPanel from '../../components/SubagentDetailPanel.vue'
import RunEventTimeline from '../../components/RunEventTimeline.vue'
import AgentRunTabs from '../../components/AgentRunTabs.vue'
import AgentRunExecutionPanel from '../../components/AgentRunExecutionPanel.vue'
import WorkflowRunDetailPanel from '../../components/WorkflowRunDetailPanel.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import StatCard from '../../components/StatCard.vue'
import RevisionHistoryPanel from '../../components/version/RevisionHistoryPanel.vue'
import { createDefaultGraph, deriveManualInputFields, deriveWorkflowBackendKeys, isProtectedSummaryEdge, migrateWorkflowGraph } from '../../lib/workflowDefinition'
import { workflowNodeToneClass, workflowNodeTypeText } from '../../lib/workflowNodeVisuals'
import { deriveAvailableData } from '../../lib/workflowReferences'
import {
  ALL_ARTIFACTS_SENTINEL,
  ALL_STATUS_SENTINEL,
  ALL_TYPE_SENTINEL,
  taskStatusLabel as labelTaskStatus,
  taskId,
  canRunTask,
} from '../../lib/workflowTasks'
import { renderMarkdown } from '../../lib/markdown'
import { useWorkflowRoute } from '../../composables/useWorkflowRoute'
import { useWorkflowEditorState } from '../../composables/useWorkflowEditorState'
import { useWorkflowArtifacts } from '../../composables/useWorkflowArtifacts'
import { useWorkflowDesigner } from '../../composables/useWorkflowDesigner'
import { useWorkflowTasks } from '../../composables/useWorkflowTasks'
import { useWorkflowRunProgress } from '../../composables/useWorkflowRunProgress'
import { formatLocalDatetime, formatDuration } from '../../lib/time'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'
import { PRODUCT_TOUR_ACTION_EVENT, workflowDetailFirstUseTour, workflowEditorFirstUseTour, workflowFirstUseTour } from '../../lib/onboardingTours'
import { useOnboardingTour } from '../../composables/useOnboardingTour'
import { isSharedResourceReadOnly } from '../../lib/resourceAccess'

const WORKFLOW_RUN_CACHE_LIMIT = 50
const ARTIFACT_PAGE_SIZE_OPTIONS = [10, 20, 50] as const
const props = defineProps<{ routeKey: string }>()
const router = useRouter()
const { toast } = useToast()

const workflows = ref<WorkflowDefinition[]>([])
const actorContext = ref<AccessActorContext | null>(null)
const profiles = ref<ProjectProfile[]>([])
const scripts = ref<ManagedScript[]>([])
const skills = ref<SkillPrompt[]>([])
const defaultBackend = ref('codex')
const agentRuntimeConfig = ref<AgentRuntimeConfig>({ default_backend: 'claude', backends: [] })
const selectedKey = ref('')
const loading = ref(true)
const workflowPage = ref(1)
const workflowPageSize = ref(10)
const error = ref('')
const workflowDetailError = ref('')
const showClearConfirm = ref(false)
const detailTab = ref<'overview' | 'tasks' | 'artifacts' | 'runs' | 'versions'>('overview')
const workflowDetailTourPreview = ref(false)
let detailTabBeforeTour = detailTab.value
const workflowEditorTourAgentPreview = ref(false)
const workflowEditorTourAgentNode = ref<WorkflowNode>({
  id: 'tour-agent-preview',
  type: 'agent',
  name: '分析资料并生成结论',
  position: { x: 0, y: 0 },
  config: {
    prompt: '结合任务输入与上游资料，提取关键事实、风险和下一步建议。',
    backend_key: 'codex',
    mcp_enabled: true,
    skill_names: [],
    timeout_seconds: 600,
    result_mode: 'json',
    output_schema: {
      type: 'object',
      properties: {
        summary: { type: 'string', description: '结论摘要' },
        risks: { type: 'array', items: { type: 'string' }, description: '风险列表' },
      },
      required: ['summary'],
    },
  },
})
const taskWorkflowKey = ref('')
const clearing = ref(false)
const clearTarget = ref<WorkflowDefinition | null>(null)
// `runIdToAgentRunKey` and `progressSubagentDetailState` are owned by the run-progress
// composable (resolved below). The task subagent detail state closures read those
// lazily at call time, so referencing them before the composable is set up is safe.
const taskSubagentDetailState = useSubagentDetails(async (runId, taskIdStr) => {
  let runKey = runIdToAgentRunKey.value[runId]
  if (!runKey) {
    const agentRun = await api.getAgentRunForWorkflowRun(runId)
    if (!agentRun) throw new Error('未找到该运行对应的 Agent 记录')
    runKey = agentRun.run_key
    runIdToAgentRunKey.value = { ...runIdToAgentRunKey.value, [runId]: runKey }
  }
  return api.getAgentRunSubagentDetail(runKey, taskIdStr)
})
const showWorkflowImport = ref(false)
const workflowImportPreview = ref<WorkflowImportPreview | null>(null)
const workflowImportLoading = ref(false)
const workflowImportConfirming = ref(false)
const workflowImportError = ref('')
const workflowImportTargetKey = ref('')
const workflowImportTargetMode = ref<WorkflowImportTargetMode>('auto')
const workflowImportFile = ref<File | null>(null)
let workflowImportRequestToken = 0
const routeError = ref('')
const manualInputValues = ref<Record<string, string>>({})
const advancedInput = ref('{}')
const { maybeStartTour } = useOnboardingTour()

const {
  form,
  saving,
  formError,
  formDirty,
  graphErrors,
  schemaEditorErrors,
  runValidationGuard,
  selectedNodeId,
  selectedEdgeId,
  configDrawerOpen,
  configDrawerMode,
  selectedNode,
  selectedEdge,
  resetForm,
  prepareCreateForm,
  prepareEditForm,
  scopedGraphIssues,
  parseWorkflowIssues,
  workflowDraft,
  validateWorkflowDraft,
  saveWorkflow,
  changeWorkflowType,
  addNode,
  selectWorkflowNode,
  selectWorkflowEdge,
  setConfigDrawerOpen,
  setConfigDrawerMode,
  taskRefreshPolicy,
  setNodeSchemaValidity,
  replaceNode,
  replaceEdge,
} = useWorkflowEditorState({
  defaultBackend,
  profiles,
  toast,
  onSaved: async (saved) => {
    selectedKey.value = saved.workflow_key
    workflows.value = await api.listWorkflows()
    await loadRunOverviews()
    void router.replace(`/workflow/${saved.workflow_key}/detail`)
  },
})
const editedWorkflowRunBusy = computed(() => runValidationGuard.value.validating || testing.value)

const {
  showDesigner,
  designMode,
  designPrompt,
  designing,
  designError,
  designResponse,
  designStopRequested,
  workflowDesignDraft,
  openWorkflowDesigner,
  runWorkflowDesigner,
  stopWorkflowDesigner,
} = useWorkflowDesigner({
  current: mode => mode === 'modify'
    ? { ...workflowDraft() }
    : {
        profile_key: form.value.profile_key,
        status: 'active',
        workflow_type: form.value.workflow_type,
        definition: createDefaultGraph(form.value.workflow_type, defaultBackend.value),
      },
  profileKey: () => form.value.profile_key || undefined,
})

const selectedWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === selectedKey.value) || workflows.value[0] || null
)

const {
  artifacts,
  artifactLoading,
  artifactTotal,
  artifactPage,
  artifactPageSize,
  artifactError,
  artifactQuery,
  artifactPathMatch,
  artifactFormat,
  artifactDetail,
  artifactHistory,
  artifactHistoryTarget,
  detailLoading,
  visibilitySaving,
  historyLoading,
  showArtifact,
  showArtifactHistory,
  fullscreenArtifact,
  collapsedPaths,
  artifactHtml,
  fullscreenArtifactHtml,
  recentArtifacts,
  humanReadableArtifactCount,
  artifactRows,
  openArtifactFullscreen,
  closeArtifactFullscreen,
  togglePath,
  loadRecentArtifacts,
  searchArtifacts,
  resetArtifactPage,
  setArtifactFormat,
  openArtifact,
  openArtifactHistory,
  setArtifactVisibility,
  clearArtifacts,
} = useWorkflowArtifacts(() => ({
  profileKey: selectedWorkflow.value?.profile_key || form.value.profile_key || undefined,
  workflowKey: selectedWorkflow.value?.workflow_key,
}))
const artifactDetailReadOnly = computed(() => isSharedResourceReadOnly(actorContext.value, artifactDetail.value))

function openTaskArtifactFullscreen(task: WorkflowTask) {
  const artifact = activeTaskArtifact(task)
  if (artifact) openArtifactFullscreen(artifact)
}

const {
  routeParts,
  workflowKey: routeWorkflowKey,
  mode: routeMode,
  isFormPage: isWorkflowFormPage,
} = useWorkflowRoute(() => props.routeKey, formDirty)
const pageWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === routeWorkflowKey.value) || null
)

const {
  progressWorkflowKey,
  progressRunId,
  workflowRuns,
  workflowRunTotals,
  workflowTaskStatus,
  runsLoading,
  selectedRunId,
  runEvents,
  runLogs,
  logsLoading,
  progressRunArtifacts,
  progressArtifactsLoading,
  progressAgentRuns,
  progressAgentRunKey,
  progressAgentRunsLoading,
  progressAgentRunDetail,
  progressAgentRunDetailLoading,
  progressAgentRunDetailError,
  progressDetailError,
  progressRunDetail,
  runIdToAgentRunKey,
  testing,
  testingRunId,
  testError,
  runPage,
  runPageSize,
  progressWorkflow,
  progressRun,
  progressArtifacts,
  progressFinished,
  applyRunOverviews,
  loadRunOverviews,
  mergeWorkflowRun,
  loadRuns,
  runningRunFor,
  loadLogs,
  selectRun,
  loadProgressAgentRuns,
  loadProgressAgentEvents,
  selectProgressAgentRun,
  setProgressAgentRunKey,
  progressSubagentDetail,
  progressSubagentDetailLoading,
  progressSubagentDetailError,
  ensureProgressSubagentDetail,
  loadProgressArtifacts,
  openProgressArtifactDetail,
  openProgressArtifact,
  openProgressHtmlReport,
  stopProgressAgentEventStream,
  stopTestPolling,
  pollTestRun,
  startTestPolling,
  runWorkflow,
  openProgress,
  prepareProgress,
  refreshProgress,
  openScriptRun,
  openAgentRun,
  applyProgressRoute,
} = useWorkflowRunProgress({
  selectedWorkflow,
  workflows,
  formProfileKey: () => form.value.profile_key,
  routeParts,
  openArtifactFullscreen,
  setSelectedKey: (value: string) => { selectedKey.value = value },
  searchArtifacts,
  setArtifactError: (value: string) => { artifactError.value = value },
  setPageError: (value: string) => { error.value = value },
})

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
// workflow 整体状态：优先取 task 代表版本聚合状态，旧后端缺失时回退到最近 run 状态。
const workflowStatus = computed(() => {
  const key = selectedWorkflow.value?.workflow_key || ''
  return workflowTaskStatus.value[key]?.status || latestRun.value?.status || ''
})
const latestRunTone = computed<'neutral' | 'ok' | 'err' | 'info'>(() => {
  const status = workflowStatus.value || latestRun.value?.status
  if (status === 'completed') return 'ok'
  if (status === 'failed' || status === 'stopped') return 'err'
  if (status === 'running') return 'info'
  return 'neutral'
})
const latestRunValue = computed(() => {
  const status = workflowStatus.value
  if (!status) return '暂无运行'
  const duration = latestRun.value ? formatDuration(latestRun.value.duration_ms) : ''
  const label = aggregatedStatusLabel(status)
  return duration ? `${label} · ${duration}` : label
})
const hasAnyRunningRun = computed(() =>
  Object.values(workflowRuns.value).some(items => items.some(run => run.status === 'running')),
)
const taskWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === taskWorkflowKey.value) || selectedWorkflow.value
)
const workflowNodeCount = computed(() => selectedWorkflow.value?.definition?.nodes.length || 0)

const {
  workflowTasks,
  tasksLoading,
  taskError,
  expandedTaskIds,
  taskRunLogs,
  taskRunEvents,
  taskRunPayloads,
  taskRunPayloadErrors,
  taskLogLoading,
  taskPage,
  taskPageSize,
  taskStatusFilter,
  taskTypeFilter,
  taskArtifactsFilter,
  taskSearchInput,
  taskSearch,
  taskSort,
  expandedArtifactIds,
  taskArtifacts,
  taskArtifactLoading,
  taskArtifactError,
  taskActionLoading,
  taskActionError,
  refreshingTasks,
  taskPreviews,
  taskPreviewLoading,
  resetTarget,
  resetting,
  selectedTaskIds,
  batchAction,
  batchProgress,
  batchCurrentTask,
  batchCurrentTaskId,
  batchCurrentRunId,
  batchRunDetailError,
  batchSummary,
  showTaskImport,
  taskImportPreview,
  taskImportLoading,
  taskImportConfirming,
  taskImportError,
  batchStopRequested,
  taskActorFilter,
  taskArtifactActive,
  tasks,
  taskStats,
  taskStatuses,
  taskTypes,
  filteredTasks,
  pagedTasks,
  selectedTasks,
  refreshableSelectedTasks,
  allVisibleTasksSelected,
  someVisibleTasksSelected,
  batchBusy,
  batchProgressPercent,
  batchPendingCount,
  batchRunDetailVisible,
  loadTasks,
  resetTaskFilters,
  onTaskSearchInput,
  openTaskImport,
  closeTaskImport,
  previewTaskImport,
  downloadTaskImportTemplate,
  confirmTaskImport,
  taskArtifactsOf,
  isTaskArtifactLoading,
  isTaskArtifactExpanded,
  taskArtifactActiveId,
  selectTaskArtifact,
  activeTaskArtifact,
  toggleTaskArtifacts,
  taskExecutionMode,
  canPreviewTask,
  taskPreview,
  previewTask,
  canResetTask,
  taskActionKey,
  isTaskActionLoading,
  executeTask,
  canRefreshTask,
  refreshTask,
  refreshSelectedTasks,
  openResetConfirm,
  closeResetConfirm,
  confirmResetTask,
  setTaskSelectedFromEvent,
  setVisibleTasksSelectedFromEvent,
  resetBatchRunDetail,
  createBatchProgress,
  runSelectedTasks,
  resetSelectedTasks,
  stopBatchRun,
  taskRunLogKey,
  taskLogs,
  taskEvents,
  taskAgentRun,
  loadTaskPayload,
  taskActors,
  taskActorFilterFor,
  setTaskActorFilter,
  taskFilteredEvents,
  isTaskLogLoading,
  toggleTaskLogs,
  prepareTasks: prepareTaskState,
  cancelBatchQueue,
  resetExecutionData,
} = useWorkflowTasks({
  taskWorkflow,
  routeWorkflowKey,
  routeMode,
  detailTab,
  openTaskArtifactFullscreen,
  batchRunDetail: {
    runIdToAgentRunKey,
    loadRunOverviews,
    loadProgressAgentRuns,
    loadProgressAgentEvents,
    setProgressWorkflowKey: (value: string) => { progressWorkflowKey.value = value },
    setProgressRunId: (value: string) => { progressRunId.value = value },
    setSelectedRunId: (value: string) => { selectedRunId.value = value },
    setProgressAgentRunKey,
    setProgressDetailError: (value: string) => { progressDetailError.value = value },
  },
  navigateToTaskProgress: (workflowKey, runId) => {
    void router.push(`/workflow/${workflowKey}/progress/${runId}`)
  },
})

// “需要处理”与队列可执行状态保持一致：stale 表示定义已变更、等待增量执行，
// 不能因其不是 pending 而从详情页的待处理数字中漏掉。
const pendingTaskCount = computed(() =>
  (taskStats.value.pending || 0)
  + (taskStats.value.stale || 0)
  + (taskStats.value.failed || 0)
  + (taskStats.value.abandoned || 0),
)
const detailTabs = computed(() => [
  { key: 'overview', label: '概览' },
  { key: 'tasks', label: '任务队列', count: pendingTaskCount.value || undefined },
  { key: 'artifacts', label: '工作流产物', count: artifactTotal.value || undefined },
  { key: 'runs', label: '运行记录', count: workflowRunTotals.value[selectedWorkflow.value?.workflow_key || ''] || undefined },
  { key: 'versions', label: '版本历史' },
])
const pagedWorkflows = computed(() => paginate(workflows.value, workflowPage.value, workflowPageSize.value))

function handleProductTourAction(event: Event) {
  const action = (event as CustomEvent<{ action?: string }>).detail?.action || ''
  if (action === 'workflow-editor:agent-preview:start') {
    workflowEditorTourAgentPreview.value = true
    workflowEditorTourAgentNode.value = {
      ...workflowEditorTourAgentNode.value,
      config: { ...workflowEditorTourAgentNode.value.config, backend_key: defaultBackend.value },
    } as WorkflowNode
    return
  }
  if (action === 'workflow-editor:agent-preview:stop') {
    workflowEditorTourAgentPreview.value = false
    return
  }
  if (action === 'workflow-detail:preview:stop') {
    workflowDetailTourPreview.value = false
    detailTab.value = detailTabBeforeTour
    return
  }
  const previewTab = action.match(/^workflow-detail:preview:(overview|tasks|artifacts|runs|versions)$/)?.[1]
  if (!previewTab) return
  if (!workflowDetailTourPreview.value) detailTabBeforeTour = detailTab.value
  workflowDetailTourPreview.value = true
  detailTab.value = previewTab as typeof detailTab.value
}

function replaceTourAgentNode(node: WorkflowNode) {
  workflowEditorTourAgentNode.value = node
}

onMounted(async () => {
  window.addEventListener(PRODUCT_TOUR_ACTION_EVENT, handleProductTourAction)
  const [, actor] = await Promise.all([loadAll(), api.getAccessContext()])
  actorContext.value = actor
  await applyRoute()
  await maybeStartWorkflowTour()
  await maybeStartWorkflowEditorTour()
  await maybeStartWorkflowDetailTour()
})

watch(selectedKey, () => {
  testError.value = ''
})

watch(
  () => props.routeKey,
  async () => {
    closeTaskImport()
    closeWorkflowImport()
    cancelBatchQueue()
    await applyRoute()
    await maybeStartWorkflowTour()
    await maybeStartWorkflowEditorTour()
    await maybeStartWorkflowDetailTour()
  },
)

onUnmounted(() => {
  window.removeEventListener(PRODUCT_TOUR_ACTION_EVENT, handleProductTourAction)
  stopTestPolling()
  stopProgressAgentEventStream()
  cancelBatchQueue()
  closeWorkflowImport()
})

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [workflowList, profileList, runOverviews] = await Promise.all([
      api.listWorkflows(),
      api.listProfiles(),
      api.listWorkflowRunOverviews(),
    ])
    workflows.value = workflowList
    profiles.value = profileList
    applyRunOverviews(runOverviews)
    selectedKey.value = selectedKey.value || workflowList[0]?.workflow_key || ''
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function maybeStartWorkflowTour() {
  if (routeMode.value !== 'list' || loading.value || error.value || routeError.value) return
  await maybeStartTour(workflowFirstUseTour)
}

async function maybeStartWorkflowEditorTour() {
  if (!isWorkflowFormPage.value || routeError.value || formError.value) return
  await maybeStartTour(workflowEditorFirstUseTour)
}

async function maybeStartWorkflowDetailTour() {
  if (routeMode.value !== 'detail' || routeError.value || workflowDetailError.value || !selectedWorkflow.value) return
  await maybeStartTour(workflowDetailFirstUseTour)
}

let editorResourcesLoaded = false
async function loadEditorResources() {
  if (editorResourcesLoaded) return
  const [scriptList, skillList, runtimeConfig] = await Promise.all([
    api.listScripts(),
    api.listSkills(),
    api.getAgentRuntimeConfig(),
  ])
  scripts.value = scriptList
  skills.value = skillList
  agentRuntimeConfig.value = runtimeConfig
  defaultBackend.value = runtimeConfig.default_backend || defaultBackend.value
  editorResourcesLoaded = true
}

async function ensureWorkflowDetail(workflow: WorkflowDefinition) {
  // 详情定义不能跨路由复用：另一个标签页可能已经保存了更新。
  const detail = await api.getWorkflow(workflow.workflow_key)
  workflows.value = workflows.value.map(item =>
    item.workflow_key === detail.workflow_key ? detail : item,
  )
  return detail
}

watch([runPage, runPageSize], () => {
  if (detailTab.value === 'runs' && selectedWorkflow.value) {
    void loadRuns(selectedWorkflow.value.workflow_key)
  }
})

function openCreate() {
  void router.push('/workflow/new')
}

function openEdit(item: WorkflowDefinition) {
  void router.push(`/workflow/${item.workflow_key}/edit`)
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
  void router.push(`/workflow/${item.workflow_key}/detail`)
}

async function prepareDetail(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  detailTab.value = 'overview'
  runPage.value = 1
  resetArtifactPage()
  await loadRecentArtifacts()
}

async function selectDetailTab(value: string) {
  if (value !== 'overview' && value !== 'tasks' && value !== 'artifacts' && value !== 'runs' && value !== 'versions') return
  detailTab.value = value
  if (value === 'tasks' && taskWorkflow.value) await loadTasks(taskWorkflow.value.workflow_key)
  if (value === 'artifacts') await searchArtifacts()
  if (value === 'runs' && selectedWorkflow.value) await loadRuns(selectedWorkflow.value.workflow_key)
}

function goList() {
  void router.replace('/workflow')
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
  void router.replace(`/workflow/${key}/detail`)
}

/** Return from the edit/new page; the shared navigation guard handles dirty forms. */
function backFromForm() {
  if (routeMode.value === 'new') {
    goList()
    return
  }
  goDetail()
}

async function applyRoute() {
  routeError.value = ''
  if (!props.routeKey || loading.value) return
  if (routeMode.value === 'new') {
    try {
      await loadEditorResources()
    } catch (e: unknown) {
      routeError.value = errorMessage(e)
      return
    }
    prepareCreateForm()
    return
  }
  const summary = pageWorkflow.value
  if (!summary) {
    routeError.value = '无法加载该工作流（可能已被删除或不存在）'
    return
  }
  let workflow = summary
  try {
    if (routeMode.value === 'edit') {
      await loadEditorResources()
      workflow = await ensureWorkflowDetail(summary)
    } else if (routeMode.value === 'detail') {
      workflow = await ensureWorkflowDetail(summary)
    }
  } catch (e: unknown) {
    routeError.value = errorMessage(e)
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
    await applyProgressRoute(workflow)
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

/** task 代表版本聚合后的 workflow 状态文案。 */
function aggregatedStatusLabel(status: string) {
  const map: Record<string, string> = {
    running: '执行中',
    completed: '已完成',
    pending: '待处理',
    failed: '失败',
  }
  return map[status] || runStatusLabel(status)
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

async function ensureSubagentDetail(runId: string | null | undefined, taskIdStr: string) {
  await taskSubagentDetailState.ensure(runId, taskIdStr)
}

function subagentDetail(runId: string | null | undefined, taskIdStr: string) {
  return taskSubagentDetailState.detailFor(runId, taskIdStr)
}

function subagentDetailLoadingFor(runId: string | null | undefined, taskIdStr: string) {
  return taskSubagentDetailState.isLoading(runId, taskIdStr)
}

function subagentDetailErrorFor(runId: string | null | undefined, taskIdStr: string) {
  return taskSubagentDetailState.errorFor(runId, taskIdStr)
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
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
    void router.push(`/workflow/${result.workflow_key}/detail`)
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

async function openTasks(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  detailTab.value = 'tasks'
  if (routeMode.value === 'detail' && routeWorkflowKey.value === item.workflow_key) {
    await loadTasks(item.workflow_key)
    return
  }
  void router.push(`/workflow/${item.workflow_key}/tasks`)
}

async function prepareTasks(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  taskWorkflowKey.value = item.workflow_key
  prepareTaskState(item)
  await loadTasks(item.workflow_key)
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
    clearArtifacts()
    runEvents.value = []
    runLogs.value = []
    selectedRunId.value = ''
    progressRunId.value = ''
    resetExecutionData()
    await Promise.all([
      loadRunOverviews(),
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
      {{ routeError }}。请<button type="button" class="underline" @click="goList">返回列表</button>。
    </div>

    <template v-if="routeMode === 'list'">
    <!-- 页头操作：新建与导入工作流进 #ph-actions（仅列表态） -->
    <Teleport v-if="routeMode === 'list'" to="#ph-actions" defer>
      <Button data-tour="workflow-import" variant="outline" size="lg" @click="openWorkflowImport()">
        <Upload :size="14" />
        导入工作流
      </Button>
      <Button data-tour="workflow-create" size="lg" class="shadow-btn" @click="openCreate">
        <Plus :size="14" />
        新建工作流
      </Button>
    </Teleport>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
      {{ error }}
    </div>

    <Card data-tour="workflow-list">
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-medium text-foreground">工作流</div>
            <div class="text-xs text-muted-foreground">{{ workflows.length }} 个定义</div>
          </div>
          <Button variant="outline" size="sm" :disabled="runsLoading" @click="loadRunOverviews()">{{ runsLoading ? '刷新中' : '刷新运行状态' }}</Button>
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
              <div class="mt-1">{{ workflowRunTotals[item.workflow_key] || 0 }} 次运行记录</div>
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
        <div v-if="selectedWorkflow" data-tour="workflow-detail-actions" class="flex flex-wrap gap-2">
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

          <div data-tour="workflow-detail-summary" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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

          <SegmentedTabs v-model="detailTab" :tabs="detailTabs" data-tour="workflow-detail-tabs" @update:model-value="selectDetailTab" />

          <WorkflowDetailTourPreview v-if="workflowDetailTourPreview" :tab="detailTab" />

          <div v-if="detailTab === 'overview' && !workflowDetailTourPreview" class="space-y-4">
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
                  <WorkflowRunGraph :definition-snapshot="selectedWorkflow.definition" :node-runs="[]" @open-agent-run="openAgentRun" @open-script-run="openScriptRun" />
                </CardContent>
              </Card>
              <div v-else class="rounded-lg border border-warning/30 bg-warning-soft p-4 text-sm text-warning-soft-fg">该历史工作流需要迁移。进入编辑页并显式保存后才会写入结构化定义。</div>
              <div class="space-y-4">
                <Card size="sm" class="shadow-card">
                  <CardContent class="space-y-3">
                    <div class="flex items-center justify-between gap-2"><h3 class="text-sm font-semibold">最近运行</h3><StatusBadge v-if="workflowStatus" :status="workflowStatus === 'completed' ? 'success' : workflowStatus === 'running' ? 'running' : workflowStatus === 'failed' || workflowStatus === 'stopped' ? 'error' : 'blocked'" :label="aggregatedStatusLabel(workflowStatus)" /></div>
                    <template v-if="latestRun"><div class="font-mono text-xs text-muted-foreground">{{ latestRun.run_id }}</div><div class="grid grid-cols-2 gap-x-3 gap-y-2 text-xs"><span class="text-muted-foreground">开始时间</span><span class="text-right tabular-nums">{{ formatLocalDatetime(latestRun.started_at) }}</span><span class="text-muted-foreground">耗时</span><span class="text-right font-medium tabular-nums">{{ formatDuration(latestRun.duration_ms) || '—' }}</span></div><Button class="w-full" size="sm" variant="outline" @click="openProgress(selectedWorkflow, latestRun.run_id)">查看运行详情</Button></template>
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
              <Card size="sm" class="shadow-card"><CardContent><div class="mb-2"><h3 class="text-sm font-semibold">活动</h3><p class="mt-0.5 text-xs text-muted-foreground">最近运行与产物更新</p></div><div v-if="latestRun || recentArtifacts.length" class="divide-y"><button v-if="latestRun" type="button" class="list-row-interactive flex w-full items-start justify-between gap-3 px-1 py-2.5 text-left" @click="openProgress(selectedWorkflow, latestRun.run_id)"><div><div class="text-sm font-medium">工作流{{ aggregatedStatusLabel(workflowStatus) }}</div><div class="mt-0.5 font-mono text-xs text-muted-foreground">{{ latestRun.run_id }}</div></div><span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(latestRun.started_at) }}</span></button><div v-for="item in recentArtifacts.slice(0, 2)" :key="`activity:${item.artifact_id}`" class="flex items-start justify-between gap-3 px-1 py-2.5"><div><div class="text-sm font-medium">产物已更新</div><div class="mt-0.5 truncate text-xs text-muted-foreground">{{ item.path }}</div></div><span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(item.updated_at) }}</span></div></div><p v-else class="py-5 text-center text-sm text-muted-foreground">还没有活动记录</p></CardContent></Card>
            </div>
          </div>

          <WorkflowArtifactBrowser
            v-if="detailTab === 'artifacts' && !workflowDetailTourPreview"
            v-model:query="artifactQuery"
            v-model:path-match="artifactPathMatch"
            v-model:page="artifactPage"
            v-model:page-size="artifactPageSize"
            :format="artifactFormat"
            :loading="artifactLoading"
            :error="artifactError"
            :rows="artifactRows"
            :collapsed-paths="collapsedPaths"
            :total="artifactTotal"
            :page-size-options="ARTIFACT_PAGE_SIZE_OPTIONS"
            @search="resetArtifactPage(); searchArtifacts()"
            @update:format="setArtifactFormat"
            @toggle-folder="togglePath"
            @open="openArtifact"
            @history="openArtifactHistory"
          />

          <WorkflowRunHistory
            v-if="detailTab === 'runs' && !workflowDetailTourPreview"
            v-model:page="runPage"
            v-model:page-size="runPageSize"
            :runs="runs"
            :total="workflowRunTotals[selectedWorkflow.workflow_key] || 0"
            :loading="runsLoading"
            :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
            :status-label="runStatusLabel"
            :badge-class="runBadgeClass"
            :format-datetime="formatLocalDatetime"
            @refresh="loadRuns(selectedWorkflow.workflow_key)"
            @open="openProgress(selectedWorkflow, $event)"
          />
          <section v-if="detailTab === 'versions' && !workflowDetailTourPreview" class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
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
            重置后该任务会回到待处理状态，可被再次领取执行；不会立即触发执行，也不会改变其他任务的执行顺序。历史尝试次数会保留，成功重跑后当前错误提示会清除；失败 Run 仍可在运行记录中查看。
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
      v-if="routeMode === 'tasks' || (routeMode === 'detail' && detailTab === 'tasks' && !workflowDetailTourPreview)"
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

    <section v-if="(routeMode === 'tasks' || (routeMode === 'detail' && detailTab === 'tasks' && !workflowDetailTourPreview)) && !routeError" class="space-y-4">
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
          <WorkflowTaskToolbar
            :search-input="taskSearchInput"
            :status="taskStatusFilter"
            :type="taskTypeFilter"
            :has-artifacts="taskArtifactsFilter"
            :sort="taskSort"
            :statuses="taskStatuses"
            :types="taskTypes"
            :status-counts="taskStats"
            :status-label="taskStatusLabel"
            :show-reset="taskStatusFilter !== ALL_STATUS_SENTINEL || taskTypeFilter !== ALL_TYPE_SENTINEL || taskArtifactsFilter !== ALL_ARTIFACTS_SENTINEL || !!taskSearch || taskSort !== 'default'"
            :visible-task-count="pagedTasks.length"
            :all-visible-selected="allVisibleTasksSelected"
            :some-visible-selected="someVisibleTasksSelected"
            :selected-count="selectedTasks.length"
            :refreshable-selected-count="refreshableSelectedTasks.length"
            :refreshing-tasks="refreshingTasks"
            :batch-busy="batchBusy"
            :batch-action="batchAction"
            :batch-current="batchProgress.current"
            :batch-total="batchProgress.total"
            :stop-requested="batchStopRequested"
            :filtered-count="filteredTasks.length"
            :total-count="tasks.length"
            :has-workflow="!!taskWorkflow"
            :loading="tasksLoading"
            @update:search-input="taskSearchInput = $event"
            @update:status="taskStatusFilter = $event"
            @update:type="taskTypeFilter = $event"
            @update:has-artifacts="taskArtifactsFilter = $event"
            @update:sort="taskSort = $event"
            @search="onTaskSearchInput"
            @reset-filters="resetTaskFilters"
            @select-visible="setVisibleTasksSelectedFromEvent"
            @reset-selected="resetSelectedTasks"
            @run-selected="runSelectedTasks"
            @refresh-selected="refreshSelectedTasks"
            @stop-batch="stopBatchRun"
            @download-template="downloadTaskImportTemplate"
            @import="openTaskImport"
            @refresh="taskWorkflow && loadTasks(taskWorkflow.workflow_key)"
          />

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
              :agent-run-detail="progressAgentRunDetail"
              :agent-run-detail-loading="progressAgentRunDetailLoading"
              :agent-run-detail-error="progressAgentRunDetailError"
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
              :agent-run-detail="progressAgentRunDetail"
              :agent-run-detail-loading="progressAgentRunDetailLoading"
              :agent-run-detail-error="progressAgentRunDetailError"
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
                    <Badge v-if="task.needs_refresh" variant="outline" class="bg-warning-soft text-warning-soft-fg">当前版本未刷新</Badge>
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
                    v-if="canRunTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs text-primary"
                    :disabled="batchBusy || isTaskActionLoading(task)"
                    @click="executeTask(task)"
                  >
                    {{ isTaskActionLoading(task) ? '执行中' : task.needs_refresh || task.status === 'stale' ? '增量运行' : task.status === 'completed' ? '全量运行' : '执行' }}
                  </Button>
                  <Button
                    v-if="canRefreshTask(task)"
                    variant="ghost"
                    size="sm"
                    class="h-8 text-xs text-primary"
                    :disabled="batchBusy || isTaskActionLoading(task)"
                    @click="refreshTask(task)"
                  >
                    {{ isTaskActionLoading(task) ? '安排中' : '安排增量' }}
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
              <WorkflowTaskExecutionPreview :plan="taskPreview(task)" />
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
                  <AgentRunExecutionPanel :run="taskAgentRun(task)" :loading="isTaskLogLoading(task)" />
                  <div v-if="!taskEvents(task).length" class="rounded-md border bg-background px-3 py-6 text-center text-sm text-muted-foreground">
                    还没有 Agent 输出，任务被领取执行后这里会按时间顺序显示对话流。
                  </div>
                  <div v-else class="max-h-[30rem] overflow-auto pr-1">
                    <RunEventTimeline
                      :events="taskFilteredEvents(task)"
                      :context-key="'task:' + taskRunLogKey(task)"
                      :payloads="task.lease_run_id ? (taskRunPayloads[task.lease_run_id] || {}) : {}"
                      :payload-errors="task.lease_run_id ? (taskRunPayloadErrors[task.lease_run_id] || {}) : {}"
                      @expand="(taskId: string) => ensureSubagentDetail(task.lease_run_id, taskId)"
                      @load-payload="(refKey: string) => loadTaskPayload(task.lease_run_id, refKey)"
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
              :agent-run-detail="progressAgentRunDetail"
              :agent-run-detail-loading="progressAgentRunDetailLoading"
              :agent-run-detail-error="progressAgentRunDetailError"
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
              :agent-run-detail="progressAgentRunDetail"
              :agent-run-detail-loading="progressAgentRunDetailLoading"
              :agent-run-detail-error="progressAgentRunDetailError"
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
      <EditorActionBar class="-mx-7 px-7">
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
          <div data-tour="workflow-editor-actions" class="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" :disabled="designing" @click="openWorkflowDesigner('modify')">
              <WandSparkles class="mr-1.5 h-4 w-4" />
              AI 设计
            </Button>
            <Button variant="outline" size="sm" :disabled="editedWorkflowRunBusy" @click="runEditedWorkflow">
              <Play class="mr-1.5 h-4 w-4" />
              测试运行
            </Button>
            <Select v-model="taskRefreshPolicy" :disabled="saving">
              <SelectTrigger class="h-8 w-[210px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">保存并安排增量刷新</SelectItem>
                <SelectItem value="defer">仅保存，暂不刷新任务</SelectItem>
              </SelectContent>
            </Select>
            <Button :disabled="saving" size="sm" @click="saveWorkflow">
              <Save class="mr-1.5 h-4 w-4" />
              {{ saving ? '保存中' : '保存' }}
            </Button>
          </div>
        </div>
      </EditorActionBar>

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

            <div data-tour="workflow-editor-canvas" class="workflow-editor-region flex flex-col overflow-hidden rounded-md border bg-muted/10" style="height: clamp(480px, calc(100vh - 240px), 820px);">
              <!-- 调色板：横排置顶，自然高度 -->
              <div data-tour="workflow-editor-palette"><WorkflowNodePalette orientation="horizontal" @add-node="addNode" /></div>
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
          <Card v-if="workflowEditorTourAgentPreview" data-tour="workflow-editor-agent-panel" class="shadow-card">
            <CardContent class="space-y-3 p-4">
              <div class="flex items-center justify-between gap-2 border-b pb-2">
                <div><Badge class="mb-1 bg-info-soft text-info-soft-fg">指南示例</Badge><div class="text-sm font-semibold">Agent 节点配置</div></div>
                <span class="text-xs text-muted-foreground">不会写入工作流</span>
              </div>
              <WorkflowNodeConfigPanel
                :node="workflowEditorTourAgentNode"
                :scripts="scripts"
                :skills="skills"
                :backends="backendKeys"
                :reference-items="[]"
                :issues="[]"
                @replace="replaceTourAgentNode"
              />
            </CardContent>
          </Card>
          <!-- 配置态：选中节点/边且 overlay 模式时，整栏只显示配置（元数据隐藏） -->
          <Card v-else-if="(selectedNode || selectedEdge) && configDrawerOpen && configDrawerMode === 'overlay'" class="shadow-card">
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

      <WorkflowDesignerDrawer
        :open="showDesigner"
        :mode="designMode"
        :prompt="designPrompt"
        :busy="designing"
        :stop-requested="designStopRequested"
        :error="designError"
        :response="designResponse"
        :draft="workflowDesignDraft"
        :saving="saving"
        @update:open="showDesigner = $event"
        @update:mode="designMode = $event"
        @update:prompt="designPrompt = $event"
        @run="runWorkflowDesigner"
        @stop="stopWorkflowDesigner"
        @accept="acceptWorkflowDesign"
      />
    </section>

    <WorkflowArtifactDialogs
      :open="showArtifact"
      :detail="artifactDetail"
      :detail-loading="detailLoading"
      :visibility-saving="visibilitySaving"
      :read-only="artifactDetailReadOnly"
      :detail-html="artifactHtml"
      :fullscreen="fullscreenArtifact"
      :fullscreen-html="fullscreenArtifactHtml"
      :history-open="showArtifactHistory"
      :history-title="artifactHistoryTarget?.title || ''"
      :history-loading="historyLoading"
      :history="artifactHistory"
      @update:open="showArtifact = $event"
      @update:history-open="showArtifactHistory = $event"
      @open-fullscreen="openArtifactFullscreen"
      @close-fullscreen="closeArtifactFullscreen"
      @set-visibility="setArtifactVisibility"
    />
  </div>
</template>
