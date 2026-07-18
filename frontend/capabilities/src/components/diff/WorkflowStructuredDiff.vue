<script setup lang="ts">
/**
 * Semantic diff view for workflow definitions. Renders the `structured` payload
 * from `/workflows/{key}/diff`: node/edge add/remove/change cards plus top-level
 * metadata changes. Falls back to "无变更" when `identical` is true.
 */
import { computed } from 'vue'
import type { WorkflowStructuredDiff } from '@/api/types'

const props = defineProps<{ diff: WorkflowStructuredDiff }>()

const hasMetadata = computed(() => props.diff.metadata.length > 0)
const hasNodes = computed(
  () =>
    props.diff.nodes.added.length +
      props.diff.nodes.removed.length +
      props.diff.nodes.changed.length >
    0,
)
const hasEdges = computed(
  () =>
    props.diff.edges.added.length +
      props.diff.edges.removed.length +
      props.diff.edges.changed.length >
    0,
)

const METADATA_LABELS: Record<string, string> = {
  name: '名称',
  description: '描述',
  status: '状态',
  workflow_type: '类型',
  profile_key: '项目空间',
}

function metaLabel(field: string): string {
  return METADATA_LABELS[field] || field
}

function nodeTag(type?: string): string {
  return type || 'node'
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value || '(空)'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
</script>

<template>
  <div class="space-y-4">
    <div
      v-if="diff.identical"
      class="rounded-md border border-border bg-card px-3 py-6 text-center text-sm text-muted-foreground"
    >
      两个版本结构相同
    </div>

    <!-- Metadata -->
    <section v-if="hasMetadata">
      <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">基本信息</h4>
      <div class="overflow-hidden rounded-md border border-border">
        <table class="w-full text-sm">
          <tbody>
            <tr
              v-for="(m, i) in diff.metadata"
              :key="m.field"
              :class="i > 0 ? 'border-t border-border' : ''"
            >
              <td class="w-32 bg-secondary/40 px-3 py-1.5 text-muted-foreground">{{ metaLabel(m.field) }}</td>
              <td class="px-3 py-1.5 font-mono text-xs text-rose-600 line-through dark:text-rose-400">
                {{ formatValue(m.from) }}
              </td>
              <td class="px-2 py-1.5 text-muted-foreground">→</td>
              <td class="px-3 py-1.5 font-mono text-xs text-emerald-700 dark:text-emerald-300">
                {{ formatValue(m.to) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Nodes -->
    <section v-if="hasNodes">
      <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">节点</h4>
      <div class="grid gap-2 sm:grid-cols-2">
        <div
          v-for="n in diff.nodes.added"
          :key="`a-${n.id}`"
          class="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-sm"
        >
          <span class="mr-2 font-mono text-xs text-emerald-700 dark:text-emerald-300">+ 新增</span>
          <span class="font-medium">{{ n.id }}</span>
          <span class="ml-2 text-xs text-muted-foreground">{{ nodeTag(n.type) }}{{ n.label ? ' · ' + n.label : '' }}</span>
        </div>
        <div
          v-for="n in diff.nodes.removed"
          :key="`r-${n.id}`"
          class="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-sm"
        >
          <span class="mr-2 font-mono text-xs text-rose-700 dark:text-rose-300">− 删除</span>
          <span class="font-medium">{{ n.id }}</span>
          <span class="ml-2 text-xs text-muted-foreground">{{ nodeTag(n.type) }}{{ n.label ? ' · ' + n.label : '' }}</span>
        </div>
        <div
          v-for="n in diff.nodes.changed"
          :key="`c-${n.id}`"
          class="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm"
        >
          <div class="mb-1">
            <span class="mr-2 font-mono text-xs text-amber-700 dark:text-amber-300">~ 修改</span>
            <span class="font-medium">{{ n.id }}</span>
          </div>
          <ul class="space-y-0.5 font-mono text-xs">
            <li v-for="(c, j) in n.changes" :key="j" class="text-muted-foreground">
              <span class="text-foreground">{{ c.field }}</span>:
              <span class="text-rose-600 line-through dark:text-rose-400">{{ formatValue(c.from) }}</span>
              →
              <span class="text-emerald-700 dark:text-emerald-300">{{ formatValue(c.to) }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <!-- Edges -->
    <section v-if="hasEdges">
      <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">连线</h4>
      <div class="grid gap-2 sm:grid-cols-2">
        <div
          v-for="e in diff.edges.added"
          :key="`ea-${e.id}`"
          class="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-sm"
        >
          <span class="mr-2 font-mono text-xs text-emerald-700 dark:text-emerald-300">+ 新增</span>
          <span class="font-mono text-xs">{{ e.source }} → {{ e.target }}</span>
        </div>
        <div
          v-for="e in diff.edges.removed"
          :key="`er-${e.id}`"
          class="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-sm"
        >
          <span class="mr-2 font-mono text-xs text-rose-700 dark:text-rose-300">− 删除</span>
          <span class="font-mono text-xs">{{ e.source }} → {{ e.target }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
