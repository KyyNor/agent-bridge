import assert from 'node:assert/strict'
import test from 'node:test'
import { api, beginWorkflowValidationRun, finishWorkflowValidationRun, hasBlockingWorkflowValidationErrors, invalidateWorkflowValidationRun, isCurrentWorkflowValidationRun, workflowValidationIssuesFor } from '../src/api/client.ts'
import { createDefaultGraph, deriveManualInputFields, deriveWorkflowBackendKeys, isProtectedSummaryEdge, isProtectedSummaryNode, migrateWorkflowGraph } from '../src/views/workflow/workflowDefinition.ts'
import type { AgentRuntimeConfig, ManagedScript, WorkflowGraph, WorkflowValidationResult } from '../src/api/types.ts'

test('summary graph creates protected markdown and html pair', () => {
  const graph = createDefaultGraph('summary', 'codex')
  assert.deepEqual(graph.nodes.map(node => node.id), ['markdown-output', 'html-output'])
  assert.equal(isProtectedSummaryNode(graph.nodes[0], 'summary'), true)
  assert.equal(isProtectedSummaryEdge(graph.edges[0], 'summary'), true)
  assert.deepEqual(graph.nodes.map(node => node.config.backend_key), ['codex', 'codex'])
  assert.deepEqual(graph.nodes[1].config.skill_names, ['design_html_report'])
})

test('manual input fields are derived from selected script schemas', () => {
  const graph: WorkflowGraph = { nodes: [{ id: 'collect', type: 'script', name: 'Collect', position: { x: 0, y: 0 }, config: { script_key: 'collect', params: { repo: '{{ input.repo }}', limit: '{{ input.limit }}' }, timeout_seconds: 60 } }], edges: [] }
    const scripts: ManagedScript[] = [{ script_key: 'collect', name: 'Collect', description: '', language: 'python', status: 'active', owner_type: 'system', owner_key: '', content_hash: '', created_by: '', updated_by: '', created_at: '', updated_at: '', input_schema: { type: 'object', properties: { repo: { type: 'string' }, limit: { type: 'integer' } }, required: ['repo'] }, output_schema: null, is_builtin: false }]
  assert.deepEqual(deriveManualInputFields(graph, scripts), [
    { path: 'input.limit', type: 'integer', required: false, description: '' },
    { path: 'input.repo', type: 'string', required: true, description: '' },
  ])
})

test('workflow validation issues locate node and edge fields', () => {
  const result: WorkflowValidationResult = {
    valid: false,
    errors: [
      { scope: 'node', id: 'agent-1', field: 'config.prompt', code: 'invalid_reference', message: '引用不存在' },
      { scope: 'edge', id: 'agent-1-output-1', field: 'condition.field', code: 'invalid_condition', message: '条件字段不合法' },
      { scope: 'workflow', id: null, field: 'profile_key', code: 'missing_profile', message: 'Profile 不存在' },
    ],
    warnings: [],
  }

  assert.equal(hasBlockingWorkflowValidationErrors(result), true)
  assert.deepEqual(workflowValidationIssuesFor(result.errors, 'node', 'agent-1'), [result.errors[0]])
  assert.deepEqual(workflowValidationIssuesFor(result.errors, 'edge', 'agent-1-output-1'), [result.errors[1]])
})

test('validateWorkflow posts draft workflow without saving it', async () => {
  const calls: Array<{ url: string; init: RequestInit }> = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init || {} })
    return new Response(JSON.stringify({ valid: true, errors: [], warnings: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  try {
    const result = await api.validateWorkflow({
      workflow_key: 'draft-only',
      name: 'Draft Only',
      description: '',
      profile_key: 'report-plane',
      status: 'active',
      workflow_type: 'operation',
      definition: { nodes: [], edges: [] },
    })

    assert.equal(result.valid, true)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/workflows/validate')
    assert.equal(calls[0].init.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
      workflow: {
        workflow_key: 'draft-only',
        name: 'Draft Only',
        description: '',
        profile_key: 'report-plane',
        status: 'active',
        workflow_type: 'operation',
        definition: { nodes: [], edges: [] },
      },
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('workflow list reads bypass the browser cache after an import', async () => {
  const calls: Array<{ url: string; init: RequestInit }> = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init || {} })
    return new Response('[]', {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  try {
    await api.listWorkflows()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/workflows')
    assert.equal(calls[0].init.cache, 'no-store')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('workflow import errors preserve node, field, and validation reason', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => new Response(JSON.stringify({
    detail: '工作流定义校验失败',
    errors: [
      {
        scope: 'node',
        id: 'seed-tasks',
        field: 'config.script_key',
        code: 'missing_script',
        message: '脚本不存在或未启用: seed_fine_report_tasks',
      },
    ],
  }), { status: 400, headers: { 'Content-Type': 'application/json' } })) as typeof fetch

  try {
    const file = Object.assign(new Blob(['{}'], { type: 'application/json' }), { name: 'workflow.json' }) as File
    await assert.rejects(
      () => api.previewWorkflowImport(file),
      (error: unknown) => {
        assert.ok(error instanceof Error)
        assert.match(error.message, /工作流定义校验失败/)
        assert.match(error.message, /seed-tasks/)
        assert.match(error.message, /config\.script_key/)
        assert.match(error.message, /seed_fine_report_tasks/)
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('workflow type migration preserves ordinary DAG nodes and valid edges', () => {
  const operation: WorkflowGraph = {
    nodes: [
      { id: 'collect', type: 'agent', name: 'Collect', position: { x: 0, y: 0 }, config: { prompt: '', backend_key: 'codex', mcp_enabled: false, skill_names: [], result_mode: 'text', output_schema: null } },
      { id: 'clean', type: 'script', name: 'Clean', position: { x: 160, y: 0 }, config: { script_key: 'clean', params: {}, timeout_seconds: 60 } },
      { id: 'old-output', type: 'output', name: 'Old Output', position: { x: 320, y: 0 }, config: { format: 'markdown', title: 'Old', path: 'old.md', tags: [], prompt: '', backend_key: 'codex', mcp_enabled: false, skill_names: [] } },
    ],
    edges: [
      { id: 'collect-clean', source: 'collect', target: 'clean', condition: null },
      { id: 'clean-old', source: 'clean', target: 'old-output', condition: null },
      { id: 'dangling', source: 'missing', target: 'clean', condition: null },
    ],
  }

  const summary = migrateWorkflowGraph(operation, 'operation', 'summary', 'codex')

  assert.deepEqual(summary.nodes.map(node => node.id), ['collect', 'clean', 'old-output', 'markdown-output', 'html-output'])
  assert.equal(summary.edges.some(edge => edge.id === 'collect-clean'), true)
  assert.equal(summary.edges.some(edge => edge.id === 'clean-old'), true)
  assert.equal(summary.edges.some(edge => edge.source === 'old-output' && edge.target === 'markdown-output'), true)
  assert.equal(summary.edges.some(edge => edge.id === 'markdown-to-html'), true)
  assert.equal(summary.edges.some(edge => edge.id === 'dangling'), true)

  const roundTrip = migrateWorkflowGraph(summary, 'summary', 'operation', 'codex')
  assert.deepEqual(roundTrip, operation)
})

test('summary migration marks system nodes and avoids user node and edge id collisions', () => {
  const operation: WorkflowGraph = {
    nodes: [
      { id: 'markdown-output', type: 'agent', name: 'User node', position: { x: 0, y: 0 }, config: { prompt: '', backend_key: 'team-codex', mcp_enabled: false, skill_names: [], result_mode: 'text', output_schema: null } },
    ],
    edges: [
      { id: 'markdown-to-html', source: 'markdown-output', target: 'missing', condition: null },
    ],
  }

  const summary = migrateWorkflowGraph(operation, 'operation', 'summary', 'team-codex')
  const systemNodes = summary.nodes.filter(node => node.type === 'output' && node.config.system_role)
  const systemEdge = summary.edges.find(edge => edge.system_role === 'summary_markdown_to_html')

  assert.equal(summary.nodes.some(node => node.id === 'markdown-output' && node.type === 'agent'), true)
  assert.deepEqual(systemNodes.map(node => node.id), ['markdown-output-2', 'html-output'])
  assert.equal(systemEdge?.id, 'markdown-to-html-2')
  assert.equal(new Set(summary.nodes.map(node => node.id)).size, summary.nodes.length)
  assert.equal(new Set(summary.edges.map(edge => edge.id)).size, summary.edges.length)
  assert.deepEqual(migrateWorkflowGraph(summary, 'summary', 'operation', 'team-codex'), operation)
})

test('legacy summary output pair is recognized by node types instead of fixed ids', () => {
  const legacy = createDefaultGraph('summary', 'codex')
  legacy.nodes[0].id = 'legacy-markdown'
  legacy.nodes[1].id = 'legacy-html'
  legacy.edges[0] = {
    id: 'legacy-bridge',
    source: 'legacy-markdown',
    target: 'legacy-html',
    condition: null,
  }
  delete legacy.nodes[0].config.system_role
  delete legacy.nodes[1].config.system_role

  assert.deepEqual(migrateWorkflowGraph(legacy, 'summary', 'operation', 'codex'), {
    nodes: [],
    edges: [],
  })
})

test('workflow backend options come from the runtime registry payload and keep custom slugs', () => {
  const runtime: AgentRuntimeConfig = {
    default_backend: 'team-codex',
    backends: [{ slug: 'team-codex', type: 'codex', command: 'codex', model: null }],
    available_backends: [
      { slug: 'claude', display_name: 'Claude', source: 'claude_agent_sdk', capabilities: { supports_mcp: true } },
      { slug: 'team-codex', display_name: 'Codex', source: 'codex_cli', capabilities: { supports_mcp: false } },
    ],
  }

  assert.deepEqual(deriveWorkflowBackendKeys(runtime), ['claude', 'team-codex'])
})

test('workflow validation run guard blocks duplicates and ignores stale responses', () => {
  const guard = { validating: false, token: 0 }

  const first = beginWorkflowValidationRun(guard)
  assert.equal(first, 1)
  assert.equal(guard.validating, true)
  assert.equal(beginWorkflowValidationRun(guard), null)

  invalidateWorkflowValidationRun(guard)
  assert.equal(isCurrentWorkflowValidationRun(guard, first), false)

  const second = beginWorkflowValidationRun(guard)
  assert.equal(second, 3)
  assert.equal(finishWorkflowValidationRun(guard, first), false)
  assert.equal(guard.validating, true)
  assert.equal(finishWorkflowValidationRun(guard, second), true)
  assert.equal(guard.validating, false)
})
