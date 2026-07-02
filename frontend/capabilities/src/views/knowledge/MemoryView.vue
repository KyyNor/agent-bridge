<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Database, Maximize2, Minimize2, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { MemoryBlock, MemoryDashboardStatus, MemorySearchResult, MemoryTimelineResult } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { confirm, alert } from '../../composables/useConfirm'

const props = defineProps<{ routeKey: string }>()

const blocks = ref<MemoryBlock[]>([])
const loading = ref(true)
const error = ref('')
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')
const healthLoading = ref(false)
const healthError = ref('')
const selectedHealth = ref<Record<string, unknown> | null>(null)
const form = ref({ block_key: '', name: '', description: '' })
const query = ref('')
const searchLoading = ref(false)
const searchError = ref('')
const searchResult = ref<MemorySearchResult | null>(null)
const timelineLoading = ref(false)
const timelineError = ref('')
const timeline = ref<MemoryTimelineResult | null>(null)
const dashboardStatus = ref<MemoryDashboardStatus | null>(null)
const dashboardLoading = ref(false)
const dashboardStarting = ref(false)
const dashboardStopping = ref(false)
const dashboardError = ref('')
const dashboardMaximized = ref(false)
let dashboardTouchTimer: ReturnType<typeof setInterval> | null = null

const mode = computed<'list' | 'detail'>(() => (props.routeKey ? 'detail' : 'list'))
const selected = computed(() => blocks.value.find(block => block.block_key === props.routeKey) || null)
const healthStatus = computed(() => {
  const health = selectedHealth.value || selected.value?.last_health || {}
  const value = health.status || health.state || selected.value?.status || 'unknown'
  return String(value)
})
const healthPreview = computed(() => {
  const health = selectedHealth.value || selected.value?.last_health
  return health ? JSON.stringify(health, null, 2) : '{}'
})
const dashboardSrc = computed(() => {
  if (!dashboardStatus.value?.running || !dashboardStatus.value.url) return ''
  return dashboardStatus.value.url
})

onMounted(loadBlocks)
onBeforeUnmount(stopDashboardTouchTimer)

watch(
  () => props.routeKey,
  () => {
    searchResult.value = null
    timeline.value = null
    dashboardStatus.value = null
    dashboardError.value = ''
    dashboardMaximized.value = false
    stopDashboardTouchTimer()
    void loadHealth()
    void loadDashboardStatus()
  },
)

async function loadBlocks() {
  loading.value = true
  error.value = ''
  try {
    blocks.value = await api.listMemoryBlocks()
  } catch (e: unknown) {
    error.value = `加载记忆区块失败：${errorMessage(e)}`
    blocks.value = []
  } finally {
    loading.value = false
  }
  await loadHealth()
  await loadDashboardStatus()
}

async function deleteBlock(block: MemoryBlock) {
  if (!await confirm({ title: '删除记忆区块', description: `确定删除记忆区块「${block.name}」？将停止 worker 进程并清除该区块的全部记忆数据，且不可恢复。`, destructive: true, confirmText: '删除' })) return
  try {
    await api.deleteMemoryBlock(block.block_key)
    await loadBlocks()
  } catch (e: unknown) {
    await alert({ title: '删除失败', description: errorMessage(e), destructive: true })
  }
}

async function createBlock() {
  if (!form.value.block_key.trim() || !form.value.name.trim()) return
  creating.value = true
  createError.value = ''
  try {
    const created = await api.createMemoryBlock({
      block_key: form.value.block_key.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim() || undefined,
    })
    form.value = { block_key: '', name: '', description: '' }
    showCreate.value = false
    await loadBlocks()
    openDetail(created)
  } catch (e: unknown) {
    createError.value = `创建记忆区块失败：${errorMessage(e)}`
  } finally {
    creating.value = false
  }
}

async function loadHealth() {
  if (!selected.value) {
    selectedHealth.value = null
    return
  }
  healthLoading.value = true
  healthError.value = ''
  try {
    selectedHealth.value = await api.getMemoryBlockHealth(selected.value.block_key)
  } catch (e: unknown) {
    selectedHealth.value = selected.value.last_health || null
    healthError.value = `健康检查失败：${errorMessage(e)}`
  } finally {
    healthLoading.value = false
  }
}

async function loadDashboardStatus() {
  if (!selected.value) {
    dashboardStatus.value = null
    stopDashboardTouchTimer()
    return
  }
  dashboardLoading.value = true
  dashboardError.value = ''
  try {
    dashboardStatus.value = await api.getMemoryDashboardStatus(selected.value.block_key)
    if (dashboardStatus.value.running) {
      startDashboardTouchTimer()
    } else {
      stopDashboardTouchTimer()
    }
  } catch (e: unknown) {
    dashboardError.value = `加载 worker 页面状态失败：${errorMessage(e)}`
  } finally {
    dashboardLoading.value = false
  }
}

async function startMemoryDashboard() {
  if (!selected.value) return
  dashboardStarting.value = true
  dashboardError.value = ''
  try {
    dashboardStatus.value = await api.startMemoryDashboard(selected.value.block_key)
    if (!dashboardStatus.value.running) {
      dashboardError.value = dashboardStatus.value.error || 'worker 页面启动失败'
      stopDashboardTouchTimer()
      return
    }
    startDashboardTouchTimer()
  } catch (e: unknown) {
    dashboardError.value = `启动 worker 页面失败：${errorMessage(e)}`
  } finally {
    dashboardStarting.value = false
  }
}

async function stopMemoryDashboard() {
  if (!selected.value) return
  dashboardStopping.value = true
  dashboardError.value = ''
  try {
    await api.stopMemoryDashboard(selected.value.block_key)
    dashboardStatus.value = { running: false, url: null }
    dashboardMaximized.value = false
    stopDashboardTouchTimer()
  } catch (e: unknown) {
    dashboardError.value = `停止 worker 页面失败：${errorMessage(e)}`
  } finally {
    dashboardStopping.value = false
  }
}

function startDashboardTouchTimer() {
  if (dashboardTouchTimer || !selected.value) return
  dashboardTouchTimer = setInterval(() => {
    if (selected.value && dashboardStatus.value?.running) {
      void api.touchMemoryDashboard(selected.value.block_key).catch(() => {})
    }
  }, 60_000)
}

function stopDashboardTouchTimer() {
  if (!dashboardTouchTimer) return
  clearInterval(dashboardTouchTimer)
  dashboardTouchTimer = null
}

async function runSearch() {
  if (!selected.value || !query.value.trim()) return
  searchLoading.value = true
  searchError.value = ''
  try {
    searchResult.value = await api.searchMemoryBlock(selected.value.block_key, query.value.trim(), 10)
  } catch (e: unknown) {
    searchError.value = `搜索失败：${errorMessage(e)}`
    searchResult.value = null
  } finally {
    searchLoading.value = false
  }
}

async function loadTimeline(cursor?: string | null) {
  if (!selected.value) return
  timelineLoading.value = true
  timelineError.value = ''
  try {
    timeline.value = await api.getMemoryTimeline(selected.value.block_key, 20, cursor || undefined)
  } catch (e: unknown) {
    timelineError.value = `加载时间线失败：${errorMessage(e)}`
    timeline.value = null
  } finally {
    timelineLoading.value = false
  }
}

function goList() {
  window.location.hash = 'memory'
}

function openDetail(block: MemoryBlock) {
  window.location.hash = 'memory/' + block.block_key
}

function statusClass(status: string) {
  if (status === 'active' || status === 'worker_ready' || status === 'ok') return 'bg-green-50 text-green-700'
  if (status === 'disabled') return 'text-muted-foreground'
  if (status === 'error' || status === 'failed') return 'bg-red-50 text-red-700'
  return 'bg-blue-50 text-blue-700'
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else-if="mode === 'list'" class="space-y-5">
    <div class="flex flex-wrap items-center gap-4">
      <Button @click="showCreate = true">
        <Plus :size="14" />
        新建记忆区块
      </Button>
      <Button variant="outline" :disabled="loading" @click="loadBlocks">
        <RefreshCw :size="14" />
        刷新
      </Button>
    </div>

    <div v-if="error" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ error }}</div>

    <Card>
      <CardContent class="p-0">
        <div v-if="blocks.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          暂无记忆区块，点击「新建记忆区块」开始
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">标识</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">绑定 Profile</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="block in blocks" :key="block.block_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <div class="text-sm font-medium">{{ block.name }}</div>
                <div class="text-xs text-muted-foreground">{{ block.description }}</div>
              </td>
              <td class="px-4 py-3 font-mono text-xs text-muted-foreground">{{ block.block_key }}</td>
              <td class="px-4 py-3">
                <Badge variant="secondary" :class="statusClass(block.status)" class="text-[11px]">{{ block.status }}</Badge>
              </td>
              <td class="px-4 py-3 tabular-nums text-sm">{{ block.bound_profile_count || 0 }}</td>
              <td class="px-4 py-3">
                <div class="flex gap-2">
                  <Button variant="outline" size="sm" class="h-8 text-xs" @click="openDetail(block)">详情</Button>
                  <Button variant="ghost" size="sm" class="h-8 gap-1.5 text-xs text-destructive" @click="deleteBlock(block)">
                    <Trash2 :size="14" />
                    删除
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <Dialog :open="showCreate" @update:open="showCreate = $event">
      <DialogContent class="sm:max-w-[450px]">
        <DialogHeader>
          <DialogTitle>新建记忆区块</DialogTitle>
        </DialogHeader>
        <form class="space-y-4" @submit.prevent="createBlock">
          <div v-if="createError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ createError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">标识 <span class="text-destructive">*</span></label>
            <Input v-model="form.block_key" placeholder="dev-memory" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="form.name" placeholder="Dev Memory" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="form.description" placeholder="描述，可选" />
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" type="button" @click="showCreate = false">取消</Button>
          <Button :disabled="creating || !form.block_key.trim() || !form.name.trim()" @click="createBlock">
            {{ creating ? '创建中...' : '创建' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>

  <div v-else class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
        <ArrowLeft :size="14" />
        返回
      </Button>
      <Button variant="outline" size="sm" :disabled="loading" @click="loadBlocks">
        <RefreshCw :size="14" />
        刷新
      </Button>
    </div>

    <div v-if="error" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ error }}</div>

    <Card v-if="selected">
      <CardContent class="space-y-5 p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2 text-base font-semibold">
              <Database :size="16" />
              <span class="break-all">{{ selected.name }}</span>
            </div>
            <div class="mt-1 break-all text-sm text-muted-foreground">{{ selected.description || selected.block_key }}</div>
          </div>
          <div class="flex items-center gap-2">
            <Badge variant="secondary" :class="statusClass(selected.status)">{{ selected.status }}</Badge>
            <Badge variant="outline">{{ healthStatus }}</Badge>
          </div>
        </div>

        <div class="grid gap-3 text-sm lg:grid-cols-[minmax(0,1fr)_220px]">
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-xs font-medium text-muted-foreground">data_dir</div>
            <div class="mt-1 break-all font-mono text-xs text-foreground">{{ selected.data_dir }}</div>
          </div>
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-xs font-medium text-muted-foreground">worker</div>
            <div class="mt-1 truncate text-xs text-foreground">{{ selected.worker_base_url || 'auto' }}</div>
          </div>
        </div>

        <div class="space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="text-sm font-medium">健康</div>
            <Button variant="outline" size="sm" :disabled="healthLoading" @click="loadHealth">
              {{ healthLoading ? '检查中...' : '检查健康' }}
            </Button>
          </div>
          <div v-if="healthError" class="rounded-md bg-red-50 px-3 py-2 text-xs text-destructive">{{ healthError }}</div>
          <pre class="max-h-[160px] overflow-auto rounded-md bg-secondary p-3 text-xs leading-relaxed text-foreground">{{ healthPreview }}</pre>
        </div>

        <div class="space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="text-sm font-medium">worker 页面</div>
            <div class="flex items-center gap-2">
              <Button variant="outline" size="sm" :disabled="dashboardLoading" @click="loadDashboardStatus">
                <RefreshCw :size="14" />
                {{ dashboardLoading ? '刷新中...' : '刷新' }}
              </Button>
              <Button size="sm" :disabled="dashboardStarting || Boolean(dashboardSrc)" @click="startMemoryDashboard">
                {{ dashboardStarting ? '启动中...' : '打开页面' }}
              </Button>
              <Button variant="outline" size="sm" :disabled="dashboardStopping || !dashboardSrc" @click="stopMemoryDashboard">
                {{ dashboardStopping ? '停止中...' : '停止' }}
              </Button>
            </div>
          </div>
          <div v-if="dashboardError" class="rounded-md bg-red-50 px-3 py-2 text-xs text-destructive">{{ dashboardError }}</div>
          <div v-if="dashboardSrc" class="flex min-h-[60vh] flex-col overflow-hidden rounded-md border border-border">
            <div class="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-2">
              <div class="truncate font-mono text-xs text-muted-foreground">{{ dashboardSrc }}</div>
              <Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="最大化" @click="dashboardMaximized = true">
                <Maximize2 :size="14" />
              </Button>
            </div>
            <iframe :src="dashboardSrc" title="claude-mem worker" class="min-h-[60vh] flex-1 border-0" />
          </div>
          <div v-else class="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
            worker 页面未运行
          </div>
        </div>

        <div class="space-y-3">
          <div class="flex gap-2">
            <Input v-model="query" placeholder="搜索记忆" @keyup.enter="runSearch" />
            <Button size="sm" :disabled="searchLoading || !query.trim()" @click="runSearch">
              <Search :size="14" />
              {{ searchLoading ? '搜索中...' : '搜索' }}
            </Button>
            <Button size="sm" variant="outline" :disabled="timelineLoading" @click="loadTimeline()">
              {{ timelineLoading ? '加载中...' : '时间线' }}
            </Button>
          </div>
          <div v-if="searchError" class="rounded-md bg-red-50 px-3 py-2 text-xs text-destructive">{{ searchError }}</div>
          <div v-if="searchResult" class="space-y-2">
            <div class="text-xs font-medium text-muted-foreground">搜索结果 · {{ searchResult.status }}</div>
            <div v-if="searchResult.items.length === 0" class="rounded-md border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
              无匹配记忆
            </div>
            <div v-for="item in searchResult.items" :key="item.id" class="rounded-md border border-border p-3">
              <div class="flex items-center justify-between gap-2">
                <div class="min-w-0 break-all text-sm font-medium">{{ item.summary || item.id }}</div>
                <span class="shrink-0 text-xs text-muted-foreground">{{ item.score ?? '-' }}</span>
              </div>
              <div class="mt-1 text-xs text-muted-foreground">{{ item.timestamp || 'no timestamp' }}</div>
              <p class="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">{{ item.content_preview }}</p>
            </div>
          </div>
        </div>

        <div v-if="timelineError" class="rounded-md bg-red-50 px-3 py-2 text-xs text-destructive">{{ timelineError }}</div>
        <div v-if="timeline" class="space-y-2">
          <div class="flex items-center justify-between gap-2">
            <div class="text-xs font-medium text-muted-foreground">时间线 · {{ timeline.status }}</div>
            <Button v-if="timeline.next_cursor" variant="outline" size="sm" :disabled="timelineLoading" @click="loadTimeline(timeline.next_cursor)">
              下一页
            </Button>
          </div>
          <div v-if="timeline.items.length === 0" class="rounded-md border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
            暂无时间线事件
          </div>
          <div v-for="item in timeline.items" :key="item.id" class="rounded-md border border-border p-3">
            <div class="text-xs text-muted-foreground">{{ item.timestamp || 'no timestamp' }} · {{ item.event_type }}</div>
            <div class="mt-1 break-all text-sm text-foreground">{{ item.summary || item.id }}</div>
          </div>
        </div>
      </CardContent>
    </Card>

    <Card v-else>
      <CardContent class="px-5 py-12 text-center text-sm text-muted-foreground">
        未找到记忆区块
      </CardContent>
    </Card>

    <div v-if="dashboardMaximized && dashboardSrc" class="fixed inset-0 z-[10000] flex flex-col bg-background">
      <div class="flex h-11 items-center justify-between border-b border-border px-4">
        <div class="truncate font-mono text-xs text-muted-foreground">{{ dashboardSrc }}</div>
        <Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="还原" @click="dashboardMaximized = false">
          <Minimize2 :size="14" />
        </Button>
      </div>
      <iframe :src="dashboardSrc" title="claude-mem worker maximized" class="flex-1 border-0" />
    </div>
  </div>
</template>
