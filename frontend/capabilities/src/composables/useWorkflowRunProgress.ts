import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { api } from '../api/client'
import type {
  AgentRun,
  WorkflowArtifact,
  WorkflowDefinition,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowRunLog,
  WorkflowRunSummary,
} from '../api/types'

/** Task 代表版本聚合后的 workflow 状态（缺失时回退到 latestRun.status）。 */
type WorkflowTaskStatus = Record<string, {
  status: string
  total: number
  completed: number
  running: number
  failed: number
}>
import { useSubagentDetails } from './useSubagentDetails'
import { useAgentRunEventStream } from './useAgentRunEventStream'
import { alert } from './useConfirm'
import {
  buildAgentRunHash,
  buildScriptRunHash,
  currentHash,
  navigateTo,
} from '../lib/navigation'
import { lastAgentRunEventId, mergeAgentRunEvent, normalizeAgentRunEvents } from '../lib/agentRunEvents'
import { queryClient, queryKeys } from '../lib/query'

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

const WORKFLOW_RUN_CACHE_LIMIT = 50

export interface UseWorkflowRunProgressOptions {
  /** Reactive getter for the workflow currently selected in the detail header. */
  selectedWorkflow: Ref<WorkflowDefinition | null>
  /** Reactive list of all loaded workflows (used to resolve the progress workflow). */
  workflows: Ref<WorkflowDefinition[]>
  /** Reactive getter for the editor form profile_key (fallback for artifact fetches). */
  formProfileKey: () => string | undefined
  /** Reactive getter for the current route parts (progress run id lives at index 2). */
  routeParts: Ref<string[]>
  /** Opens the full-screen artifact preview (owned by the artifacts composable). */
  openArtifactFullscreen(artifact: WorkflowArtifact | WorkflowArtifactDetailLike): void
  /** Set the selected workflow key (host owns the ref shared with editor/detail). */
  setSelectedKey(value: string): void
  /** Search artifacts on the detail page (called when a run finishes successfully). */
  searchArtifacts(): Promise<void> | void
  /** Surface artifact errors on the shared error banner (artifacts composable). */
  setArtifactError(value: string): void
  /** Surface page-level errors (e.g. failed run-overview refresh). */
  setPageError(value: string): void
}

/** Minimal shape we need from an artifact to open it fullscreen. */
export type WorkflowArtifactDetailLike = {
  title: string
  path: string
  summary: string
  tags: string[]
  content?: string
  format: string
  artifact_id?: string
}

/**
 * 工作流运行进度与时间轴：运行列表、轮询、进度视图（multi agent-run）、
 * 进度产物、子 Agent 详情与跳转。抽自 WorkflowView.vue 的运行/进度区逻辑。
 */
export function useWorkflowRunProgress(options: UseWorkflowRunProgressOptions) {
  const {
    selectedWorkflow,
    workflows,
    formProfileKey,
    routeParts,
    openArtifactFullscreen,
    setSelectedKey,
    searchArtifacts,
    setArtifactError,
    setPageError,
  } = options

  const progressWorkflowKey = ref('')
  const progressRunId = ref('')
  const workflowRuns = ref<Record<string, WorkflowRunSummary[]>>({})
  const workflowRunTotals = ref<Record<string, number>>({})
  const workflowTaskStatus = ref<WorkflowTaskStatus>({})
  const runsLoading = ref(false)
  const selectedRunId = ref('')
  const runEvents = ref<WorkflowRunEvent[]>([])
  const runLogs = ref<WorkflowRunLog[]>([])
  const logsLoading = ref(false)
  const progressRunArtifacts = ref<Record<string, WorkflowArtifact[]>>({})
  const progressArtifactsLoading = ref(false)
  const progressAgentRuns = ref<AgentRun[]>([])
  const progressAgentRunKey = ref('')
  const progressAgentRunsLoading = ref(false)
  const progressAgentRunDetail = ref<AgentRun | null>(null)
  const progressAgentRunDetailLoading = ref(false)
  const progressAgentRunDetailError = ref('')
  const progressDetailError = ref('')
  const progressRunDetail = ref<WorkflowRun | null>(null)
  /** Maps a workflow_run_id to its agent_runs.run_key, so subagent-detail (which is
   *  keyed by run_key under /agent-runs) can be resolved from the workflow view. */
  const runIdToAgentRunKey = ref<Record<string, string>>({})
  const testing = ref(false)
  const testingRunId = ref('')
  const testError = ref('')
  const runPage = ref(1)
  const runPageSize = ref(10)
  let testPoll: ReturnType<typeof setInterval> | null = null
  let progressAgentRunDetailRequest = 0
  let streamedAgentRunKey = ''

  const progressSubagentDetailState = useSubagentDetails(
    (runKey, taskIdStr) => api.getAgentRunSubagentDetail(runKey, taskIdStr),
  )
  const progressAgentEventStream = useAgentRunEventStream()

  const progressWorkflow = computed(() =>
    workflows.value.find(item => item.workflow_key === progressWorkflowKey.value) || selectedWorkflow.value,
  )
  const progressRun = computed(() =>
    (workflowRuns.value[progressWorkflowKey.value] || []).find(run => run.run_id === progressRunId.value) || null,
  )
  const progressArtifacts = computed(() => progressRunArtifacts.value[progressRunId.value] || [])
  const progressFinished = computed(() =>
    !!progressRun.value && ['completed', 'no_task', 'failed', 'stopped'].includes(progressRun.value.status),
  )

  async function fetchWorkflowRun(runId: string, options: { fresh?: boolean } = {}) {
    return queryClient.fetchQuery({
      queryKey: queryKeys.workflowRun(runId),
      queryFn: ({ signal }) => api.getWorkflowRun(runId, { signal }),
      ...(options.fresh ? { staleTime: 0 } : {}),
    })
  }

  function applyRunOverviews(overviews: Array<{
    workflow_key: string
    run_count: number
    latest_run: WorkflowRunSummary | null
    running_run: WorkflowRunSummary | null
    task_aggregated_status?: string
    task_total?: number
    task_completed?: number
    task_running?: number
    task_failed?: number
  }>) {
    const nextRuns: Record<string, WorkflowRunSummary[]> = {}
    const nextTotals: Record<string, number> = {}
    const nextTaskStatus: WorkflowTaskStatus = {}
    for (const overview of overviews) {
      const items = [overview.latest_run, overview.running_run]
        .filter((run): run is WorkflowRunSummary => Boolean(run))
        .filter((run, index, list) => list.findIndex(item => item.run_id === run.run_id) === index)
      nextRuns[overview.workflow_key] = items
      nextTotals[overview.workflow_key] = overview.run_count
      if (overview.task_aggregated_status) {
        nextTaskStatus[overview.workflow_key] = {
          status: overview.task_aggregated_status,
          total: overview.task_total ?? 0,
          completed: overview.task_completed ?? 0,
          running: overview.task_running ?? 0,
          failed: overview.task_failed ?? 0,
        }
      }
    }
    workflowRuns.value = nextRuns
    workflowRunTotals.value = nextTotals
    workflowTaskStatus.value = nextTaskStatus
    const runningEntry = overviews.find(item => item.running_run)
    if (runningEntry?.running_run) {
      testing.value = true
      testingRunId.value = runningEntry.running_run.run_id
      progressWorkflowKey.value = progressWorkflowKey.value || runningEntry.workflow_key
    } else {
      testing.value = false
      testingRunId.value = ''
      stopTestPolling()
    }
  }

  async function loadRunOverviews() {
    runsLoading.value = true
    try {
      applyRunOverviews(await api.listWorkflowRunOverviews())
    } catch (e: unknown) {
      setPageError(errorMessage(e))
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
    workflowRuns.value = { ...workflowRuns.value, [key]: nextRuns.slice(0, WORKFLOW_RUN_CACHE_LIMIT) }
    workflowRunTotals.value = {
      ...workflowRunTotals.value,
      [key]: Math.max(workflowRunTotals.value[key] || 0, nextRuns.length),
    }
  }

  async function loadRuns(
    workflowKey = selectedWorkflow.value?.workflow_key || '',
    options: { preserveSelectedRun?: boolean } = {},
  ) {
    const key = workflowKey
    if (!key) {
      workflowRuns.value = { ...workflowRuns.value }
      return
    }
    runsLoading.value = true
    try {
      const result = await api.listWorkflowRunSummaries(
        key,
        runPageSize.value,
        (runPage.value - 1) * runPageSize.value,
      )
      workflowRuns.value = { ...workflowRuns.value, [key]: result.runs }
      workflowRunTotals.value = { ...workflowRunTotals.value, [key]: result.total }
      if (
        !options.preserveSelectedRun
        && key === selectedWorkflow.value?.workflow_key
        && !result.runs.some(r => r.run_id === selectedRunId.value)
      ) {
        selectedRunId.value = result.runs[0]?.run_id || ''
        await loadLogs()
      }
    } finally {
      runsLoading.value = false
    }
  }

  function runningRunFor(workflowKey: string) {
    return (workflowRuns.value[workflowKey] || []).find(run => run.status === 'running') || null
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
    } catch {
      if (!options.quiet) {
        runLogs.value = []
        runEvents.value = []
      }
    } finally {
      if (!options.quiet) logsLoading.value = false
    }
  }

  async function selectRun(runId: string) {
    selectedRunId.value = runId
    await loadLogs()
  }

  // ===== Progress page: multi agent-run support =====

  function clearProgressAgentRunDetail() {
    progressAgentRunDetailRequest += 1
    stopProgressAgentEventStream()
    progressAgentRunDetail.value = null
    progressAgentRunDetailLoading.value = false
    progressAgentRunDetailError.value = ''
  }

  function setProgressAgentRunKey(value: string) {
    if (value !== progressAgentRunKey.value) stopProgressAgentEventStream()
    progressAgentRunKey.value = value
    progressAgentRunDetailError.value = ''
    if (!value) clearProgressAgentRunDetail()
  }

  async function loadProgressAgentRunDetail(options: { quiet?: boolean } = {}) {
    const requestId = ++progressAgentRunDetailRequest
    const agentRunKey = progressAgentRunKey.value
    if (!agentRunKey) {
      progressAgentRunDetail.value = null
      progressAgentRunDetailLoading.value = false
      progressAgentRunDetailError.value = ''
      return
    }
    if (!options.quiet) progressAgentRunDetailLoading.value = true
    try {
      const detail = await api.getAgentRun(agentRunKey)
      if (requestId !== progressAgentRunDetailRequest || agentRunKey !== progressAgentRunKey.value) return
      progressAgentRunDetail.value = detail
      progressAgentRunDetailError.value = ''
      ensureProgressAgentEventStream()
    } catch (error: unknown) {
      if (requestId !== progressAgentRunDetailRequest || agentRunKey !== progressAgentRunKey.value) return
      progressAgentRunDetailError.value = errorMessage(error)
    } finally {
      if (requestId === progressAgentRunDetailRequest && agentRunKey === progressAgentRunKey.value) {
        progressAgentRunDetailLoading.value = false
      }
    }
  }

  async function loadProgressAgentRuns(options: { quiet?: boolean } = {}) {
    const workflowRunId = progressRunId.value
    if (!workflowRunId) {
      progressAgentRuns.value = []
      setProgressAgentRunKey('')
      return
    }
    if (!options.quiet) progressAgentRunsLoading.value = true
    try {
      const runs = await api.listAgentRunsForWorkflowRun(workflowRunId)
      progressAgentRuns.value = runs
      progressDetailError.value = ''
      // Default to the first agent run (oldest = main workflow agent).
      if (!progressAgentRunKey.value || !runs.some(r => r.run_key === progressAgentRunKey.value)) {
        setProgressAgentRunKey(runs[0]?.run_key || '')
      }
      await loadProgressAgentRunDetail(options)
    } catch (e: unknown) {
      progressAgentRuns.value = []
      setProgressAgentRunKey('')
      progressDetailError.value = errorMessage(e)
    } finally {
      if (!options.quiet) progressAgentRunsLoading.value = false
    }
  }

  async function loadProgressAgentEvents(options: { quiet?: boolean } = {}) {
    const agentRunKey = progressAgentRunKey.value
    if (!agentRunKey) {
      runEvents.value = []
      return
    }
    if (!options.quiet) logsLoading.value = true
    try {
      const events = await api.getAgentRunEvents(agentRunKey)
      runEvents.value = normalizeAgentRunEvents(events)
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
    await progressSubagentDetailState.refreshLoaded(agentRunKey)
  }

  function selectedProgressAgentRun() {
    return progressAgentRuns.value.find(run => run.run_key === progressAgentRunKey.value) || null
  }

  function stopProgressAgentEventStream() {
    progressAgentEventStream.stop()
    streamedAgentRunKey = ''
  }

  function ensureProgressAgentEventStream() {
    const agentRunKey = progressAgentRunKey.value
    const selected = selectedProgressAgentRun()
    if (!agentRunKey || progressAgentRunDetail.value?.status !== 'running' || selected?.status !== 'running') {
      if (streamedAgentRunKey) stopProgressAgentEventStream()
      return
    }
    if (streamedAgentRunKey === agentRunKey) return
    streamedAgentRunKey = agentRunKey
    progressAgentEventStream.start(agentRunKey, lastAgentRunEventId(runEvents.value), {
      onAgentEvent(event) {
        if (progressAgentRunKey.value !== agentRunKey) return
        runEvents.value = mergeAgentRunEvent(runEvents.value, event)
      },
      async onTerminal() {
        if (progressAgentRunKey.value !== agentRunKey) return
        const detail = await api.getAgentRun(agentRunKey)
        progressAgentRunDetail.value = detail
        progressAgentRuns.value = progressAgentRuns.value.map(run => run.run_key === agentRunKey ? detail : run)
        streamedAgentRunKey = ''
        await refreshProgressSubagentDetails(agentRunKey)
      },
      async onResyncRequired() {
        if (progressAgentRunKey.value !== agentRunKey) return
        await Promise.all([
          loadProgressAgentEvents({ quiet: true }),
          loadProgressAgentRunDetail({ quiet: true }),
        ])
        streamedAgentRunKey = ''
        ensureProgressAgentEventStream()
      },
    })
  }

  async function selectProgressAgentRun(agentRunKey: string) {
    if (agentRunKey === progressAgentRunKey.value) return
    setProgressAgentRunKey(agentRunKey)
    await Promise.all([loadProgressAgentEvents(), loadProgressAgentRunDetail()])
  }

  function progressSubagentDetail(taskIdStr: string) {
    return progressSubagentDetailState.detailFor(progressAgentRunKey.value, taskIdStr)
  }

  function progressSubagentDetailLoading(taskIdStr: string) {
    return progressSubagentDetailState.isLoading(progressAgentRunKey.value, taskIdStr)
  }

  function progressSubagentDetailError(taskIdStr: string) {
    return progressSubagentDetailState.errorFor(progressAgentRunKey.value, taskIdStr)
  }

  async function ensureProgressSubagentDetail(taskIdStr: string) {
    await progressSubagentDetailState.ensure(progressAgentRunKey.value, taskIdStr)
  }

  async function loadProgressArtifacts() {
    const runId = progressRunId.value
    const workflowKey = progressWorkflowKey.value
    if (!runId || !workflowKey) return
    if (progressRunArtifacts.value[runId]) return
    progressArtifactsLoading.value = true
    try {
      const params = {
        workflow_key: workflowKey,
        run_id: runId,
        // 编排收尾失败时，当前 run 仍可能有有效的部分产物；精确 run_id
        // 会把查询范围限制在当前 run 内。
        include_history: true,
        full: false,
        format: 'all',
        limit: 20,
      }
      const result = await queryClient.fetchQuery({
        queryKey: queryKeys.workflowArtifacts(params),
        queryFn: ({ signal }) => api.searchWorkflowArtifacts(params, { signal }),
      })
      progressRunArtifacts.value = { ...progressRunArtifacts.value, [runId]: result.items }
    } catch (e: unknown) {
      setArtifactError(errorMessage(e))
      progressRunArtifacts.value = { ...progressRunArtifacts.value, [runId]: [] }
    } finally {
      progressArtifactsLoading.value = false
    }
  }

  async function openProgressArtifactDetail(artifact: WorkflowArtifact) {
    try {
      const profileKey = selectedWorkflow.value?.profile_key || formProfileKey() || undefined
      const detail = await queryClient.fetchQuery({
        queryKey: queryKeys.workflowArtifact(artifact.artifact_id, profileKey),
        queryFn: ({ signal }) => api.getWorkflowArtifact(artifact.artifact_id, profileKey, { signal }),
      })
      openArtifactFullscreen(detail)
    } catch (e: unknown) {
      setArtifactError(errorMessage(e))
    }
  }

  async function openProgressArtifact() {
    await loadProgressArtifacts()
    const artifact = progressArtifacts.value.find(item => item.format === 'markdown') || progressArtifacts.value[0]
    if (!artifact) {
      await alert({ title: '暂无产物', description: '本次运行没有可查看的报告产物。' })
      return
    }
    await openProgressArtifactDetail(artifact)
  }

  async function openProgressHtmlReport() {
    await loadProgressArtifacts()
    const artifact = progressArtifacts.value.find(item => item.format === 'html')
    if (!artifact) {
      await alert({ title: '暂无 HTML 报告', description: '本次运行没有生成 HTML 报告，或报告仍在生成中。' })
      return
    }
    await openProgressArtifactDetail(artifact)
  }

  function stopTestPolling() {
    if (testPoll) {
      clearInterval(testPoll)
      testPoll = null
    }
  }

  async function pollTestRun() {
    const runId = testingRunId.value
    if (!runId) return
    try {
      const run = await fetchWorkflowRun(runId, { fresh: true })
      progressRunDetail.value = run
      mergeWorkflowRun(run)
      // Refresh agent runs list (to pick up html reporter when it starts) and
      // refresh its detail. The selected agent timeline is updated by SSE.
      await loadProgressAgentRuns({ quiet: true })
      if (['completed', 'no_task', 'failed', 'stopped'].includes(run.status)) {
        stopTestPolling()
        testing.value = false
        testingRunId.value = ''
        const workflowKey = progressWorkflowKey.value || run.workflow_key
        await loadRuns(workflowKey)
        if (run.status === 'completed' || run.status === 'no_task') {
          await queryClient.invalidateQueries({ queryKey: ['workflow-artifacts'] })
          await searchArtifacts()
        }
      }
    } catch {
      // transient poll error: keep polling
    }
  }

  function startTestPolling(runId: string) {
    testing.value = true
    testingRunId.value = runId
    stopTestPolling()
    testPoll = setInterval(pollTestRun, 1500)
  }

  async function runWorkflow(item: WorkflowDefinition, input: Record<string, unknown> = {}) {
    const wf = item
    if (!wf || testing.value) return
    testError.value = ''
    testing.value = true
    try {
      const res = await api.runWorkflow(wf.workflow_key, input)
      // The API normalizes the scheduler response to `run_status`; a run id is
      // the stable signal that the progress page can load and poll.
      if (res.run_id) {
        await queryClient.invalidateQueries({ queryKey: ['workflow-runs'] })
        testingRunId.value = res.run_id
        progressWorkflowKey.value = wf.workflow_key
        progressRunId.value = res.run_id
        setSelectedKey(wf.workflow_key)
        selectedRunId.value = res.run_id
        setProgressAgentRunKey('')
        void navigateTo(`workflow/${wf.workflow_key}/progress/${res.run_id}`)
        await loadRuns(wf.workflow_key)
        await loadProgressAgentRuns()
        await loadProgressAgentEvents()
        stopTestPolling()
        testPoll = setInterval(pollTestRun, 1500)
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
    void navigateTo(`workflow/${item.workflow_key}/progress/${run.run_id}`)
  }

  async function prepareProgress(item: WorkflowDefinition, runId?: string) {
    const run = runId ? (workflowRuns.value[item.workflow_key] || []).find(r => r.run_id === runId) : runningRunFor(item.workflow_key)
    if (!run) return
    setSelectedKey(item.workflow_key)
    progressWorkflowKey.value = item.workflow_key
    progressRunId.value = run.run_id
    selectedRunId.value = run.run_id
    setProgressAgentRunKey('')
    progressRunDetail.value = await fetchWorkflowRun(run.run_id)
    await loadProgressAgentRuns()
    await loadProgressAgentEvents()
    if (run.status === 'running') {
      startTestPolling(run.run_id)
    }
  }

  async function refreshProgress() {
    if (progressWorkflowKey.value) {
      await loadRuns(progressWorkflowKey.value, { preserveSelectedRun: true })
    }
    await loadProgressAgentRuns()
    await loadProgressAgentEvents()
    if (progressRunId.value) {
      const detail = await fetchWorkflowRun(progressRunId.value, { fresh: true })
      progressRunDetail.value = detail
      mergeWorkflowRun(detail)
      selectedRunId.value = progressRunId.value
    }
  }

  async function openScriptRun(runId: string) {
    const returnTo = currentHash()
    try {
      const scriptRun = await api.getScriptRun(runId)
      void navigateTo(buildScriptRunHash(scriptRun.script_key, runId, returnTo))
    } catch (e: unknown) {
      testError.value = errorMessage(e)
    }
  }

  function openAgentRun(runKey: string) {
    void navigateTo(buildAgentRunHash(runKey, currentHash()))
  }

  /** Resolve the progress run id from the route and load the progress view.
   *  Returns the resolved run id (or '') so the host route dispatcher can chain
   *  any further route-specific work (e.g. starting the poller). */
  async function applyProgressRoute(workflow: WorkflowDefinition) {
    const runId = routeParts.value[2] || runningRunFor(workflow.workflow_key)?.run_id || ''
    progressWorkflowKey.value = workflow.workflow_key
    progressRunId.value = runId
    selectedRunId.value = runId
    setProgressAgentRunKey('')
    await loadRuns(workflow.workflow_key, { preserveSelectedRun: true })
    if (runId) {
      const detail = await fetchWorkflowRun(runId)
      progressRunDetail.value = detail
      mergeWorkflowRun(detail)
      selectedRunId.value = runId
      await loadProgressAgentRuns()
      await loadProgressAgentEvents()
    }
    const run = (workflowRuns.value[workflow.workflow_key] || []).find(item => item.run_id === runId)
    if (run?.status === 'running') {
      startTestPolling(run.run_id)
    }
  }

  return {
    // state
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
    // computed
    progressWorkflow,
    progressRun,
    progressArtifacts,
    progressFinished,
    // methods
    applyRunOverviews,
    loadRunOverviews,
    mergeWorkflowRun,
    loadRuns,
    runningRunFor,
    loadLogs,
    selectRun,
    loadProgressAgentRuns,
    loadProgressAgentEvents,
    loadProgressAgentRunDetail,
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
  }
}
