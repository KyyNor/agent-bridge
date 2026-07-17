<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { Search, RotateCw, ArrowLeft } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { AgentRun, AgentRunCounts, WorkflowSubagentDetail } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import RunEventTimeline from '../../components/RunEventTimeline.vue'
import SubagentDetailPanel from '../../components/SubagentDetailPanel.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import {
  agentRunBadgeVariant,
  agentRunOkFilterParam,
  agentRunStatusFilterParam,
  agentRunStatusLabel,
  type AgentRunFilter,
} from '../../lib/agentRunStatus'
import { countAgentRunTabs } from '../../lib/filterTabs'
import { LOG_PAGE_SIZE_OPTIONS } from '../../lib/pagination'
import type { WorkflowRunEvent } from '../../api/types'

const props = defineProps<{ routeKey?: string }>()

const runs = ref<AgentRun[]>([])
const runTotal = ref(0)
const runCounts = ref<AgentRunCounts>({ all: 0, success: 0, failed: 0, running: 0, stopped: 0 })
const loading = ref(false)
const okFilter = ref<AgentRunFilter>('')
const search = ref('')
const page = ref(1)
const pageSize = ref(50)

// Detail (sub-route) state. When routeKey is set we show a detail panel instead
// of the list — enabling deep links (#agent-runs/{runKey}) aligned with how the
// workflow view handles its sub-routes.
const detailRun = ref<AgentRun | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const detailEvents = ref<WorkflowRunEvent[]>([])
let detailEventsPoll: ReturnType<typeof setInterval> | null = null
let listRequestToken = 0
let searchDebounce: ReturnType<typeof setTimeout> | null = null

// Sub-agent transcript detail (lazy-loaded when a sub-agent is expanded).
const subagentDetails = ref<Record<string, WorkflowSubagentDetail>>({})
const subagentDetailLoading = ref<Set<string>>(new Set())
const subagentDetailErrors = ref<Record<string, string>>({})

function subagentDetailKey(taskId: string) {
  return `${activeRunKey.value}:${taskId}`
}

async function ensureSubagentDetail(taskId: string) {
  const runKey = activeRunKey.value
  if (!runKey) return
  const key = subagentDetailKey(taskId)
  if (subagentDetails.value[key] || subagentDetailLoading.value.has(key)) return
  const loading = new Set(subagentDetailLoading.value)
  loading.add(key)
  subagentDetailLoading.value = loading
  const nextErrors = { ...subagentDetailErrors.value }
  delete nextErrors[key]
  subagentDetailErrors.value = nextErrors
  try {
    const detail = await api.getAgentRunSubagentDetail(runKey, taskId)
    subagentDetails.value = { ...subagentDetails.value, [key]: detail }
  } catch (e: unknown) {
    subagentDetailErrors.value = { ...subagentDetailErrors.value, [key]: e instanceof Error ? e.message : '加载失败' }
  } finally {
    const done = new Set(subagentDetailLoading.value)
    done.delete(key)
    subagentDetailLoading.value = done
  }
}

function subagentDetail(taskId: string) {
  return subagentDetails.value[subagentDetailKey(taskId)] || null
}
function subagentDetailLoadingFor(taskId: string) {
  return subagentDetailLoading.value.has(subagentDetailKey(taskId))
}
function subagentDetailErrorFor(taskId: string) {
  return subagentDetailErrors.value[subagentDetailKey(taskId)] || ''
}

/** The run key extracted from the sub-route (e.g. "agent-runs/<runKey>"). */
const activeRunKey = computed(() => props.routeKey || '')

function formatDate(d: Date) {
  return d.toISOString().slice(0, 10)
}
const dateFrom = ref(formatDate(new Date(Date.now() - 86400000 * 3)))
const dateTo = ref(formatDate(new Date()))

onMounted(() => {
  loadRunData()
  if (activeRunKey.value) loadDetail(activeRunKey.value)
})

onUnmounted(() => stopDetailEventsPolling())

function baseRunParams(): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
  if (dateFrom.value) params.created_from = `${dateFrom.value} 00:00:00`
  if (dateTo.value) params.created_to = `${dateTo.value} 23:59:59`
  if (search.value.trim()) params.search = search.value.trim()
  return params
}

function addActiveRunFilter(params: Record<string, string | number | boolean>) {
  const okParam = agentRunOkFilterParam(okFilter.value)
  const statusParam = agentRunStatusFilterParam(okFilter.value)
  if (okParam != null) params.ok = okParam
  if (statusParam) params.status = statusParam
  return params
}

async function loadRunData() {
  const token = ++listRequestToken
  loading.value = true
  const params = baseRunParams()
  const activeParams = addActiveRunFilter({ ...params })
  try {
    const [basePage, activePage] = await Promise.all([
      api.listAgentRunsPage(params),
      api.listAgentRunsPage(activeParams),
    ])
    if (token !== listRequestToken) return
    runCounts.value = basePage.counts
    runs.value = activePage.items
    runTotal.value = activePage.total
  } catch {
    if (token !== listRequestToken) return
    runCounts.value = { all: 0, success: 0, failed: 0, running: 0, stopped: 0 }
    runs.value = []
    runTotal.value = 0
  } finally {
    if (token === listRequestToken) loading.value = false
  }
}

function applyOkFilter(key: string) {
  okFilter.value = key as AgentRunFilter
  const wasFirstPage = page.value === 1
  page.value = 1
  if (wasFirstPage) loadRunData()
}

function applyDateFilter() {
  const wasFirstPage = page.value === 1
  page.value = 1
  if (wasFirstPage) loadRunData()
}

function scheduleSearch() {
  page.value = 1
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => loadRunData(), 250)
}

watch([page, pageSize], () => {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => loadRunData(), 0)
})

onUnmounted(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})

/** Open a run's detail as a sub-route (deep-linkable). */
function openDetail(run: AgentRun) {
  detailRun.value = run
  detailError.value = ''
  window.location.hash = `agent-runs/${run.run_key}`
}

function backToList() {
  detailRun.value = null
  detailError.value = ''
  detailEvents.value = []
  stopDetailEventsPolling()
  window.location.hash = 'agent-runs'
}

async function loadDetail(runKey: string) {
  detailLoading.value = true
  detailError.value = ''
  stopDetailEventsPolling()
  try {
    detailRun.value = await api.getAgentRun(runKey)
    await loadDetailEvents(runKey)
    if (detailRun.value?.status === 'running') {
      detailEventsPoll = setInterval(() => loadDetailEvents(runKey, { quiet: true }), 1500)
    }
  } catch (e: unknown) {
    detailRun.value = null
    detailEvents.value = []
    detailError.value = e instanceof Error ? e.message : '加载失败'
  }
  detailLoading.value = false
}

async function loadDetailEvents(runKey: string, options: { quiet?: boolean } = {}) {
  try {
    detailEvents.value = await api.getAgentRunEvents(runKey)
    if (options.quiet) await refreshLoadedSubagentDetails(runKey)
    if (detailRun.value?.run_key === runKey && detailRun.value.status === 'running') {
      const refreshed = await api.getAgentRun(runKey)
      detailRun.value = refreshed
      if (refreshed.status !== 'running') stopDetailEventsPolling()
    }
  } catch (e: unknown) {
    if (!options.quiet) {
      detailEvents.value = []
      detailError.value = e instanceof Error ? e.message : '事件流加载失败'
    }
  }
}

async function refreshLoadedSubagentDetails(runKey: string) {
  const taskIds = Object.keys(subagentDetails.value)
    .filter(key => key.startsWith(`${runKey}:`))
    .map(key => key.slice(runKey.length + 1))
  if (!taskIds.length) return
  const entries = await Promise.all(
    taskIds.map(async taskId => {
      try {
        return [subagentDetailKey(taskId), await api.getAgentRunSubagentDetail(runKey, taskId)] as const
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

function stopDetailEventsPolling() {
  if (detailEventsPoll) {
    clearInterval(detailEventsPoll)
    detailEventsPoll = null
  }
}

// React to sub-route changes (browser back/forward, manual hash edit).
watch(activeRunKey, (key) => {
  if (key) {
    // If the list already has a summary row, show it immediately while loading.
    if (!detailRun.value || detailRun.value.run_key !== key) {
      detailRun.value = runs.value.find(r => r.run_key === key) || null
    }
    loadDetail(key)
  } else {
    detailRun.value = null
    detailEvents.value = []
    detailError.value = ''
    stopDetailEventsPolling()
  }
})

const displayRuns = computed(() => runs.value)
const filterTabs = computed(() => countAgentRunTabs(runCounts.value))
const pagedRuns = computed(() => displayRuns.value)

function formatCost(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${Number(v).toFixed(4)}`
}

// agent run 的 variant → StatusBadge 语义状态
function runBadgeStatus(run: AgentRun): 'success' | 'error' | 'running' | 'disabled' {
  const v = agentRunBadgeVariant(run)
  if (v === 'success') return 'success'
  if (v === 'running') return 'running'
  if (v === 'stopped') return 'disabled'
  return 'error'
}

</script>

<template>
  <!-- Detail sub-route view -->
  <div v-if="activeRunKey" class="space-y-4">
    <div class="flex items-center gap-3">
      <Button variant="ghost" size="sm" class="h-8 px-2" @click="backToList">
        <ArrowLeft class="mr-1 h-4 w-4" />
        返回
      </Button>
      <div>
        <h2 class="text-lg font-semibold text-foreground">Agent 运行详情</h2>
        <p class="font-mono text-xs text-muted-foreground">{{ activeRunKey }}</p>
      </div>
    </div>

    <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
    <div v-else-if="detailError" class="rounded-lg border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive">
      {{ detailError }}
    </div>
    <div v-else-if="detailRun" class="space-y-4">
      <div class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div>
          <span class="text-muted-foreground">Agent</span>
          <div class="font-mono font-medium">{{ detailRun.agent_name }}</div>
        </div>
        <div>
          <span class="text-muted-foreground">状态</span>
          <div>
            <StatusBadge :status="runBadgeStatus(detailRun)" :label="agentRunStatusLabel(detailRun)" />
          </div>
        </div>
        <div>
          <span class="text-muted-foreground">Profile</span>
          <div class="font-medium">{{ detailRun.profile_key || '—' }}</div>
        </div>
        <div>
          <span class="text-muted-foreground">工作流</span>
          <div class="font-mono text-xs font-medium">
            {{ detailRun.workflow_key || '—' }}
            <span v-if="detailRun.workflow_run_id" class="text-muted-foreground">
              · {{ detailRun.workflow_run_id }}
            </span>
          </div>
        </div>
        <div>
          <span class="text-muted-foreground">耗时</span>
          <div class="tabular-nums font-medium">
            {{ detailRun.duration_ms != null ? `${detailRun.duration_ms}ms` : '—' }}
          </div>
        </div>
        <div>
          <span class="text-muted-foreground">Cost</span>
          <div class="tabular-nums font-medium">{{ formatCost(detailRun.cost_usd) }}</div>
        </div>
        <div>
          <span class="text-muted-foreground">轮数</span>
          <div class="tabular-nums font-medium">{{ detailRun.num_turns ?? '—' }}</div>
        </div>
        <div>
          <span class="text-muted-foreground">Session</span>
          <div class="font-mono text-xs font-medium">{{ detailRun.session_id || '—' }}</div>
        </div>
        <div class="col-span-2 sm:col-span-4">
          <span class="text-muted-foreground">工作目录</span>
          <div class="font-mono text-xs font-medium">{{ detailRun.cwd || '—' }}</div>
        </div>
      </div>

      <div
        v-if="detailRun.error"
        class="rounded-lg border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive"
      >
        {{ detailRun.error }}
      </div>

      <div v-if="detailRun.prompt">
        <div class="mb-1 text-xs font-medium text-muted-foreground">Prompt</div>
        <pre class="max-h-[180px] overflow-auto whitespace-pre-wrap rounded-lg bg-secondary px-4 py-3 text-xs">{{ detailRun.prompt }}</pre>
      </div>

      <div v-if="detailRun.result != null">
        <div class="mb-1 text-xs font-medium text-muted-foreground">结果</div>
        <JsonViewer :value="detailRun.result" max-height="240px" />
      </div>

      <div v-if="detailEvents.length">
        <div class="mb-1 text-xs font-medium text-muted-foreground">
          事件流（{{ detailEvents.length }}）
        </div>
        <RunEventTimeline
          :events="detailEvents"
          :context-key="detailRun.run_key"
          @expand="(taskId: string) => ensureSubagentDetail(taskId)"
        >
          <template #subagent-body="{ taskId }">
            <SubagentDetailPanel
              :detail="subagentDetail(taskId)"
              :loading="subagentDetailLoadingFor(taskId)"
              :error="subagentDetailErrorFor(taskId)"
            />
          </template>
        </RunEventTimeline>
      </div>
    </div>
  </div>

  <!-- List view -->
  <div v-else-if="loading && runs.length === 0" class="py-12 text-center text-sm text-muted-foreground">
    加载中...
  </div>
  <div v-else class="space-y-5">
    <!-- 页头操作：刷新进 #ph-actions（仅列表态） -->
    <Teleport v-if="!activeRunKey" to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="loadRunData">
        <RotateCw :size="14" />
        刷新
      </Button>
    </Teleport>

    <!-- 页头筛选：搜索 + 日期范围 + 状态分段进 #ph-filters（仅列表态） -->
    <Teleport v-if="!activeRunKey" to="#ph-filters" defer>
      <div class="relative w-full max-w-[280px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-placeholder" />
        <Input v-model="search" placeholder="搜索 Agent、Profile 或工作流..." class="h-9 pl-8" @update:model-value="scheduleSearch" />
      </div>
      <div class="flex items-center gap-2 text-sm">
        <Input v-model="dateFrom" type="date" class="h-9 w-[140px]" @change="applyDateFilter" />
        <span class="text-muted-foreground">至</span>
        <Input v-model="dateTo" type="date" class="h-9 w-[140px]" @change="applyDateFilter" />
      </div>
      <SegmentedTabs v-model="okFilter" :tabs="filterTabs" @update:model-value="applyOkFilter" />
    </Teleport>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="runTotal === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          暂无 Agent 运行记录
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">时间</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Agent</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Profile</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">工作流</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">耗时</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Cost</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">轮数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in pagedRuns"
              :key="r.run_key"
              class="cursor-pointer border-b border-border/60"
              @click="openDetail(r)"
            >
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                {{ formatLocalDatetime(r.created_at) }}
              </td>
              <td class="px-4 py-3 font-mono text-sm">{{ r.agent_name }}</td>
              <td class="px-4 py-3 text-sm">{{ r.profile_key || '—' }}</td>
              <td class="px-4 py-3 text-sm">
                <span v-if="r.workflow_key" class="font-mono text-xs">{{ r.workflow_key }}</span>
                <span v-else>—</span>
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">
                {{ r.duration_ms != null ? `${r.duration_ms}ms` : '—' }}
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">
                {{ formatCost(r.cost_usd) }}
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">
                {{ r.num_turns ?? '—' }}
              </td>
              <td class="px-4 py-3">
                <StatusBadge :status="runBadgeStatus(r)" :label="agentRunStatusLabel(r)" />
              </td>
              <td class="px-4 py-3 text-right">
                <Button variant="ghost" size="sm" class="h-8 text-xs" @click.stop="openDetail(r)">详情</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <PaginationBar
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="runTotal"
      :page-size-options="LOG_PAGE_SIZE_OPTIONS"
    />
  </div>
</template>
