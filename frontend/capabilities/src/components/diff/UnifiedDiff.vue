<script setup lang="ts">
/**
 * Renders a unified-diff patch (as produced by the backend diff endpoints) as a
 * colourised, monospace line grid. Zero-dependency — relies on parseUnifiedDiff
 * to split the patch into rows.
 */
import { computed } from 'vue'
import { parseUnifiedDiff, diffStats, type DiffLine } from '@/lib/unifiedDiff'

const props = withDefaults(
  defineProps<{
    /** Raw unified-diff text from the backend `text.content` field. */
    content: string
    /** Optional caption shown above the grid. */
    caption?: string
  }>(),
  { caption: '' },
)

const rows = computed<DiffLine[]>(() => parseUnifiedDiff(props.content))
const stats = computed(() => diffStats(props.content))
const empty = computed(() => rows.value.length === 0)

function lineClass(type: DiffLine['type']): string {
  switch (type) {
    case 'add':
      return 'bg-success-soft text-success-soft-fg'
    case 'del':
      return 'bg-destructive-soft text-destructive-soft-fg'
    case 'hunk':
      return 'bg-secondary text-muted-foreground'
    default:
      return 'text-foreground/80'
  }
}

function marker(type: DiffLine['type']): string {
  switch (type) {
    case 'add':
      return '+'
    case 'del':
      return '-'
    default:
      return ''
  }
}
</script>

<template>
  <div class="rounded-md border border-border bg-card">
    <div
      v-if="caption || !empty"
      class="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-xs text-muted-foreground"
    >
      <span v-if="caption" class="truncate font-medium">{{ caption }}</span>
      <span v-else />
      <span v-if="!empty" class="flex shrink-0 items-center gap-3 tabular-nums">
        <span class="text-success-soft-fg">+{{ stats.added }}</span>
        <span class="text-destructive-soft-fg">−{{ stats.removed }}</span>
      </span>
    </div>
    <div v-if="empty" class="px-3 py-6 text-center text-sm text-muted-foreground">
      两个版本内容相同
    </div>
    <div v-else class="overflow-x-auto py-1 font-mono text-[12px] leading-5">
      <div
        v-for="(row, idx) in rows"
        :key="idx"
        :class="['flex gap-2 whitespace-pre px-3', lineClass(row.type)]"
      >
        <span class="w-3 shrink-0 select-none text-muted-foreground/60">{{ marker(row.type) }}</span>
        <span class="shrink-0 select-none text-muted-foreground/50">{{ row.type === 'hunk' ? '' : idx }}</span>
        <span class="flex-1">{{ row.text }}</span>
      </div>
    </div>
  </div>
</template>
