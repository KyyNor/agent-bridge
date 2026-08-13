<script setup lang="ts">
import {
  ArrowUpRight,
  BookOpen,
  Braces,
  Brain,
  CheckCircle2,
  Database,
  Plug,
  RefreshCw,
  XCircle,
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import type { ToolCallLog, WorkflowRun } from '../../api/types'
import { timeAgo } from '../../lib/time'
import { Button } from '../../components/ui/button'

type KnowledgeKey = 'documents' | 'code' | 'memory' | 'ledger' | 'capability'
type ToolCategoryKey = KnowledgeKey

interface DailyWorkflowMetric {
  key: string
  label: string
  success: number
  failed: number
}

interface RecentToolActivity {
  log: ToolCallLog | null
  calls: number
}

const router = useRouter()
const loading = ref(true)
const refreshing = ref(false)
const loadError = ref('')
const refreshedAt = ref<Date | null>(null)

const assetTotals = ref<Record<KnowledgeKey, number>>({
  documents: 0,
  code: 0,
  memory: 0,
  ledger: 0,
  capability: 0,
})

const workflowDays = ref<DailyWorkflowMetric[]>(buildDailyWorkflowMetrics())
const toolCallsByDay = ref<Record<ToolCategoryKey, number[]>>(emptyToolTrend())
const recentToolActivity = ref<Record<ToolCategoryKey, RecentToolActivity>>(emptyRecentActivity())

const knowledgeCards = computed(() => [
  { key: 'documents' as const, label: '文档知识', hint: '已纳管文档', route: 'knowledge', icon: BookOpen },
  { key: 'code' as const, label: '代码知识', hint: '已接入仓库', route: 'code-repos', icon: Braces },
  { key: 'memory' as const, label: '记忆区块', hint: '可用记忆区块', route: 'memory', icon: Brain },
  { key: 'ledger' as const, label: '业务台账', hint: '已维护台账', route: 'business-ledgers', icon: Database },
  { key: 'capability' as const, label: '能力接入', hint: '已接入服务', route: 'services', icon: Plug },
])

const toolCategories = [
  { key: 'documents' as const, label: '文档工具', icon: BookOpen },
  { key: 'code' as const, label: '代码工具', icon: Braces },
  { key: 'memory' as const, label: '记忆工具', icon: Brain },
  { key: 'ledger' as const, label: '业务台账工具', icon: Database },
  { key: 'capability' as const, label: '能力接入工具', icon: Plug },
]

const workflowTotals = computed(() => workflowDays.value.reduce(
  (totals, day) => ({
    completed: totals.completed + day.success + day.failed,
    success: totals.success + day.success,
    failed: totals.failed + day.failed,
  }),
  { completed: 0, success: 0, failed: 0 },
))

const workflowSuccessRate = computed(() => {
  if (!workflowTotals.value.completed) return 0
  return Math.round((workflowTotals.value.success / workflowTotals.value.completed) * 1000) / 10
})

const workflowMax = computed(() => Math.max(1, ...workflowDays.value.map(day => day.success + day.failed)))
const toolMax = computed(() => Math.max(1, ...toolCategories.flatMap(category => toolCallsByDay.value[category.key])))
const toolTotal = computed(() => toolCategories.reduce(
  (total, category) => total + toolCallsByDay.value[category.key].reduce((sum, value) => sum + value, 0),
  0,
))

function goto(path: string) {
  void router.push(`/${path}`)
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function localDateKey(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function dayLabel(key: string) {
  const [year, month, day] = key.split('-').map(Number)
  return `${month}/${day}`
}

function recentDayKeys() {
  const days: string[] = []
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  for (let offset = 6; offset >= 0; offset -= 1) {
    const date = new Date(today)
    date.setDate(today.getDate() - offset)
    days.push(localDateKey(date))
  }
  return days
}

function buildDailyWorkflowMetrics(): DailyWorkflowMetric[] {
  return recentDayKeys().map(key => ({ key, label: dayLabel(key), success: 0, failed: 0 }))
}

function emptyToolTrend(): Record<ToolCategoryKey, number[]> {
  const days = recentDayKeys()
  return {
    documents: days.map(() => 0),
    code: days.map(() => 0),
    memory: days.map(() => 0),
    ledger: days.map(() => 0),
    capability: days.map(() => 0),
  }
}

function emptyRecentActivity(): Record<ToolCategoryKey, RecentToolActivity> {
  return {
    documents: { log: null, calls: 0 },
    code: { log: null, calls: 0 },
    memory: { log: null, calls: 0 },
    ledger: { log: null, calls: 0 },
    capability: { log: null, calls: 0 },
  }
}

function utcQueryTimestamp(date: Date) {
  return date.toISOString().slice(0, 19).replace('T', ' ')
}

function categoryForValues(resourceType: unknown, sourceType: unknown, sourceKey: unknown): ToolCategoryKey | null {
  const resource = String(resourceType || '').toLowerCase()
  const source = String(sourceType || '').toLowerCase()
  const key = String(sourceKey || '').toLowerCase()

  if (resource === 'knowledge_base' || resource === 'wiki_kb' || key.includes('wiki') || key.includes('knowledge')) return 'documents'
  if (resource === 'code_repository' || resource === 'code_repo' || key.includes('codegraph') || key.includes('code')) return 'code'
  if (resource === 'memory_block' || key.includes('memory') || key.includes('claude-mem')) return 'memory'
  if (resource === 'business_ledger' || key.includes('ledger')) return 'ledger'
  if (resource === 'mcp_service' || resource === 'openapi_service' || source === 'mcp_service' || source === 'openapi_service') return 'capability'
  return null
}

function categoryForLog(log: ToolCallLog): ToolCategoryKey | null {
  return categoryForValues(log.resource_type, log.source_type, log.source_key)
}

function dateKeyFromValue(value: unknown): string | null {
  if (!value) return null
  const date = new Date(String(value))
  return Number.isNaN(date.getTime()) ? null : localDateKey(date)
}

function workflowBarHeight(value: number) {
  if (!value) return '0%'
  return `${Math.max(6, (value / workflowMax.value) * 100)}%`
}

function toolLinePoints(category: ToolCategoryKey) {
  const values = toolCallsByDay.value[category]
  const width = 560
  const height = 170
  const top = 14
  const bottom = 22
  const left = 8
  const usableWidth = width - left * 2
  const usableHeight = height - top - bottom
  return values.map((value, index) => {
    const x = left + (usableWidth * index) / Math.max(1, values.length - 1)
    const y = top + usableHeight - (value / toolMax.value) * usableHeight
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

function toolStrokeOpacity(index: number) {
  return [1, 0.82, 0.66, 0.5, 0.36][index]
}

function toolDash(index: number) {
  return ['', '7 4', '2 4', '10 4 2 4', '3 5'][index]
}

function logStatusLabel(log: ToolCallLog | null) {
  if (!log) return '暂无调用'
  return log.status === 'success' ? '成功' : log.status === 'error' ? '失败' : log.status
}

function logStatusClass(log: ToolCallLog | null) {
  if (!log) return 'text-muted-foreground'
  return log.status === 'success' ? 'text-success' : log.status === 'error' ? 'text-destructive' : 'text-muted-foreground'
}

async function loadDashboard() {
  const dashboardStart = new Date()
  dashboardStart.setHours(0, 0, 0, 0)
  dashboardStart.setDate(dashboardStart.getDate() - 6)
  const dashboardEnd = new Date()
  dashboardEnd.setHours(0, 0, 0, 0)
  dashboardEnd.setDate(dashboardEnd.getDate() + 1)

  const [knowledgeResult, codeReposResult, memoryBlocksResult, ledgersResult, mcpServicesResult, openApiServicesResult, workflowsResult, logsPageResult, toolStatsResult] = await Promise.allSettled([
    api.listWikiKbs(),
    api.listCodeRepos(),
    api.listMemoryBlocks(),
    api.listBusinessLedgers(),
    api.listServices(true),
    api.listOpenApiServices(true),
    api.listWorkflows(),
    api.listLogsPage({ limit: 200, created_from: utcQueryTimestamp(dashboardStart), created_to: utcQueryTimestamp(dashboardEnd) }),
    api.stats({
      dimensions: 'resource_type,source_type,status',
      created_from: utcQueryTimestamp(dashboardStart),
      created_to: utcQueryTimestamp(dashboardEnd),
      bucket: 'day',
    }),
  ])

  const initialResults = [
    knowledgeResult,
    codeReposResult,
    memoryBlocksResult,
    ledgersResult,
    mcpServicesResult,
    openApiServicesResult,
    workflowsResult,
    logsPageResult,
    toolStatsResult,
  ]
  if (initialResults.some(result => result.status === 'rejected')) {
    loadError.value = '部分概览数据暂不可用，已展示可访问的数据。'
  }

  const knowledge = knowledgeResult.status === 'fulfilled' ? knowledgeResult.value : []
  const codeRepos = codeReposResult.status === 'fulfilled' ? codeReposResult.value : []
  const memoryBlocks = memoryBlocksResult.status === 'fulfilled' ? memoryBlocksResult.value : []
  const ledgers = ledgersResult.status === 'fulfilled' ? ledgersResult.value : []
  const mcpServices = mcpServicesResult.status === 'fulfilled' ? mcpServicesResult.value : []
  const openApiServices = openApiServicesResult.status === 'fulfilled' ? openApiServicesResult.value : []
  const workflows = workflowsResult.status === 'fulfilled' ? workflowsResult.value : []
  const logsPage = logsPageResult.status === 'fulfilled' ? logsPageResult.value : { items: [] as ToolCallLog[] }
  const statsItems = toolStatsResult.status === 'fulfilled'
    ? toolStatsResult.value.items as Array<Record<string, unknown>>
    : []

  assetTotals.value = {
    documents: knowledge.reduce((total, kb) => total + Number(kb.document_count || 0), 0),
    code: codeRepos.length,
    memory: memoryBlocks.length,
    ledger: ledgers.length,
    capability: mcpServices.length + openApiServices.length,
  }

  const days = buildDailyWorkflowMetrics()
  const dayIndex = new Map(days.map((day, index) => [day.key, index]))
  const runResults = await Promise.allSettled(workflows.map(workflow => api.listWorkflowRuns(workflow.workflow_key)))
  for (const result of runResults) {
    if (result.status !== 'fulfilled') continue
    for (const run of result.value as WorkflowRun[]) {
      const key = dateKeyFromValue(run.finished_at || run.started_at)
      const index = key ? dayIndex.get(key) : undefined
      if (index == null) continue
      if (run.status === 'completed') days[index].success += 1
      if (run.status === 'failed') days[index].failed += 1
    }
  }
  workflowDays.value = days

  const trend = emptyToolTrend()
  const recent = emptyRecentActivity()
  for (const item of statsItems) {
    const category = categoryForValues(item.resource_type, item.source_type, null)
    const key = String(item.bucket || '')
    const index = dayIndex.get(key)
    if (category && index != null) trend[category][index] += Number(item.calls || 0)
  }
  for (const log of logsPage.items) {
    const category = categoryForLog(log)
    if (!category) continue
    if (!recent[category].log) recent[category].log = log
  }
  for (const category of toolCategories) {
    recent[category.key].calls = trend[category.key].reduce((total, value) => total + value, 0)
  }
  toolCallsByDay.value = trend
  recentToolActivity.value = recent
  refreshedAt.value = new Date()
}

async function refresh() {
  refreshing.value = true
  loadError.value = ''
  try {
    await loadDashboard()
  } catch {
    loadError.value = '部分概览数据加载失败，请稍后重试。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

onMounted(() => { void refresh() })
</script>

<template>
  <Teleport to="#ph-actions" defer>
    <Button variant="outline" size="lg" :disabled="refreshing" @click="refresh">
      <RefreshCw :size="14" :class="{ 'animate-spin': refreshing }" />
      {{ refreshing ? '刷新中...' : '刷新' }}
    </Button>
  </Teleport>

  <div v-if="loading" class="py-16 text-center text-sm text-muted-foreground">正在整理平台数据...</div>
  <div v-else class="space-y-5">
    <div v-if="loadError" class="rounded-md border border-destructive/20 bg-destructive-soft px-4 py-3 text-sm text-destructive-soft-fg">
      {{ loadError }}
    </div>

    <section>
      <div class="mb-3 flex items-baseline justify-between gap-4">
        <div>
          <h2 class="text-sm font-semibold text-foreground">知识资产</h2>
          <p class="mt-1 text-xs text-muted-foreground">按领域查看已纳管的资源总量</p>
        </div>
        <span class="shrink-0 text-xs text-muted-foreground">{{ refreshedAt ? '刚刚更新' : '等待更新' }}</span>
      </div>
      <div class="grid grid-cols-1 divide-y rounded-lg border border-border bg-card shadow-card sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-5">
        <button
          v-for="item in knowledgeCards"
          :key="item.key"
          type="button"
          class="group min-w-0 px-4 py-4 text-left transition-colors hover:bg-accent/45 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
          @click="goto(item.route)"
        >
          <div class="flex items-start justify-between gap-3">
            <component :is="item.icon" :size="19" class="mt-0.5 shrink-0 text-primary" :stroke-width="1.65" />
            <ArrowUpRight :size="15" class="shrink-0 text-primary opacity-0 transition-opacity group-hover:opacity-100" />
          </div>
          <div class="mt-5 text-[11px] font-medium text-muted-foreground">{{ item.label }}</div>
          <div class="mt-1.5 text-2xl font-semibold leading-none tabular-nums text-foreground">{{ assetTotals[item.key].toLocaleString() }}</div>
          <div class="mt-2 flex items-center justify-between gap-2">
            <span class="truncate text-xs text-muted-foreground">{{ item.hint }}</span>
            <span class="shrink-0 text-xs font-medium text-primary">查看</span>
          </div>
        </button>
      </div>
    </section>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.12fr)]">
      <section class="rounded-lg border border-border bg-card p-5 shadow-card">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-sm font-semibold text-foreground">工作流执行 <span class="font-normal text-muted-foreground">（近 7 天）</span></h2>
            <div class="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
              <span class="inline-flex items-center gap-1.5"><span class="size-2 rounded-sm bg-success" />成功</span>
              <span class="inline-flex items-center gap-1.5"><span class="size-2 rounded-sm bg-destructive" />失败</span>
            </div>
          </div>
          <button type="button" class="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline" @click="goto('workflow')">
            工作流管理 <ArrowUpRight :size="13" />
          </button>
        </div>

        <div class="mt-5 grid grid-cols-[minmax(0,1fr)_112px] gap-4">
          <div class="min-w-0">
            <div v-if="workflowTotals.completed" class="flex h-[178px] items-end gap-2 border-b border-border px-1 pb-0 sm:gap-3">
              <div v-for="day in workflowDays" :key="day.key" class="flex min-w-0 flex-1 flex-col items-center justify-end gap-1">
                <div class="text-[10px] tabular-nums text-muted-foreground">{{ day.success + day.failed || '—' }}</div>
                <div class="flex h-[138px] w-full max-w-8 flex-col justify-end overflow-hidden rounded-t-sm bg-secondary/70">
                  <div v-if="day.success" class="bg-success" :style="{ height: workflowBarHeight(day.success) }" />
                  <div v-if="day.failed" class="bg-destructive" :style="{ height: workflowBarHeight(day.failed) }" />
                </div>
              </div>
            </div>
            <div v-else class="flex h-[178px] items-center justify-center border-b border-border text-sm text-muted-foreground">近 7 天暂无已完成或失败的工作流</div>
            <div v-if="workflowTotals.completed" class="mt-2 flex gap-2 px-1 sm:gap-3">
              <span v-for="day in workflowDays" :key="`${day.key}:label`" class="min-w-0 flex-1 text-center text-[10px] tabular-nums text-muted-foreground">{{ day.label }}</span>
            </div>
          </div>
          <dl class="divide-y divide-border rounded-md border border-border text-xs">
            <div class="px-3 py-3">
              <dt class="text-muted-foreground">总完成数</dt>
              <dd class="mt-1 text-lg font-semibold tabular-nums text-foreground">{{ workflowTotals.completed }}</dd>
            </div>
            <div class="px-3 py-3">
              <dt class="text-muted-foreground">成功率</dt>
              <dd class="mt-1 text-lg font-semibold tabular-nums text-success">{{ workflowSuccessRate }}%</dd>
            </div>
            <div class="px-3 py-3">
              <dt class="text-muted-foreground">失败数量</dt>
              <dd class="mt-1 text-lg font-semibold tabular-nums text-destructive">{{ workflowTotals.failed }}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section class="rounded-lg border border-border bg-card p-5 shadow-card">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-sm font-semibold text-foreground">工具调用趋势 <span class="font-normal text-muted-foreground">（近 7 天）</span></h2>
            <div class="mt-2 flex flex-wrap gap-x-3 gap-y-1.5 text-[11px] text-muted-foreground">
              <span v-for="(category, index) in toolCategories" :key="category.key" class="inline-flex items-center gap-1.5">
                <span class="h-0.5 w-3 bg-primary" :class="{ 'opacity-80': index === 1, 'opacity-65': index === 2, 'opacity-50': index === 3, 'opacity-35': index === 4 }" />
                {{ category.label }}
              </span>
            </div>
          </div>
          <button type="button" class="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline" @click="goto('stats')">
            调用统计 <ArrowUpRight :size="13" />
          </button>
        </div>

        <div class="mt-4">
          <svg v-if="toolTotal" viewBox="0 0 560 170" preserveAspectRatio="none" role="img" aria-label="近七天各类别工具调用趋势" class="h-[178px] w-full overflow-visible">
            <line v-for="value in 4" :key="value" x1="8" :y1="14 + ((170 - 14 - 22) * value / 4)" x2="552" :y2="14 + ((170 - 14 - 22) * value / 4)" stroke="var(--border)" stroke-width="1" />
            <polyline
              v-for="(category, index) in toolCategories"
              :key="category.key"
              :points="toolLinePoints(category.key)"
              fill="none"
              stroke="var(--primary)"
              :stroke-opacity="toolStrokeOpacity(index)"
              :stroke-dasharray="toolDash(index)"
              stroke-linecap="round"
              stroke-linejoin="round"
              :stroke-width="index === 0 ? 2.2 : 1.7"
            />
          </svg>
          <div v-else class="flex h-[178px] items-center justify-center border-b border-border text-sm text-muted-foreground">近 7 天暂无工具调用</div>
          <div v-if="toolTotal" class="mt-1 flex justify-between px-1 text-[10px] tabular-nums text-muted-foreground">
            <span v-for="day in workflowDays" :key="`${day.key}:tool-label`">{{ day.label }}</span>
          </div>
        </div>
        <div class="mt-3 flex items-center justify-between border-t border-border pt-3 text-xs">
          <span class="text-muted-foreground">总调用量（近 7 天）</span>
          <span class="font-semibold tabular-nums text-foreground">{{ toolTotal.toLocaleString() }}</span>
        </div>
      </section>
    </div>

    <section class="overflow-hidden rounded-lg border border-border bg-card shadow-card">
      <div class="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 class="text-sm font-semibold text-foreground">近期工具调用</h2>
          <p class="mt-1 text-xs text-muted-foreground">按工具领域汇总最近一次调用及近 7 天用量</p>
        </div>
        <button type="button" class="shrink-0 text-xs font-medium text-primary hover:underline" @click="goto('logs')">查看全部日志</button>
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-[760px] w-full">
          <thead>
            <tr class="border-b border-border/70 text-left text-[11px] font-medium text-muted-foreground">
              <th class="px-5 py-3">工具类别</th>
              <th class="px-4 py-3">最近工具标识</th>
              <th class="px-4 py-3">最后调用</th>
              <th class="px-4 py-3">结果</th>
              <th class="px-4 py-3 text-right">调用次数（近 7 天）</th>
              <th class="px-5 py-3 text-right" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="category in toolCategories" :key="category.key" class="border-b border-border/60 last:border-0 hover:bg-muted/35">
              <td class="px-5 py-3">
                <div class="flex items-center gap-2.5 text-sm font-medium text-foreground">
                  <component :is="category.icon" :size="16" class="text-primary" :stroke-width="1.7" />
                  {{ category.label }}
                </div>
              </td>
              <td class="px-4 py-3 font-mono text-xs text-foreground">{{ recentToolActivity[category.key].log?.tool_name || '—' }}</td>
              <td class="px-4 py-3 text-xs text-muted-foreground">{{ timeAgo(recentToolActivity[category.key].log?.created_at || null) }}</td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center gap-1.5 text-xs" :class="logStatusClass(recentToolActivity[category.key].log)">
                  <CheckCircle2 v-if="recentToolActivity[category.key].log?.status === 'success'" :size="13" />
                  <XCircle v-else-if="recentToolActivity[category.key].log?.status === 'error'" :size="13" />
                  <span v-else class="size-1.5 rounded-full bg-current" />
                  {{ logStatusLabel(recentToolActivity[category.key].log) }}
                </span>
              </td>
              <td class="px-4 py-3 text-right text-sm font-medium tabular-nums text-foreground">{{ recentToolActivity[category.key].calls.toLocaleString() }}</td>
              <td class="px-5 py-3 text-right">
                <button type="button" class="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline" @click="goto('logs')">
                  查看日志 <ArrowUpRight :size="13" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
