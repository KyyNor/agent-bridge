<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Handle, Position, VueFlow, type Connection, type Edge, type Node } from '@vue-flow/core'
import { Maximize, Trash2 } from 'lucide-vue-next'
import type { WorkflowGraph, WorkflowNodeType, WorkflowType, WorkflowValidationError } from '../../api/types'
import { fromVueFlowElements, isProtectedSummaryEdge, isProtectedSummaryNode, toVueFlowElements } from './workflowDefinition'
import Button from '../../components/ui/button/Button.vue'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const graph = defineModel<WorkflowGraph>('graph', { required: true })
const props = defineProps<{ workflowType: WorkflowType; errors: WorkflowValidationError[] }>()
const emit = defineEmits<{ selectNode: [nodeId: string]; selectEdge: [edgeId: string]; addNode: [type: WorkflowNodeType, position?: { x: number; y: number }] }>()

const flowNodes = shallowRef<Node[]>([])
const flowEdges = shallowRef<Edge[]>([])
const selectedNodeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
watch(graph, value => { const elements = toVueFlowElements(value); flowNodes.value = elements.nodes; flowEdges.value = elements.edges }, { immediate: true, deep: true })
const issueById = computed(() => new Map(props.errors.filter(issue => issue.id).map(issue => [issue.id as string, issue.message])))

function sync() { graph.value = fromVueFlowElements(flowNodes.value, flowEdges.value) }
function connect(connection: Connection) {
  if (!connection.source || !connection.target || connection.source === connection.target) return
  const id = `edge-${connection.source}-${connection.target}-${Date.now()}`
  flowEdges.value = [...flowEdges.value, { id, source: connection.source, target: connection.target, data: { id, source: connection.source, target: connection.target, condition: null } }]
  sync()
}
function selectNode(event: { node: Node }) { selectedNodeId.value = event.node.id; selectedEdgeId.value = null; emit('selectNode', event.node.id) }
function selectEdge(event: { edge: Edge }) { selectedEdgeId.value = event.edge.id; selectedNodeId.value = null; emit('selectEdge', event.edge.id) }
function updatePosition(event: { node: Node }) { const node = flowNodes.value.find(item => item.id === event.node.id); if (node) { node.position = event.node.position; sync() } }
function deleteSelected() {
  if (selectedNodeId.value) {
    const domain = graph.value.nodes.find(node => node.id === selectedNodeId.value)
    if (domain && !isProtectedSummaryNode(domain, props.workflowType)) {
      flowNodes.value = flowNodes.value.filter(node => node.id !== selectedNodeId.value)
      flowEdges.value = flowEdges.value.filter(edge => edge.source !== selectedNodeId.value && edge.target !== selectedNodeId.value)
      selectedNodeId.value = null; sync()
    }
    return
  }
  if (selectedEdgeId.value) {
    const domain = graph.value.edges.find(edge => edge.id === selectedEdgeId.value)
    if (domain && !isProtectedSummaryEdge(domain, props.workflowType)) { flowEdges.value = flowEdges.value.filter(edge => edge.id !== selectedEdgeId.value); selectedEdgeId.value = null; sync() }
  }
}
function drop(event: DragEvent) {
  const type = event.dataTransfer?.getData('application/agent-bridge-workflow-node') as WorkflowNodeType | ''
  if (!type) return
  const target = event.currentTarget as HTMLElement
  const bounds = target.getBoundingClientRect()
  emit('addNode', type, { x: event.clientX - bounds.left, y: event.clientY - bounds.top })
}
</script>

<template>
  <div class="relative min-h-[520px] border" @dragover.prevent @drop.prevent="drop">
    <div class="absolute right-3 top-3 z-10 flex gap-2">
      <Button variant="outline" size="sm" class="h-8 w-8 p-0" title="删除所选" @click="deleteSelected"><Trash2 class="h-4 w-4" /></Button>
    </div>
    <VueFlow v-model:nodes="flowNodes" v-model:edges="flowEdges" :delete-key-code="null" :fit-view-on-init="true" class="bg-background" @connect="connect" @node-click="selectNode" @edge-click="selectEdge" @node-drag-stop="updatePosition">
      <Background pattern-color="#cbd5e1" :gap="18" />
      <Controls :show-interactive="false"><template #control-fit-view><Maximize class="h-4 w-4" /></template></Controls>
      <template #node-workflow="slotProps">
        <div class="w-44 rounded-sm border bg-background px-3 py-2 shadow-sm" :class="issueById.has(slotProps.id) ? 'border-destructive' : ''">
          <Handle type="target" :position="Position.Left" />
          <div class="truncate text-sm font-medium">{{ slotProps.data.name }}</div>
          <div class="mt-1 truncate font-mono text-[11px] text-muted-foreground">{{ slotProps.data.type }}<span v-if="slotProps.data.config.backend_key"> · {{ slotProps.data.config.backend_key }}</span><span v-else-if="slotProps.data.config.script_key"> · {{ slotProps.data.config.script_key }}</span></div>
          <div v-if="issueById.has(slotProps.id)" class="mt-1 truncate text-[11px] text-destructive">{{ issueById.get(slotProps.id) }}</div>
          <Handle type="source" :position="Position.Right" />
        </div>
      </template>
    </VueFlow>
  </div>
</template>
