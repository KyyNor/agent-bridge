import assert from 'node:assert/strict'
import test from 'node:test'

import {
  lastAgentRunEventId,
  mergeAgentRunEvent,
  normalizeAgentRunEvents,
} from '../src/lib/agentRunEvents.ts'

test('agent run event helpers project historical ids and deduplicate SSE replays', () => {
  const snapshot = normalizeAgentRunEvents([
    { kind: 'status', status: 'running' },
    { kind: 'agent_message', message: 'hello' },
  ])

  assert.deepEqual(snapshot.map(event => event.event_id), [1, 2])
  assert.equal(lastAgentRunEventId(snapshot), 2)
  assert.deepEqual(
    mergeAgentRunEvent(snapshot, { event_id: 2, kind: 'agent_message', message: 'hello again' }),
    [
      { event_id: 1, kind: 'status', status: 'running' },
      { event_id: 2, kind: 'agent_message', message: 'hello again' },
    ],
  )
  assert.equal(
    mergeAgentRunEvent(snapshot, { event_id: 3, kind: 'stage', stage_name: 'run.total' }).length,
    3,
  )
})
