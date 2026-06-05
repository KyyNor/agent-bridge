<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import type { ToolCallLog } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog'

const logs = ref<ToolCallLog[]>([])
const loading = ref(false)
const statusFilter = ref('')
const search = ref('')

const showDetail = ref(false)
const detailLog = ref<ToolCallLog | null>(null)
const detailLoading = ref(false)

onMounted(() => loadLogs())

async function loadLogs() {
  loading.value = true
  const params: Record<string, string | number> = { limit: 50 }
  if (statusFilter.value) params.status = statusFilter.value
  try { logs.value = await api.listLogs(params) } catch { logs.value = [] }
  loading.value = false
}

function applyFilter(status: string) {
  statusFilter.value = status
  loadLogs()
}

async function openDetail(log: ToolCallLog) {
  detailLog.value = log
  showDetail.value = true
  detailLoading.value = true
  try {
    detailLog.value = await api.getLog(log.log_id)
  } catch { /* use list data as fallback */ }
  detailLoading.value = false
}

const displayLogs = computed(() => {
  if (!search.value) return logs.value
  const q = search.value.toLowerCase()
  return logs.value.filter(l =>
    l.tool_name?.toLowerCase().includes(q) ||
    l.actor?.toLowerCase().includes(q) ||
    l.entrypoint?.toLowerCase().includes(q)
  )
})

const filterTabs = computed(() => [
  { key: '', label: '全部', count: logs.value.length },
  { key: 'success', label: '成功', count: logs.value.filter(l => l.status === 'success').length },
  { key: 'error', label: '失败', count: logs.value.filter(l => l.status === 'error').length },
  { key: 'blocked', label: '拦截', count: logs.value.filter(l => l.status === 'blocked').length },
])
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <Input v-model="search" placeholder="搜索工具、调用者或入口..." class="pl-8" />
      </div>
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="tab in filterTabs" :key="tab.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            statusFilter === tab.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="applyFilter(tab.key)"
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span></button>
      </div>
      <Button variant="outline" @click="loadLogs">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mr-1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </Button>
    </div>

    <!-- Table -->
    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="displayLogs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无调用日志</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">时间</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">调用者</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">工具</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">耗时</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="l in displayLogs" :key="l.log_id" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ l.created_at?.slice(0, 19) }}</td>
              <td class="px-4 py-3 text-sm">{{ l.actor }}</td>
              <td class="px-4 py-3 font-mono text-sm">{{ l.tool_name || '—' }}</td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">{{ l.duration_ms != null ? `${l.duration_ms}ms` : '—' }}</td>
              <td class="px-4 py-3">
                <Badge v-if="l.status === 'success'" variant="secondary" class="bg-green-50 text-green-700">成功</Badge>
                <Badge v-else-if="l.status === 'error'" variant="destructive">失败</Badge>
                <Badge v-else-if="l.status === 'blocked'" variant="secondary" class="bg-amber-50 text-amber-700">拦截</Badge>
                <Badge v-else variant="secondary">{{ l.status }}</Badge>
              </td>
              <td class="px-4 py-3">
                <Button variant="ghost" size="sm" @click="openDetail(l)" class="h-8 text-xs">详情</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>共 {{ displayLogs.length }} 条记录</span>
    </div>

    <!-- Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>调用详情 {{ detailLog?.log_id }}</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="detailLog" class="space-y-4">
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div><span class="text-muted-foreground">调用者</span><div class="font-medium">{{ detailLog.actor }}</div></div>
            <div><span class="text-muted-foreground">入口</span><div class="font-medium">{{ detailLog.entrypoint }}</div></div>
            <div><span class="text-muted-foreground">工具</span><div class="font-mono font-medium">{{ detailLog.tool_name || '—' }}</div></div>
            <div><span class="text-muted-foreground">耗时</span><div class="font-medium tabular-nums">{{ detailLog.duration_ms != null ? `${detailLog.duration_ms}ms` : '—' }}</div></div>
            <div><span class="text-muted-foreground">Profile</span><div class="font-medium">{{ detailLog.profile_key || '—' }}</div></div>
            <div><span class="text-muted-foreground">来源</span><div class="font-medium">{{ detailLog.source_key || '—' }}</div></div>
            <div><span class="text-muted-foreground">状态</span>
              <div>
                <Badge v-if="detailLog.status === 'success'" variant="secondary" class="bg-green-50 text-green-700">成功</Badge>
                <Badge v-else-if="detailLog.status === 'error'" variant="destructive">失败</Badge>
                <Badge v-else-if="detailLog.status === 'blocked'" variant="secondary" class="bg-amber-50 text-amber-700">拦截</Badge>
                <Badge v-else variant="secondary">{{ detailLog.status }}</Badge>
              </div>
            </div>
            <div><span class="text-muted-foreground">时间</span><div class="font-medium">{{ detailLog.created_at?.slice(0, 19) }}</div></div>
          </div>

          <div v-if="detailLog.error_message" class="rounded-lg border border-destructive/30 bg-red-50 px-4 py-3 text-sm text-destructive">
            {{ detailLog.error_message }}
          </div>

          <div v-if="detailLog.failure_stage" class="text-sm">
            <span class="text-muted-foreground">失败阶段:</span> {{ detailLog.failure_stage }}
            <span v-if="detailLog.failure_owner"> · {{ detailLog.failure_owner }}</span>
            <span v-if="detailLog.error_type"> · {{ detailLog.error_type }}</span>
          </div>

          <div v-if="detailLog.request">
            <div class="mb-1 text-xs font-medium text-muted-foreground">请求</div>
            <pre class="max-h-[200px] overflow-auto rounded-lg bg-secondary px-4 py-3 text-xs">{{ JSON.stringify(detailLog.request, null, 2) }}</pre>
          </div>

          <div v-if="detailLog.response">
            <div class="mb-1 text-xs font-medium text-muted-foreground">响应</div>
            <pre class="max-h-[200px] overflow-auto rounded-lg bg-secondary px-4 py-3 text-xs">{{ JSON.stringify(detailLog.response, null, 2) }}</pre>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
