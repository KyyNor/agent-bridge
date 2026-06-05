<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import { Card, CardContent } from '../components/ui/card'
import { Button } from '../components/ui/button'

const stats = ref<Record<string, unknown>[]>([])
const loading = ref(false)
const dimension = ref('profile_key,source_key,tool_name')

const dimensions = [
  { key: 'profile_key,source_key,tool_name', label: '全部维度' },
  { key: 'profile_key', label: '按 Profile' },
  { key: 'source_key', label: '按来源' },
  { key: 'tool_name', label: '按工具' },
]

onMounted(() => loadStats())

async function loadStats() {
  loading.value = true
  try {
    const r = await api.stats({ dimensions: dimension.value })
    stats.value = (r as unknown as { buckets: Record<string, unknown>[] }).buckets || []
  } catch { stats.value = [] }
  loading.value = false
}

function applyDimension(key: string) {
  dimension.value = key
  loadStats()
}

const columns = computed(() => {
  if (stats.value.length === 0) return []
  return Object.keys(stats.value[0]).filter(k => k !== 'count')
})

const columnLabels: Record<string, string> = {
  profile_key: 'Profile',
  source_key: '来源',
  tool_name: '工具',
}

const totalCount = computed(() => stats.value.reduce((sum, s) => sum + Number(s.count || 0), 0))
const maxCount = computed(() => stats.value.length ? Math.max(...stats.value.map(s => Number(s.count || 0))) : 0)
</script>

<template>
  <div class="space-y-5">
    <!-- Summary Cards -->
    <div class="grid grid-cols-4 gap-4">
      <div class="rounded-lg border border-border bg-card p-4">
        <div class="text-[13px] font-medium text-muted-foreground">总调用次数</div>
        <div class="text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ totalCount }}</div>
      </div>
      <div class="rounded-lg border border-border bg-card p-4">
        <div class="text-[13px] font-medium text-muted-foreground">维度条目</div>
        <div class="text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ stats.length }}</div>
      </div>
      <div class="rounded-lg border border-border bg-card p-4">
        <div class="text-[13px] font-medium text-muted-foreground">最高频次</div>
        <div class="text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ maxCount }}</div>
      </div>
      <div class="rounded-lg border border-border bg-card p-4">
        <div class="text-[13px] font-medium text-muted-foreground">当前维度</div>
        <div class="text-[24px] font-bold leading-tight text-foreground">{{ dimensions.find(d => d.key === dimension)?.label }}</div>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="d in dimensions" :key="d.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            dimension === d.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="applyDimension(d.key)"
        >{{ d.label }}</button>
      </div>
      <Button variant="outline" @click="loadStats">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mr-1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </Button>
    </div>

    <!-- Table -->
    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="loading" class="px-5 py-12 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="stats.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无统计数据</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th v-for="col in columns" :key="col" class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ columnLabels[col] || col }}</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">调用次数</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in stats" :key="i" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td v-for="col in columns" :key="col" class="px-4 py-3 text-sm">{{ (s as Record<string, unknown>)[col] || '—' }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-3">
                  <div class="h-2 rounded-full bg-primary" :style="{ width: `${Math.max(8, (Number(s.count) / maxCount) * 120)}px` }" />
                  <span class="font-semibold tabular-nums">{{ s.count }}</span>
                </div>
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">{{ totalCount ? ((Number(s.count) / totalCount) * 100).toFixed(1) : 0 }}%</td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
  </div>
</template>
