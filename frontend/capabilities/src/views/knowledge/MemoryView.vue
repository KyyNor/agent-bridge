<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Database, Plus, RefreshCw, Search } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { MemoryBlock, MemorySearchResult, MemoryTimelineResult } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'

const blocks = ref<MemoryBlock[]>([])
const selectedKey = ref('')
const loading = ref(true)
const creating = ref(false)
const error = ref('')
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

const selected = computed(() => blocks.value.find(block => block.block_key === selectedKey.value) || null)
const activeBlocks = computed(() => blocks.value.filter(block => block.status === 'active').length)
const healthStatus = computed(() => {
  const health = selectedHealth.value || selected.value?.last_health || {}
  const value = health.status || health.state || selected.value?.status || 'unknown'
  return String(value)
})
const healthPreview = computed(() => {
  const health = selectedHealth.value || selected.value?.last_health
  return health ? JSON.stringify(health, null, 2) : '{}'
})

onMounted(loadBlocks)

watch(selectedKey, () => {
  searchResult.value = null
  timeline.value = null
  void loadHealth()
})

async function loadBlocks() {
  loading.value = true
  error.value = ''
  try {
    blocks.value = await api.listMemoryBlocks()
    if (!selectedKey.value && blocks.value.length > 0) selectedKey.value = blocks.value[0].block_key
    if (selectedKey.value && !blocks.value.some(block => block.block_key === selectedKey.value)) {
      selectedKey.value = blocks.value[0]?.block_key || ''
    }
  } catch (e: unknown) {
    error.value = `加载记忆区块失败：${errorMessage(e)}`
    blocks.value = []
  } finally {
    loading.value = false
  }
}

async function createBlock() {
  if (!form.value.block_key.trim() || !form.value.name.trim()) return
  creating.value = true
  error.value = ''
  try {
    const created = await api.createMemoryBlock({
      block_key: form.value.block_key.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim() || undefined,
    })
    form.value = { block_key: '', name: '', description: '' }
    await loadBlocks()
    selectedKey.value = created.block_key
  } catch (e: unknown) {
    error.value = `创建记忆区块失败：${errorMessage(e)}`
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

function selectBlock(block: MemoryBlock) {
  selectedKey.value = block.block_key
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
  <div class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="grid grid-cols-3 gap-3 text-sm">
        <div class="rounded-lg border border-border bg-card px-3 py-2">
          <div class="text-xs text-muted-foreground">区块数</div>
          <div class="mt-1 font-semibold tabular-nums">{{ blocks.length }}</div>
        </div>
        <div class="rounded-lg border border-border bg-card px-3 py-2">
          <div class="text-xs text-muted-foreground">Active</div>
          <div class="mt-1 font-semibold tabular-nums">{{ activeBlocks }}</div>
        </div>
        <div class="rounded-lg border border-border bg-card px-3 py-2">
          <div class="text-xs text-muted-foreground">当前健康</div>
          <div class="mt-1 truncate font-semibold">{{ selected ? healthStatus : '-' }}</div>
        </div>
      </div>
      <Button variant="outline" size="sm" :disabled="loading" @click="loadBlocks">
        <RefreshCw :size="14" />
        刷新
      </Button>
    </div>

    <div v-if="error" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ error }}</div>

    <div class="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <div class="space-y-4">
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="flex items-center gap-2 text-sm font-medium">
              <Plus :size="14" />
              新建记忆区块
            </div>
            <Input v-model="form.block_key" placeholder="dev-memory" />
            <Input v-model="form.name" placeholder="Dev Memory" />
            <Input v-model="form.description" placeholder="描述，可选" />
            <Button size="sm" :disabled="creating || !form.block_key.trim() || !form.name.trim()" @click="createBlock">
              {{ creating ? '创建中...' : '创建' }}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent class="p-0">
            <div v-if="loading" class="px-4 py-10 text-center text-sm text-muted-foreground">加载中...</div>
            <div v-else-if="blocks.length === 0" class="px-4 py-10 text-center text-sm text-muted-foreground">
              暂无记忆区块
            </div>
            <template v-else>
              <button
                v-for="block in blocks"
                :key="block.block_key"
                type="button"
                class="block w-full border-b border-border/70 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-muted/50"
                :class="selectedKey === block.block_key ? 'bg-secondary/80' : 'bg-card'"
                @click="selectBlock(block)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="min-w-0 break-all text-sm font-medium text-foreground">{{ block.name }}</span>
                  <Badge variant="secondary" :class="statusClass(block.status)" class="shrink-0 text-[11px]">{{ block.status }}</Badge>
                </div>
                <div class="mt-1 break-all text-xs text-muted-foreground">{{ block.block_key }}</div>
                <div class="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>profiles {{ block.bound_profile_count || 0 }}</span>
                  <span class="truncate">{{ String(block.last_health?.status || 'unknown') }}</span>
                </div>
              </button>
            </template>
          </CardContent>
        </Card>
      </div>

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
          选择或创建一个记忆区块
        </CardContent>
      </Card>
    </div>
  </div>
</template>
