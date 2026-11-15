import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { WorkflowGraph, WorkflowNode } from '../src/api/types'
import { collectOutputImpact, activeOutputImpacts, outputImpactIssues } from '../src/lib/workflowOutputImpact'
const a = { id: 'a', type: 'agent', name: 'A', position: { x: 0, y: 0 }, config: { prompt: 'old', result_mode: 'json', output_schema: { type: 'object', properties: { status: { type: 'string' } } } } } as WorkflowNode
const b = { ...a, id: 'b', config: { ...a.config, prompt: '{{ nodes.a.output.status }}' } } as WorkflowNode
const replacement = { ...a, config: { ...a.config, result_mode: 'text', output_schema: null } } as WorkflowNode
const graph = { nodes: [a, b, { id: 'c', type: 'script', config: { params: { nested: ['{{ nodes.a.output.status }}'] } } }], edges: [{ id: 'ab', source: 'a', target: 'b', condition: { field: 'nodes.a.output.status', operator: 'equals', value: 'ok' } }] } as WorkflowGraph

test('输出变化列出直接引用和嵌套参数、条件，并给目标节点红色问题', () => {
  const impacts = collectOutputImpact(graph, replacement)
  assert.equal(impacts.length, 3)
  assert.ok(impacts.some(i => i.field === 'params.nested.0'))
  assert.ok(impacts.some(i => i.edgeId === 'ab' && i.nodeId === 'b'))
  assert.equal(outputImpactIssues(impacts)[0].code, 'upstream_output_changed')
  assert.equal(collectOutputImpact(graph, { ...a, name: 'renamed' }).length, 0)
})
test('移除引用或恢复输出后消除提醒，仍使用旧字段时保留', () => {
  const impacts = collectOutputImpact(graph, replacement)
  const changed = { ...graph, nodes: [replacement, b] }
  assert.equal(activeOutputImpacts(changed, impacts).length, 2)
  const repaired = { ...changed, nodes: [replacement, { ...b, config: { ...b.config, prompt: '{{ nodes.a.output.text }}' } } as WorkflowNode], edges: [] }
  assert.equal(activeOutputImpacts(repaired, impacts).length, 0)
  assert.equal(activeOutputImpacts(graph, impacts).length, 0)
})
