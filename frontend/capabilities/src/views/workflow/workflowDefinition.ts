import type { Edge, Node } from '@vue-flow/core'
import type { ManagedScript, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowType } from '../../api/types'

export interface ManualInputField { path: string; type: string; required: boolean; description: string }

const markdownPrompt = '根据全部上游节点输出生成结构清晰的 Markdown 主报告；返回 title、summary、content，content 必须是完整 Markdown。'
const htmlPrompt = '只根据 Markdown 主产物生成完整 HTML 文档；返回 title、summary、content，content 必须包含 html 或 body 标签、内联 CSS、无外链脚本。'

export function createDefaultGraph(type: WorkflowType, defaultBackend: string): WorkflowGraph {
  if (type === 'operation') return { nodes: [], edges: [] }
  return {
    nodes: [
      { id: 'markdown-output', type: 'output', name: 'Markdown 主报告', position: { x: 160, y: 120 }, config: { format: 'markdown', title: '总结报告', path: 'reports/index.md', tags: [], prompt: markdownPrompt, backend_key: defaultBackend, mcp_enabled: false, skill_names: [] } },
      { id: 'html-output', type: 'output', name: 'HTML 派生报告', position: { x: 480, y: 120 }, config: { format: 'html', title: '总结报告 HTML', path: 'reports/index.html', tags: [], prompt: htmlPrompt, backend_key: defaultBackend, mcp_enabled: false, skill_names: [] } },
    ],
    edges: [{ id: 'markdown-to-html', source: 'markdown-output', target: 'html-output', condition: null }],
  }
}

export function isProtectedSummaryNode(node: WorkflowNode, workflowType: WorkflowType) {
  return workflowType === 'summary' && (node.id === 'markdown-output' || node.id === 'html-output')
}

export function isProtectedSummaryEdge(edge: WorkflowEdge, workflowType: WorkflowType) {
  return workflowType === 'summary' && edge.id === 'markdown-to-html'
}

export function toVueFlowElements(graph: WorkflowGraph): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map(node => ({ id: node.id, type: 'workflow', position: node.position, data: node })),
    edges: graph.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, data: edge, label: edge.condition?.operator || '' })),
  }
}

export function fromVueFlowElements(nodes: Node[], edges: Edge[]): WorkflowGraph {
  return {
    nodes: nodes.map(node => ({ ...(node.data as WorkflowNode), id: node.id, position: { x: node.position.x, y: node.position.y } })),
    edges: edges.map(edge => ({ ...(edge.data as WorkflowEdge || { id: edge.id, source: edge.source, target: edge.target, condition: null }), id: edge.id, source: edge.source, target: edge.target })),
  }
}

function inputPath(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const matched = value.match(/^\{\{\s*(input\.[A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}$/)
  return matched?.[1] || null
}

export function deriveManualInputFields(graph: WorkflowGraph, scripts: ManagedScript[]): ManualInputField[] {
  const selected = new Map(scripts.map(script => [script.script_key, script]))
  const fields = new Map<string, ManualInputField>()
  for (const node of graph.nodes) {
    if (node.type !== 'script') continue
    const script = selected.get(node.config.script_key)
    const properties = (script?.input_schema?.properties || {}) as Record<string, Record<string, unknown>>
    const required = new Set(Array.isArray(script?.input_schema?.required) ? script.input_schema.required.filter((key): key is string => typeof key === 'string') : [])
    for (const [param, value] of Object.entries(node.config.params)) {
      const path = inputPath(value)
      if (!path) continue
      const schema = properties[param] || {}
      fields.set(path, { path, type: typeof schema.type === 'string' ? schema.type : 'string', required: required.has(param), description: typeof schema.description === 'string' ? schema.description : '' })
    }
  }
  return [...fields.values()].sort((left, right) => left.path.localeCompare(right.path))
}
