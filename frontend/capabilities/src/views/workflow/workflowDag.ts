import { parse } from '@babel/parser'
import generate from '@babel/generator'

export interface WorkflowDagNode {
  id: string
  label: string
  kind: 'agent' | 'parallel' | 'terminal'
  phase: string
  order: number
}

export interface WorkflowDagEdge {
  from: string
  to: string
  when?: string
}

export interface WorkflowDag {
  nodes: WorkflowDagNode[]
  edges: WorkflowDagEdge[]
  warnings: string[]
}

interface ParseState {
  nodes: WorkflowDagNode[]
  edges: WorkflowDagEdge[]
  warnings: string[]
  ids: Set<string>
  order: number
  phase: string
}

interface FlowResult {
  first: string[]
  exits: string[]
}

interface WalkOptions {
  edgeWhen?: string
}

const emptyFlow: FlowResult = { first: [], exits: [] }

export function parseWorkflowDag(source: string): WorkflowDag {
  const state: ParseState = {
    nodes: [],
    edges: [],
    warnings: [],
    ids: new Set(),
    order: 0,
    phase: '',
  }

  if (!source.trim()) {
    return { ...state, warnings: ['workflow.js 为空，无法生成 DAG'] }
  }

  let ast: any
  try {
    ast = parse(source, {
      sourceType: 'module',
      allowAwaitOutsideFunction: true,
      allowReturnOutsideFunction: true,
      errorRecovery: true,
    })
  } catch (error) {
    return {
      nodes: [],
      edges: [],
      warnings: [`workflow.js 解析失败：${error instanceof Error ? error.message : '未知错误'}`],
    }
  }

  if (ast.errors?.length) {
    state.warnings.push(...ast.errors.slice(0, 3).map((error: Error) => `解析警告：${error.message}`))
  }

  walkStatements(ast.program?.body || [], state, [])

  if (!state.nodes.length) {
    state.warnings.push('没有识别到 agent() / parallel() / return 调用')
  }

  return {
    nodes: state.nodes,
    edges: dedupeEdges(state.edges),
    warnings: state.warnings,
  }
}

function walkStatements(statements: any[], state: ParseState, incoming: string[], options: WalkOptions = {}): FlowResult {
  let first: string[] = []
  let exits = incoming
  let pendingWhen = options.edgeWhen

  for (const statement of statements) {
    if (isMetaExport(statement) || isDeclarationOnly(statement)) continue

    const result = walkStatement(statement, state, exits, { edgeWhen: pendingWhen })
    pendingWhen = undefined
    if (!result.first.length && !result.exits.length) continue
    if (!first.length) first = result.first
    exits = result.exits
  }

  return { first, exits }
}

function walkStatement(statement: any, state: ParseState, incoming: string[], options: WalkOptions): FlowResult {
  if (statement.type === 'ExpressionStatement') {
    const expression = unwrapAwait(statement.expression)
    if (isPhaseCall(expression)) {
      state.phase = stringArg(expression, 0) || state.phase
      return { first: [], exits: incoming }
    }
    return flowFromExpression(expression, state, incoming, options)
  }

  if (statement.type === 'VariableDeclaration') {
    let result: FlowResult = emptyFlow
    for (const declaration of statement.declarations || []) {
      const expression = unwrapAwait(declaration.init)
      result = mergeSequence(result, flowFromExpression(expression, state, result.exits.length ? result.exits : incoming, options))
    }
    return result.first.length || result.exits.length ? result : { first: [], exits: incoming }
  }

  if (statement.type === 'IfStatement') {
    return flowFromIf(statement, state, incoming, options)
  }

  if (statement.type === 'ReturnStatement') {
    const node = addNode(state, 'return', 'return', 'terminal')
    connectAll(state, incoming, [node.id], options.edgeWhen)
    return { first: [node.id], exits: [] }
  }

  if (statement.type === 'BlockStatement') {
    return walkStatements(statement.body || [], state, incoming, options)
  }

  return { first: [], exits: incoming }
}

function flowFromExpression(expression: any, state: ParseState, incoming: string[], options: WalkOptions): FlowResult {
  if (!expression) return { first: [], exits: incoming }

  if (isAssignmentWithExpression(expression)) {
    return flowFromExpression(unwrapAwait(expression.right), state, incoming, options)
  }

  if (isAgentCall(expression)) {
    const node = addAgentNode(state, expression)
    connectAll(state, incoming, [node.id], options.edgeWhen)
    return { first: [node.id], exits: [node.id] }
  }

  if (isParallelCall(expression)) {
    return flowFromParallel(expression, state, incoming, options)
  }

  const nestedAgent = findFirstCall(expression, 'agent')
  const nestedParallel = findFirstCall(expression, 'parallel')
  if (nestedParallel) return flowFromParallel(nestedParallel, state, incoming, options)
  if (nestedAgent) {
    const node = addAgentNode(state, nestedAgent)
    connectAll(state, incoming, [node.id], options.edgeWhen)
    return { first: [node.id], exits: [node.id] }
  }

  return { first: [], exits: incoming }
}

function flowFromIf(statement: any, state: ParseState, incoming: string[], options: WalkOptions): FlowResult {
  const condition = toCode(statement.test)
  const thenFlow = walkStatements(asStatementList(statement.consequent), state, incoming, { edgeWhen: condition })
  const elseFlow = statement.alternate
    ? walkStatements(asStatementList(statement.alternate), state, incoming, { edgeWhen: `else: ${condition}` })
    : { first: [], exits: incoming }

  if (options.edgeWhen) {
    for (const edge of state.edges) {
      if (thenFlow.first.includes(edge.to) && incoming.includes(edge.from)) {
        edge.when = `${options.edgeWhen} && ${edge.when || condition}`
      }
    }
  }

  return {
    first: [...thenFlow.first, ...elseFlow.first],
    exits: [...thenFlow.exits, ...elseFlow.exits],
  }
}

function flowFromParallel(expression: any, state: ParseState, incoming: string[], options: WalkOptions): FlowResult {
  const branches = expression.arguments?.[0]
  if (branches?.type !== 'ArrayExpression') {
    const node = addNode(state, 'parallel', 'parallel', 'parallel')
    connectAll(state, incoming, [node.id], options.edgeWhen)
    state.warnings.push('识别到动态 parallel(...)，只能展示为单个并行节点')
    return { first: [node.id], exits: [node.id] }
  }

  const branchFlows: FlowResult[] = []
  for (const item of branches.elements || []) {
    const body = item?.body
    if (!body) continue
    const expressionBody = body.type === 'BlockStatement' ? null : unwrapAwait(body)
    const flow = expressionBody
      ? flowFromExpression(expressionBody, state, incoming, options)
      : walkStatements(body.body || [], state, incoming, options)
    if (flow.first.length || flow.exits.length) branchFlows.push(flow)
  }

  if (!branchFlows.length) {
    const node = addNode(state, 'parallel', 'parallel', 'parallel')
    connectAll(state, incoming, [node.id], options.edgeWhen)
    return { first: [node.id], exits: [node.id] }
  }

  return {
    first: branchFlows.flatMap(flow => flow.first),
    exits: branchFlows.flatMap(flow => flow.exits),
  }
}

function addAgentNode(state: ParseState, call: any): WorkflowDagNode {
  const options = call.arguments?.[1]
  const label = objectStringProperty(options, 'label') || `agent_${state.order + 1}`
  const phase = objectStringProperty(options, 'phase') || state.phase
  return addNode(state, label, label, 'agent', phase)
}

function addNode(state: ParseState, rawId: string, label: string, kind: WorkflowDagNode['kind'], phase = state.phase): WorkflowDagNode {
  const id = uniqueId(state, slug(rawId || label || kind))
  const node: WorkflowDagNode = {
    id,
    label,
    kind,
    phase,
    order: state.order++,
  }
  state.nodes.push(node)
  return node
}

function connectAll(state: ParseState, from: string[], to: string[], when?: string) {
  for (const source of from) {
    for (const target of to) {
      if (source && target && source !== target) state.edges.push({ from: source, to: target, when })
    }
  }
}

function mergeSequence(previous: FlowResult, next: FlowResult): FlowResult {
  if (!previous.first.length) return next
  if (!next.first.length && !next.exits.length) return previous
  return { first: previous.first, exits: next.exits }
}

function uniqueId(state: ParseState, base: string) {
  let id = base || 'node'
  let index = 2
  while (state.ids.has(id)) id = `${base}_${index++}`
  state.ids.add(id)
  return id
}

function slug(value: string) {
  return value.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'node'
}

function dedupeEdges(edges: WorkflowDagEdge[]) {
  const seen = new Set<string>()
  return edges.filter(edge => {
    const key = `${edge.from}->${edge.to}:${edge.when || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function isMetaExport(statement: any) {
  return statement.type === 'ExportNamedDeclaration'
    && statement.declaration?.type === 'VariableDeclaration'
    && statement.declaration.declarations?.some((declaration: any) => declaration.id?.name === 'meta')
}

function isDeclarationOnly(statement: any) {
  if (statement.type === 'FunctionDeclaration') return true
  if (statement.type !== 'VariableDeclaration') return false
  return !statement.declarations?.some((declaration: any) => {
    const expression = unwrapAwait(declaration.init)
    return findFirstCall(expression, 'agent') || findFirstCall(expression, 'parallel')
  })
}

function isAssignmentWithExpression(expression: any) {
  return expression?.type === 'AssignmentExpression'
}

function isAgentCall(expression: any) {
  return expression?.type === 'CallExpression' && expression.callee?.type === 'Identifier' && expression.callee.name === 'agent'
}

function isParallelCall(expression: any) {
  return expression?.type === 'CallExpression' && expression.callee?.type === 'Identifier' && expression.callee.name === 'parallel'
}

function isPhaseCall(expression: any) {
  return expression?.type === 'CallExpression' && expression.callee?.type === 'Identifier' && expression.callee.name === 'phase'
}

function unwrapAwait(expression: any): any {
  return expression?.type === 'AwaitExpression' ? expression.argument : expression
}

function stringArg(call: any, index: number) {
  const arg = call?.arguments?.[index]
  return arg?.type === 'StringLiteral' ? arg.value : ''
}

function objectStringProperty(objectExpression: any, key: string) {
  if (objectExpression?.type !== 'ObjectExpression') return ''
  const property = objectExpression.properties?.find((item: any) =>
    item.type === 'ObjectProperty'
    && ((item.key?.type === 'Identifier' && item.key.name === key) || (item.key?.type === 'StringLiteral' && item.key.value === key))
  )
  return property?.value?.type === 'StringLiteral' ? property.value.value : ''
}

function asStatementList(statement: any) {
  if (!statement) return []
  return statement.type === 'BlockStatement' ? statement.body || [] : [statement]
}

function findFirstCall(node: any, name: string): any {
  if (!node || typeof node !== 'object') return null
  if (node.type === 'CallExpression' && node.callee?.type === 'Identifier' && node.callee.name === name) return node
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = findFirstCall(item, name)
        if (found) return found
      }
    } else if (value && typeof value === 'object') {
      const found = findFirstCall(value, name)
      if (found) return found
    }
  }
  return null
}

function toCode(node: any) {
  try {
    return generate(node, { comments: false, compact: true }).code
  } catch {
    return 'condition'
  }
}
