<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'

const stats = ref<Record<string, unknown>[]>([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    const r = await api.stats({ dimensions: 'profile_key,source_key,tool_name' })
    stats.value = (r as unknown as { buckets: Record<string, unknown>[] }).buckets || []
  } catch { stats.value = [] }
  loading.value = false
})
</script>

<template>
  <div class="space-y-5">
    <Card class="border-border">
      <CardHeader>
        <CardTitle>调用统计</CardTitle>
      </CardHeader>
      <CardContent class="p-0">
        <div v-if="loading" class="px-5 py-12 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="stats.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无统计数据</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Profile</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">来源</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">工具</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">调用次数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in stats" :key="i" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3 text-sm">{{ s.profile_key || '—' }}</td>
              <td class="px-4 py-3 text-sm">{{ s.source_key || '—' }}</td>
              <td class="px-4 py-3 text-sm">{{ s.tool_name || '—' }}</td>
              <td class="px-4 py-3 font-semibold tabular-nums">{{ s.count }}</td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
  </div>
</template>
