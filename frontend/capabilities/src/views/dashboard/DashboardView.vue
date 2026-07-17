<script setup lang="ts">
import { Server, Wrench, CheckCircle, XCircle, Plus, RotateCw } from 'lucide-vue-next'
import { ref, computed, onMounted } from 'vue'
import { api } from '../../api/client'
import type { McpService } from '../../api/types'
import { timeAgo } from '../../lib/time'
import StatCard from '../../components/StatCard.vue'
import StatusBadge from '../../components/StatusBadge.vue'

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

// 语义状态圆点：颜色只走令牌派生（替代原 emerald/red/gray 裸色）
function statusDotClass(s: McpService) {
  if (s.status === 'enabled') return 'bg-success'
  if (s.status === 'error') return 'bg-destructive'
  return 'bg-muted-foreground/40'
}

// 状态圆点 → StatusBadge 的 status 映射
function statusOf(s: McpService): 'enabled' | 'error' | 'disabled' {
  if (s.status === 'enabled') return 'enabled'
  if (s.status === 'error') return 'error'
  return 'disabled'
}

const disabledCount = computed(() => services.value.length - enabledCount.value - errorCount.value)
</script>

<template>
  <div v-if="loading" class="py-16 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-6">
    <!-- Stat Cards -->
    <div class="grid grid-cols-4 gap-4">
      <StatCard label="MCP 服务" :value="services.length">
        <template #icon><Server :size="18" /></template>
        <template #sub><span class="text-muted-foreground">—</span> 全部已连接</template>
      </StatCard>
      <StatCard label="工具总数" :value="toolCount ?? '...'">
        <template #icon><Wrench :size="18" /></template>
        <template #sub><span class="text-muted-foreground">—</span> 已同步</template>
      </StatCard>
      <StatCard label="启用服务" :value="enabledCount">
        <template #icon><CheckCircle :size="18" /></template>
        <template #sub>
          <span class="text-muted-foreground">{{ services.length ? Math.round((enabledCount / services.length) * 100) : 0 }}%</span> 运行率
        </template>
      </StatCard>
      <StatCard label="同步异常" :value="errorCount">
        <template #icon><XCircle :size="18" /></template>
        <template #sub>
          <span v-if="errorCount > 0" class="cursor-pointer text-destructive hover:underline" @click="goto('services')">查看</span>
          <span v-else class="text-muted-foreground">一切正常</span>
        </template>
      </StatCard>
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
            <span class="h-2 w-2 rounded-full shrink-0" :class="statusDotClass(s)" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium">{{ s.service_key }}</div>
              <div class="truncate text-xs text-muted-foreground">{{ s.description || s.name }}</div>
            </div>
            <StatusBadge :status="statusOf(s)" class="shrink-0" />
            <span class="whitespace-nowrap text-xs text-muted-foreground">{{ timeAgo(s.last_synced_at) }}</span>
          </div>
        </div>
      </div>

      <!-- Right Column -->
      <div class="space-y-6">
        <!-- Quick Actions -->
        <div class="grid grid-cols-2 gap-3">
          <button
            class="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-4 text-center transition-all hover:border-primary/40 hover:shadow-card"
            @click="goto('services')"
          >
            <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-primary">
              <Plus :size="16" />
            </div>
            <span class="text-[13px] font-medium">新增服务</span>
          </button>
          <button
            class="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-4 text-center transition-all hover:border-primary/40 hover:shadow-card"
            @click="goto('tools')"
          >
            <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-primary">
              <RotateCw :size="16" />
            </div>
            <span class="text-[13px] font-medium">同步工具</span>
          </button>
        </div>

        <!-- Health Overview -->
        <div class="rounded-lg border border-border bg-card p-5 shadow-card">
          <div class="mb-4 text-sm font-medium">服务健康概况</div>

          <div class="mb-4 flex h-2 overflow-hidden rounded-full bg-secondary">
            <div class="rounded-full bg-success transition-all" :style="{ width: services.length ? `${(enabledCount / services.length) * 100}%` : '0%' }" />
            <div class="rounded-full bg-destructive transition-all" :style="{ width: services.length ? `${(errorCount / services.length) * 100}%` : '0%' }" />
          </div>

          <div class="space-y-2.5">
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-success" />
                <span class="text-muted-foreground">正常运行</span>
              </div>
              <span class="tabular-nums font-medium">{{ enabledCount }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-muted-foreground/40" />
                <span class="text-muted-foreground">已停用</span>
              </div>
              <span class="tabular-nums font-medium">{{ disabledCount }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full bg-destructive" />
                <span class="text-muted-foreground">异常</span>
              </div>
              <span class="tabular-nums font-medium">{{ errorCount }}</span>
            </div>
          </div>

          <div v-if="errorCount > 0" class="mt-4 space-y-2 border-t border-border pt-4">
            <div class="text-xs text-muted-foreground">需要关注</div>
            <div v-for="s in services.filter(x => x.status === 'error')" :key="s.service_key" class="rounded-lg bg-destructive-soft px-3 py-2">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium">{{ s.service_key }}</span>
                <StatusBadge status="error" label="连接失败" />
              </div>
              <div v-if="s.last_error" class="mt-1 text-xs text-destructive">{{ s.last_error }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
