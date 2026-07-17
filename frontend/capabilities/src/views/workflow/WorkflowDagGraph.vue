<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Handle, MarkerType, Position, VueFlow, type Edge, type Node } from '@vue-flow/core'
import { MiniMap } from '@vue-flow/minimap'
import { Graph, layout as dagreLayout } from '@dagrejs/dagre'
import { Badge } from '../../components/ui/badge'
import type { WorkflowDag, WorkflowDagNode } from './workflowDag'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const props = defineProps<{
  dag: WorkflowDag
}>()

interface WorkflowNodeData {
  label: string
  phase: string
  kind: WorkflowDagNode['kind']
  order: number
}

const nodeWidth = 210
const nodeHeight = 74
const selectedNode = ref<WorkflowDagNode | null>(null)

const phaseCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const node of props.dag.nodes) {
    const phase = node.phase || '未分组'
    counts[phase] = (counts[phase] || 0) + 1
  }
  return counts
})

const graphElements = computed(() => {
  const graph = new Graph()
    .setGraph({
      rankdir: 'LR',
      nodesep: 42,
      ranksep: 86,
      marginx: 24,
      marginy: 24,
    })
    .setDefaultEdgeLabel(() => ({}))

  for (const node of props.dag.nodes) {
    graph.setNode(node.id, { width: nodeWidth, height: nodeHeight })
  }
  for (const edge of props.dag.edges) {
    graph.setEdge(edge.from, edge.to)
  }

  dagreLayout(graph)

  const nodes: Node<WorkflowNodeData>[] = props.dag.nodes.map((node) => {
    const point = graph.node(node.id) || { x: 0, y: 0 }
    return {
      id: node.id,
      type: 'workflow',
      position: {
        x: point.x - nodeWidth / 2,
        y: point.y - nodeHeight / 2,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      connectable: false,
      data: {
        label: node.label,
        phase: node.phase || '未分组',
        kind: node.kind,
        order: node.order,
      },
    }
  })

  const edges: Edge[] = props.dag.edges.map((edge, index) => ({
    id: `${edge.from}-${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    label: edge.when,
    type: 'smoothstep',
    markerEnd: MarkerType.ArrowClosed,
    animated: Boolean(edge.when),
    selectable: false,
    style: {
      strokeWidth: 1.6,
      stroke: edge.when ? 'var(--primary)' : 'var(--muted-foreground)',
    },
    labelBgStyle: {
      fill: 'var(--background)',
      fillOpacity: 0.96,
    },
    labelStyle: {
      fill: 'var(--muted-foreground)',
      fontSize: 11,
    },
  }))

  return { nodes, edges }
})

watch(
  () => props.dag.nodes,
  () => {
    selectedNode.value = null
  },
)

function onNodeClick(event: { node: Node<WorkflowNodeData> }) {
  selectedNode.value = props.dag.nodes.find(node => node.id === event.node.id) || null
}

function nodeKindLabel(kind: WorkflowDagNode['kind']) {
  if (kind === 'terminal') return '终止'
  if (kind === 'parallel') return '并行'
  return 'agent'
}

function nodeKindClass(kind: WorkflowDagNode['kind']) {
  if (kind === 'terminal') return 'border-border bg-neutral-soft text-neutral-soft-fg'
  if (kind === 'parallel') return 'border-primary/30 bg-cat-blue text-cat-blue-fg'
  return 'border-border bg-background text-foreground'
}

function miniMapColor(node: Node<WorkflowNodeData>) {
  // vue-flow MiniMap 接受 CSS 色值，用语义 token 派生
  if (node.data?.kind === 'terminal') return 'var(--neutral-soft)'
  if (node.data?.kind === 'parallel') return 'var(--cat-blue)'
  return 'var(--secondary)'
}
</script>

<template>
  <section class="space-y-3 rounded-md border p-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold">调用 DAG</h3>
        <p class="mt-1 text-xs text-muted-foreground">从 workflow.js 静态解析 agent / parallel / if / return。</p>
      </div>
      <div class="flex flex-wrap gap-1">
        <Badge v-for="(count, phase) in phaseCounts" :key="phase" variant="outline">{{ phase }} {{ count }}</Badge>
      </div>
    </div>

    <div v-if="dag.warnings.length" class="rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-soft-fg">
      <div v-for="warning in dag.warnings" :key="warning">{{ warning }}</div>
    </div>

    <div v-if="!dag.nodes.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无可展示的调用图</div>
    <div v-else class="grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
      <div class="h-[520px] overflow-hidden rounded-md border bg-background">
        <VueFlow
          :nodes="graphElements.nodes"
          :edges="graphElements.edges"
          :min-zoom="0.25"
          :max-zoom="1.8"
          :fit-view-on-init="true"
          :nodes-draggable="false"
          :nodes-connectable="false"
          :edges-updatable="false"
          class="workflow-dag-flow"
          @node-click="onNodeClick"
        >
          <Background pattern-color="var(--border)" :gap="18" />
          <MiniMap pannable zoomable :node-color="miniMapColor" />
          <Controls />

          <template #node-workflow="{ data, selected }">
            <div
              class="workflow-dag-node"
              :class="[nodeKindClass(data.kind), selected ? 'ring-2 ring-primary/40' : '']"
            >
              <Handle type="target" :position="Position.Left" class="opacity-0" />
              <div class="min-w-0">
                <div class="truncate text-sm font-semibold">{{ data.label }}</div>
                <div class="mt-2 flex items-center justify-between gap-3 text-xs">
                  <span class="truncate text-muted-foreground">{{ data.phase }}</span>
                  <span class="shrink-0 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-normal">{{ nodeKindLabel(data.kind) }}</span>
                </div>
              </div>
              <Handle type="source" :position="Position.Right" class="opacity-0" />
            </div>
          </template>
        </VueFlow>
      </div>

      <aside class="rounded-md border bg-muted/20 p-3">
        <div class="text-xs font-semibold text-foreground">节点详情</div>
        <div v-if="selectedNode" class="mt-3 space-y-3 text-sm">
          <div>
            <div class="text-xs text-muted-foreground">label</div>
            <div class="mt-1 font-mono text-xs text-foreground">{{ selectedNode.label }}</div>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div class="rounded-md border bg-background px-2 py-1.5">
              <div class="text-[11px] text-muted-foreground">phase</div>
              <div class="mt-1 truncate text-xs">{{ selectedNode.phase || '未分组' }}</div>
            </div>
            <div class="rounded-md border bg-background px-2 py-1.5">
              <div class="text-[11px] text-muted-foreground">kind</div>
              <div class="mt-1 text-xs">{{ nodeKindLabel(selectedNode.kind) }}</div>
            </div>
          </div>
          <div class="rounded-md border bg-background px-2 py-1.5">
            <div class="text-[11px] text-muted-foreground">node id</div>
            <div class="mt-1 break-all font-mono text-xs">{{ selectedNode.id }}</div>
          </div>
        </div>
        <div v-else class="mt-3 rounded-md border bg-background px-3 py-6 text-center text-sm text-muted-foreground">
          点击图中节点查看详情
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.workflow-dag-flow {
  --vf-node-bg: transparent;
  --vf-node-text: var(--foreground);
  --vf-connection-path: var(--primary);
  --vf-controls-button-bg: var(--background);
  --vf-controls-button-color: var(--foreground);
  --vf-controls-button-border-color: var(--border);
  --vf-minimap-bg-color: var(--background);
}

.workflow-dag-node {
  position: relative;
  width: 210px;
  min-height: 74px;
  border-width: 1px;
  border-radius: var(--radius-card);
  padding: 12px;
  box-shadow: var(--shadow-card);
}
</style>
