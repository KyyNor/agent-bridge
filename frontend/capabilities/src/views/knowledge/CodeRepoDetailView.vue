<script setup lang="ts">
import { ArrowLeft, Loader2, Maximize2, Minimize2 } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { marked } from 'marked'
import { api } from '../../api/client'
import type {
  CodeGraphExploreResult,
  CodeGraphNode,
  CodeGraphStatus,
  CodeRepository,
  RepoOverview,
  SchedulerRunProgress,
  SchedulerStatus,
  UAAvailability,
  UASummary,
  UAStatus,
} from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import StatusBadge from '../../components/StatusBadge.vue'
import JsonViewer from '../../components/JsonViewer.vue'

const props = defineProps<{
  repoKey: string
  repo: CodeRepository | null
}>()

const emit = defineEmits<{
  back: []
}>()

const detailRepo = computed(() => {
  if (!props.repo || props.repo.repo_key !== props.repoKey) return null
  return props.repo
})
const detailLoading = ref(false)
const detailError = ref('')
const detailOverview = ref<RepoOverview | null>(null)
const detailStatus = ref<CodeGraphStatus | null>(null)
const detailQuery = ref('')
const detailResults = ref<CodeGraphNode[]>([])
const detailExploreQuery = ref('')
const detailExploreResult = ref<CodeGraphExploreResult | null>(null)
const detailExploreError = ref('')
const detailTab = ref<'overview' | 'query' | 'explore' | 'understand'>('overview')
const detailSearching = ref(false)
const detailExploring = ref(false)

// UA (Understand Anything) state
const uaStatus = ref<UAStatus | null>(null)
const uaSummary = ref<UASummary | null>(null)
const uaLoading = ref(false)
const uaAvailability = ref<UAAvailability | null>(null)
const uaSchedulerStatus = ref<SchedulerStatus | null>(null)
const uaAnalyzing = ref(false)
const uaAnalyzeError = ref('')
const uaAnalyzeSuccess = ref('')
const uaDashboardStarting = ref(false)
const dashboardMaximized = ref(false)
let uaTouchTimer: ReturnType<typeof setInterval> | null = null
let detailLoadId = 0
const UA_DASHBOARD_THEME_KEY = 'ua-theme'
const UA_DASHBOARD_DEFAULT_THEME = { presetId: 'light-minimal', accentId: 'indigo' }

const dashboardSrc = computed(() => {
  const url = uaStatus.value?.dashboard_url
  if (!url) return ''
  return url + (url.includes('?') ? '&' : '?') + 'theme=dark'
})

const uaUnderstandRun = computed<SchedulerRunProgress | null>(() => {
  const understand = uaSchedulerStatus.value?.understand
  if (!understand) return null

  const jobProgress = understand.jobs.find(job => job.repo_key === detailRepo.value?.repo_key)?.progress ?? null
  return understand.last_run ?? understand.current_run ?? jobProgress
})

function goBack() {
  emit('back')
}

function resetDetailState() {
  detailLoading.value = false
  detailError.value = ''
  detailOverview.value = null
  detailStatus.value = null
  detailQuery.value = ''
  detailResults.value = []
  detailExploreQuery.value = ''
  detailExploreResult.value = null
  detailExploreError.value = ''
  detailTab.value = 'overview'
  detailSearching.value = false
  detailExploring.value = false
  uaStatus.value = null
  uaSummary.value = null
  uaLoading.value = false
  uaAvailability.value = null
  uaSchedulerStatus.value = null
  uaAnalyzing.value = false
  uaAnalyzeError.value = ''
  uaAnalyzeSuccess.value = ''
  uaDashboardStarting.value = false
  dashboardMaximized.value = false
  stopTouchTimer()
}

async function loadDetail() {
  const repo = detailRepo.value
  const loadId = ++detailLoadId
  resetDetailState()

  if (!repo) {
    detailError.value = '仓库不存在或已被删除'
    return
  }

  detailLoading.value = true
  const [status, overview] = await Promise.allSettled([
    api.getCodeGraphStatus(),
    api.getRepoOverview(repo.repo_key),
  ])
  if (loadId !== detailLoadId) return

  detailStatus.value = status.status === 'fulfilled' ? status.value : null
  detailOverview.value = overview.status === 'fulfilled' ? overview.value : null
  if (status.status === 'rejected' && overview.status === 'rejected') {
    detailError.value = '详情加载失败，请稍后重试'
  } else if (status.status === 'rejected') {
    detailError.value = '代码图谱状态加载失败，部分信息可能不可用'
  } else if (overview.status === 'rejected') {
    detailError.value = '仓库概览加载失败，部分信息可能不可用'
  }
  detailLoading.value = false
}

async function searchInRepo() {
  const term = detailQuery.value.trim()
  if (!term || !detailRepo.value) return
  detailSearching.value = true
  try {
    const result = await api.queryRepo(detailRepo.value.repo_key, term)
    detailResults.value = result.matches
  } catch {
    detailResults.value = []
  }
  detailSearching.value = false
}

async function exploreRepo() {
  const term = detailExploreQuery.value.trim()
  if (!term || !detailRepo.value) return
  detailExploring.value = true
  detailExploreError.value = ''
  detailExploreResult.value = null
  try {
    const result = await api.exploreRepo(detailRepo.value.repo_key, term)
    detailExploreResult.value = result
  } catch (e: any) {
    detailExploreError.value = e.message || 'Explore 执行失败'
  }
  detailExploring.value = false
}

const exploreMarkdownHtml = computed(() => {
  const content = detailExploreResult.value?.mcp_result?.content
  if (!Array.isArray(content)) return ''
  const textItem = content.find((c: any) => c.type === 'text' && c.text) as { text: string } | undefined
  if (!textItem) return ''
  return marked.parse(textItem.text, { async: false }) as string
})

async function loadUAData() {
  const repo = detailRepo.value
  if (!repo) return
  const repoKey = repo.repo_key
  uaLoading.value = true
  try {
    const [avail, statusResult, summaryResult, schedulerResult] = await Promise.allSettled([
      api.checkUAAvailability(repoKey),
      api.getUAStatus(repoKey),
      api.getUASummary(repoKey),
      api.getSchedulerStatus(),
    ])
    if (detailRepo.value?.repo_key !== repoKey) return
    uaAvailability.value = avail.status === 'fulfilled' ? avail.value : null
    if (statusResult.status === 'fulfilled' && statusResult.value.dashboard_running) {
      applyUADashboardDefaultTheme()
    }
    uaStatus.value = statusResult.status === 'fulfilled' ? statusResult.value : null
    uaSummary.value = summaryResult.status === 'fulfilled' ? summaryResult.value : null
    uaSchedulerStatus.value = schedulerResult.status === 'fulfilled' ? schedulerResult.value : null

    if (uaStatus.value?.graph_exists && !uaStatus.value.dashboard_running && !uaDashboardStarting.value) {
      void autoStartDashboard()
    }
    if (uaStatus.value?.dashboard_running) {
      startTouchTimer()
    }
  } catch {
    // Individual requests are handled by Promise.allSettled; keep the page usable if setup changes.
  } finally {
    if (detailRepo.value?.repo_key === repoKey) uaLoading.value = false
  }
}

async function autoStartDashboard() {
  const repo = detailRepo.value
  if (!repo) return
  const repoKey = repo.repo_key
  uaDashboardStarting.value = true
  try {
    const result = await api.startUADashboard(repoKey) as any
    if (result.success && uaStatus.value && detailRepo.value?.repo_key === repoKey) {
      applyUADashboardDefaultTheme()
      uaStatus.value.dashboard_running = true
      uaStatus.value.dashboard_url = result.url || null
      startTouchTimer()
    }
  } catch {
    // Dashboard startup is best effort; the status panel remains available.
  }
  if (detailRepo.value?.repo_key === repoKey) uaDashboardStarting.value = false
}

function applyUADashboardDefaultTheme() {
  try {
    window.localStorage.setItem(UA_DASHBOARD_THEME_KEY, JSON.stringify(UA_DASHBOARD_DEFAULT_THEME))
  } catch {
    // Ignore storage failures; the dashboard will fall back to its own default.
  }
}

function startTouchTimer() {
  stopTouchTimer()
  const repo = detailRepo.value
  if (!repo) return
  const key = repo.repo_key
  api.touchDashboard(key).catch(() => {})
  uaTouchTimer = setInterval(() => {
    api.touchDashboard(key).catch(() => {})
  }, 5 * 60 * 1000)
}

function stopTouchTimer() {
  if (uaTouchTimer) {
    clearInterval(uaTouchTimer)
    uaTouchTimer = null
  }
}

async function triggerAnalyze() {
  const repo = detailRepo.value
  if (!repo) return
  uaAnalyzing.value = true
  uaAnalyzeError.value = ''
  uaAnalyzeSuccess.value = ''
  try {
    const result = await api.triggerUAAnalyze(repo.repo_key)
    if (result.success) {
      uaAnalyzeSuccess.value = `分析完成：${result.node_count} 节点、${result.edge_count} 边，耗时 ${(result.duration_ms / 1000).toFixed(1)}s`
      await loadUAData()
    } else {
      uaAnalyzeError.value = result.error || '分析失败'
    }
  } catch (e: any) {
    uaAnalyzeError.value = e.message || '分析失败'
  }
  uaAnalyzing.value = false
}

watch([() => props.repoKey, () => props.repo], () => {
  void loadDetail()
}, { immediate: true })

watch(detailTab, (tab) => {
  if (tab === 'understand' && !uaStatus.value && !uaLoading.value) {
    void loadUAData()
  }
})

onBeforeUnmount(() => {
  stopTouchTimer()
})
</script>

<template>
  <div class="flex min-h-[calc(100vh-8rem)] min-w-0 flex-col space-y-4">
    <div class="flex shrink-0 items-start gap-3">
      <Button variant="ghost" size="sm" class="-ml-2 mt-0.5" @click="goBack">
        <ArrowLeft :size="16" />
        返回代码仓库
      </Button>
      <div class="min-w-0 border-l border-border pl-3">
        <h2 class="truncate text-lg font-semibold">{{ detailRepo?.name || '代码仓库详情' }}</h2>
        <p class="truncate text-xs text-muted-foreground">{{ detailRepo?.repo_key || props.repoKey }}</p>
      </div>
    </div>

    <div v-if="!detailRepo" class="rounded-lg border border-destructive/30 bg-destructive-soft p-6 text-sm text-destructive">
      <div class="font-medium">{{ detailError || '仓库不存在或已被删除' }}</div>
      <Button variant="outline" size="sm" class="mt-4" @click="goBack">返回列表</Button>
    </div>

    <div v-else class="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
      <div v-if="detailError" class="rounded-lg border border-warning/30 bg-warning-soft p-3 text-sm text-warning-soft-fg">
        {{ detailError }}
      </div>
      <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      <div v-else class="space-y-4">
        <!-- Status Banner -->
        <div v-if="detailStatus && !detailStatus.codegraph_installed" class="rounded-lg bg-warning-soft p-3 text-sm text-warning-soft-fg">
          {{ detailStatus.message }}
        </div>

        <!-- Overview -->
        <div v-if="detailOverview" class="grid grid-cols-3 gap-3">
          <div class="rounded-lg border border-border p-3 text-center">
            <div class="text-2xl font-semibold tabular-nums">{{ detailOverview.file_count }}</div>
            <div class="text-xs text-muted-foreground">文件数</div>
          </div>
          <div class="rounded-lg border border-border p-3 text-center">
            <div class="text-2xl font-semibold tabular-nums">{{ detailOverview.symbol_count }}</div>
            <div class="text-xs text-muted-foreground">符号数</div>
          </div>
          <div class="rounded-lg border border-border p-3 text-center">
            <div class="text-xs text-muted-foreground">最近同步</div>
            <div class="text-sm font-medium">{{ formatLocalDatetime(detailOverview.last_synced_at) }}</div>
          </div>
        </div>

        <!-- Tabs -->
        <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
          <button v-for="t in [
            { key: 'overview', label: '概览' },
            { key: 'query', label: '查询' },
            { key: 'explore', label: '探索' },
            { key: 'understand', label: '理解' },
          ]" :key="t.key"
            :class="['rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors', detailTab === t.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground']"
            @click="detailTab = t.key as any">{{ t.label }}</button>
        </div>

        <!-- Overview Tab -->
        <div v-if="detailTab === 'overview'" class="space-y-3">
          <div class="rounded-lg border border-border p-4">
            <div class="mb-3 text-sm font-medium">仓库信息</div>
            <div class="grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <div class="text-xs text-muted-foreground">仓库标识</div>
                <div class="font-mono text-xs">{{ detailRepo.repo_key }}</div>
              </div>
              <div>
                <div class="text-xs text-muted-foreground">分支</div>
                <div>{{ detailRepo.branch }}</div>
              </div>
              <div class="sm:col-span-2">
                <div class="text-xs text-muted-foreground">Git URL</div>
                <div class="break-all font-mono text-xs">{{ detailRepo.git_url }}</div>
              </div>
              <div v-if="detailRepo.description" class="sm:col-span-2">
                <div class="text-xs text-muted-foreground">描述</div>
                <div>{{ detailRepo.description }}</div>
              </div>
            </div>
          </div>
          <div v-if="detailRepo.last_error" class="rounded-lg border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive">
            <div class="mb-1 font-medium">同步错误</div>
            <div class="whitespace-pre-wrap break-words">{{ detailRepo.last_error }}</div>
          </div>
        </div>

        <!-- Query Tab -->
        <div v-if="detailTab === 'query'" class="space-y-3">
          <div class="flex gap-2">
            <Input v-model="detailQuery" placeholder="输入符号名或搜索词" class="flex-1" @keydown.enter="searchInRepo()" />
            <Button @click="searchInRepo()" :disabled="detailSearching || !detailQuery.trim()" size="sm">搜索</Button>
          </div>
          <div v-if="detailSearching" class="py-4 text-center text-sm text-muted-foreground">查询中...</div>
          <div v-else-if="detailResults.length > 0" class="max-h-[300px] overflow-y-auto rounded-lg border border-border">
            <table class="w-full table-fixed">
              <thead><tr class="border-b border-border">
                <th class="w-[20%] px-3 py-2 text-left text-xs font-medium text-muted-foreground">符号</th>
                <th class="w-[12%] px-3 py-2 text-left text-xs font-medium text-muted-foreground">类型</th>
                <th class="w-[55%] px-3 py-2 text-left text-xs font-medium text-muted-foreground">文件</th>
                <th class="w-[13%] px-3 py-2 text-left text-xs font-medium text-muted-foreground">行号</th>
              </tr></thead>
              <tbody><tr v-for="r in detailResults" :key="r.symbol + r.path" class="border-b border-border/60">
                <td class="truncate px-3 py-1.5 text-sm font-medium" :title="r.symbol">{{ r.symbol }}</td>
                <td class="px-3 py-1.5"><Badge variant="secondary" class="text-[11px]">{{ r.kind }}</Badge></td>
                <td class="truncate px-3 py-1.5 font-mono text-xs text-muted-foreground" :title="r.path">{{ r.path }}</td>
                <td class="px-3 py-1.5 text-xs tabular-nums">{{ r.line_start || '—' }}</td>
              </tr></tbody>
            </table>
          </div>
        </div>

        <!-- Explore Tab -->
        <div v-if="detailTab === 'explore'" class="space-y-3">
          <div class="flex gap-2">
            <Input v-model="detailExploreQuery" placeholder="输入要交给 CodeGraph Explore 的问题" class="flex-1" @keydown.enter="exploreRepo()" />
            <Button @click="exploreRepo()" :disabled="detailExploring || !detailExploreQuery.trim()" size="sm">
              {{ detailExploring ? '执行中...' : '执行' }}
            </Button>
          </div>
          <div v-if="detailExploreError" class="rounded-lg border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">
            {{ detailExploreError }}
          </div>
          <div v-if="detailExploring" class="py-4 text-center text-sm text-muted-foreground">执行中...</div>
          <div v-else-if="detailExploreResult" class="space-y-3">
            <div v-if="detailExploreResult.mcp_result.structured" class="rounded-lg border border-border p-3">
              <div class="mb-2 text-xs font-medium text-muted-foreground">Structured</div>
              <JsonViewer :value="detailExploreResult.mcp_result.structured" max-height="260px" />
            </div>
            <div class="rounded-lg border border-border p-3">
              <div class="mb-2 text-xs font-medium text-muted-foreground">Content</div>
              <div v-if="exploreMarkdownHtml" class="prose prose-sm max-h-[500px] max-w-none overflow-y-auto" v-html="exploreMarkdownHtml"></div>
              <JsonViewer v-else :value="detailExploreResult.mcp_result.content" max-height="260px" />
            </div>
          </div>
        </div>

        <!-- Understand Tab -->
        <div v-if="detailTab === 'understand'" class="space-y-3">
          <div v-if="uaLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
          <template v-else>
            <!-- Availability Check -->
            <div v-if="detailRepo.auto_understand" class="rounded-lg border border-info/30 bg-info-soft p-3 text-sm text-info-soft-fg">
              此代码库已开启自动理解，将按定时任务周期自动运行分析。
            </div>
            <template v-else>
              <div v-if="uaAvailability && !uaAvailability.ua_skill_available && !uaAvailability.ua_git_url_configured" class="rounded-lg border border-warning/30 bg-warning-soft p-4 text-sm text-warning-soft-fg">
                <div class="font-medium">Understand Anything 不可用</div>
                <div class="mt-1">请在「系统配置」页面填写 UA Git URL 以启用自动安装。</div>
              </div>
              <div v-else-if="uaAvailability && !uaAvailability.ua_skill_available && uaAvailability.ua_git_url_configured" class="flex items-center justify-between rounded-lg border border-success/30 bg-success-soft p-3 text-sm text-success-soft-fg">
                <span>UA 技能未安装，将在运行分析时自动安装</span>
                <Button size="sm" @click="triggerAnalyze" :disabled="uaAnalyzing">
                  {{ uaAnalyzing ? '安装并分析中...' : '安装并分析' }}
                </Button>
              </div>
              <div v-else-if="uaAvailability && uaAvailability.ua_skill_available" class="flex items-center justify-between rounded-lg border border-success/30 bg-success-soft p-3 text-sm text-success-soft-fg">
                <span>Understand Anything 技能已就绪</span>
                <Button size="sm" @click="triggerAnalyze" :disabled="uaAnalyzing">
                  {{ uaAnalyzing ? '分析中...' : '运行分析' }}
                </Button>
              </div>
            </template>

            <!-- Analyze Result -->
            <div v-if="uaAnalyzeSuccess" class="rounded-lg bg-success-soft p-3 text-sm text-success-soft-fg">{{ uaAnalyzeSuccess }}</div>
            <div v-if="uaAnalyzeError" class="rounded-lg border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">{{ uaAnalyzeError }}</div>

            <div v-if="uaUnderstandRun" class="rounded-lg border border-border p-3 text-sm">
              <div class="flex items-center justify-between gap-2">
                <div class="font-medium">最近一次定时分析</div>
                <StatusBadge
                  :status="uaUnderstandRun.status === 'succeeded' ? 'success' : uaUnderstandRun.status === 'failed' ? 'error' : 'running'"
                  :label="uaUnderstandRun.status === 'succeeded' ? '成功' : uaUnderstandRun.status === 'failed' ? '失败' : uaUnderstandRun.status"
                />
              </div>
              <div class="mt-2 grid gap-1 text-xs text-muted-foreground">
                <div v-if="uaUnderstandRun.started_at">开始时间: {{ formatLocalDatetime(uaUnderstandRun.started_at) }}</div>
                <div v-if="uaUnderstandRun.finished_at">结束时间: {{ formatLocalDatetime(uaUnderstandRun.finished_at) }}</div>
                <div v-if="uaUnderstandRun.message">{{ uaUnderstandRun.message }}</div>
                <div v-if="uaUnderstandRun.error" class="text-destructive">错误: {{ uaUnderstandRun.error }}</div>
              </div>
            </div>

            <!-- Dashboard iframe -->
            <div v-if="uaStatus?.dashboard_running && dashboardSrc" class="flex flex-col overflow-hidden rounded-lg border border-border" :class="{ '!border-0': dashboardMaximized }" style="min-height: 60vh">
              <div class="flex shrink-0 items-center justify-between gap-2 border-b border-border bg-secondary/50 px-3 py-1.5">
                <span class="text-xs font-medium text-muted-foreground">Dashboard</span>
                <Button variant="ghost" size="sm" class="h-7 w-7 p-0" :title="dashboardMaximized ? '还原' : '最大化'" @click="dashboardMaximized = !dashboardMaximized">
                  <Maximize2 v-if="!dashboardMaximized" :size="14" />
                  <Minimize2 v-else :size="14" />
                </Button>
              </div>
              <iframe v-if="!dashboardMaximized" :src="dashboardSrc" class="w-full flex-1 border-0" style="min-height: 60vh" />
            </div>
            <div v-else-if="uaDashboardStarting" class="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Loader2 class="size-4 animate-spin" />
              启动 Dashboard...
            </div>

            <!-- Status Banner -->
            <div v-if="!uaStatus?.graph_exists" class="rounded-lg border border-border bg-secondary/50 p-4 text-center">
              <div class="text-sm text-muted-foreground">暂无知识图谱</div>
              <div class="mt-1 text-xs text-muted-foreground">可通过 Understand Anything 技能生成</div>
            </div>
            <template v-else>
              <div class="rounded-lg bg-success-soft p-3 text-sm text-success-soft-fg">
                知识图谱可用
              </div>
              <div class="grid grid-cols-4 gap-2">
                <div class="rounded-lg border border-border p-2.5 text-center">
                  <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.node_count || 0 }}</div>
                  <div class="text-[11px] text-muted-foreground">节点</div>
                </div>
                <div class="rounded-lg border border-border p-2.5 text-center">
                  <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.edge_count || 0 }}</div>
                  <div class="text-[11px] text-muted-foreground">边</div>
                </div>
                <div class="rounded-lg border border-border p-2.5 text-center">
                  <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.layer_count || 0 }}</div>
                  <div class="text-[11px] text-muted-foreground">层</div>
                </div>
                <div class="rounded-lg border border-border p-2.5 text-center">
                  <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.tour_count || 0 }}</div>
                  <div class="text-[11px] text-muted-foreground">导览</div>
                </div>
              </div>

              <!-- Summary -->
              <div v-if="uaSummary" class="space-y-3">
                <div v-if="uaSummary.description" class="rounded-lg border border-border p-4">
                  <div class="text-sm text-muted-foreground">{{ uaSummary.description }}</div>
                </div>
                <div v-if="uaSummary.languages.length || uaSummary.frameworks.length" class="flex flex-wrap gap-1.5">
                  <Badge v-for="lang in uaSummary.languages" :key="lang" variant="secondary" class="bg-info-soft text-info-soft-fg">{{ lang }}</Badge>
                  <Badge v-for="fw in uaSummary.frameworks" :key="fw" variant="secondary" class="bg-success-soft text-success-soft-fg">{{ fw }}</Badge>
                </div>
                <div v-if="uaSummary.modules.length" class="rounded-lg border border-border">
                  <div class="border-b border-border px-4 py-2 text-xs font-medium text-muted-foreground">主要模块</div>
                  <div v-for="m in uaSummary.modules" :key="m.name" class="border-b border-border/40 px-4 py-2.5 last:border-b-0">
                    <div class="text-sm font-medium">{{ m.name }}</div>
                    <div v-if="m.summary" class="text-xs text-muted-foreground">{{ m.summary }}</div>
                  </div>
                </div>
                <div v-if="uaSummary.tours.length" class="rounded-lg border border-border">
                  <div class="border-b border-border px-4 py-2 text-xs font-medium text-muted-foreground">导览</div>
                  <div v-for="t in uaSummary.tours" :key="t.title" class="px-4 py-2.5">
                    <div class="text-sm font-medium">{{ t.title }}</div>
                    <div class="text-xs text-muted-foreground">{{ t.step_count }} 步 · {{ t.description }}</div>
                  </div>
                </div>
              </div>
            </template>

            <!-- Diagnostics -->
            <details v-if="uaStatus?.graph_exists" class="rounded-lg border border-border">
              <summary class="cursor-pointer px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">诊断信息</summary>
              <div class="space-y-1 border-t border-border px-4 py-3 text-xs text-muted-foreground">
                <div v-if="uaStatus?.analyzed_at">分析时间: {{ formatLocalDatetime(uaStatus.analyzed_at) }}</div>
                <div v-if="uaStatus?.git_commit">分析 commit: <span class="font-mono">{{ uaStatus.git_commit?.slice(0, 12) }}</span></div>
                <div v-if="uaStatus?.analyzed_files != null">分析文件数: {{ uaStatus.analyzed_files }}</div>
                <div v-if="uaStatus?.graph_path">图谱路径: <span class="font-mono text-[11px]">{{ uaStatus.graph_path }}</span></div>
                <div v-if="uaStatus?.error" class="text-destructive">错误: {{ uaStatus.error }}</div>
              </div>
            </details>
          </template>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="dashboardMaximized && dashboardSrc" class="pointer-events-auto fixed inset-0 z-[10000] flex flex-col bg-background">
        <div class="flex shrink-0 items-center justify-between gap-2 border-b border-border bg-secondary/50 px-4 py-2">
          <span class="text-sm font-medium text-muted-foreground">Dashboard</span>
          <Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="还原" @click="dashboardMaximized = false">
            <Minimize2 :size="14" />
          </Button>
        </div>
        <iframe :src="dashboardSrc" class="w-full flex-1 border-0 pointer-events-auto" />
      </div>
    </Teleport>
  </div>
</template>
