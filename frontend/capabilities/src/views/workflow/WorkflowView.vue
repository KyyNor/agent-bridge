<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { api } from '../../api/client'
import type { ProjectProfile, ArtifactTreeNode, WorkflowArtifact, WorkflowArtifactDetail, WorkflowArtifactHistoryVersion, WorkflowDefinition, WorkflowRun, WorkflowRunEvent, WorkflowRunLog } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'

const artifactToolName = 'artifacts_search'

const workflows = ref<WorkflowDefinition[]>([])
const profiles = ref<ProjectProfile[]>([])
const artifacts = ref<WorkflowArtifact[]>([])
const selectedKey = ref('')
const loading = ref(true)
const artifactLoading = ref(false)
const error = ref('')
const artifactError = ref('')
const showEditor = ref(false)
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
const showDetail = ref(false)
const showProgress = ref(false)
const progressWorkflowKey = ref('')
const progressRunId = ref('')
const workflowRuns = ref<Record<string, WorkflowRun[]>>({})
const runsLoading = ref(false)
const selectedRunId = ref('')
const runEvents = ref<WorkflowRunEvent[]>([])
const runLogs = ref<WorkflowRunLog[]>([])
const logsLoading = ref(false)
const testing = ref(false)
const testingRunId = ref('')
const testError = ref('')
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
  manifestText: '{\n  "name": "Page Report",\n  "nodes": [],\n  "edges": [],\n  "schemas": {}\n}',
})

const selectedWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === selectedKey.value) || workflows.value[0] || null
)

const manifestNodes = computed(() => selectedWorkflow.value?.manifest?.nodes || [])
const manifestEdges = computed(() => selectedWorkflow.value?.manifest?.edges || [])
const manifestSchemas = computed(() => selectedWorkflow.value?.manifest?.schemas || {})
const selectedProfileName = computed(() => profileName(selectedWorkflow.value?.profile_key || ''))
const runs = computed(() => workflowRuns.value[selectedWorkflow.value?.workflow_key || ''] || [])
const hasAnyRunningRun = computed(() =>
  Object.values(workflowRuns.value).some(items => items.some(run => run.status === 'running')),
)
const progressWorkflow = computed(() =>
  workflows.value.find(item => item.workflow_key === progressWorkflowKey.value) || selectedWorkflow.value
)
const progressRun = computed(() =>
  (workflowRuns.value[progressWorkflowKey.value] || []).find(run => run.run_id === progressRunId.value) || null,
)

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
})

watch(selectedKey, () => {
  testError.value = ''
})

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
      items.map(async item => [item.workflow_key, await api.listWorkflowRuns(item.workflow_key, 20)] as const),
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
  form.value = {
    workflow_key: '',
    name: '',
    description: '',
    profile_key: profiles.value[0]?.profile_key || '',
    status: 'active',
    workflow_js: '',
    manifestText: '{\n  "name": "Page Report",\n  "nodes": [],\n  "edges": [],\n  "schemas": {}\n}',
  }
  formError.value = ''
  showEditor.value = true
}

function openEdit(item: WorkflowDefinition) {
  form.value = {
    workflow_key: item.workflow_key,
    name: item.name,
    description: item.description,
    profile_key: item.profile_key,
    status: item.status,
    workflow_js: item.workflow_js,
    manifestText: JSON.stringify(item.manifest, null, 2),
  }
  formError.value = ''
  showEditor.value = true
}

async function saveWorkflow() {
  formError.value = ''
  if (!form.value.workflow_key || !form.value.name || !form.value.profile_key) {
    formError.value = '请填写工作流标识、名称，并选择关联的能力平面'
    return
  }
  let manifest: Record<string, unknown>
  try {
    manifest = JSON.parse(form.value.manifestText)
  } catch {
    formError.value = 'Manifest 不是合法 JSON'
    return
  }
  if (!isWorkflowManifest(manifest)) {
    formError.value = 'Manifest 必须包含 name、nodes、edges、schemas'
    return
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
      manifest,
    })
    selectedKey.value = saved.workflow_key
    showEditor.value = false
    workflows.value = await api.listWorkflows()
    await loadRunsForWorkflows()
    if (showDetail.value) await searchArtifacts()
  } catch (e: unknown) {
    formError.value = errorMessage(e)
  } finally {
    saving.value = false
  }
}

async function openDetail(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  showDetail.value = true
  await Promise.all([searchArtifacts(), loadRuns(item.workflow_key)])
}

function isWorkflowManifest(value: Record<string, unknown>): value is WorkflowDefinition['manifest'] {
  return typeof value.name === 'string'
    && Array.isArray(value.nodes)
    && Array.isArray(value.edges)
    && typeof value.schemas === 'object'
    && value.schemas !== null
}

function profileName(profileKey: string) {
  const profile = profiles.value.find(item => item.profile_key === profileKey)
  return profile ? `${profile.name} / ${profile.profile_key}` : profileKey
}

function nodeTitle(node: Record<string, unknown>) {
  return String(node.label || node.name || node.id || 'node')
}

function edgeTitle(edge: Record<string, unknown>) {
  const from = String(edge.from || edge.source || '')
  const to = String(edge.to || edge.target || '')
  return from && to ? `${from} -> ${to}` : JSON.stringify(edge)
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

function logLevelClass(level: string) {
  if (level === 'error') return 'border-red-400'
  if (level === 'warning' || level === 'warn') return 'border-yellow-400'
  return 'border-border'
}

function eventKindLabel(event: WorkflowRunEvent) {
  if (event.kind === 'agent_message') return event.agent_name || 'agent'
  if (event.kind === 'tool_call') return '工具调用'
  if (event.kind === 'tool_result') return event.status === 'failed' ? '工具失败' : '工具完成'
  if (event.kind === 'result') return event.status === 'failed' ? '运行失败' : '运行结果'
  if (event.kind === 'error') return '异常'
  if (event.kind === 'status') return '状态'
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
    const nextRuns = await api.listWorkflowRuns(key, 20)
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

async function loadLogs() {
  if (!selectedRunId.value) {
    runLogs.value = []
    runEvents.value = []
    return
  }
  logsLoading.value = true
  try {
    const [logs, events] = await Promise.all([
      api.getWorkflowRunLogs(selectedRunId.value),
      api.getWorkflowRunEvents(selectedRunId.value),
    ])
    runLogs.value = logs
    runEvents.value = events
  } catch (e: unknown) {
    runLogs.value = []
    runEvents.value = []
  } finally {
    logsLoading.value = false
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
      showProgress.value = true
      selectedKey.value = wf.workflow_key
      selectedRunId.value = res.run_id
      await loadRuns(wf.workflow_key)
      await loadLogs()
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
  selectedKey.value = item.workflow_key
  progressWorkflowKey.value = item.workflow_key
  progressRunId.value = run.run_id
  selectedRunId.value = run.run_id
  showProgress.value = true
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
    await loadLogs()
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
    showDetail.value = false
    await loadAll()
  } catch (e: unknown) {
    error.value = errorMessage(e)
  }
}

async function deleteWorkflow(item: WorkflowDefinition) {
  selectedKey.value = item.workflow_key
  await deleteCurrent()
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-foreground">工作流管理</h2>
        <p class="text-sm text-muted-foreground">Claude Code 动态工作流、运行产物与能力平面绑定</p>
      </div>
      <Button @click="openCreate">新建工作流</Button>
    </div>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {{ error }}
    </div>

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
                :disabled="hasAnyRunningRun || item.status !== 'active'"
                @click="runWorkflow(item)"
              >
                运行
              </Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openEdit(item)">编辑</Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openDetail(item)">详情</Button>
              <Button variant="ghost" size="sm" class="h-8 text-xs text-destructive" @click="deleteWorkflow(item)">删除</Button>
            </div>
          </div>
        </div>
        <div v-if="testError" class="border-t px-4 py-3 text-xs text-destructive">{{ testError }}</div>
      </CardContent>
    </Card>

    <Dialog v-model:open="showDetail">
      <DialogContent class="w-[96vw] max-w-[1200px] sm:max-w-[1200px]">
        <DialogHeader>
          <DialogTitle>{{ selectedWorkflow?.name || '工作流详情' }}</DialogTitle>
        </DialogHeader>
        <div v-if="selectedWorkflow" class="max-h-[78vh] space-y-5 overflow-auto pr-1">
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
                :disabled="hasAnyRunningRun || selectedWorkflow.status !== 'active'"
                @click="runWorkflow(selectedWorkflow)"
              >
                运行
              </Button>
              <Button variant="outline" size="sm" @click="openEdit(selectedWorkflow)">编辑</Button>
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

          <div class="grid gap-4 lg:grid-cols-2">
            <section class="rounded-md border p-4">
              <div class="mb-3 flex items-center justify-between">
                <h3 class="text-sm font-semibold">流程节点</h3>
                <Badge variant="outline">{{ manifestNodes.length }}</Badge>
              </div>
              <div class="space-y-2">
                <div v-for="node in manifestNodes" :key="String(node.id || nodeTitle(node))" class="rounded-md border p-3">
                  <div class="text-sm font-medium">{{ nodeTitle(node) }}</div>
                  <pre class="mt-2 max-h-28 overflow-auto rounded bg-muted p-2 text-xs">{{ JSON.stringify(node, null, 2) }}</pre>
                </div>
                <div v-if="!manifestNodes.length" class="text-sm text-muted-foreground">暂无节点</div>
              </div>
            </section>

            <section class="space-y-4 rounded-md border p-4">
              <div>
                <div class="mb-3 flex items-center justify-between">
                  <h3 class="text-sm font-semibold">流转关系</h3>
                  <Badge variant="outline">{{ manifestEdges.length }}</Badge>
                </div>
                <div class="space-y-2">
                  <div v-for="edge in manifestEdges" :key="edgeTitle(edge)" class="rounded-md border px-3 py-2 text-sm">
                    {{ edgeTitle(edge) }}
                  </div>
                  <div v-if="!manifestEdges.length" class="text-sm text-muted-foreground">暂无流转关系</div>
                </div>
              </div>
              <div>
                <h3 class="mb-3 text-sm font-semibold">数据结构</h3>
                <pre class="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{{ JSON.stringify(manifestSchemas, null, 2) }}</pre>
              </div>
            </section>
          </div>

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
        <DialogFooter>
          <Button variant="outline" @click="showDetail = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="showProgress">
      <DialogContent class="w-[96vw] max-w-[1100px] sm:max-w-[1100px]">
        <DialogHeader>
          <DialogTitle>{{ progressWorkflow?.name || '运行进度' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[78vh] space-y-4 overflow-auto pr-1">
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
        <DialogFooter>
          <Button variant="outline" @click="showProgress = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="showEditor">
      <DialogContent class="w-[96vw] max-w-[1400px] sm:max-w-[1400px]">
        <DialogHeader>
          <DialogTitle>{{ form.workflow_key ? '编辑工作流' : '新建工作流' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[78vh] space-y-5 overflow-auto pr-1">
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

          <div class="grid gap-4 xl:grid-cols-2">
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">工作流结构定义</label>
              <textarea v-model="form.manifestText" class="min-h-[34rem] w-full rounded-md border bg-background p-3 font-mono text-xs" />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">Claude Code 工作流</label>
              <textarea v-model="form.workflow_js" class="min-h-[34rem] w-full rounded-md border bg-background p-3 font-mono text-xs" />
            </div>
          </div>

          <div class="rounded-md border bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
            <div class="font-medium text-foreground">输出验收要求</div>
            <div>Workflow 必须在运行目录写入 <span class="font-mono">out/result.json</span>。</div>
            <div class="mt-2 font-mono">
              {"status":"completed","task_key":"page:a","task_version":"sha256:...","artifacts":[{"title":"...","path":"reports/a.md","tags":[],"format":"markdown","file":"out/artifacts/a.md","summary":"..."}]}
            </div>
            <div class="mt-2">没有可执行任务时输出 <span class="font-mono">{"status":"no_executable_task","reason":"..."}</span>。如任务带 task_version，result.json 必须原样写回。artifact 文件必须在运行目录内，当前只接受 Markdown。</div>
          </div>
        </div>
        <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {{ formError }}
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showEditor = false">取消</Button>
          <Button :disabled="saving" @click="saveWorkflow">{{ saving ? '保存中' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

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
