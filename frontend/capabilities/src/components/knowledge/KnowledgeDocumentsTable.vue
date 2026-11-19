<script setup lang="ts">
import { Archive, Folder } from '@lucide/vue'
import { ref } from 'vue'
import { api } from '../../api/client'
import type { Document, KnowledgeBrowseEntry } from '../../api/types'
import { alert } from '../../composables/useConfirm'
import { saveBlob } from '../../lib/download'
import { syncBadgeLabel, syncBadgeStatus } from '../../lib/knowledgeSyncStatus'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'
import StatusBadge from '../StatusBadge.vue'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'

const props = defineProps<{
  kbSlug: string
  rows: (Document | KnowledgeBrowseEntry)[]
  selected: Set<string>
  allSelected: boolean
  someSelected: boolean
  readOnly: boolean
  canAttach: boolean
}>()

const emit = defineEmits<{
  enter: [entry: KnowledgeBrowseEntry]
  select: [slug: string]
  selectAll: []
  detail: [slug: string]
  move: [slug: string]
  attach: [slug: string]
  remove: [slug: string, title: string]
}>()

const downloading = ref(new Set<string>())
async function downloadDocument(slug: string) {
  if (downloading.value.has(slug)) return
  downloading.value.add(slug)
  try {
    const { blob, filename } = await api.downloadKnowledgeDocument(props.kbSlug, slug)
    saveBlob(blob, filename)
  } catch (error: unknown) {
    await alert({ title: '下载失败', description: error instanceof Error ? error.message : '请稍后重试', destructive: true })
  } finally {
    downloading.value.delete(slug)
  }
}
</script>

<template>
  <div class="min-h-0 max-h-[calc(100vh-420px)] overflow-auto rounded-lg border border-border">
    <table class="w-full">
      <thead><tr class="border-b border-border">
        <th class="w-7 px-3 py-2 text-left">
          <input type="checkbox" aria-label="全选文档" class="size-4 rounded" :checked="allSelected"
            :indeterminate.prop="someSelected && !allSelected" @change="emit('selectAll')" />
        </th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标题</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">目录</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">版本</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
        <th class="px-3 py-2"><span class="sr-only">操作</span></th>
      </tr></thead>
      <tbody>
        <template v-for="entry in rows" :key="'kind' in entry ? `${entry.kind}-${entry.id}` : entry.slug">
          <tr v-if="'kind' in entry && entry.kind !== 'document'" class="border-b border-border/60">
            <td colspan="6" class="px-3 py-2">
              <button type="button" class="flex w-full items-center gap-2 text-left" @click="emit('enter', entry)">
                <Archive v-if="entry.kind === 'zip'" :size="16" class="shrink-0 text-warning" />
                <Folder v-else :size="16" class="shrink-0 text-primary/80" />
                <span class="min-w-0 flex-1 truncate text-sm font-medium" :title="entry.relative_path">{{ entry.name }}</span>
                <Badge variant="outline" class="shrink-0 text-[10px]">{{ entry.kind === 'zip' ? 'ZIP' : '文件夹' }}</Badge>
                <span class="shrink-0 text-xs text-muted-foreground">{{ entry.child_count }} 个文件</span>
              </button>
            </td>
          </tr>
          <tr v-else class="border-b border-border/60">
            <td class="px-3 py-2">
              <input type="checkbox" class="size-4 rounded" :aria-label="`选择 ${entry.title}`"
                :checked="selected.has(entry.slug)" @change="emit('select', entry.slug)" />
            </td>
            <td class="px-3 py-2 text-sm font-medium" :title="'kind' in entry ? entry.relative_path : entry.title">{{ entry.title }}</td>
            <td class="max-w-[180px] truncate px-3 py-2 text-xs text-muted-foreground" :title="'kind' in entry ? entry.relative_path : entry.folder_path || '根目录'">
              {{ 'kind' in entry ? entry.name : entry.folder_path || '根目录' }}
            </td>
            <td class="px-3 py-2 text-xs tabular-nums">v{{ ('kind' in entry ? entry.version_no : entry.current_version_no) || 0 }}</td>
            <td class="px-3 py-2">
              <StatusBadge :status="syncBadgeStatus(entry.sync_status || entry.status)" :label="syncBadgeLabel(entry.sync_status || entry.status)" />
            </td>
            <td class="px-3 py-2">
              <div class="flex flex-wrap justify-end gap-1">
                <Button variant="ghost" size="sm" class="h-7 text-xs" @click="emit('detail', entry.slug)">详情</Button>
                <Button v-if="!readOnly" variant="ghost" size="sm" class="h-7 text-xs" :disabled="downloading.has(entry.slug)" @click="downloadDocument(entry.slug)">下载</Button>
                <Button variant="ghost" size="sm" class="h-7 text-xs" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined" @click="emit('move', entry.slug)">移动</Button>
                <Button v-if="canAttach" variant="ghost" size="sm" class="h-7 text-xs" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined" @click="emit('attach', entry.slug)">关联</Button>
                <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined" @click="emit('remove', entry.slug, entry.title)">删除</Button>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>
