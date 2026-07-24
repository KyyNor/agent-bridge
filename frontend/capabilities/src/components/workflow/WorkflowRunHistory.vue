<script setup lang="ts">
import type { WorkflowRunSummary } from '../../api/types'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import PaginationBar from '../PaginationBar.vue'

defineProps<{
  runs: WorkflowRunSummary[]
  total: number
  loading: boolean
  page: number
  pageSize: number
  pageSizeOptions: readonly number[]
  statusLabel: (status: string) => string
  badgeClass: (status: string) => string
  formatDatetime: (value: string | null) => string
}>()

const emit = defineEmits<{
  refresh: []
  open: [runId: string]
  'update:page': [value: number]
  'update:pageSize': [value: number]
}>()
</script>

<template>
  <section class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
    <div class="flex items-center justify-between">
      <h3 class="text-sm font-semibold">运行记录</h3>
      <Button variant="outline" size="sm" :disabled="loading" @click="emit('refresh')">{{ loading ? '刷新中' : '刷新' }}</Button>
    </div>
    <div v-if="loading" class="py-4 text-center text-sm text-muted-foreground">加载中</div>
    <div v-else-if="!runs.length" class="rounded-md border px-4 py-6 text-sm text-muted-foreground">暂无运行记录</div>
    <div v-else class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
      <button v-for="run in runs" :key="run.run_id" class="list-row-interactive rounded-md border px-3 py-2 text-left" @click="emit('open', run.run_id)">
        <div class="flex items-center justify-between gap-2">
          <span class="truncate font-mono text-xs">{{ run.run_id }}</span>
          <Badge :variant="run.status === 'completed' ? 'secondary' : 'outline'" :class="badgeClass(run.status)">{{ statusLabel(run.status) }}</Badge>
        </div>
        <div class="mt-1 text-xs text-muted-foreground">{{ formatDatetime(run.started_at) }}</div>
      </button>
    </div>
    <PaginationBar
      v-if="runs.length"
      :page="page"
      :page-size="pageSize"
      :total="total"
      :page-size-options="pageSizeOptions"
      @update:page="emit('update:page', $event)"
      @update:page-size="emit('update:pageSize', $event)"
    />
  </section>
</template>
