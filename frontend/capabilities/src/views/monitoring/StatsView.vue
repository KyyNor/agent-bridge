<script setup lang="ts">
import { RotateCw } from '@lucide/vue'
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'
import { formatDuration } from '../../lib/time'

const stats = ref<Record<string, unknown>[]>([])
const loading = ref(false)
const dimension = ref('profile_key,source_key,tool_name')
const page = ref(1)
const pageSize = ref(10)

const dimensions = [
  { key: 'profile_key,source_key,tool_name', label: '全部维度' },
  { key: 'source_key', label: '按服务' },
  { key: 'source_key,tool_type', label: '按服务+层级' },
  { key: 'source_key,tool_name', label: '按工具' },
  { key: 'profile_key', label: '按 Profile' },
]

onMounted(() => loadStats())

async function loadStats() {
  loading.value = true
  try {
    const r = await api.stats({ dimensions: dimension.value })
    stats.value = r.items || []
  } catch { stats.value = [] }
  loading.value = false
}

function applyDimension(key: string) {
  dimension.value = key
  page.value = 1
  loadStats()
}

const columns = computed(() => {
  if (stats.value.length === 0) return []
  return Object.keys(stats.value[0]).filter(k => k !== 'calls')
})

const columnLabels: Record<string, string> = {
  profile_key: 'Profile',
  source_key: '服务',
  tool_type: '层级',
  tool_name: '工具',
  calls: '调用次数',
  success: '成功',
  error: '失败',
  blocked: '拦截',
  avg_duration_ms: '平均耗时',
  max_duration_ms: '最大耗时',
}

function callCount(s: Record<string, unknown>) {
  return Number(s.calls || 0)
}

// 耗时类列以秒展示（avg_duration_ms / max_duration_ms），其余原样输出
function formatCell(col: string, value: unknown): string {
  if (col.endsWith('_duration_ms')) {
    const formatted = formatDuration(value == null ? null : Number(value))
    return formatted || '—'
  }
  return (value as string | number | null) != null ? String(value) : '—'
}

const totalCount = computed(() => stats.value.reduce((sum, s) => sum + callCount(s), 0))
const maxCount = computed(() => stats.value.length ? Math.max(...stats.value.map(callCount)) : 0)
const pagedStats = computed(() => paginate(stats.value, page.value, pageSize.value))
</script>

<template>
  <div class="space-y-5">
    <!-- Summary Cards -->
    <div class="grid grid-cols-4 gap-4">
      <Card class="p-4">
        <div class="text-[13px] font-medium text-muted-foreground">总调用次数</div>
        <div class="mt-1 text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ totalCount }}</div>
      </Card>
      <Card class="p-4">
        <div class="text-[13px] font-medium text-muted-foreground">维度条目</div>
        <div class="mt-1 text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ stats.length }}</div>
      </Card>
      <Card class="p-4">
        <div class="text-[13px] font-medium text-muted-foreground">最高频次</div>
        <div class="mt-1 text-[24px] font-bold leading-tight tabular-nums text-foreground">{{ maxCount }}</div>
      </Card>
      <Card class="p-4">
        <div class="text-[13px] font-medium text-muted-foreground">当前维度</div>
        <div class="mt-1 text-[24px] font-bold leading-tight text-foreground">{{ dimensions.find(d => d.key === dimension)?.label }}</div>
      </Card>
    </div>

    <!-- 页头筛选：维度切换进全局 PageHeader 的 #ph-filters -->
    <Teleport to="#ph-filters" defer>
      <SegmentedTabs
        :model-value="dimension"
        :tabs="dimensions"
        @update:model-value="applyDimension"
      />
    </Teleport>

    <!-- 页头操作：刷新进全局 PageHeader 的 #ph-actions -->
    <Teleport to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="loadStats">
        <RotateCw :size="14" />
        刷新
      </Button>
    </Teleport>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="loading" class="px-5 py-12 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="stats.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无统计数据</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th v-for="col in columns" :key="col" class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">{{ columnLabels[col] || col }}</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">调用次数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in pagedStats" :key="i" class="border-b border-border/60">
              <td v-for="col in columns" :key="col" class="px-4 py-3 text-sm tabular-nums">{{ formatCell(col, (s as Record<string, unknown>)[col]) }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-3">
                  <div class="h-2 rounded-full bg-primary" :style="{ width: `${Math.max(8, (callCount(s) / maxCount) * 120)}px` }" />
                  <span class="font-semibold tabular-nums">{{ callCount(s) }}</span>
                </div>
              </td>
              <td class="px-4 py-3 text-sm tabular-nums text-muted-foreground">{{ totalCount ? ((callCount(s) / totalCount) * 100).toFixed(1) : 0 }}%</td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
    <PaginationBar
      v-if="stats.length"
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="stats.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />
  </div>
</template>
