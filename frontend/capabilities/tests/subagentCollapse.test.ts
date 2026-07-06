import assert from 'node:assert/strict'
import test from 'node:test'

import { useSubagentCollapse } from '../src/composables/useSubagentCollapse.ts'
import type { WorkflowRunEvent } from '../src/api/types.ts'

function ev(taskId: string): WorkflowRunEvent {
  return { kind: 'subagent_start', task_id: taskId, agent_role: 'subagent' }
}

test('initCollapsed preserves already-expanded subagents when new events arrive', () => {
  const state = useSubagentCollapse()
  state.initCollapsed('run:1', [ev('a')])
  assert.equal(state.isCollapsed('run:1', 'a'), true)

  state.toggle('run:1', 'a')
  assert.equal(state.isCollapsed('run:1', 'a'), false)

  state.initCollapsed('run:1', [ev('a'), ev('b')])
  assert.equal(state.isCollapsed('run:1', 'a'), false)
  assert.equal(state.isCollapsed('run:1', 'b'), true)
})
