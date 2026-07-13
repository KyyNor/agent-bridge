import type { ManagedScript, WorkflowGraph, WorkflowNode } from '../api/types'

export interface WorkflowReferenceItem {
  path: string
  label: string
  type: string
  description: string
  sourceNodeId?: string
}

export type WorkflowReferenceTarget =
  | { kind: 'node'; id: string }
  | { kind: 'edge'; id: string }

type JsonSchema = Record<string, unknown>

const inputReferencePattern = /\{\{\s*(input\.[A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g

export function formatWorkflowReference(item: WorkflowReferenceItem, mode: 'template' | 'condition'): string {
  return mode === 'template' ? `{{ ${item.path} }}` : item.path
}

export function deriveAvailableData(
  graph: WorkflowGraph,
  target: WorkflowReferenceTarget,
  scripts: ManagedScript[],
): WorkflowReferenceItem[] {
  const nodesById = new Map(graph.nodes.map(node => [node.id, node]))
  const scriptByKey = new Map(scripts.map(script => [script.script_key, script]))
  const lineageIds = target.kind === 'edge'
    ? deriveEdgeSourceLineage(graph, target.id)
    : deriveNodeAncestors(graph, target.id)
  const lineage = lineageIds.map(id => nodesById.get(id)).filter((node): node is WorkflowNode => Boolean(node))
  const items: WorkflowReferenceItem[] = target.kind === 'node'
    ? deriveInputReferences(graph, scriptByKey)
    : []

  if (target.kind === 'node' && lineage.some(node => node.type === 'get_task')) {
    items.push(
      { path: 'task.task_key', label: '任务 Key', type: 'string', description: '当前任务标识' },
      { path: 'task.payload', label: '任务负载', type: 'object', description: '当前任务的完整 payload' },
    )
  }

  for (const node of lineage) {
    if (node.type === 'get_task') continue
    items.push(...deriveNodeOutputReferences(node, scriptByKey.get(node.type === 'script' ? node.config.script_key : '')))
  }

  return dedupeByPath(items)
}

function deriveEdgeSourceLineage(graph: WorkflowGraph, edgeId: string): string[] {
  const edge = graph.edges.find(item => item.id === edgeId)
  if (!edge) return []
  return [...deriveNodeAncestors(graph, edge.source), edge.source]
}

function deriveNodeAncestors(graph: WorkflowGraph, nodeId: string): string[] {
  const ancestorIds = new Set<string>()
  const incoming = incomingEdgesByTarget(graph)
  const visit = (id: string) => {
    for (const edge of incoming.get(id) || []) {
      if (ancestorIds.has(edge.source)) continue
      ancestorIds.add(edge.source)
      visit(edge.source)
    }
  }
  visit(nodeId)
  return topologicalNodeIds(graph).filter(id => ancestorIds.has(id))
}

function incomingEdgesByTarget(graph: WorkflowGraph) {
  const incoming = new Map<string, typeof graph.edges>()
  for (const edge of graph.edges) {
    const items = incoming.get(edge.target) || []
    items.push(edge)
    incoming.set(edge.target, items)
  }
  return incoming
}

function topologicalNodeIds(graph: WorkflowGraph): string[] {
  const knownIds = new Set(graph.nodes.map(node => node.id))
  const indegree = new Map(graph.nodes.map(node => [node.id, 0]))
  const outgoing = new Map<string, string[]>()
  for (const edge of graph.edges) {
    if (!knownIds.has(edge.source) || !knownIds.has(edge.target)) continue
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
    const targets = outgoing.get(edge.source) || []
    targets.push(edge.target)
    outgoing.set(edge.source, targets)
  }
  const graphOrder = new Map(graph.nodes.map((node, index) => [node.id, index]))
  const queue = graph.nodes.filter(node => indegree.get(node.id) === 0).map(node => node.id)
  const result: string[] = []
  while (queue.length) {
    queue.sort((left, right) => (graphOrder.get(left) || 0) - (graphOrder.get(right) || 0))
    const id = queue.shift()!
    result.push(id)
    for (const target of outgoing.get(id) || []) {
      indegree.set(target, (indegree.get(target) || 0) - 1)
      if (indegree.get(target) === 0) queue.push(target)
    }
  }
  for (const node of graph.nodes) {
    if (!result.includes(node.id)) result.push(node.id)
  }
  return result
}

function deriveInputReferences(graph: WorkflowGraph, scriptByKey: Map<string, ManagedScript>): WorkflowReferenceItem[] {
  const fields = new Map<string, WorkflowReferenceItem>()
  const scan = (value: unknown) => {
    if (typeof value === 'string') {
      for (const matched of value.matchAll(inputReferencePattern)) {
        fields.set(matched[1], { path: matched[1], label: matched[1], type: 'unknown', description: '工作流手动输入' })
      }
      return
    }
    if (Array.isArray(value)) value.forEach(scan)
    else if (value && typeof value === 'object') Object.values(value).forEach(scan)
  }
  graph.nodes.forEach(node => scan(node.config))
  for (const node of graph.nodes) {
    if (node.type !== 'script') continue
    const script = scriptByKey.get(node.config.script_key)
    const properties = getProperties(script?.input_schema)
    for (const [param, value] of Object.entries(node.config.params)) {
      if (typeof value !== 'string') continue
      for (const matched of value.matchAll(inputReferencePattern)) {
        const schema = properties[param]
        fields.set(matched[1], {
          path: matched[1],
          label: matched[1],
          type: schemaType(schema),
          description: schemaDescription(schema) || '工作流手动输入',
        })
      }
    }
  }
  return [...fields.values()].sort((left, right) => left.path.localeCompare(right.path))
}

function deriveNodeOutputReferences(node: WorkflowNode, script?: ManagedScript): WorkflowReferenceItem[] {
  if (node.type === 'agent') {
    if (node.config.result_mode === 'json') {
      const expanded = expandSchemaProperties(node.config.output_schema, `nodes.${node.id}.output`, node.id)
      return expanded.length ? expanded : [rootOutputReference(node.id, node.name)]
    }
    return [{
      path: `nodes.${node.id}.output.text`,
      label: `${node.name} text`,
      type: 'string',
      description: '文本 Agent 输出',
      sourceNodeId: node.id,
    }]
  }
  if (node.type === 'script') {
    const expanded = expandSchemaProperties(script?.output_schema, `nodes.${node.id}.output`, node.id)
    return expanded.length ? expanded : [rootOutputReference(node.id, node.name)]
  }
  if (node.type === 'output') {
    return ['title', 'summary', 'content', 'artifact_ids'].map(field => ({
      path: `nodes.${node.id}.output.${field}`,
      label: `${node.name} ${field}`,
      type: field === 'artifact_ids' ? 'array' : 'string',
      description: '输出节点产物字段',
      sourceNodeId: node.id,
    }))
  }
  return []
}

function expandSchemaProperties(schema: unknown, basePath: string, sourceNodeId: string): WorkflowReferenceItem[] {
  const properties = getProperties(schema)
  const items: WorkflowReferenceItem[] = []
  for (const [key, propertySchema] of Object.entries(properties)) {
    const path = `${basePath}.${key}`
    items.push({
      path,
      label: path,
      type: schemaType(propertySchema),
      description: schemaDescription(propertySchema),
      sourceNodeId,
    })
    if (schemaType(propertySchema) === 'object') {
      items.push(...expandSchemaProperties(propertySchema, path, sourceNodeId))
    }
  }
  return items
}

function getProperties(schema: unknown): Record<string, JsonSchema> {
  if (!schema || typeof schema !== 'object') return {}
  const properties = (schema as JsonSchema).properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return {}
  const result: Record<string, JsonSchema> = {}
  for (const [key, value] of Object.entries(properties as Record<string, unknown>)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) result[key] = value as JsonSchema
  }
  return result
}

function schemaType(schema: unknown): string {
  if (!schema || typeof schema !== 'object') return 'unknown'
  const type = (schema as JsonSchema).type
  return typeof type === 'string' ? type : 'unknown'
}

function schemaDescription(schema: unknown): string {
  if (!schema || typeof schema !== 'object') return ''
  const description = (schema as JsonSchema).description
  return typeof description === 'string' ? description : ''
}

function rootOutputReference(nodeId: string, name: string): WorkflowReferenceItem {
  return {
    path: `nodes.${nodeId}.output`,
    label: `${name} output`,
    type: 'object',
    description: '完整节点输出',
    sourceNodeId: nodeId,
  }
}

function dedupeByPath(items: WorkflowReferenceItem[]): WorkflowReferenceItem[] {
  const paths = new Set<string>()
  return items.filter(item => {
    if (paths.has(item.path)) return false
    paths.add(item.path)
    return true
  })
}
