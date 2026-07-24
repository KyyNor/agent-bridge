import assert from 'node:assert/strict'
import test from 'node:test'
import { buildArtifactTree, flattenArtifactTree } from '../src/lib/workflowArtifactTree.ts'
import type { WorkflowArtifact } from '../src/api/types.ts'

const artifacts = [
  { path: 'reports/a.md', artifact_id: 'a' },
  { path: 'reports/nested/b.md', artifact_id: 'b' },
] as WorkflowArtifact[]

test('workflow artifact tree creates stable folder rows and honors collapsed paths', () => {
  const tree = buildArtifactTree(artifacts)
  assert.deepEqual(tree.map(node => node.path), ['reports'])
  assert.deepEqual(flattenArtifactTree(tree, new Set()).map(row => row.path), [
    'reports', 'reports/a.md', 'reports/nested', 'reports/nested/b.md',
  ])
  assert.deepEqual(flattenArtifactTree(tree, new Set(['reports'])).map(row => row.path), ['reports'])
})
