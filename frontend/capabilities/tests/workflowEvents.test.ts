import assert from 'node:assert/strict'
import test from 'node:test'

import {
  distinctActors,
  groupEventsByActor,
  filterEventsByActor,
  subagentLabel,
  subagentUsage,
  subagentStatus,
  subagentTaskIds,
  subagentStatusLabel,
} from '../src/lib/workflowEvents.ts'
import type { WorkflowRunEvent } from '../src/api/types.ts'

function ev(kind: string, overrides: Partial<WorkflowRunEvent> = {}): WorkflowRunEvent {
  return { kind, ...overrides }
}

const mixed: WorkflowRunEvent[] = [
  ev('agent_message', { message: 'main start', agent_role: 'main' }),
  ev('tool_call', { tool_name: 'Task', tool_use_id: 'tu1', agent_role: 'main' }),
  ev('subagent_start', { task_id: 'task_1', description: 'search code', tool_use_id: 'tu1', agent_role: 'subagent' }),
  ev('subagent_progress', { task_id: 'task_1', usage: { total_tokens: 100, tool_uses: 1, duration_ms: 500 }, last_tool_name: 'Grep', agent_role: 'subagent' }),
  ev('tool_call', { tool_name: 'Grep', tool_use_id: 'g1', task_id: 'task_1', parent_tool_use_id: 'tu1', agent_role: 'subagent' }),
  ev('subagent_end', { task_id: 'task_1', status: 'completed', usage: { total_tokens: 300, tool_uses: 3, duration_ms: 2000 }, agent_role: 'subagent' }),
  ev('agent_message', { message: 'main done', agent_role: 'main' }),
]

test('distinctActors returns main first, then sub-agents in first-seen order', () => {
  const actors = distinctActors(mixed)
  assert.equal(actors.length, 2)
  assert.equal(actors[0].role, 'main')
  assert.equal(actors[1].id, 'task_1')
  assert.equal(actors[1].label, 'search code')
})

test('subagentLabel falls back to a default when no description', () => {
  const labelFor = subagentLabel([ev('subagent_start', { task_id: 'x' })])
  assert.equal(labelFor('x'), '子 Agent x')
})

test('groupEventsByActor splits main vs sub-agent events', () => {
  const groups = groupEventsByActor(mixed)
  assert.equal(groups.length, 2)
  const main = groups.find(g => g.actor.role === 'main')!
  const sub = groups.find(g => g.actor.role === 'subagent')!
  // main: start msg, Task tool_call, final msg = 3
  assert.equal(main.events.length, 3)
  // sub: start, progress, tool_call, end = 4
  assert.equal(sub.events.length, 4)
  assert.equal(sub.actor.id, 'task_1')
})

test('groupEventsByActor keeps order within each group', () => {
  const groups = groupEventsByActor(mixed)
  const sub = groups.find(g => g.actor.role === 'subagent')!
  assert.deepEqual(sub.events.map(e => e.kind), ['subagent_start', 'subagent_progress', 'tool_call', 'subagent_end'])
})

test('filterEventsByActor empty id returns all', () => {
  assert.equal(filterEventsByActor(mixed, '').length, mixed.length)
})

test('filterEventsByActor main excludes sub-agent events', () => {
  const mainOnly = filterEventsByActor(mixed, 'main')
  assert.ok(mainOnly.every(e => !(e.agent_role === 'subagent') && !e.task_id))
})

test('filterEventsByActor sub-agent id returns only that task', () => {
  const subOnly = filterEventsByActor(mixed, 'task_1')
  assert.ok(subOnly.every(e => e.task_id === 'task_1'))
  assert.equal(subOnly.length, 4)
})

test('subagentUsage returns the latest usage', () => {
  const usage = subagentUsage(mixed, 'task_1')
  assert.deepEqual(usage, { total_tokens: 300, tool_uses: 3, duration_ms: 2000 })
})

test('subagentUsage returns null when no usage events', () => {
  assert.equal(subagentUsage([ev('subagent_start', { task_id: 't' })], 't'), null)
})

test('subagentStatus returns terminal status', () => {
  assert.equal(subagentStatus(mixed, 'task_1'), 'completed')
})

test('subagentStatus returns null when not terminal', () => {
  const inProgress = [ev('subagent_start', { task_id: 't' }), ev('subagent_progress', { task_id: 't' })]
  assert.equal(subagentStatus(inProgress, 't'), null)
})

test('subagentTaskIds returns only real sub-agent ids in first-seen order', () => {
  const events = [
    ev('agent_message', { agent_role: 'main' }),
    ev('subagent_progress', { task_id: 'task_b', agent_role: 'subagent' }),
    ev('subagent_progress', { task_id: 'task_a', agent_role: 'subagent' }),
    ev('subagent_updated', { task_id: 'task_b', agent_role: 'subagent', status: 'completed' }),
  ]
  assert.deepEqual(subagentTaskIds(events), ['task_b', 'task_a'])
})

test('subagentStatusLabel shows running for active sub-agents and completion for terminal ones', () => {
  assert.equal(subagentStatusLabel([ev('subagent_progress', { task_id: 't' })], 't'), 'running')
  assert.equal(subagentStatusLabel([ev('subagent_updated', { task_id: 't', status: 'completed' })], 't'), '完成')
  assert.equal(subagentStatusLabel([ev('subagent_updated', { task_id: 't', status: 'failed' })], 't'), '失败')
  assert.equal(subagentStatusLabel([ev('agent_message', { agent_role: 'main' })], 't'), '—')
})

test('a stream with only main events yields a single main group', () => {
  const onlyMain = [ev('agent_message', { agent_role: 'main' }), ev('tool_call', { agent_role: 'main' })]
  const groups = groupEventsByActor(onlyMain)
  assert.equal(groups.length, 1)
  assert.equal(groups[0].actor.role, 'main')
})
