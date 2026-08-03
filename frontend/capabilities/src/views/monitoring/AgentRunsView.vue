<script setup lang="ts">
import { onUnmounted, ref, computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { Search, RotateCw, ArrowLeft } from '@lucide/vue'
import { api } from '../../api/client'
import type { AgentRun, AgentRunCounts, WorkflowRunEvent } from '../../api/types'
import { formatLocalDatetime, formatDuration } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import RunEventTimeline from '../../components/RunEventTimeline.vue'
import SubagentDetailPanel from '../../components/SubagentDetailPanel.vue'
import AgentRunExecutionPanel from '../../components/AgentRunExecutionPanel.vue'
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
import { buildAgentRunHash, navigateTo, parseSubRoute, routeReturnTo } from '../../lib/navigation'
import { useSubagentDetails } from '../../composables/useSubagentDetails'
import { queryKeys } from '../../lib/query'

const props = defineProps<{ routeKey?: string }>()

const okFilter = ref<AgentRunFilter>('')
const search = ref('')
const debouncedSearch = ref('')
const page = ref(1)
const pageSize = ref(50)

// Detail (sub-route) state. When routeKey is set we show a detail panel instead
// of the list — enabling deep links (#agent-runs/{runKey}) aligned with how the
// workflow view handles its sub-routes.
const selectedRun = ref<AgentRun | null>(null)
const payloads = ref<Record<string, string>>({})
const payloadErrors = ref<Record<string, string>>({})
let searchDebounce: ReturnType<typeof setTimeout> | null = null

// 子 Agent 详情仅在展开时加载；状态管理与 Workflow 页面共用。
const subagentDetailState = useSubagentDetails(
  (runKey, taskId) => api.getAgentRunSubagentDetail(runKey, taskId),
)

async function ensureSubagentDetail(taskId: string) {
  await subagentDetailState.ensure(activeRunKey.value, taskId)
}

function subagentDetail(taskId: string) {
  return subagentDetailState.detailFor(activeRunKey.value, taskId)
}
function subagentDetailLoadingFor(taskId: string) {
  return subagentDetailState.isLoading(activeRunKey.value, taskId)
}
function subagentDetailErrorFor(taskId: string) {
  return subagentDetailState.errorFor(activeRunKey.value, taskId)
}

/** The run key extracted from the sub-route (e.g. "agent-runs/<runKey>"). */
const activeRunKey = computed(() => parseSubRoute(props.routeKey || '').segments[0] || '')
const returnToRoute = computed(() => routeReturnTo(props.routeKey || ''))

function formatDate(d: Date) {
  return d.toISOString().slice(0, 10)
}
const dateFrom = ref(formatDate(new Date(Date.now() - 86400000 * 3)))
const dateTo = ref(formatDate(new Date()))

function baseRunParams(): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
  if (dateFrom.value) params.created_from = `${dateFrom.value} 00:00:00`
  if (dateTo.value) params.created_to = `${dateTo.value} 23:59:59`
  if (debouncedSearch.value) params.search = debouncedSearch.value
  return params
}

function addActiveRunFilter(params: Record<string, string | number | boolean>) {
  const okParam = agentRunOkFilterParam(okFilter.value)
  const statusParam = agentRunStatusFilterParam(okFilter.value)
  if (okParam != null) params.ok = okParam
  if (statusParam) params.status = statusParam
  return params
}

const runParams = computed(() => {
  const params = baseRunParams()
  addActiveRunFilter(params)
  return params
})

const runListQuery = useQuery({
  queryKey: computed(() => queryKeys.agentRuns(runParams.value)),
  queryFn: ({ signal }) => api.listAgentRunsPage(runParams.value, { signal }),
})

const detailQuery = useQuery({
  queryKey: computed(() => queryKeys.agentRun(activeRunKey.value)),
  queryFn: ({ signal }) => api.getAgentRun(activeRunKey.value, { signal }),
  enabled: computed(() => Boolean(activeRunKey.value)),
  refetchInterval: query => query.state.data?.status === 'running' ? 1_500 : false,
})

const detailEventsQuery = useQuery({
  queryKey: computed(() => queryKeys.agentRunEvents(activeRunKey.value)),
  queryFn: ({ signal }) => api.getAgentRunEvents(activeRunKey.value, { signal }),
  enabled: computed(() => Boolean(activeRunKey.value)),
  refetchInterval: computed(() => detailQuery.data.value?.status === 'running' ? 1_500 : false),
})

const emptyCounts: AgentRunCounts = { all: 0, success: 0, failed: 0, running: 0, stopped: 0 }
const runs = computed(() => runListQuery.data.value?.items || [])
const runTotal = computed(() => runListQuery.data.value?.total || 0)
const runCounts = computed(() => runListQuery.data.value?.counts || emptyCounts)
const loading = computed(() => runListQuery.isLoading.value)
const detailRun = computed(() => detailQuery.data.value || selectedRun.value)
const detailLoading = computed(() => detailQuery.isLoading.value)
const detailEvents = computed<WorkflowRunEvent[]>(() => detailEventsQuery.data.value || [])
const detailError = computed(() => {
  const error = detailQuery.error.value || detailEventsQuery.error.value
  return error instanceof Error ? error.message : error ? '运行详情加载失败' : ''
})

function applyOkFilter(key: string) {
  okFilter.value = key as AgentRunFilter
  page.value = 1
}

function applyDateFilter() {
  page.value = 1
}

function scheduleSearch() {
  page.value = 1
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => { debouncedSearch.value = search.value.trim() }, 250)
}

onUnmounted(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})

function openDetail(run: AgentRun) {
  selectedRun.value = run
  void navigateTo(buildAgentRunHash(run.run_key, returnToRoute.value))
}

function backToList() {
  selectedRun.value = null
  payloads.value = {}
  payloadErrors.value = {}
  void navigateTo(returnToRoute.value || 'agent-runs', { replace: true })
}

async function loadPayload(ref: string) {
  if (!ref || payloads.value[ref] !== undefined) return
  try {
    const blob = await api.getAgentRunPayload(activeRunKey.value, ref)
    payloads.value = { ...payloads.value, [ref]: await blob.text() }
  } catch (e: unknown) {
    payloadErrors.value = {
      ...payloadErrors.value,
      [ref]: e instanceof Error ? e.message : '详情加载失败',
    }
  }
}

watch(activeRunKey, key => {
  selectedRun.value = key ? runs.value.find(run => run.run_key === key) || null : null
  payloads.value = {}
  payloadErrors.value = {}
})

watch(detailEvents, () => {
  if (activeRunKey.value && detailRun.value?.status === 'running') {
    void subagentDetailState.refreshLoaded(activeRunKey.value)
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

function backendBadgeClass(backend: string | null | undefined): string {
  if (backend === 'claude') return 'border-info/30 bg-info-soft text-info-soft-fg'
  if (backend === 'opencode') return 'border-success/30 bg-success-soft text-success-soft-fg'
  if (backend === 'codex') return 'border-warning/30 bg-warning-soft text-warning-soft-fg'
  return 'border-border bg-muted text-muted-foreground'
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
          <span class="text-muted-foreground">后端</span>
          <div>
            <Badge variant="outline" class="font-mono text-[11px]" :class="backendBadgeClass(detailRun.backend_key)">
              {{ detailRun.backend_key || 'unknown' }}
            </Badge>
          </div>
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
            {{ formatDuration(detailRun.duration_ms) || '—' }}
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

      <AgentRunExecutionPanel :run="detailRun" />

      <div v-if="detailEvents.length">
        <div class="mb-1 text-xs font-medium text-muted-foreground">
          事件流（{{ detailEvents.length }}）
        </div>
        <RunEventTimeline
          :events="detailEvents"
          :context-key="detailRun.run_key"
          :payloads="payloads"
          :payload-errors="payloadErrors"
          @expand="(taskId: string) => ensureSubagentDetail(taskId)"
          @load-payload="loadPayload"
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
      <Button variant="outline" size="lg" @click="runListQuery.refetch()">
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
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">后端</th>
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
              <td class="px-4 py-3">
                <Badge variant="outline" class="font-mono text-[11px]" :class="backendBadgeClass(r.backend_key)">
                  {{ r.backend_key || 'unknown' }}
                </Badge>
              </td>
              <td class="px-4 py-3 text-sm">{{ r.profile_key || '—' }}</td>
              <td class="px-4 py-3 text-sm">
                <span v-if="r.workflow_key" class="font-mono text-xs">{{ r.workflow_key }}</span>
                <span v-else>—</span>
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">
                {{ formatDuration(r.duration_ms) || '—' }}
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
