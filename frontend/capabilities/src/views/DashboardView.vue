<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client'
import type { McpService } from '../api/types'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
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
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-6">
    <!-- Stat Cards -->
    <div class="grid grid-cols-4 gap-5">
      <Card class="p-5 transition-shadow hover:shadow-sm">
        <div class="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-accent text-primary">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
        </div>
        <div class="text-[13px] font-medium text-muted-foreground">MCP 服务</div>
        <div class="text-[28px] font-bold leading-tight tabular-nums text-foreground">{{ services.length }}</div>
      </Card>

      <Card class="p-5 transition-shadow hover:shadow-sm">
        <div class="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-green-50 text-green-600">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        </div>
        <div class="text-[13px] font-medium text-muted-foreground">工具总数</div>
        <div class="text-[28px] font-bold leading-tight tabular-nums text-foreground">{{ toolCount ?? '...' }}</div>
      </Card>

      <Card class="p-5 transition-shadow hover:shadow-sm">
        <div class="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-primary">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 12l2.5 2.5L16 9"/></svg>
        </div>
        <div class="text-[13px] font-medium text-muted-foreground">启用服务</div>
        <div class="text-[28px] font-bold leading-tight tabular-nums text-foreground">{{ enabledCount }}</div>
      </Card>

      <Card class="p-5 transition-shadow hover:shadow-sm">
        <div class="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-red-50 text-destructive">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        </div>
        <div class="text-[13px] font-medium text-muted-foreground">同步异常</div>
        <div class="text-[28px] font-bold leading-tight tabular-nums text-foreground">{{ errorCount }}</div>
        <div class="mt-3 cursor-pointer text-xs text-destructive hover:underline" @click="goto('services')">查看详情 &rarr;</div>
      </Card>
    </div>

    <!-- Main Content: Two Column -->
    <div class="grid grid-cols-[2fr_1fr] gap-5">
      <!-- Left: Sync Records -->
      <Card class="border-border">
        <CardHeader>
          <CardTitle>最近同步记录</CardTitle>
        </CardHeader>
        <CardContent class="p-0">
          <div v-if="services.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
            暂无已登记的服务
          </div>
          <table v-else class="w-full">
            <thead>
              <tr class="border-b border-border bg-secondary/50">
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">服务名称</th>
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">最近同步</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in services.slice(0, 10)" :key="s.service_key" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
                <td class="px-4 py-3">
                  <span class="cursor-pointer text-[13px] font-medium text-foreground hover:text-primary" @click="goto('services')">{{ s.service_key }}</span>
                  <div class="mt-0.5 text-xs text-muted-foreground">{{ s.description || s.name }}</div>
                </td>
                <td class="px-4 py-3">
                  <Badge v-if="s.status === 'enabled'" variant="secondary" class="bg-green-50 text-green-700">已启用</Badge>
                  <Badge v-else-if="s.status === 'error'" variant="destructive">连接失败</Badge>
                  <Badge v-else variant="secondary" class="text-muted-foreground">已停用</Badge>
                </td>
                <td class="px-4 py-3 text-xs text-muted-foreground">{{ timeAgo(s.last_synced_at) }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <!-- Right: Quick Actions + Health -->
      <div class="space-y-5">
        <Card class="border-border">
          <CardHeader>
            <CardTitle>快速操作</CardTitle>
          </CardHeader>
          <CardContent class="flex flex-wrap gap-3">
            <Card class="flex flex-1 cursor-pointer items-center gap-3 p-3 text-left transition-all hover:border-primary hover:shadow-sm" @click="goto('services')">
              <div class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-accent text-primary">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              </div>
              <div>
                <div class="text-[13px] font-medium">新增 MCP 服务</div>
                <div class="text-xs text-muted-foreground">登记新的 HTTP MCP 服务</div>
              </div>
            </Card>
            <Card class="flex flex-1 cursor-pointer items-center gap-3 p-3 text-left transition-all hover:border-primary hover:shadow-sm" @click="goto('tools')">
              <div class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-green-50 text-green-600">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
              </div>
              <div>
                <div class="text-[13px] font-medium">同步所有工具</div>
                <div class="text-xs text-muted-foreground">刷新已启用服务的工具列表</div>
              </div>
            </Card>
          </CardContent>
        </Card>

        <Card class="border-border">
          <CardHeader>
            <CardTitle>服务健康概况</CardTitle>
          </CardHeader>
          <CardContent class="space-y-4">
            <div class="flex h-1.5 overflow-hidden rounded-full bg-secondary">
              <div class="bg-green-500 transition-all" :style="{ width: services.length ? `${(enabledCount / services.length) * 100}%` : '0%' }" />
              <div class="bg-red-500 transition-all" :style="{ width: services.length ? `${(errorCount / services.length) * 100}%` : '0%' }" />
              <div class="flex-1 bg-gray-300" />
            </div>

            <div class="space-y-3">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full bg-green-500" />
                  <span class="text-sm">已启用 · 正常运行</span>
                </div>
                <span class="text-sm tabular-nums text-muted-foreground">{{ enabledCount }} 服务</span>
              </div>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full bg-gray-300" />
                  <span class="text-sm">已停用</span>
                </div>
                <span class="text-sm tabular-nums text-muted-foreground">{{ services.length - enabledCount - errorCount }} 服务</span>
              </div>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full bg-destructive" />
                  <span class="text-sm">异常</span>
                </div>
                <span class="text-sm tabular-nums text-muted-foreground">{{ errorCount }} 服务</span>
              </div>
            </div>

            <hr class="border-border/60">

            <div v-if="errorCount > 0">
              <div class="mb-3 text-sm font-semibold">需要关注</div>
              <div class="space-y-3">
                <div v-for="s in services.filter(x => x.status === 'error')" :key="s.service_key" class="border-l-[3px] border-l-destructive bg-red-50 px-3 py-2">
                  <div class="flex items-center justify-between">
                    <span class="text-sm font-medium">{{ s.service_key }}</span>
                    <Badge variant="destructive" class="text-[11px]">连接失败</Badge>
                  </div>
                  <div class="mt-1 text-xs text-muted-foreground">{{ s.last_error || '—' }}</div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  </div>
</template>
