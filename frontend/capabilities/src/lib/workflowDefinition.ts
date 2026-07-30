import type { Edge, Node } from '@vue-flow/core'
import type { AgentRuntimeConfig, ManagedScript, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowType } from '../api/types'

export interface ManualInputField { path: string; type: string; required: boolean; description: string }

const markdownPrompt = '根据全部上游节点输出生成结构清晰的 Markdown 主报告；返回 title、summary、content，content 必须是完整 Markdown。'
const htmlPrompt = '只根据 Markdown 主产物生成完整 HTML 文档；返回 title、summary、content，content 必须包含 html 或 body 标签、内联 CSS、无外链脚本。'

export function createDefaultGraph(type: WorkflowType, defaultBackend: string): WorkflowGraph {
  if (type === 'operation') return { nodes: [], edges: [] }
  return {
    nodes: [
      { id: 'markdown-output', type: 'output', name: 'Markdown 主报告', position: { x: 160, y: 120 }, config: { format: 'markdown', title: '总结报告', path: 'reports/index.md', tags: [], prompt: markdownPrompt, backend_key: defaultBackend, mcp_enabled: false, skill_names: [], timeout_seconds: 600, system_role: 'summary_markdown' } },
      { id: 'html-output', type: 'output', name: 'HTML 派生报告', position: { x: 480, y: 120 }, config: { format: 'html', title: '总结报告 HTML', path: 'reports/index.html', tags: [], prompt: htmlPrompt, backend_key: defaultBackend, mcp_enabled: false, skill_names: ['design_html_report'], timeout_seconds: 600, system_role: 'summary_html' } },
    ],
    edges: [{ id: 'markdown-to-html', source: 'markdown-output', target: 'html-output', condition: null, system_role: 'summary_markdown_to_html' }],
  }
}

export function migrateWorkflowGraph(
  graph: WorkflowGraph,
  from: WorkflowType,
  to: WorkflowType,
  defaultBackend: string,
): WorkflowGraph {
  if (from === to) return structuredClone(graph)

  const protectedIds = summarySystemNodeIds(graph, from)
  if (to === 'operation') {
    const nodes = graph.nodes.filter(node => !protectedIds.has(node.id))
    return {
      nodes: structuredClone(nodes),
      edges: structuredClone(
        graph.edges.filter(
          edge => !edge.system_role && !protectedIds.has(edge.source) && !protectedIds.has(edge.target),
        ),
      ),
    }
  }

  const nodes = structuredClone(graph.nodes.filter(node => !protectedIds.has(node.id)))
  const nodeIds = new Set(nodes.map(node => node.id))
  const edges = structuredClone(
    graph.edges.filter(
      edge => !edge.system_role && !protectedIds.has(edge.source) && !protectedIds.has(edge.target),
    ),
  )
  const outgoing = new Set(
    edges
      .filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map(edge => edge.source),
  )
  const usedNodeIds = new Set(nodes.map(node => node.id))
  const usedEdgeIds = new Set(edges.map(edge => edge.id))
  const maxX = nodes.reduce((value, node) => Math.max(value, node.position.x), 0)
  const summary = createDefaultGraph('summary', defaultBackend)
  const markdownId = uniqueGraphId('markdown-output', usedNodeIds)
  const htmlId = uniqueGraphId('html-output', usedNodeIds)
  summary.nodes[0].id = markdownId
  summary.nodes[1].id = htmlId
  summary.nodes[0].position = { x: maxX + 240, y: 120 }
  summary.nodes[1].position = { x: maxX + 560, y: 120 }
  summary.edges[0] = {
    ...summary.edges[0],
    id: uniqueGraphId('markdown-to-html', usedEdgeIds),
    source: markdownId,
    target: htmlId,
  }

  for (const node of nodes) {
    if (outgoing.has(node.id)) continue
    edges.push({
      id: uniqueGraphId(`${node.id}-${markdownId}`, usedEdgeIds),
      source: node.id,
      target: markdownId,
      condition: null,
    })
  }

  return {
    nodes: [...nodes, ...summary.nodes],
    edges: [...edges, ...summary.edges],
  }
}

function summarySystemNodeIds(graph: WorkflowGraph, workflowType: WorkflowType): Set<string> {
  const marked = graph.nodes.filter(
    node => node.type === 'output' && Boolean(node.config.system_role),
  )
  if (marked.length || workflowType !== 'summary') return new Set(marked.map(node => node.id))

  const outputs = graph.nodes.filter(node => node.type === 'output')
  if (outputs.length !== 2) return new Set()
  const markdown = outputs.find(node => node.config.format === 'markdown')
  const html = outputs.find(node => node.config.format === 'html')
  if (!markdown || !html) return new Set()
  const hasLegacyBridge = graph.edges.some(
    edge => edge.source === markdown.id && edge.target === html.id && edge.condition === null,
  )
  return hasLegacyBridge ? new Set([markdown.id, html.id]) : new Set()
}

function uniqueGraphId(base: string, used: Set<string>): string {
  let value = base
  let suffix = 2
  while (used.has(value)) value = `${base}-${suffix++}`
  used.add(value)
  return value
}

export function isProtectedSummaryNode(node: WorkflowNode, workflowType: WorkflowType) {
  return workflowType === 'summary' && node.type === 'output' && Boolean(node.config.system_role)
}

export function isProtectedSummaryEdge(edge: WorkflowEdge, workflowType: WorkflowType) {
  return workflowType === 'summary' && edge.system_role === 'summary_markdown_to_html'
}

export function deriveWorkflowBackendKeys(runtime: AgentRuntimeConfig): string[] {
  const registered = runtime.available_backends?.map(item => item.slug) || []
  const candidates = registered.length
    ? registered
    : [runtime.default_backend, ...runtime.backends.map(item => item.slug)]
  return candidates.filter((item, index, all) => Boolean(item) && all.indexOf(item) === index)
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
