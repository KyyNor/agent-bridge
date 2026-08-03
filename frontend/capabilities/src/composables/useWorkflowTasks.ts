import { computed, onUnmounted, ref } from 'vue'
import type { Ref } from 'vue'
import { api } from '../api/client'
import type {
  AgentRun,
  WorkflowArtifact,
  WorkflowDefinition,
  WorkflowExecutionMode,
  WorkflowExecutionPlan,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowRunLog,
  WorkflowTask,
  WorkflowTaskImportPreview,
} from '../api/types'
import {
  ALL_ARTIFACTS_SENTINEL,
  ALL_STATUS_SENTINEL,
  ALL_TYPE_SENTINEL,
  canForceRun,
  canRunNormally,
  canRunTask,
  distinctStatuses,
  distinctTypes,
  filterAndSortTasks,
  runWorkflowTaskQueue,
  runWorkflowTaskResetQueue,
  taskId,
  taskStats as computeTaskStats,
  togglePageTaskSelection,
  toggleTaskSelection,
} from '../lib/workflowTasks'
import { distinctActors, filterEventsByActor } from '../lib/workflowEvents'
import { paginate } from '../lib/pagination'
import { registerNavigationGuard } from '../lib/navigation'
import { confirm } from './useConfirm'

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

/** Hook the task queue uses to surface the active batch run inside the agent-run
 *  detail panel. Implemented by the run-progress composable (zone B). */
export interface BatchRunDetailHook {
  /** Maps a workflow run_id to its agent_runs.run_key for subagent-detail lookups. */
  runIdToAgentRunKey: Ref<Record<string, string>>
  loadProgressAgentRuns(): Promise<void>
  loadProgressAgentEvents(options?: { quiet?: boolean }): Promise<void>
  setProgressWorkflowKey(value: string): void
  setProgressRunId(value: string): void
  setSelectedRunId(value: string): void
  setProgressAgentRunKey(value: string): void
  /** Returns the freshly written progress detail error. */
  setProgressDetailError(value: string): void
}

export interface UseWorkflowTasksOptions {
  /** Reactive getter for the workflow currently owning the task page. */
  taskWorkflow: Ref<WorkflowDefinition | null>
  /** Reactive getter for the route workflow key (used by import guards). */
  routeWorkflowKey: Ref<string>
  /** Reactive getter for the current route mode (list/detail/tasks/...). */
  routeMode: Ref<string>
  /** Reactive getter for the currently selected detail tab. */
  detailTab: Ref<string>
  /** Opens the full-screen artifact preview for a task artifact. */
  openTaskArtifactFullscreen(task: WorkflowTask): void
  /** Surfaces the active run into the shared progress view during batch runs. */
  batchRunDetail: BatchRunDetailHook
  /** Navigate to the per-task progress page after a single execute returns a run id. */
  navigateToTaskProgress(workflowKey: string, runId: string): void
}

/**
 * 工作流任务队列：加载、过滤、分页、批量执行 / 重置、产物展开、日志展开、导入。
 *
 * 抽自 WorkflowView.vue 的任务区逻辑，保留与原视图一致的 API 调用和状态语义。
 */
export function useWorkflowTasks(options: UseWorkflowTasksOptions) {
  const {
    taskWorkflow,
    routeWorkflowKey,
    routeMode,
    detailTab,
    openTaskArtifactFullscreen,
    batchRunDetail,
    navigateToTaskProgress,
  } = options

  const workflowTasks = ref<Record<string, WorkflowTask[]>>({})
  const tasksLoading = ref(false)
  const taskError = ref('')
  const expandedTaskIds = ref<Set<string>>(new Set())
  const taskRunLogs = ref<Record<string, WorkflowRunLog[]>>({})
  const taskRunEvents = ref<Record<string, WorkflowRunEvent[]>>({})
  const taskRunDetails = ref<Record<string, AgentRun>>({})
  const taskRunPayloads = ref<Record<string, Record<string, string>>>({})
  const taskRunPayloadErrors = ref<Record<string, Record<string, string>>>({})
  const taskLogLoading = ref<Set<string>>(new Set())
  const taskPage = ref(1)
  const taskPageSize = ref(10)
  const taskStatusFilter = ref(ALL_STATUS_SENTINEL)
  const taskTypeFilter = ref(ALL_TYPE_SENTINEL)
  const taskArtifactsFilter = ref(ALL_ARTIFACTS_SENTINEL)
  const taskSearchInput = ref('')
  const taskSearch = ref('')
  const taskSort = ref('default')
  const expandedArtifactIds = ref<Set<string>>(new Set())
  const taskArtifacts = ref<Record<string, WorkflowArtifact[]>>({})
  const taskArtifactLoading = ref<Set<string>>(new Set())
  const taskArtifactError = ref('')
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
  let batchToken = 0
  const batchStopRequested = ref(false)
  const taskActorFilter = ref<Record<string, string>>({})
  const taskArtifactActive = ref<Record<string, string>>({})
  let taskSearchDebounce: ReturnType<typeof setTimeout> | null = null

  const tasks = computed(() => workflowTasks.value[taskWorkflow.value?.workflow_key || ''] || [])
  const taskStats = computed(() => computeTaskStats(tasks.value))
  const taskStatuses = computed(() => distinctStatuses(tasks.value))
  const taskTypes = computed(() => distinctTypes(tasks.value))
  const filteredTasks = computed(() =>
    filterAndSortTasks(tasks.value, {
      status: taskStatusFilter.value,
      type: taskTypeFilter.value,
      hasArtifacts: taskArtifactsFilter.value,
      search: taskSearch.value,
      sort: taskSort.value,
    }),
  )
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

  const removeBatchNavigationGuard = registerNavigationGuard(() => {
    if (!batchBusy.value) return true
    return confirm({
      title: '批量操作进行中',
      description: '当前批量操作尚未完成，离开后页面队列会停止，已启动的任务可能继续在后台运行。确认离开？',
      destructive: true,
      confirmText: '离开',
    })
  })

  function handleBeforeUnload(event: BeforeUnloadEvent) {
    if (!batchBusy.value) return
    event.preventDefault()
    // 浏览器会使用自己的通用文案，不能可靠展示自定义提示文本。
    event.returnValue = ''
  }

  if (typeof window !== 'undefined') window.addEventListener('beforeunload', handleBeforeUnload)
  onUnmounted(() => {
    removeBatchNavigationGuard()
    if (typeof window !== 'undefined') window.removeEventListener('beforeunload', handleBeforeUnload)
  })

  function taskWorkflowKey() {
    return taskWorkflow.value?.workflow_key || (routeMode.value === 'tasks' ? routeWorkflowKey.value : '')
  }

  async function loadTasks(workflowKey: string) {
    if (!workflowKey) return
    tasksLoading.value = true
    taskError.value = ''
    try {
      const result = await api.listWorkflowTasks(workflowKey)
      workflowTasks.value = { ...workflowTasks.value, [workflowKey]: result.tasks }
    } catch (e: unknown) {
      taskError.value = errorMessage(e)
      workflowTasks.value = { ...workflowTasks.value, [workflowKey]: [] }
    } finally {
      tasksLoading.value = false
    }
  }

  function resetTaskFilters() {
    taskStatusFilter.value = ALL_STATUS_SENTINEL
    taskTypeFilter.value = ALL_TYPE_SENTINEL
    taskArtifactsFilter.value = ALL_ARTIFACTS_SENTINEL
    taskSearchInput.value = ''
    taskSearch.value = ''
    taskSort.value = 'default'
  }

  function onTaskSearchInput() {
    if (taskSearchDebounce) clearTimeout(taskSearchDebounce)
    taskSearchDebounce = setTimeout(() => {
      taskSearch.value = taskSearchInput.value
    }, 250)
  }

  // ===== Task import =====

  function resetTaskImportState() {
    taskImportRequestToken += 1
    taskImportPreview.value = null
    taskImportLoading.value = false
    taskImportConfirming.value = false
    taskImportError.value = ''
  }

  function openTaskImport() {
    if (!taskWorkflowKey() || !taskWorkflow.value || batchBusy.value) return
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
      && taskWorkflowKey() === workflowKey
  }

  async function previewTaskImport(file: File) {
    const workflowKey = taskWorkflowKey()
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
    const workflowKey = taskWorkflowKey()
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
      if (taskWorkflowKey() === workflowKey) showTaskImport.value = true
    } finally {
      anchor?.remove()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }

  async function confirmTaskImport() {
    const workflowKey = taskWorkflowKey()
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
      batchSummary.value = `导入完成：新增 ${result.created}，更新 ${result.updated}，取代旧版本 ${result.superseded ?? 0}，跳过（运行中） ${result.skipped_running}，跳过（已完成） ${result.skipped_completed}，跳过（历史版本） ${result.skipped_historical ?? 0}，重开（已过期） ${result.reopened_expired}`
      await loadTasks(workflowKey)
    } catch (e: unknown) {
      if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportError.value = errorMessage(e)
    } finally {
      if (isCurrentTaskImportRequest(requestToken, workflowKey)) taskImportConfirming.value = false
    }
  }

  // ===== Per-task artifacts =====

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

  // ===== Single task execution / reset / preview =====

  function taskExecutionMode(task: WorkflowTask): WorkflowExecutionMode {
    // Preview requests use execution_mode: 'incremental' for stale tasks and
    // execution_mode: 'force_full' for completed tasks with existing output.
    if (task.status === 'stale') return 'incremental'
    if (canForceRun(task)) return 'force_full'
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
      || (task.status === 'running' && canRunNormally(task))
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
        navigateToTaskProgress(task.workflow_key, result.run_id)
        return result
      }
      await loadTasks(task.workflow_key)
    } catch (e: unknown) {
      taskActionError.value = errorMessage(e)
    } finally {
      const done = new Set(taskActionLoading.value)
      done.delete(key)
      taskActionLoading.value = done
    }
    return undefined
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

  // ===== Batch execution / reset =====

  function sleep(milliseconds: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, milliseconds))
  }

  function shouldStopBatchError(error: unknown): boolean {
    const message = error instanceof Error ? error.message : String(error)
    return /(?:^|\s)(?:4\d\d|5\d\d):/.test(message)
      || /already running|conflict|network|failed to fetch|fetch failed|页面队列已停止/i.test(message)
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

  async function loadBatchRunDetail(task: WorkflowTask | null, runId: string, quiet: boolean) {
    if (!task || !runId) return
    const runChanged = batchCurrentRunId.value !== runId
    batchCurrentTask.value = task
    batchCurrentTaskId.value = taskId(task)
    batchCurrentRunId.value = runId
    batchRunDetail.setProgressWorkflowKey(task.workflow_key)
    batchRunDetail.setProgressRunId(runId)
    batchRunDetail.setSelectedRunId(runId)
    if (runChanged) {
      batchRunDetail.setProgressAgentRunKey('')
      batchRunDetail.setProgressDetailError('')
    }
    try {
      await batchRunDetail.loadProgressAgentRuns()
      await batchRunDetail.loadProgressAgentEvents({ quiet })
    } catch (e: unknown) {
      batchRunDetail.setProgressDetailError(errorMessage(e))
    }
    batchRunDetailError.value = '' // sync after load; the host exposes progressDetailError separately
  }

  async function waitForBatchRun(
    runId: string,
    token: number,
    onUpdate?: (run: WorkflowRun) => void | Promise<void>,
  ): Promise<WorkflowRun> {
    while (true) {
      if (token !== batchToken && !batchStopRequested.value) throw new Error('页面队列已停止')
      const run = await api.getWorkflowRun(runId)
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
      const success = result.outcomes.filter(item => item.status === 'success').length
      const skipped = result.outcomes.filter(item => item.status === 'skipped').length
      const failed = result.outcomes.filter(item => item.status === 'failed').length
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
        canExecute: canRunTask,
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

  // ===== Task logs / events / payloads =====

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

  function taskAgentRun(task: WorkflowTask) {
    return task.lease_run_id ? taskRunDetails.value[task.lease_run_id] || null : null
  }

  async function loadTaskPayload(runId: string | null | undefined, refKey: string) {
    if (!runId) return
    let runKey = batchRunDetail.runIdToAgentRunKey.value[runId]
    try {
      if (!runKey) {
        const agentRun = await api.getAgentRunForWorkflowRun(runId)
        if (!agentRun) throw new Error('未找到该运行对应的 Agent 记录')
        runKey = agentRun.run_key
        batchRunDetail.runIdToAgentRunKey.value = { ...batchRunDetail.runIdToAgentRunKey.value, [runId]: runKey }
      }
      if (taskRunPayloads.value[runId]?.[refKey]) return
      const blob = await api.getAgentRunPayload(runKey, refKey)
      const content = await blob.text()
      taskRunPayloads.value = {
        ...taskRunPayloads.value,
        [runId]: { ...(taskRunPayloads.value[runId] || {}), [refKey]: content },
      }
    } catch (error: unknown) {
      taskRunPayloadErrors.value = {
        ...taskRunPayloadErrors.value,
        [runId]: {
          ...(taskRunPayloadErrors.value[runId] || {}),
          [refKey]: error instanceof Error ? error.message : '完整内容加载失败',
        },
      }
    }
  }

  function taskActors(task: WorkflowTask) {
    return distinctActors(taskEvents(task))
  }

  function taskActorFilterFor(task: WorkflowTask) {
    return taskActorFilter.value[taskId(task)] || ''
  }

  function setTaskActorFilter(task: WorkflowTask, actorId: string) {
    taskActorFilter.value = { ...taskActorFilter.value, [taskId(task)]: actorId }
  }

  function taskFilteredEvents(task: WorkflowTask) {
    return filterEventsByActor(taskEvents(task), taskActorFilterFor(task))
  }

  function isTaskLogLoading(task: WorkflowTask) {
    return task.lease_run_id ? taskLogLoading.value.has(task.lease_run_id) : false
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
    if (!task.lease_run_id || (taskRunLogs.value[task.lease_run_id] && taskRunDetails.value[task.lease_run_id])) return
    const loading = new Set(taskLogLoading.value)
    loading.add(task.lease_run_id)
    taskLogLoading.value = loading
    try {
      const logsPromise = taskRunLogs.value[task.lease_run_id]
        ? Promise.resolve(taskRunLogs.value[task.lease_run_id])
        : api.getWorkflowRunLogs(task.lease_run_id)
      let runKey = batchRunDetail.runIdToAgentRunKey.value[task.lease_run_id]
      let agentRun: AgentRun | null = taskRunDetails.value[task.lease_run_id] || null
      if (!agentRun && runKey) {
        agentRun = await api.getAgentRun(runKey)
      }
      if (!agentRun) {
        agentRun = await api.getAgentRunForWorkflowRun(task.lease_run_id)
      }
      if (agentRun) {
        runKey = agentRun.run_key
        batchRunDetail.runIdToAgentRunKey.value = { ...batchRunDetail.runIdToAgentRunKey.value, [task.lease_run_id]: runKey }
        taskRunDetails.value = { ...taskRunDetails.value, [task.lease_run_id]: agentRun }
      }
      const [logs, events] = await Promise.all([
        logsPromise,
        taskRunEvents.value[task.lease_run_id]
          ? Promise.resolve(taskRunEvents.value[task.lease_run_id])
          : runKey ? api.getAgentRunEvents(runKey) : Promise.resolve([] as WorkflowRunEvent[]),
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

  // ===== Selection lifecycle helpers used by the host route handler =====

  function prepareTasks(item: WorkflowDefinition) {
    selectedTaskIds.value = new Set()
    resetBatchRunDetail()
    batchProgress.value = createBatchProgress()
    batchSummary.value = ''
    batchAction.value = ''
  }

  /** Cancel any in-flight batch queue (route change / unmount). */
  function cancelBatchQueue() {
    batchToken += 1
    batchStopRequested.value = false
    if (batchAction.value) batchAction.value = ''
    resetBatchRunDetail()
  }

  /** Reset everything tied to cleared execution data. */
  function resetExecutionData() {
    expandedTaskIds.value = new Set()
    taskRunLogs.value = {}
    taskRunEvents.value = {}
    taskRunDetails.value = {}
    taskRunPayloads.value = {}
    taskRunPayloadErrors.value = {}
    resetTaskFilters()
    expandedArtifactIds.value = new Set()
    taskArtifacts.value = {}
    taskArtifactActive.value = {}
    taskActionLoading.value = new Set()
    taskActionError.value = ''
    resetTarget.value = null
    taskActorFilter.value = {}
  }

  return {
    // state
    workflowTasks,
    tasksLoading,
    taskError,
    expandedTaskIds,
    taskRunLogs,
    taskRunEvents,
    taskRunDetails,
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
    // computed
    tasks,
    taskStats,
    taskStatuses,
    taskTypes,
    filteredTasks,
    pagedTasks,
    selectedTasks,
    allVisibleTasksSelected,
    someVisibleTasksSelected,
    batchBusy,
    batchProgressPercent,
    batchPendingCount,
    batchRunDetailVisible,
    // methods
    loadTasks,
    resetTaskFilters,
    onTaskSearchInput,
    resetTaskImportState,
    openTaskImport,
    closeTaskImport,
    previewTaskImport,
    downloadTaskImportTemplate,
    confirmTaskImport,
    taskArtifactKey,
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
    prepareTasks,
    cancelBatchQueue,
    resetExecutionData,
  }
}
