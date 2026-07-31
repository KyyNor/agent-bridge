<script setup lang="ts">
import { computed, ref } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Handle, Position, VueFlow, type Edge, type NodeMouseEvent } from '@vue-flow/core'
import { AlertTriangle, Bot, Check, Clock3, FileOutput, FileTerminal, LoaderCircle, X, XCircle } from '@lucide/vue'
import JsonViewer from '../../components/JsonViewer.vue'
import type { ConditionOperator, WorkflowGraph, WorkflowNodeRun } from '../../api/types'
import { workflowReuseReasonText } from '../../lib/workflowExecutionPlan'
import { workflowNodeToneClass, workflowNodeTypeText } from '../../lib/workflowNodeVisuals'
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
  const condition = edgeCondition(edge.id)
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    selectable: true,
    interactionWidth: 20,
    animated: runs.value.get(edge.target)?.status === 'running' && condition?.matched !== false,
    label: condition ? (condition.matched ? '命中' : '未命中') : '',
    style: condition && !condition.matched ? { strokeDasharray: '5 4', opacity: 0.45 } : undefined,
  }
}))

/** 当前选中的条件边求值结果，驱动右侧悬浮面板。 */
const selectedEdgeId = ref<string | null>(null)
const selectedCondition = computed(() => selectedEdgeId.value ? edgeCondition(selectedEdgeId.value) ?? null : null)

function edgeCondition(edgeId: string) {
  const edge = props.definitionSnapshot.edges.find(item => item.id === edgeId)
  if (!edge) return undefined
  return runs.value.get(edge.target)?.condition_results?.find(item => item.edge_id === edgeId)
}

const operatorText: Record<ConditionOperator, string> = {
  equals: '等于',
  not_equals: '不等于',
  exists: '存在',
  not_exists: '不存在',
  contains: '包含',
}
function expectedText(value: unknown) {
  return value == null ? '（未设置）' : formatWorkflowConditionActual(value)
}
/** 把期望/实际值转成适合悬浮面板的短文本。 */
function formatWorkflowConditionActual(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  if (typeof value !== 'object') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return '（无法序列化的对象）'
  }
}

function selectEdge(event: { edge: Edge }) {
  selectedEdgeId.value = edgeCondition(event.edge.id) ? event.edge.id : null
}
function clearSelection() {
  selectedEdgeId.value = null
}

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
function effectiveStatus(run?: WorkflowNodeRun) {
  return run?.action === 'reuse' ? 'completed' : run?.status || 'pending'
}
function openNode(event: NodeMouseEvent) {
  clearSelection()
  const run = runs.value.get(event.node.id)
  if (run?.agent_run_key) emit('openAgentRun', run.agent_run_key)
  else if (run?.script_run_id) emit('openScriptRun', run.script_run_id)
}
</script>

<template>
  <div class="h-[420px] min-h-[360px] border bg-background">
    <VueFlow :nodes="nodes" :edges="edges" :fit-view-on-init="true" :nodes-draggable="false" :nodes-connectable="false" :elements-selectable="true" :zoom-on-double-click="false" @node-click="openNode" @edge-click="selectEdge" @pane-click="clearSelection">
      <Background pattern-color="var(--border)" :gap="18" />
      <Controls :show-interactive="false" />
      <template #node-workflow-run="slotProps">
        <div class="relative h-[92px] w-48 border-2 px-3 py-2 pl-4" :class="statusClass(effectiveStatus(slotProps.data.run))">
          <span aria-hidden="true" class="absolute inset-y-0 left-0 w-1 rounded-l-sm" :class="workflowNodeToneClass(slotProps.data.node.type, 'rail')" />
          <Handle type="target" :position="Position.Left" :connectable="false" />
          <div class="flex items-center gap-2">
            <component :is="nodeIcon(slotProps.data.node.type)" class="h-4 w-4 shrink-0" />
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ slotProps.data.node.name }}</span>
            <component :is="statusIcon(effectiveStatus(slotProps.data.run))" class="h-4 w-4 shrink-0" :class="effectiveStatus(slotProps.data.run) === 'running' ? 'animate-spin' : ''" />
          </div>
          <div class="mt-2 flex items-center justify-between gap-2 font-mono text-[11px]">
            <span class="inline-flex shrink-0 rounded-sm px-1.5 py-0.5 font-sans text-[10px] font-medium" :class="workflowNodeToneClass(slotProps.data.node.type, 'badge')">
              {{ workflowNodeTypeText(slotProps.data.node.type) }}
            </span>
            <span>{{ slotProps.data.run?.action === 'reuse' ? '复用' : slotProps.data.run?.status || 'pending' }}</span>
          </div>
          <div v-if="slotProps.data.run?.action === 'reuse'" class="mt-1 truncate text-[10px] text-muted-foreground" :title="slotProps.data.run.reuse_reason || ''">
            来源 {{ slotProps.data.run.source_run_id || '历史运行' }} · {{ workflowReuseReasonText(slotProps.data.run.reuse_reason) }}
          </div>
          <div v-if="slotProps.data.run?.error" class="mt-1 truncate text-[11px]" :title="slotProps.data.run.error">{{ slotProps.data.run.error }}</div>
          <Handle type="source" :position="Position.Right" :connectable="false" />
        </div>
      </template>
    </VueFlow>
    <div v-if="selectedCondition" class="absolute right-3 top-3 z-10 flex max-h-[360px] w-72 flex-col overflow-hidden rounded-md border border-border bg-background text-card-foreground shadow-lg">
      <div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span class="inline-flex items-center gap-1 text-xs font-medium" :class="selectedCondition.matched ? 'text-success-soft-fg' : 'text-muted-foreground'">
          <Check v-if="selectedCondition.matched" class="h-3.5 w-3.5" />
          <XCircle v-else class="h-3.5 w-3.5" />
          {{ selectedCondition.matched ? '命中' : '未命中' }}
        </span>
        <button type="button" class="rounded-sm p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" aria-label="关闭" @click="clearSelection">
          <X class="h-3.5 w-3.5" />
        </button>
      </div>
      <div class="flex flex-col gap-1.5 overflow-y-auto px-3 py-2 text-xs">
        <div class="flex gap-2">
          <span class="w-14 shrink-0 text-muted-foreground">字段</span>
          <span class="min-w-0 break-all font-mono text-[11px]">{{ selectedCondition.field || '（未设置）' }}</span>
        </div>
        <div class="flex gap-2">
          <span class="w-14 shrink-0 text-muted-foreground">操作</span>
          <span>{{ selectedCondition.operator ? operatorText[selectedCondition.operator] : '（未设置）' }}</span>
        </div>
        <div v-if="selectedCondition.operator !== 'exists' && selectedCondition.operator !== 'not_exists'" class="flex gap-2">
          <span class="w-14 shrink-0 text-muted-foreground">期望</span>
          <span class="min-w-0 break-all font-mono text-[11px]">{{ expectedText(selectedCondition.expected) }}</span>
        </div>
        <div class="mt-1 flex items-center gap-2 text-muted-foreground">
          <span class="w-14 shrink-0">实际值</span>
        </div>
        <JsonViewer v-if="selectedCondition.actual != null" :value="selectedCondition.actual" max-height="220px" density="compact" />
        <span v-else class="text-muted-foreground">无</span>
      </div>
    </div>
  </div>
</template>
