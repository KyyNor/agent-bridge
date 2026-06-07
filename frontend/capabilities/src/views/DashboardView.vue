<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client'
import type { McpService } from '../api/types'
import { Badge } from '../components/ui/badge'

const services = ref<McpService[]>([])
const loading = ref(true)
const enabledCount = computed(() => services.value.filter(s => s.status === 'enabled').length)
const errorCount = computed(() => services.value.filter(s => s.status === 'error').length)
const toolCount = ref<number | null>(null)

function goto(hash: string) {
  window.location.hash = hash
}

onMounted(async () => {
  try {
    services.value = await api.listServices()
    const active = services.value.filter(s => s.status === 'enabled')
    let total = 0
    await Promise.all(active.map(async s => {
      try { total += (await api.listTools(s.service_key)).length } catch { /* skip */ }
    }))
    toolCount.value = total
  } catch { /* empty state */ }
  loading.value = false
})

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—'
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

function statusDot(s: McpService) {
  if (s.status === 'enabled') return 'bg-emerald-400'
  if (s.status === 'error') return 'bg-red-400'
  return 'bg-gray-300'
}
</script>

<template>
  <div v-if="loading" class="py-16 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-6">
    <!-- Stat Cards -->
    <div class="grid grid-cols-4 gap-4">
      <div class="rounded-lg border border-border bg-card p-5">
        <div class="flex items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-accent text-primary">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/></svg>
          </div>
          <div>
            <div class="text-[13px] text-muted-foreground">MCP 服务</div>
            <div class="text-2xl font-bold tabular-nums">{{ services.length }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-5">
        <div class="flex items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          </div>
          <div>
            <div class="text-[13px] text-muted-foreground">工具总数</div>
            <div class="text-2xl font-bold tabular-nums">{{ toolCount ?? '...' }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-5">
        <div class="flex items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-primary">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12l2.5 2.5L16 9"/></svg>
          </div>
          <div>
            <div class="text-[13px] text-muted-foreground">启用服务</div>
            <div class="text-2xl font-bold tabular-nums">{{ enabledCount }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-5">
        <div class="flex items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-red-50 text-red-500">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          </div>
          <div>
            <div class="text-[13px] text-muted-foreground">同步异常</div>
            <div class="flex items-baseline gap-2">
              <span class="text-2xl font-bold tabular-nums">{{ errorCount }}</span>
              <span v-if="errorCount > 0" class="cursor-pointer text-xs text-destructive hover:underline" @click="goto('services')">查看</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="grid grid-cols-[5fr_3fr] gap-6">
      <!-- Left: Service List -->
      <div>
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-sm font-medium text-foreground">服务列表</h2>
          <span class="cursor-pointer text-xs text-primary hover:underline" @click="goto('services')">查看全部</span>
        </div>
        <div v-if="services.length === 0" class="rounded-lg border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
          暂无已登记的服务
        </div>
        <div v-else class="space-y-2">
          <div
            v-for="s in services.slice(0, 8)" :key="s.service_key"
            class="flex items-center gap-3 rounded-lg border border-border bg-card p-3 transition-colors hover:border-primary/30 cursor-pointer"
            @click="goto('services')"
          >
            <span class="h-2 w-2 rounded-full shrink-0" :class="statusDot(s)" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium">{{ s.service_key }}</div>
              <div class="truncate text-xs text-muted-foreground">{{ s.description || s.name }}</div>
            </div>
            <Badge v-if="s.status === 'enabled'" variant="secondary" class="bg-emerald-50 text-emerald-700 shrink-0">已启用</Badge>
            <Badge v-else-if="s.status === 'error'" variant="destructive" class="shrink-0">异常</Badge>
            <Badge v-else variant="secondary" class="text-muted-foreground shrink-0">停用</Badge>
            <span class="whitespace-nowrap text-xs text-muted-foreground">{{ timeAgo(s.last_synced_at) }}</span>
          </div>
        </div>
      </div>

      <!-- Right Column -->
      <div class="space-y-6">
        <!-- Quick Actions -->
        <div class="grid grid-cols-2 gap-3">
          <button
            class="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-4 text-center transition-all hover:border-primary/40 hover:shadow-sm"
            @click="goto('services')"
          >
            <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-primary">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </div>
            <span class="text-[13px] font-medium">新增服务</span>
          </button>
          <button
            class="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-4 text-center transition-all hover:border-primary/40 hover:shadow-sm"
            @click="goto('tools')"
          >
            <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            </div>
            <span class="text-[13px] font-medium">同步工具</span>
          </button>
        </div>

        <!-- Health Overview -->
        <div class="rounded-lg border border-border bg-card p-5">
          <div class="mb-4 text-sm font-medium">服务健康概况</div>

          <div class="mb-4 flex h-2 overflow-hidden rounded-full bg-secondary">
            <div class="rounded-full bg-emerald-400 transition-all" :style="{ width: services.length ? `${(enabledCount / services.length) * 100}%` : '0%' }" />
            <div class="rounded-full bg-red-400 transition-all" :style="{ width: services.length ? `${(errorCount / services.length) * 100}%` : '0%' }" />
          </div>

          <div class="space-y-2.5">
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-emerald-400" />
                <span class="text-muted-foreground">正常运行</span>
              </div>
              <span class="tabular-nums font-medium">{{ enabledCount }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-gray-300" />
                <span class="text-muted-foreground">已停用</span>
              </div>
              <span class="tabular-nums font-medium">{{ services.length - enabledCount - errorCount }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-red-400" />
                <span class="text-muted-foreground">异常</span>
              </div>
              <span class="tabular-nums font-medium">{{ errorCount }}</span>
            </div>
          </div>

          <div v-if="errorCount > 0" class="mt-4 space-y-2 border-t border-border pt-4">
            <div class="text-xs text-muted-foreground">需要关注</div>
            <div v-for="s in services.filter(x => x.status === 'error')" :key="s.service_key" class="rounded-lg bg-red-50 px-3 py-2">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium">{{ s.service_key }}</span>
                <Badge variant="destructive" class="text-[11px]">连接失败</Badge>
              </div>
              <div v-if="s.last_error" class="mt-1 text-xs text-muted-foreground">{{ s.last_error }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
