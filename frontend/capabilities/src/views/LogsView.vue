<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { ToolCallLog } from '../api/types'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'

const logs = ref<ToolCallLog[]>([])
const loading = ref(false)
const filters = ref({ status: '', limit: 50 })

onMounted(() => loadLogs())

async function loadLogs() {
  loading.value = true
  const params: Record<string, string | number> = { limit: filters.value.limit }
  if (filters.value.status) params.status = filters.value.status
  try { logs.value = await api.listLogs(params) } catch { logs.value = [] }
  loading.value = false
}
</script>

<template>
  <div class="space-y-5">
    <Card class="border-border">
      <CardHeader>
        <div class="flex items-center justify-between gap-4">
          <CardTitle>调用日志</CardTitle>
          <div class="flex items-center gap-3">
            <select v-model="filters.status" @change="loadLogs" class="rounded-lg border border-input px-3 py-2 text-sm outline-none focus:border-primary">
              <option value="">全部状态</option>
              <option value="success">成功</option>
              <option value="error">失败</option>
              <option value="blocked">拦截</option>
            </select>
            <Button variant="outline" @click="loadLogs">刷新</Button>
          </div>
        </div>
      </CardHeader>
      <CardContent class="p-0">
        <div v-if="loading" class="px-5 py-12 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="logs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无调用日志</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">时间</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">调用者</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">入口</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">工具</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="l in logs" :key="l.log_id" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ l.created_at?.slice(0, 19) }}</td>
              <td class="px-4 py-3 text-sm">{{ l.actor }}</td>
              <td class="px-4 py-3 text-sm">{{ l.entrypoint }}</td>
              <td class="px-4 py-3 text-sm">{{ l.tool_name || '—' }}</td>
              <td class="px-4 py-3">
                <Badge v-if="l.status === 'success'" variant="secondary" class="bg-green-50 text-green-700">成功</Badge>
                <Badge v-else-if="l.status === 'error'" variant="destructive">失败</Badge>
                <Badge v-else-if="l.status === 'blocked'" variant="secondary" class="bg-amber-50 text-amber-700">拦截</Badge>
                <Badge v-else variant="secondary">{{ l.status }}</Badge>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
  </div>
</template>
