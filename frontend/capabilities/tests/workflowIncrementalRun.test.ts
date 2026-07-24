import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

import type { WorkflowExecutionPlan } from '../src/api/types.ts'

const root = resolve(import.meta.dirname, '..')
const source = (path: string) => readFileSync(resolve(root, path), 'utf8')

const preview: WorkflowExecutionPlan = {
  mode: 'incremental',
  baseline_run_id: 'run-baseline',
  affected_node_ids: ['render'],
  reusable_node_ids: ['load'],
  warnings: [],
  nodes: [
    { node_id: 'load', action: 'reuse', reason: 'fingerprint_match', source_run_id: 'run-baseline', source_node_id: 'load', node_fingerprint: 'fp-load' },
    { node_id: 'render', action: 'execute', reason: 'upstream_execute', source_run_id: null, source_node_id: null, node_fingerprint: 'fp-render' },
  ],
}

test('execution plan retains reuse and execute nodes with reasons and sources for preview rendering', () => {
  const reuse = preview.nodes.filter(node => node.action === 'reuse')
  const execute = preview.nodes.filter(node => node.action === 'execute')
  assert.equal(preview.baseline_run_id, 'run-baseline')
  assert.deepEqual(reuse.map(node => [node.node_id, node.reason, node.source_run_id]), [['load', 'fingerprint_match', 'run-baseline']])
  assert.deepEqual(execute.map(node => [node.node_id, node.reason]), [['render', 'upstream_execute']])
})

test('WorkflowView previews stale increments and gives completed tasks a force-full entry', () => {
  const view = source('src/views/workflow/WorkflowView.vue')
  const tasksComposable = source('src/composables/useWorkflowTasks.ts')
  const previewPanel = source('src/components/workflow/WorkflowTaskExecutionPreview.vue')
  assert.match(tasksComposable, /api\.previewWorkflowRun/)
  assert.match(tasksComposable, /execution_mode: 'incremental'/)
  assert.match(tasksComposable, /execution_mode: 'force_full'/)
  assert.match(view, /<WorkflowTaskExecutionPreview :plan="taskPreview\(task\)"/)
  assert.match(previewPanel, /复用节点/)
  assert.match(previewPanel, /重新执行节点/)
  assert.match(view, /全量运行/)
})

test('progress artifact lookup keeps artifacts from the selected run', () => {
  const view = source('src/views/workflow/WorkflowView.vue')
  const loader = view.slice(view.indexOf('async function loadProgressArtifacts'))
  assert.match(loader, /run_id: runId/)
  assert.match(loader, /include_history: true/)
})

test('WorkflowRunGraph displays node actions, sources, and reuse reasons instead of waiting', () => {
  const graph = source('src/views/workflow/WorkflowRunGraph.vue')
  assert.match(graph, /action === 'reuse'/)
  assert.match(graph, /source_run_id/)
  assert.match(graph, /reuse_reason/)
  assert.match(graph, /effectiveStatus/)
})

test('workflow run graph displays node type colors with semantic category tokens', () => {
  const graph = source('src/views/workflow/WorkflowRunGraph.vue')
  const visuals = source('src/lib/workflowNodeVisuals.ts')

  assert.match(graph, /workflowNodeToneClass/)
  assert.match(graph, /workflowNodeTypeText/)
  assert.match(visuals, /get_task: 'blue'/)
  assert.match(visuals, /agent: 'violet'/)
  assert.match(visuals, /script: 'teal'/)
  assert.match(visuals, /output: 'amber'/)
  assert.match(visuals, /bg-cat-blue-fg/)
  assert.match(visuals, /bg-cat-violet-fg/)
  assert.match(visuals, /bg-cat-teal-fg/)
  assert.match(visuals, /bg-cat-amber-fg/)
})
