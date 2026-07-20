<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Files, Folder, FolderOpen, Move, Pencil, Plus, Trash2 } from '@lucide/vue'
import type { KnowledgeFolder } from '../../api/types'

const props = withDefaults(defineProps<{
  folders: KnowledgeFolder[]
  selectedId: number | null
  allSelected?: boolean
  rootLabel?: string
  allCount?: number
  loading?: boolean
  actionsEnabled?: boolean
  showAll?: boolean
  compact?: boolean
}>(), {
  allSelected: false,
  rootLabel: '根目录',
  allCount: 0,
  loading: false,
  actionsEnabled: true,
  showAll: true,
  compact: false,
})

const emit = defineEmits<{
  select: [folderId: number | null]
  create: [parentFolderId: number]
  rename: [folder: KnowledgeFolder]
  move: [folder: KnowledgeFolder]
  remove: [folder: KnowledgeFolder]
}>()

const expandedIds = ref<Set<number>>(new Set())

watch(() => props.folders, folders => {
  const ids = new Set(folders.map(folder => folder.id))
  const next = new Set([...expandedIds.value].filter(id => ids.has(id)))
  folders.filter(folder => folder.is_root).forEach(folder => next.add(folder.id))
  expandedIds.value = next
}, { deep: true, immediate: true })

const childrenByParent = computed(() => {
  const children = new Map<number | null, KnowledgeFolder[]>()
  for (const folder of props.folders) {
    const list = children.get(folder.parent_id) || []
    list.push(folder)
    children.set(folder.parent_id, list)
  }
  for (const list of children.values()) {
    list.sort((a, b) => a.path.localeCompare(b.path, 'zh-CN') || a.id - b.id)
  }
  return children
})

const rows = computed(() => {
  const result: Array<{ folder: KnowledgeFolder; depth: number }> = []
  const append = (folder: KnowledgeFolder, depth: number) => {
    result.push({ folder, depth })
    if (!expandedIds.value.has(folder.id)) return
    for (const child of childrenByParent.value.get(folder.id) || []) append(child, depth + 1)
  }
  for (const root of childrenByParent.value.get(null) || []) append(root, 0)
  return result
})

function toggleExpanded(folder: KnowledgeFolder) {
  const next = new Set(expandedIds.value)
  if (next.has(folder.id)) next.delete(folder.id)
  else next.add(folder.id)
  expandedIds.value = next
}

function hasChildren(folder: KnowledgeFolder) {
  return (childrenByParent.value.get(folder.id) || []).length > 0
}

function fileCount(folder: KnowledgeFolder) {
  return folder.descendant_file_count ?? folder.file_count ?? 0
}

function folderCount(folder: KnowledgeFolder) {
  return folder.descendant_folder_count ?? Math.max((folder.folder_count ?? 1) - 1, 0)
}
</script>

<template>
  <div class="space-y-1">
    <div v-if="loading" class="px-2 py-5 text-center text-xs text-muted-foreground">目录加载中...</div>
    <template v-else>
      <button
        v-if="showAll"
        type="button"
        :class="[
          'flex w-full items-center gap-2 rounded-md px-2 text-left transition-colors',
          compact ? 'py-1.5 text-xs' : 'py-2 text-sm',
          allSelected ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
        ]"
        @click="emit('select', null)"
      >
        <Files :size="compact ? 13 : 14" />
        <span class="min-w-0 flex-1 truncate">全部文档</span>
        <span class="shrink-0 text-[11px] tabular-nums text-muted-foreground">{{ allCount }}</span>
      </button>

      <div v-if="rows.length === 0" class="px-2 py-4 text-xs text-muted-foreground">暂无目录</div>
      <div
        v-for="row in rows"
        :key="row.folder.id"
        :class="[
          'group flex items-center gap-1 rounded-md transition-colors',
          compact ? 'min-h-7' : 'min-h-8',
          !allSelected && selectedId === row.folder.id ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60',
        ]"
        :style="{ paddingLeft: `${row.depth * (compact ? 12 : 16) + 4}px` }"
      >
        <button
          type="button"
          class="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          :aria-label="expandedIds.has(row.folder.id) ? '折叠目录' : '展开目录'"
          @click.stop="hasChildren(row.folder) && toggleExpanded(row.folder)"
        >
          <ChevronDown v-if="hasChildren(row.folder) && expandedIds.has(row.folder.id)" :size="13" />
          <ChevronRight v-else-if="hasChildren(row.folder)" :size="13" />
        </button>
        <button
          type="button"
          class="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left"
          @click="emit('select', row.folder.id)"
        >
          <FolderOpen v-if="row.folder.is_root || expandedIds.has(row.folder.id)" :size="compact ? 13 : 14" class="shrink-0 text-primary/80" />
          <Folder v-else :size="compact ? 13 : 14" class="shrink-0 text-muted-foreground" />
          <span class="min-w-0 flex-1 truncate" :title="row.folder.path || rootLabel">{{ row.folder.is_root ? rootLabel : row.folder.name }}</span>
          <span class="shrink-0 whitespace-nowrap text-[10px] tabular-nums text-muted-foreground">
            {{ folderCount(row.folder) }}目录 · {{ fileCount(row.folder) }}文件
          </span>
        </button>
        <div v-if="actionsEnabled" class="mr-1 flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="新建子目录" @click.stop="emit('create', row.folder.id)">
            <Plus :size="12" />
          </button>
          <template v-if="!row.folder.is_root">
            <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="重命名" @click.stop="emit('rename', row.folder)">
              <Pencil :size="12" />
            </button>
            <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="移动目录" @click.stop="emit('move', row.folder)">
              <Move :size="12" />
            </button>
            <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive" title="删除目录" @click.stop="emit('remove', row.folder)">
              <Trash2 :size="12" />
            </button>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>
