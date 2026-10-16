import type { WorkflowEdge, WorkflowGraph, WorkflowNode } from '../api/types'

/**
 * 节点 ID 是 `{{ nodes.<id>.* }}` 引用与边 source/target 的锚点。
 * 引用解析按首个点号切分节点 ID（与后端 `_node_templates`、`_ancestors` 的约定一致），
 * 因此 ID 本身不允许出现点号，否则 `nodes.a.b` 无法区分“节点 a 的 b 字段”与“节点 a.b”。
 */
export const NODE_ID_PATTERN = /^[a-z][a-z0-9_-]*$/
const NODE_ID_MAX_LENGTH = 64

/** 判断 `marker` 在 `text` 的 `start` 位置是否构成完整引用（避免 `agent-1` 误匹配 `agent-11`）。 */
function isWholeReference(text: string, start: number, marker: string): boolean {
  const next = text[start + marker.length]
  return next === undefined || !/[A-Za-z0-9_-]/.test(next)
}

function forEachTemplateExpression(
  template: string,
  visit: (expression: string) => string | null,
): string {
  return template.replace(/\{\{\s*nodes\.[A-Za-z0-9_-]+[^}]*?\}\}/g, expression => visit(expression) ?? expression)
}

/** 把模板里指向被重命名节点的 `{{ nodes.<old>.* }}` 换成 `{{ nodes.<new>.* }}`。 */
export function rewriteTemplateReferences(template: string, rename: Map<string, string>): string {
  if (!template || !rename.size) return template
  return forEachTemplateExpression(template, expression => {
    for (const [from, to] of rename) {
      const marker = `nodes.${from}`
      const markerIndex = expression.indexOf(marker)
      if (markerIndex < 0 || !isWholeReference(expression, markerIndex, marker)) continue
      return `${expression.slice(0, markerIndex)}nodes.${to}${expression.slice(markerIndex + marker.length)}`
    }
    return null
  })
}

/** 删除模板里指向已删节点的整个 `{{ nodes.<id>.* }}` 表达式，并收拾残留空白。 */
export function stripTemplateReferences(template: string, removed: Set<string>): string {
  if (!template || !removed.size) return template
  let stripped = false
  const next = forEachTemplateExpression(template, expression => {
    for (const id of removed) {
      const marker = `nodes.${id}`
      const markerIndex = expression.indexOf(marker)
      if (markerIndex < 0 || !isWholeReference(expression, markerIndex, marker)) continue
      stripped = true
      return ''
    }
    return null
  })
  // 仅在实际删除过引用时整理空白，避免顺带改写用户原有排版。
  return stripped ? next.replace(/[ \t]{2,}/g, ' ').trim() : template
}

/** 读取边条件引用的节点 ID；条件不指向 `nodes.` 命名空间时返回 null。 */
export function edgeConditionNodeId(edge: WorkflowEdge): string | null {
  const field = edge.condition?.field
  if (!field?.startsWith('nodes.')) return null
  return field.slice('nodes.'.length).match(/^[A-Za-z0-9_-]+/)?.[0] ?? null
}

function renameConditionField(field: string, rename: Map<string, string>): string {
  for (const [from, to] of rename) {
    if (field === `nodes.${from}`) return `nodes.${to}`
    if (field.startsWith(`nodes.${from}.`)) return `nodes.${to}${field.slice(`nodes.${from}`.length)}`
  }
  return field
}

function mapNodeTemplates(node: WorkflowNode, transform: (template: string) => string): WorkflowNode {
  if (node.type === 'agent') {
    const prompt = transform(node.config.prompt || '')
    return prompt === node.config.prompt ? node : { ...node, config: { ...node.config, prompt } }
  }
  if (node.type === 'output') {
    const prompt = transform(node.config.prompt || '')
    const path = transform(node.config.path || '')
    if (prompt === node.config.prompt && path === node.config.path) return node
    return { ...node, config: { ...node.config, prompt, path } }
  }
  if (node.type === 'script') {
    let changed = false
    const params: Record<string, unknown> = { ...node.config.params }
    for (const [key, value] of Object.entries(params)) {
      if (typeof value !== 'string') continue
      const next = transform(value)
      if (next !== value) { params[key] = next; changed = true }
    }
    return changed ? { ...node, config: { ...node.config, params } } : node
  }
  return node
}

export function validateNodeId(value: string, taken: Iterable<string>): string | null {
  const id = value.trim()
  if (!id) return '节点 ID 不能为空'
  if (id.length > NODE_ID_MAX_LENGTH) return `节点 ID 不能超过 ${NODE_ID_MAX_LENGTH} 个字符`
  if (!NODE_ID_PATTERN.test(id)) return '节点 ID 只能使用小写字母、数字、下划线和连字符，且须以小写字母开头'
  if (new Set(taken).has(id)) return `节点 ID 已被占用：${id}`
  return null
}

/**
 * 重命名节点并级联重写边端点、模板引用与边条件。
 * 注意：节点 ID 参与工作流 `content_hash`，重命名与改提示词同属执行语义变更，
 * 会让受影响任务的既有结果失去增量复用资格。
 */
export function applyNodeIdRename(graph: WorkflowGraph, from: string, to: string): WorkflowGraph {
  if (from === to) return graph
  const rename = new Map([[from, to]])
  return {
    nodes: graph.nodes.map(node => mapNodeTemplates(
      node.id === from ? { ...node, id: to } : node,
      template => rewriteTemplateReferences(template, rename),
    )),
    edges: graph.edges.map(edge => {
      const field = edge.condition?.field
      return {
        ...edge,
        source: edge.source === from ? to : edge.source,
        target: edge.target === from ? to : edge.target,
        condition: field ? { ...edge.condition!, field: renameConditionField(field, rename) } : edge.condition ?? null,
      }
    }),
  }
}

export interface NodeRemovalImpact {
  /** 指向被删节点、引用会被清理的节点。 */
  danglingNodes: Array<{ node: WorkflowNode; fields: string[] }>
  /** 条件字段指向被删节点、必须一并删除的边。 */
  orphanedEdges: WorkflowEdge[]
}

/** 收集删除节点会留下的悬空引用，用于删除前向用户明示影响面。 */
export function collectNodeRemovalImpact(graph: WorkflowGraph, removedIds: Iterable<string>): NodeRemovalImpact {
  const removed = new Set(removedIds)
  const danglingNodes: NodeRemovalImpact['danglingNodes'] = []
  for (const node of graph.nodes) {
    if (removed.has(node.id)) continue
    const fields: string[] = []
    if (node.type === 'agent' || node.type === 'output') {
      for (const [label, template] of [['prompt', node.config.prompt], ['path', node.type === 'output' ? node.config.path : '']] as const) {
        if (template && stripTemplateReferences(template, removed) !== template) fields.push(label)
      }
    } else if (node.type === 'script') {
      for (const [key, value] of Object.entries(node.config.params)) {
        if (typeof value === 'string' && stripTemplateReferences(value, removed) !== value) fields.push(`params.${key}`)
      }
    }
    if (fields.length) danglingNodes.push({ node, fields })
  }
  return {
    danglingNodes,
    orphanedEdges: graph.edges.filter(edge => {
      const id = edgeConditionNodeId(edge)
      return id !== null && removed.has(id)
    }),
  }
}

/** 删除节点并连带清理悬空引用、失效条件边与相连的普通边。 */
export function removeNodesWithReferences(graph: WorkflowGraph, removedIds: Iterable<string>): WorkflowGraph {
  const removed = new Set(removedIds)
  const orphanEdges = new Set(collectNodeRemovalImpact(graph, removed).orphanedEdges.map(edge => edge.id))
  return {
    nodes: graph.nodes
      .filter(node => !removed.has(node.id))
      .map(node => mapNodeTemplates(node, template => stripTemplateReferences(template, removed))),
    edges: graph.edges.filter(edge => !orphanEdges.has(edge.id) && !removed.has(edge.source) && !removed.has(edge.target)),
  }
}
