import { computed, ref, watch } from 'vue'
import type { WorkflowGraph, WorkflowValidationIssue } from '../api/types'
import { activeOutputImpacts, outputImpactIssues, type OutputImpact } from '../lib/workflowOutputImpact'

/** 输出影响属于当前编辑会话；与服务端保存校验分开，避免一次校验清掉待处理提醒。 */
export function useWorkflowOutputImpacts(options: {
  graph: () => WorkflowGraph
  workflowKey: () => string
  selectedNodeId: () => string | undefined
  errors: () => WorkflowValidationIssue[]
}) {
  const outputImpacts = ref<OutputImpact[]>([])
  const activeImpacts = computed(() => activeOutputImpacts(options.graph(), outputImpacts.value))
  const combinedGraphErrors = computed(() => [...options.errors(), ...outputImpactIssues(activeImpacts.value)])
  const nodeRepairContext = () => activeImpacts.value.filter(i => i.nodeId === options.selectedNodeId())
  const acknowledgeOutputImpact = () => {
    outputImpacts.value = outputImpacts.value.filter(i => i.nodeId !== options.selectedNodeId())
  }
  watch(options.workflowKey, () => { outputImpacts.value = [] })
  return { outputImpacts, activeImpacts, combinedGraphErrors, nodeRepairContext, acknowledgeOutputImpact }
}
