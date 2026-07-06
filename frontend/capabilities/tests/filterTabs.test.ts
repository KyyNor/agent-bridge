import assert from 'node:assert/strict'
import test from 'node:test'

import { countAgentRunTabs, countToolCallTabs } from '../src/lib/filterTabs.ts'
import type { AgentRun, ToolCallLog } from '../src/api/types.ts'

function log(status: string): ToolCallLog {
  return {
    id: 1,
    log_id: status,
    actor: 'a',
    profile_key: null,
    entrypoint: 'e',
    source_type: null,
    source_key: null,
    tool_name: null,
    status,
    error_message: null,
    failure_stage: null,
    failure_owner: null,
    error_type: null,
    resource_type: null,
    resource_key: null,
    duration_ms: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function run(status: string, ok: boolean): AgentRun {
  return {
    id: 1,
    run_key: status,
    agent_name: 'agent',
    profile_key: null,
    workflow_key: null,
    workflow_run_id: null,
    session_id: null,
    cwd: null,
    model: null,
    ok,
    status,
    error: null,
    duration_ms: null,
    cost_usd: null,
    num_turns: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

test('countToolCallTabs is independent of the active status list', () => {
  const baseline = [log('success'), log('success'), log('error'), log('blocked')]
  const activeList = baseline.filter(item => item.status === 'error')
  assert.deepEqual(countToolCallTabs(baseline), countToolCallTabs(baseline, activeList))
})

test('countAgentRunTabs is independent of the active status list', () => {
  const baseline = [run('completed', true), run('running', false), run('failed', false)]
  const activeList = baseline.filter(item => item.status === 'failed')
  assert.deepEqual(countAgentRunTabs(baseline), countAgentRunTabs(baseline, activeList))
})
