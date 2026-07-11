import assert from 'node:assert/strict'
import test from 'node:test'
import { createDefaultGraph, deriveManualInputFields, isProtectedSummaryEdge, isProtectedSummaryNode } from '../src/views/workflow/workflowDefinition.ts'
import type { ManagedScript, WorkflowGraph } from '../src/api/types.ts'

test('summary graph creates protected markdown and html pair', () => {
  const graph = createDefaultGraph('summary', 'codex')
  assert.deepEqual(graph.nodes.map(node => node.id), ['markdown-output', 'html-output'])
  assert.equal(isProtectedSummaryNode(graph.nodes[0], 'summary'), true)
  assert.equal(isProtectedSummaryEdge(graph.edges[0], 'summary'), true)
  assert.deepEqual(graph.nodes.map(node => node.config.backend_key), ['codex', 'codex'])
})

test('manual input fields are derived from selected script schemas', () => {
  const graph: WorkflowGraph = { nodes: [{ id: 'collect', type: 'script', name: 'Collect', position: { x: 0, y: 0 }, config: { script_key: 'collect', params: { repo: '{{ input.repo }}', limit: '{{ input.limit }}' }, timeout_seconds: 60 } }], edges: [] }
  const scripts: ManagedScript[] = [{ script_key: 'collect', name: 'Collect', description: '', language: 'python', status: 'active', owner_type: 'system', owner_key: '', content_hash: '', created_by: '', updated_by: '', created_at: '', updated_at: '', input_schema: { type: 'object', properties: { repo: { type: 'string' }, limit: { type: 'integer' } }, required: ['repo'] } }]
  assert.deepEqual(deriveManualInputFields(graph, scripts), [
    { path: 'input.limit', type: 'integer', required: false, description: '' },
    { path: 'input.repo', type: 'string', required: true, description: '' },
  ])
})
