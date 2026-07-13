import assert from 'node:assert/strict'
import test from 'node:test'
import { deriveAvailableData, formatWorkflowReference, referenceValueForTarget } from '../src/lib/workflowReferences.ts'
import type { ManagedScript, WorkflowGraph } from '../src/api/types.ts'

const scripts: ManagedScript[] = [
  {
    script_key: 'collect',
    name: 'Collect pages',
    description: 'Collect repository pages',
    language: 'python',
    status: 'active',
    owner_type: 'system',
    owner_key: '',
    content_hash: '',
    created_by: '',
    updated_by: '',
    created_at: '',
    updated_at: '',
    input_schema: { type: 'object', properties: { repo: { type: 'string' } } },
    output_schema: {
      type: 'object',
      properties: {
        pages: { type: 'array', description: 'Collected pages' },
      },
    },
    is_builtin: false,
  },
]

const graphWithAgentAndScript: WorkflowGraph = {
  nodes: [
    {
      id: 'task',
      type: 'get_task',
      name: 'Task',
      position: { x: 0, y: 0 },
      config: {},
    },
    {
      id: 'collect',
      type: 'script',
      name: 'Collect',
      position: { x: 120, y: 0 },
      config: { script_key: 'collect', params: { repo: '{{ input.repo }}' }, timeout_seconds: 60 },
    },
    {
      id: 'enrich',
      type: 'agent',
      name: 'Enrich',
      position: { x: 240, y: 0 },
      config: {
        prompt: '{{ nodes.collect.output.pages }}',
        backend_key: 'codex',
        mcp_enabled: false,
        skill_names: [],
        result_mode: 'json',
        output_schema: {
          type: 'object',
          properties: {
            summary: { type: 'string', description: 'Short summary' },
          },
        },
      },
    },
    {
      id: 'parallel',
      type: 'agent',
      name: 'Parallel',
      position: { x: 240, y: 120 },
      config: {
        prompt: '',
        backend_key: 'codex',
        mcp_enabled: false,
        skill_names: [],
        result_mode: 'text',
        output_schema: null,
      },
    },
    {
      id: 'report',
      type: 'output',
      name: 'Report',
      position: { x: 360, y: 0 },
      config: {
        format: 'markdown',
        title: 'Report',
        path: 'reports/index.md',
        tags: [],
        prompt: '',
        backend_key: 'codex',
        mcp_enabled: false,
        skill_names: [],
      },
    },
  ],
  edges: [
    { id: 'task-collect', source: 'task', target: 'collect', condition: null },
    { id: 'collect-enrich', source: 'collect', target: 'enrich', condition: null },
    { id: 'enrich-report', source: 'enrich', target: 'report', condition: null },
    { id: 'task-parallel', source: 'task', target: 'parallel', condition: null },
  ],
}

test('node target exposes input, task, and ancestor output fields only', () => {
  const result = deriveAvailableData(graphWithAgentAndScript, { kind: 'node', id: 'report' }, scripts)
  assert.deepEqual(result.map(item => item.path), [
    'input.repo',
    'task.task_key',
    'task.task_version',
    'task.type',
    'task.payload',
    'nodes.collect.output.pages',
    'nodes.enrich.output.summary',
  ])
  assert.equal(result.some(item => item.path.includes('parallel')), false)
})

test('edge target exposes source lineage and returns raw condition paths', () => {
  const graph: WorkflowGraph = {
    nodes: [
      graphWithAgentAndScript.nodes[0],
      {
        id: 'classify',
        type: 'agent',
        name: 'Classify',
        position: { x: 120, y: 0 },
        config: {
          prompt: '',
          backend_key: 'codex',
          mcp_enabled: false,
          skill_names: [],
          result_mode: 'json',
          output_schema: {
            type: 'object',
            properties: {
              category: { type: 'string' },
            },
          },
        },
      },
      {
        id: 'sibling',
        type: 'agent',
        name: 'Sibling',
        position: { x: 120, y: 120 },
        config: {
          prompt: '',
          backend_key: 'codex',
          mcp_enabled: false,
          skill_names: [],
          result_mode: 'json',
          output_schema: {
            type: 'object',
            properties: {
              value: { type: 'string' },
            },
          },
        },
      },
      {
        id: 'handle',
        type: 'script',
        name: 'Handle',
        position: { x: 240, y: 0 },
        config: { script_key: 'collect', params: {}, timeout_seconds: 60 },
      },
    ],
    edges: [
      { id: 'task-classify', source: 'task', target: 'classify', condition: null },
      { id: 'task-sibling', source: 'task', target: 'sibling', condition: null },
      { id: 'classify-handle', source: 'classify', target: 'handle', condition: { field: 'nodes.classify.output.category', operator: 'equals', value: 'bug' } },
    ],
  }
  const result = deriveAvailableData(graph, { kind: 'edge', id: 'classify-handle' }, scripts)
  assert.deepEqual(result.map(item => item.path), ['nodes.classify.output.category'])
  assert.equal(result.some(item => item.path === 'nodes.classify.output.category'), true)
  assert.equal(result.some(item => item.path === 'nodes.sibling.output.value'), false)
  assert.equal(formatWorkflowReference(result.find(item => item.path === 'nodes.classify.output.category')!, 'condition'), 'nodes.classify.output.category')
})

test('reference formatter inserts templates for prompt-like fields', () => {
  const item = { path: 'nodes.collect.output.pages', label: '', type: 'array', description: '' }
  assert.equal(formatWorkflowReference(item, 'template'), '{{ nodes.collect.output.pages }}')
  assert.equal(referenceValueForTarget(item, 'template', false), 'nodes.collect.output.pages')
})

test('downstream node can reference text agent and output artifact fields', () => {
  const graph: WorkflowGraph = {
    nodes: [
      {
        id: 'draft',
        type: 'agent',
        name: 'Draft',
        position: { x: 0, y: 0 },
        config: {
          prompt: '',
          backend_key: 'codex',
          mcp_enabled: false,
          skill_names: [],
          result_mode: 'text',
          output_schema: null,
        },
      },
      {
        id: 'artifact',
        type: 'output',
        name: 'Artifact',
        position: { x: 120, y: 0 },
        config: {
          format: 'markdown',
          title: 'Report',
          path: 'reports/index.md',
          tags: [],
          prompt: '{{ nodes.draft.output.text }}',
          backend_key: 'codex',
          mcp_enabled: false,
          skill_names: [],
        },
      },
      {
        id: 'publish',
        type: 'output',
        name: 'Publish',
        position: { x: 240, y: 0 },
        config: {
          format: 'html',
          title: 'HTML',
          path: 'reports/index.html',
          tags: [],
          prompt: '',
          backend_key: 'codex',
          mcp_enabled: false,
          skill_names: [],
        },
      },
    ],
    edges: [
      { id: 'draft-artifact', source: 'draft', target: 'artifact', condition: null },
      { id: 'artifact-publish', source: 'artifact', target: 'publish', condition: null },
    ],
  }
  assert.deepEqual(deriveAvailableData(graph, { kind: 'node', id: 'publish' }, []).map(item => item.path), [
    'nodes.draft.output.text',
    'nodes.artifact.output.title',
    'nodes.artifact.output.summary',
    'nodes.artifact.output.content',
    'nodes.artifact.output.artifact_ids',
  ])
})
