import type { WorkflowEdge, WorkflowGraph, WorkflowNode } from '../api/types'

/**
 * 节点 ID 是 `{{ nodes.<id>.* }}` 引用与边 source/target 的锚点。
 * 引用解析按首个点号切分节点 ID（与后端 `_node_templates`、`_ancestors` 的约定一致），
 * 因此 ID 本身不允许出现点号，否则 `nodes.a.b` 无法区分“节点 a 的 b 字段”与“节点 a.b”。
 */
export const NODE_ID_PATTERN = /^[a-z][a-z0-9_-]*$/
const NODE_ID_MAX_LENGTH = 64

function forEachTemplateExpression(
  template: string,
  visit: (expression: string, nodeId: string, prefix: string, suffix: string) => string | null,
): string {
  // 与后端 REFERENCE_RE 使用相同的路径字符；节点 ID 只取 nodes 后第一个路径段。
  return template.replace(
    /(\{\{\s*nodes\.)([A-Za-z0-9_-]+)((?:\.[A-Za-z0-9_.-]*)?\s*\}\})/g,
    (expression, prefix: string, nodeId: string, suffix: string) => visit(expression, nodeId, prefix, suffix) ?? expression,
  )
}

/** 把模板里指向被重命名节点的 `{{ nodes.<old>.* }}` 换成 `{{ nodes.<new>.* }}`。 */
export function rewriteTemplateReferences(template: string, rename: Map<string, string>): string {
  if (!template || !rename.size) return template
  return forEachTemplateExpression(template, (_expression, nodeId, prefix, suffix) => {
    const to = rename.get(nodeId)
    return to === undefined ? null : `${prefix}${to}${suffix}`
  })
}

/** 删除模板里指向已删节点的整个 `{{ nodes.<id>.* }}` 表达式，并收拾残留空白。 */
export function stripTemplateReferences(template: string, removed: Set<string>): string {
  if (!template || !removed.size) return template
  let stripped = false
  const next = forEachTemplateExpression(template, (_expression, nodeId) => {
    if (!removed.has(nodeId)) return null
    stripped = true
    return ''
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

type TemplateTransform = (template: string, field: string) => string

/** 脚本参数支持任意层次的 JSON 对象/数组，与后端 render_value 和校验遍历保持一致。 */
function mapTemplateValue(value: unknown, field: string, transform: TemplateTransform): unknown {
  if (typeof value === 'string') return transform(value, field)
  if (Array.isArray(value)) {
    const next = value.map((item, index) => mapTemplateValue(item, `${field}.${index}`, transform))
    return next.every((item, index) => item === value[index]) ? value : next
  }
  if (value !== null && typeof value === 'object') {
    let changed = false
    const entries = Object.entries(value).map(([key, item]) => {
      const next = mapTemplateValue(item, `${field}.${key}`, transform)
      if (next !== item) changed = true
      return [key, next]
    })
    return changed ? Object.fromEntries(entries) : value
  }
  return value
}

function mapNodeTemplates(node: WorkflowNode, transform: TemplateTransform): WorkflowNode {
  if (node.type === 'agent') {
    const prompt = transform(node.config.prompt || '', 'prompt')
    return prompt === node.config.prompt ? node : { ...node, config: { ...node.config, prompt } }
  }
  if (node.type === 'output') {
    const prompt = transform(node.config.prompt || '', 'prompt')
    const path = transform(node.config.path || '', 'path')
    const title = transform(node.config.title || '', 'title')
    if (prompt === node.config.prompt && path === node.config.path && title === node.config.title) return node
    return { ...node, config: { ...node.config, prompt, path, title } }
  }
  if (node.type === 'script') {
    const params = mapTemplateValue(node.config.params, 'params', transform) as Record<string, unknown>
    return params === node.config.params ? node : { ...node, config: { ...node.config, params } }
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
    // 影响预览和实际改写共用字段遍历，避免确认弹窗漏报嵌套参数或标题。
    mapNodeTemplates(node, (template, field) => {
      if (stripTemplateReferences(template, removed) !== template) fields.push(field)
      return template
    })
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
