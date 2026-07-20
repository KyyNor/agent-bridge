<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search } from '@lucide/vue'
import Input from '../ui/input/Input.vue'
import type { WorkflowReferenceItem } from '../../lib/workflowReferences'
import { formatWorkflowReference } from '../../lib/workflowReferences'

const props = defineProps<{
  items: WorkflowReferenceItem[]
  mode: 'template' | 'condition'
}>()

const emit = defineEmits<{
  insert: [value: string, rawPath: string]
  /** 双击插入后触发，携带被插入项的 path，供父组件显示 banner */
  inserted: [rawPath: string]
}>()
const query = ref('')
const filteredItems = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return props.items
  return props.items.filter(item =>
    item.path.toLowerCase().includes(needle)
    || item.type.toLowerCase().includes(needle)
    || item.description.toLowerCase().includes(needle),
  )
})

function insert(item: WorkflowReferenceItem) {
  emit('insert', formatWorkflowReference(item, props.mode), item.path)
  emit('inserted', item.path)
}
</script>

<template>
  <div class="rounded-sm border bg-muted/10 p-2">
    <div class="mb-2 flex items-center gap-2">
      <Search class="h-4 w-4 shrink-0 text-muted-foreground" />
      <Input v-model="query" class="h-7" placeholder="搜索可引用数据" />
    </div>
    <p class="mb-1 px-1 text-[11px] text-muted-foreground">双击变量插入到当前输入框</p>
    <div class="max-h-48 overflow-auto">
      <button
        v-for="item in filteredItems"
        :key="item.path"
        type="button"
        class="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-2 border-t px-1 py-2 text-left text-xs first:border-t-0 hover:bg-accent"
        @dblclick="insert(item)"
      >
        <span class="min-w-0">
          <span class="block truncate font-mono text-foreground">{{ item.path }}</span>
          <span v-if="item.description" class="block truncate text-muted-foreground">{{ item.description }}</span>
        </span>
        <span class="rounded-sm border px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{{ item.type }}</span>
      </button>
      <div v-if="!filteredItems.length" class="px-1 py-4 text-xs text-muted-foreground">没有可引用数据。</div>
    </div>
  </div>
</template>
