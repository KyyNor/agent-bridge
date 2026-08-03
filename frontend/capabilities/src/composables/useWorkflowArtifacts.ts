import { computed, ref, watch } from 'vue'
import { api } from '../api/client'
import type { WorkflowArtifact, WorkflowArtifactDetail, WorkflowArtifactHistoryVersion } from '../api/types'
import { buildArtifactTree, flattenArtifactTree } from '../lib/workflowArtifactTree'
import type { ArtifactFormat } from '../lib/workflowArtifactFormats'
import { renderMarkdown } from '../lib/markdown'
import { queryClient, queryKeys } from '../lib/query'

type ArtifactContext = {
  profileKey?: string
  workflowKey?: string
}

export type FullscreenArtifact = {
  title: string
  path: string
  summary: string
  tags: string[]
  content: string
  format: string
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

function isCancelled(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
    || typeof error === 'object' && error !== null && 'name' in error && error.name === 'CancelledError'
}

/** 工作流产物的检索、详情、历史和全屏预览状态。 */
export function useWorkflowArtifacts(getContext: () => ArtifactContext) {
  const artifacts = ref<WorkflowArtifact[]>([])
  const artifactLoading = ref(false)
  const artifactTotal = ref(0)
  const artifactPage = ref(1)
  const artifactPageSize = ref(50)
  const artifactError = ref('')
  const artifactQuery = ref('')
  const artifactPathMatch = ref('')
  const artifactFormat = ref<ArtifactFormat>('all')
  const artifactDetail = ref<WorkflowArtifactDetail | null>(null)
  const artifactHistory = ref<WorkflowArtifactHistoryVersion[]>([])
  const artifactHistoryTarget = ref<WorkflowArtifact | null>(null)
  const detailLoading = ref(false)
  const historyLoading = ref(false)
  const showArtifact = ref(false)
  const showArtifactHistory = ref(false)
  const fullscreenArtifact = ref<FullscreenArtifact | null>(null)
  const collapsedPaths = ref<Set<string>>(new Set())

  const artifactHtml = computed(() =>
    artifactDetail.value ? renderMarkdown(artifactDetail.value.content) : '',
  )
  const fullscreenArtifactHtml = computed(() =>
    fullscreenArtifact.value ? renderMarkdown(fullscreenArtifact.value.content) : '',
  )
  const recentArtifacts = computed(() => artifacts.value.slice(0, 3))
  const humanReadableArtifactCount = computed(() => artifacts.value.filter(item => item.format === 'html').length)
  const artifactTree = computed(() => buildArtifactTree(artifacts.value))
  const artifactRows = computed(() => flattenArtifactTree(artifactTree.value, collapsedPaths.value))

  function context() {
    return getContext()
  }

  function openArtifactFullscreen(artifact: WorkflowArtifact | WorkflowArtifactDetail) {
    showArtifact.value = false
    fullscreenArtifact.value = {
      title: artifact.title,
      path: artifact.path,
      summary: artifact.summary,
      tags: artifact.tags,
      content: artifact.content || '',
      format: artifact.format,
    }
  }

  function closeArtifactFullscreen() {
    fullscreenArtifact.value = null
  }

  function togglePath(path: string) {
    const next = new Set(collapsedPaths.value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    collapsedPaths.value = next
  }

  async function fetchArtifacts(params: {
    profile_key?: string
    workflow_key?: string
    query?: string
    path_match?: string
    format?: string
    limit?: number
    offset?: number
  }, options: { force?: boolean } = {}) {
    await queryClient.cancelQueries({ queryKey: ['workflow-artifacts', 'list'] })
    if (options.force) await queryClient.invalidateQueries({ queryKey: queryKeys.workflowArtifacts(params) })
    artifactLoading.value = true
    artifactError.value = ''
    try {
      const result = await queryClient.fetchQuery({
        queryKey: queryKeys.workflowArtifacts(params),
        queryFn: ({ signal }) => api.searchWorkflowArtifacts(params, { signal }),
      })
      artifacts.value = result.items
      artifactTotal.value = result.total ?? result.items.length
    } catch (error: unknown) {
      if (isCancelled(error)) return
      artifactError.value = errorMessage(error)
      artifactTotal.value = 0
    } finally {
      artifactLoading.value = false
    }
  }

  async function loadRecentArtifacts() {
    const { profileKey, workflowKey } = context()
    await fetchArtifacts({
      profile_key: profileKey,
      workflow_key: workflowKey,
      format: 'all',
      limit: 3,
      offset: 0,
    })
  }

  async function searchArtifacts() {
    const { profileKey, workflowKey } = context()
    await fetchArtifacts({
      profile_key: profileKey,
      workflow_key: workflowKey,
      query: artifactQuery.value || undefined,
      path_match: artifactPathMatch.value || undefined,
      format: artifactFormat.value,
      limit: artifactPageSize.value,
      offset: (artifactPage.value - 1) * artifactPageSize.value,
    }, { force: true })
  }

  function resetArtifactPage() {
    artifactPage.value = 1
  }

  async function setArtifactFormat(format: ArtifactFormat) {
    artifactFormat.value = format
    resetArtifactPage()
    await searchArtifacts()
  }

  async function openArtifact(item: WorkflowArtifact) {
    detailLoading.value = true
    showArtifact.value = true
    artifactDetail.value = null
    fullscreenArtifact.value = null
    try {
      artifactDetail.value = await queryClient.fetchQuery({
        queryKey: queryKeys.workflowArtifact(item.artifact_id, context().profileKey),
        queryFn: ({ signal }) => api.getWorkflowArtifact(item.artifact_id, context().profileKey, { signal }),
      })
    } catch (error: unknown) {
      artifactDetail.value = null
      showArtifact.value = false
      artifactError.value = errorMessage(error)
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
      const params = {
        profile_key: context().profileKey,
        workflow_key: item.workflow_key,
        task_key: item.task_key,
        limit: 20,
      }
      const result = await queryClient.fetchQuery({
        queryKey: queryKeys.workflowArtifactHistory(params),
        queryFn: ({ signal }) => api.getWorkflowArtifactHistory(params, { signal }),
      })
      artifactHistory.value = result.versions
    } catch (error: unknown) {
      artifactHistory.value = []
      showArtifactHistory.value = false
      artifactError.value = errorMessage(error)
    } finally {
      historyLoading.value = false
    }
  }

  function clearArtifacts() {
    void queryClient.cancelQueries({ queryKey: ['workflow-artifacts'] })
    artifacts.value = []
    artifactDetail.value = null
    artifactHistory.value = []
    fullscreenArtifact.value = null
  }

  watch([artifactPage, artifactPageSize], () => {
    if (context().workflowKey) void searchArtifacts()
  })

  return {
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
    clearArtifacts,
  }
}
