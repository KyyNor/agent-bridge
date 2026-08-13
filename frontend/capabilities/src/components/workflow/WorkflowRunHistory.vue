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
    <div v-else class="overflow-hidden rounded-md border border-border">
      <div class="hidden grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_120px_150px] gap-4 border-b bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground md:grid">
        <span>运行 ID</span>
        <span>任务</span>
        <span>状态</span>
        <span>开始时间</span>
      </div>
      <div class="divide-y">
        <button
          v-for="run in runs"
          :key="run.run_id"
          class="list-row-interactive grid w-full gap-1 px-3 py-3 text-left md:grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_120px_150px] md:items-center md:gap-4"
          @click="emit('open', run.run_id)"
        >
          <span class="truncate font-mono text-xs text-foreground">{{ run.run_id }}</span>
          <span class="truncate text-xs text-muted-foreground">{{ run.task_key || '手动运行' }}</span>
          <span><Badge :variant="run.status === 'completed' ? 'secondary' : 'outline'" :class="badgeClass(run.status)">{{ statusLabel(run.status) }}</Badge></span>
          <span class="text-xs text-muted-foreground">{{ formatDatetime(run.started_at) }}</span>
        </button>
      </div>
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
