<script setup lang="ts">
import { computed } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Handle, Position, VueFlow, type NodeMouseEvent } from '@vue-flow/core'
import { AlertTriangle, Bot, Check, Clock3, FileOutput, FileTerminal, LoaderCircle, XCircle } from 'lucide-vue-next'
import type { WorkflowGraph, WorkflowNodeRun } from '../../api/types'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const props = defineProps<{ definitionSnapshot: WorkflowGraph; nodeRuns: WorkflowNodeRun[] }>()
const emit = defineEmits<{ openAgentRun: [runKey: string]; openScriptRun: [runId: string] }>()

const runs = computed(() => new Map(props.nodeRuns.map(run => [run.node_id, run])))
const nodes = computed(() => props.definitionSnapshot.nodes.map(node => ({
  id: node.id,
  type: 'workflow-run',
  position: node.position,
  draggable: false,
  selectable: true,
  data: { node, run: runs.value.get(node.id) },
})))
const edges = computed(() => props.definitionSnapshot.edges.map(edge => {
  const condition = runs.value.get(edge.target)?.condition_results?.find(item => item.edge_id === edge.id)
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    selectable: false,
    animated: runs.value.get(edge.target)?.status === 'running' && condition?.matched !== false,
    label: condition ? `${condition.matched ? '命中' : '未命中'}${condition.actual == null ? '' : ` · ${String(condition.actual)}`}` : '',
    style: condition && !condition.matched ? { strokeDasharray: '5 4', opacity: 0.45 } : undefined,
  }
}))

function nodeIcon(type: string) {
  if (type === 'agent') return Bot
  if (type === 'script') return FileTerminal
  if (type === 'output') return FileOutput
  return Clock3
}
function statusClass(status?: string) {
  return ({
    pending: 'border-border bg-muted text-muted-foreground',
    running: 'border-info/30 bg-info-soft text-info-soft-fg',
    completed: 'border-success/30 bg-success-soft text-success-soft-fg',
    skipped: 'border-border bg-muted text-muted-foreground',
    failed: 'border-destructive/30 bg-destructive-soft text-destructive-soft-fg',
    cancelled: 'border-border bg-muted text-muted-foreground',
    warning: 'border-warning/30 bg-warning-soft text-warning-soft-fg',
  } as Record<string, string>)[status || 'pending']
}
function statusIcon(status?: string) {
  if (status === 'completed') return Check
  if (status === 'failed') return XCircle
  if (status === 'warning') return AlertTriangle
  if (status === 'running') return LoaderCircle
  return Clock3
}
function openNode(event: NodeMouseEvent) {
  const run = runs.value.get(event.node.id)
  if (run?.agent_run_key) emit('openAgentRun', run.agent_run_key)
  else if (run?.script_run_id) emit('openScriptRun', run.script_run_id)
}
</script>

<template>
  <div class="h-[420px] min-h-[360px] border bg-background">
    <VueFlow :nodes="nodes" :edges="edges" :fit-view-on-init="true" :nodes-draggable="false" :nodes-connectable="false" :elements-selectable="true" :zoom-on-double-click="false" @node-click="openNode">
      <Background pattern-color="var(--border)" :gap="18" />
      <Controls :show-interactive="false" />
      <template #node-workflow-run="slotProps">
        <div class="h-[92px] w-48 border-2 px-3 py-2" :class="statusClass(slotProps.data.run?.status)">
          <Handle type="target" :position="Position.Left" :connectable="false" />
          <div class="flex items-center gap-2">
            <component :is="nodeIcon(slotProps.data.node.type)" class="h-4 w-4 shrink-0" />
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ slotProps.data.node.name }}</span>
            <component :is="statusIcon(slotProps.data.run?.status)" class="h-4 w-4 shrink-0" :class="slotProps.data.run?.status === 'running' ? 'animate-spin' : ''" />
          </div>
          <div class="mt-2 flex items-center justify-between gap-2 font-mono text-[11px]">
            <span class="truncate">{{ slotProps.data.node.type }}</span>
            <span>{{ slotProps.data.run?.status || 'pending' }}</span>
          </div>
          <div v-if="slotProps.data.run?.error" class="mt-1 truncate text-[11px]" :title="slotProps.data.run.error">{{ slotProps.data.run.error }}</div>
          <Handle type="source" :position="Position.Right" :connectable="false" />
        </div>
      </template>
    </VueFlow>
  </div>
</template>
