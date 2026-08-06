<script setup lang="ts">
import { Search, RotateCw } from '@lucide/vue'
import { onUnmounted, ref, computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { api } from '../../api/client'
import type { ToolCallLog, ToolCallLogCounts } from '../../api/types'
import { formatLocalDatetime, formatDuration } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import CategoryBadge from '../../components/CategoryBadge.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import LogMarkdownPreview from '../../components/LogMarkdownPreview.vue'
import { countToolCallTabs } from '../../lib/filterTabs'
import { extractLogMarkdownPreview } from '../../lib/logMarkdownPreview'
import { LOG_PAGE_SIZE_OPTIONS } from '../../lib/pagination'
import { toolCallDisplayName } from '../../lib/toolCallDisplay'
import { queryKeys } from '../../lib/query'

const statusFilter = ref('')
const sourceFilter = ref('__all__')
const search = ref('')
const debouncedSearch = ref('')
const page = ref(1)
const pageSize = ref(100)

const showDetail = ref(false)
const selectedLog = ref<ToolCallLog | null>(null)
const selectedLogId = ref('')
const previewOpen = ref(false)
let searchDebounce: ReturnType<typeof setTimeout> | null = null

function todayRange() {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 1)
  return {
    from: start.toISOString().slice(0, 19).replace('T', ' '),
    to: end.toISOString().slice(0, 19).replace('T', ' '),
  }
}

function formatDate(d: Date) {
  return d.toISOString().slice(0, 10)
}

const dateFrom = ref(formatDate(new Date(Date.now() - 86400000)))
const dateTo = ref(formatDate(new Date()))

onUnmounted(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})

function baseParams(): Record<string, string | number> {
  const params: Record<string, string | number> = {
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
  if (sourceFilter.value !== '__all__') params.source_type = sourceFilter.value
  if (dateFrom.value) params.created_from = `${dateFrom.value} 00:00:00`
  if (dateTo.value) params.created_to = `${dateTo.value} 23:59:59`
  if (debouncedSearch.value) params.search = debouncedSearch.value
  return params
}

const logParams = computed(() => {
  const params = baseParams()
  if (statusFilter.value) params.status = statusFilter.value
  return params
})

const logListQuery = useQuery({
  queryKey: computed(() => queryKeys.toolCallLogs(logParams.value)),
  queryFn: ({ signal }) => api.listLogsPage(logParams.value, { signal }),
})

const detailQuery = useQuery({
  queryKey: computed(() => queryKeys.toolCallLog(selectedLogId.value)),
  queryFn: ({ signal }) => api.getLog(selectedLogId.value, { signal }),
  enabled: computed(() => showDetail.value && Boolean(selectedLogId.value)),
})

const emptyCounts: ToolCallLogCounts = { all: 0, success: 0, failed: 0, running: 0, error: 0, blocked: 0 }
const logs = computed(() => logListQuery.data.value?.items || [])
const logTotal = computed(() => logListQuery.data.value?.total || 0)
const logCounts = computed(() => logListQuery.data.value?.counts || emptyCounts)
const loading = computed(() => logListQuery.isLoading.value)
const detailLog = computed(() => detailQuery.data.value || selectedLog.value)
const detailLoading = computed(() => detailQuery.isLoading.value)

function applyFilter(status: string) {
  statusFilter.value = status
  page.value = 1
}

function applyDateFilter() {
  page.value = 1
}

function applySourceFilter() {
  page.value = 1
}

function scheduleSearch() {
  page.value = 1
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => { debouncedSearch.value = search.value.trim() }, 250)
}

function openDetail(log: ToolCallLog) {
  selectedLog.value = log
  selectedLogId.value = log.log_id
  showDetail.value = true
  previewOpen.value = false
}

const displayLogs = computed(() => logs.value)
const filterTabs = computed(() => countToolCallTabs(logCounts.value))
const pagedLogs = computed(() => displayLogs.value)
const detailMarkdownPreview = computed(() => detailLog.value ? extractLogMarkdownPreview(detailLog.value) : null)

const sourceOptions = [
  { value: '__all__', label: '全部来源' },
  { value: 'hook', label: 'Hook' },
  { value: 'mcp_service', label: 'MCP' },
  { value: 'openapi_service', label: 'OpenAPI' },
  { value: 'builtin', label: 'Builtin' },
]

// 调用日志 status → StatusBadge 的语义状态（success/error/blocked/running）
type BadgeStatus = 'success' | 'error' | 'blocked' | 'running'
function statusOf(status: string): BadgeStatus {
  if (status === 'success' || status === 'error' || status === 'blocked' || status === 'running') return status
  return 'success'
}

// 慢调用标记：≥1000ms 用 warning 色突出，便于扫读
function durationClass(durationMs: number | null | undefined): string {
  return durationMs != null && durationMs >= 1000 ? 'text-warning' : ''
}

function entrypointLabel(entrypoint: string): string {
  switch (entrypoint) {
    case 'memory_hook_claude_code':
      return 'Claude Code Hook'
    case 'metamcp_execute':
      return 'MetaMCP Execute'
    case 'metamcp_search':
      return 'MetaMCP Search'
    default:
      return entrypoint
  }
}
</script>

<template>
  <div v-if="loading && logs.length === 0" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- 页头操作：刷新进 #ph-actions（LogsView 无 primary 主操作） -->
    <Teleport to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="logListQuery.refetch()">
        <RotateCw :size="14" />
        刷新
      </Button>
    </Teleport>

    <!-- 页头筛选：搜索 + 来源 + 日期范围 + 状态分段进 #ph-filters -->
    <Teleport to="#ph-filters" defer>
      <div class="relative w-full max-w-[280px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-placeholder" />
        <Input v-model="search" placeholder="搜索工具、调用者或入口..." class="h-9 pl-8" @update:model-value="scheduleSearch" />
      </div>
      <Select v-model="sourceFilter" @update:model-value="applySourceFilter">
        <SelectTrigger size="lg" class="w-[160px]">
          <SelectValue placeholder="全部来源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem v-for="option in sourceOptions" :key="option.value" :value="option.value">{{ option.label }}</SelectItem>
        </SelectContent>
      </Select>
      <div class="flex items-center gap-2 text-sm">
        <Input v-model="dateFrom" type="date" class="h-9 w-[140px]" @change="applyDateFilter" />
        <span class="text-muted-foreground">至</span>
        <Input v-model="dateTo" type="date" class="h-9 w-[140px]" @change="applyDateFilter" />
      </div>
      <SegmentedTabs v-model="statusFilter" :tabs="filterTabs" @update:model-value="applyFilter" />
    </Teleport>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="logTotal === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无调用日志</div>
        <div v-else class="overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">时间</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">来源</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Profile</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">工具</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">耗时</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">错误</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="l in pagedLogs" :key="l.log_id" class="border-b border-border/60">
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ formatLocalDatetime(l.created_at) }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-2">
                  <CategoryBadge kind="source" :value="l.source_type || ''" />
                  <span class="font-mono text-xs text-muted-foreground">{{ l.source_key || '—' }}</span>
                </div>
              </td>
              <td class="px-4 py-3 text-sm">{{ l.profile_key || '—' }}</td>
              <td :title="l.tool_name || ''" class="px-4 py-3 font-mono text-sm">{{ toolCallDisplayName(l) }}</td>
              <td class="px-4 py-3 text-sm font-mono tabular-nums text-muted-foreground" :class="durationClass(l.duration_ms)">{{ formatDuration(l.duration_ms) || '—' }}</td>
              <td class="px-4 py-3">
                <StatusBadge :status="statusOf(l.status)" />
              </td>
              <td class="max-w-[200px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ l.error_message || '—' }}</td>
              <td class="px-4 py-3">
                <Button variant="ghost" size="sm" @click="openDetail(l)" class="h-8 text-xs">详情</Button>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </CardContent>
    </Card>

    <PaginationBar
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="logTotal"
      :page-size-options="LOG_PAGE_SIZE_OPTIONS"
    />

    <!-- Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="w-[min(1180px,calc(100vw-2rem))] sm:max-w-[1180px] overflow-hidden">
        <DialogHeader>
          <DialogTitle>调用详情</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="detailLog" class="min-w-0 space-y-4">
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div><span class="text-muted-foreground">调用者</span><div class="font-medium">{{ detailLog.actor }}</div></div>
            <div><span class="text-muted-foreground">入口</span><div class="font-medium">{{ entrypointLabel(detailLog.entrypoint) }}</div></div>
            <div><span class="text-muted-foreground">工具</span><div :title="detailLog.tool_name || ''" class="font-mono font-medium">{{ toolCallDisplayName(detailLog) }}</div></div>
            <div><span class="text-muted-foreground">耗时</span><div class="font-medium tabular-nums">{{ formatDuration(detailLog.duration_ms) || '—' }}</div></div>
            <div><span class="text-muted-foreground">Profile</span><div class="font-medium">{{ detailLog.profile_key || '—' }}</div></div>
            <div>
              <span class="text-muted-foreground">来源</span>
              <div class="flex items-center gap-2">
                <CategoryBadge kind="source" :value="detailLog.source_type || ''" />
                <span class="font-mono text-xs font-medium">{{ detailLog.source_key || '—' }}</span>
              </div>
            </div>
            <div><span class="text-muted-foreground">状态</span>
              <div>
                <StatusBadge :status="statusOf(detailLog.status)" />
              </div>
            </div>
            <div><span class="text-muted-foreground">时间</span><div class="font-medium">{{ formatLocalDatetime(detailLog.created_at) }}</div></div>
          </div>

          <div v-if="detailLog.error_message" class="rounded-lg border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive">
            {{ detailLog.error_message }}
          </div>

          <div v-if="detailLog.failure_stage" class="text-sm text-muted-foreground">
            失败阶段: {{ detailLog.failure_stage }}
            <span v-if="detailLog.failure_owner"> · 责任方: {{ detailLog.failure_owner }}</span>
            <span v-if="detailLog.error_type"> · 类型: {{ detailLog.error_type }}</span>
          </div>

          <div v-if="detailLog.request_json">
            <div class="mb-1 text-xs font-medium text-muted-foreground">请求</div>
            <JsonViewer :value="detailLog.request_json" max-height="260px" />
          </div>

          <div v-if="detailLog.response_json">
            <div class="mb-1 flex items-center justify-between gap-3 text-xs font-medium text-muted-foreground">
              <span>响应</span>
              <Button v-if="detailMarkdownPreview" variant="outline" size="sm" class="h-7 text-xs" @click="previewOpen = true">预览</Button>
            </div>
            <JsonViewer :value="detailLog.response_json" max-height="260px" />
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <LogMarkdownPreview
      v-if="detailMarkdownPreview"
      v-model:open="previewOpen"
      :title="detailMarkdownPreview.title"
      :markdown="detailMarkdownPreview.markdown"
    />
  </div>
</template>
