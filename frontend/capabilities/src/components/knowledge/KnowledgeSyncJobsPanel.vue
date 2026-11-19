<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../../api/client'
import type { SyncJob } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { syncBadgeLabel, syncBadgeStatus } from '../../lib/knowledgeSyncStatus'
import StatusBadge from '../StatusBadge.vue'
import { Button } from '../ui/button'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'

const props = defineProps<{
  kbSlug: string
  jobs: SyncJob[]
  onSynced: () => Promise<void>
  readOnly?: boolean
}>()

const syncing = ref(false)

async function triggerSync() {
  syncing.value = true
  try {
    await api.triggerKbSync(props.kbSlug)
    await props.onSynced()
  } catch {
    // 请求失败时保留当前任务表，用户可再次发起同步。
  } finally {
    syncing.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3">
      <Button size="sm" @click="triggerSync" :disabled="syncing || readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">{{ syncing ? '同步中...' : '立即同步' }}</Button>
      <span class="text-sm text-muted-foreground">处理当前知识库已启用同步的后端任务</span>
    </div>
    <div v-if="jobs.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无同步任务</div>
    <table v-else class="w-full">
      <thead><tr class="border-b border-border">
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">文档</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">操作</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">后端</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">错误</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">时间</th>
      </tr></thead>
      <tbody><tr v-for="job in jobs" :key="job.id" class="border-b border-border/60">
        <td class="px-3 py-2 text-sm">{{ job.doc_title }}</td>
        <td class="px-3 py-2 text-xs">{{ job.operation }}</td>
        <td class="px-3 py-2"><StatusBadge :status="syncBadgeStatus(job.status)" :label="syncBadgeLabel(job.status)" /></td>
        <td class="px-3 py-2 text-xs text-muted-foreground">{{ job.backend_slug }}</td>
        <td class="px-3 py-2 max-w-[200px] overflow-hidden text-ellipsis text-xs text-destructive" :title="job.error ?? ''">{{ job.error || '—' }}</td>
        <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ formatLocalDatetime(job.updated_at) }}</td>
      </tr></tbody>
    </table>
  </div>
</template>
