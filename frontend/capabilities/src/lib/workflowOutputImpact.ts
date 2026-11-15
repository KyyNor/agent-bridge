import type { WorkflowGraph, WorkflowNode, WorkflowValidationIssue } from '../api/types'
import { mapNodeTemplates } from './workflowNodeIds'

export interface OutputImpact {
  nodeId: string
  field: string
  path: string
  edgeId?: string
  before: unknown
  after: unknown
  original: string
}
function contract(node: WorkflowNode) {
  return node.type === 'agent' ? { result_mode: node.config.result_mode, output_schema: node.config.output_schema } : null
}
/** 按直接引用收集影响，包括跨多条边的引用与嵌套脚本参数。 */
export function collectOutputImpact(graph: WorkflowGraph, replacement: WorkflowNode): OutputImpact[] {
  const previous = graph.nodes.find(n => n.id === replacement.id)
  if (!previous || JSON.stringify(contract(previous)) === JSON.stringify(contract(replacement))) return []
  const impacts: OutputImpact[] = []
  const prefix = `nodes.${replacement.id}.output`
  const add = (nodeId: string, field: string, path: string, original: string, edgeId?: string) => {
    if (path === prefix || path.startsWith(prefix + '.')) impacts.push({ nodeId, field, path, original, edgeId, before: contract(previous), after: contract(replacement) })
  }
  for (const node of graph.nodes) {
    if (node.id === replacement.id) continue
    mapNodeTemplates(node, (text, field) => {
      for (const match of text.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g)) add(node.id, field, match[1], text)
      return text
    })
  }
  for (const edge of graph.edges) if (edge.condition) add(edge.target, 'condition.field', edge.condition.field, JSON.stringify(edge.condition), edge.id)
  return impacts
}
/** 删除受影响引用后消除提醒；仍保留该引用时需用户明确确认语义兼容。 */
export function activeOutputImpacts(graph: WorkflowGraph, impacts: OutputImpact[]): OutputImpact[] {
  return impacts.filter(impact => {
    const source = graph.nodes.find(n => impact.path.startsWith(`nodes.${n.id}.output`))
    if (source && JSON.stringify(contract(source)) === JSON.stringify(impact.before)) return false
    if (impact.edgeId) return graph.edges.find(e => e.id === impact.edgeId)?.condition?.field === impact.path
    const node = graph.nodes.find(n => n.id === impact.nodeId)
    if (!node) return false
    let unchanged = false
    mapNodeTemplates(node, (text, field) => { if (field === impact.field && [...text.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g)].some(m => m[1] === impact.path)) unchanged = true; return text })
    return unchanged
  })
}
export function outputImpactIssues(impacts: OutputImpact[]): WorkflowValidationIssue[] {
  return impacts.map(i => ({ scope: 'node', id: i.nodeId, field: i.field, code: 'upstream_output_changed',
    message: `上游输出已变化，请检查 ${i.edgeId ? `连线 ${i.edgeId} 的 ` : ''}${i.field}：${i.path}。可使用节点助手修复，或检查后确认。` }))
}
