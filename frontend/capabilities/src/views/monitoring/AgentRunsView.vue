<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { Search, RotateCw } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { AgentRun, AgentRunEvent } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../components/ui/dialog'

const runs = ref<AgentRun[]>([])
const loading = ref(false)
const okFilter = ref<'' | 'success' | 'failed'>('')
const search = ref('')

const showDetail = ref(false)
const detailRun = ref<AgentRun | null>(null)
const detailLoading = ref(false)

function formatDate(d: Date) {
  return d.toISOString().slice(0, 10)
}
const dateFrom = ref(formatDate(new Date(Date.now() - 86400000 * 3)))
const dateTo = ref(formatDate(new Date()))

onMounted(() => loadRuns())

async function loadRuns() {
  loading.value = true
  const params: Record<string, string | number | boolean> = { limit: 100 }
  if (okFilter.value === 'success') params.ok = true
  if (okFilter.value === 'failed') params.ok = false
  if (dateFrom.value) params.created_from = `${dateFrom.value} 00:00:00`
  if (dateTo.value) params.created_to = `${dateTo.value} 23:59:59`
  try {
    runs.value = await api.listAgentRuns(params)
  } catch {
    runs.value = []
  }
  loading.value = false
}

function applyOkFilter(key: '' | 'success' | 'failed') {
  okFilter.value = key
  loadRuns()
}

async function openDetail(run: AgentRun) {
  detailRun.value = run
  showDetail.value = true
  detailLoading.value = true
  try {
    detailRun.value = await api.getAgentRun(run.run_key)
  } catch {
    /* keep list data as fallback */
  }
  detailLoading.value = false
}

const displayRuns = computed(() => {
  if (!search.value) return runs.value
  const q = search.value.toLowerCase()
  return runs.value.filter(
    r =>
      r.agent_name?.toLowerCase().includes(q) ||
      r.profile_key?.toLowerCase().includes(q) ||
      r.workflow_key?.toLowerCase().includes(q) ||
      r.error?.toLowerCase().includes(q),
  )
})

const filterTabs = computed(() => [
  { key: '' as const, label: '全部', count: runs.value.length },
  { key: 'success' as const, label: '成功', count: runs.value.filter(r => r.ok).length },
  { key: 'failed' as const, label: '失败', count: runs.value.filter(r => !r.ok).length },
])

function formatCost(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${Number(v).toFixed(4)}`
}

function pretty(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function eventKindLabel(kind: string): string {
  switch (kind) {
    case 'agent_message':
      return '消息'
    case 'tool_call':
      return '调用工具'
    case 'tool_result':
      return '工具结果'
    case 'status':
      return '状态'
    case 'result':
      return '完成'
    case 'error':
      return '错误'
    default:
      return kind
  }
}

function eventKindClass(kind: string, status?: string): string {
  if (kind === 'error' || status === 'failed') return 'bg-red-50 text-red-700'
  if (kind === 'result' || status === 'success') return 'bg-green-50 text-green-700'
  if (kind === 'tool_call') return 'bg-blue-50 text-blue-700'
  if (kind === 'tool_result') return 'bg-violet-50 text-violet-700'
  return 'bg-secondary text-muted-foreground'
}
</script>

<template>
  <div v-if="loading && runs.length === 0" class="py-12 text-center text-sm text-muted-foreground">
    加载中...
  </div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[280px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <Input v-model="search" placeholder="搜索 Agent、Profile 或工作流..." class="pl-8" />
      </div>
      <div class="flex items-center gap-2 text-sm">
        <Input v-model="dateFrom" type="date" class="w-[140px]" @change="loadRuns" />
        <span class="text-muted-foreground">至</span>
        <Input v-model="dateTo" type="date" class="w-[140px]" @change="loadRuns" />
      </div>
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="tab in filterTabs"
          :key="tab.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            okFilter === tab.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          ]"
          @click="applyOkFilter(tab.key)"
        >
          {{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span>
        </button>
      </div>
      <Button variant="outline" @click="loadRuns">
        <RotateCw :size="14" class="mr-1.5" />
        刷新
      </Button>
    </div>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="displayRuns.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
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
              v-for="r in displayRuns"
              :key="r.run_key"
              class="border-b border-border/60 transition-colors hover:bg-muted/50"
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
                <Badge v-if="r.ok" variant="secondary" class="bg-green-50 text-green-700">成功</Badge>
                <Badge v-else variant="destructive">失败</Badge>
              </td>
              <td class="px-4 py-3">
                <Button variant="ghost" size="sm" class="h-8 text-xs" @click="openDetail(r)">详情</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>共 {{ displayRuns.length }} 条记录</span>
    </div>

    <!-- Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[760px]">
        <DialogHeader>
          <DialogTitle>Agent 运行详情</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="detailRun" class="max-h-[76vh] space-y-4 overflow-y-auto pr-2">
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span class="text-muted-foreground">Agent</span>
              <div class="font-mono font-medium">{{ detailRun.agent_name }}</div>
            </div>
            <div>
              <span class="text-muted-foreground">状态</span>
              <div>
                <Badge v-if="detailRun.ok" variant="secondary" class="bg-green-50 text-green-700">成功</Badge>
                <Badge v-else variant="destructive">失败</Badge>
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
            <div class="col-span-2">
              <span class="text-muted-foreground">工作目录</span>
              <div class="font-mono text-xs font-medium">{{ detailRun.cwd || '—' }}</div>
            </div>
          </div>

          <div
            v-if="detailRun.error"
            class="rounded-lg border border-destructive/30 bg-red-50 px-4 py-3 text-sm text-destructive"
          >
            {{ detailRun.error }}
          </div>

          <div v-if="detailRun.prompt">
            <div class="mb-1 text-xs font-medium text-muted-foreground">Prompt</div>
            <pre class="max-h-[180px] overflow-auto whitespace-pre-wrap rounded-lg bg-secondary px-4 py-3 text-xs">{{ detailRun.prompt }}</pre>
          </div>

          <div v-if="detailRun.result != null">
            <div class="mb-1 text-xs font-medium text-muted-foreground">结果</div>
            <pre class="max-h-[180px] overflow-auto rounded-lg bg-secondary px-4 py-3 text-xs">{{ pretty(detailRun.result) }}</pre>
          </div>

          <div v-if="detailRun.events && detailRun.events.length">
            <div class="mb-1 text-xs font-medium text-muted-foreground">
              事件流（{{ detailRun.events.length }}）
            </div>
            <div class="space-y-1.5">
              <div
                v-for="(ev, i) in detailRun.events"
                :key="i"
                class="flex items-start gap-2 rounded-md border border-border/60 px-3 py-2 text-xs"
              >
                <Badge variant="secondary" :class="eventKindClass(ev.kind, ev.status)">
                  {{ eventKindLabel(ev.kind) }}
                </Badge>
                <div class="flex-1">
                  <div v-if="ev.message" class="break-all">{{ ev.message }}</div>
                  <div class="mt-0.5 flex flex-wrap gap-x-3 text-muted-foreground">
                    <span v-if="ev.tool_name" class="font-mono">{{ ev.tool_name }}</span>
                    <span v-if="ev.status">状态: {{ ev.status }}</span>
                    <span v-if="ev.num_turns != null">轮数: {{ ev.num_turns }}</span>
                    <span v-if="ev.total_cost_usd != null">{{ formatCost(ev.total_cost_usd) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
