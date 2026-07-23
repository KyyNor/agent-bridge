import assert from 'node:assert/strict'
import test from 'node:test'

import { buildTimeline, eventKindLabel } from '../src/lib/runEventRender.ts'
import type { WorkflowRunEvent } from '../src/api/types.ts'

const main = { id: 'main', role: 'main' as const, label: '主 Agent' }

function ev(kind: string, created_at: string, overrides: Partial<WorkflowRunEvent> = {}): WorkflowRunEvent {
  return { kind, created_at, agent_role: 'main', ...overrides }
}

test('buildTimeline joins text deltas into one visible message', () => {
  const timeline = buildTimeline([{
    actor: main,
    events: [
      ev('agent_message', '2026-07-23T10:00:00.001Z', { message: '我', partial: true, stream_id: 'text_1' }),
      ev('agent_message', '2026-07-23T10:00:00.002Z', { message: '已', partial: true, stream_id: 'text_1' }),
      ev('agent_message', '2026-07-23T10:00:00.003Z', { message: '掌握足够的数据。', partial: true, stream_id: 'text_1' }),
    ],
  }])

  assert.equal(timeline.length, 1)
  assert.equal(timeline[0].event.message, '我已掌握足够的数据。')
})

test('buildTimeline merges a tool call and result into one card', () => {
  const timeline = buildTimeline([{
    actor: main,
    events: [
      ev('tool_call', '2026-07-23T10:00:00.001Z', {
        status: 'started',
        tool_name: 'bash',
        tool_use_id: 'call_1',
        input: { command: 'pwd' },
        message: '调用工具 bash',
      }),
      ev('tool_result', '2026-07-23T10:00:00.010Z', {
        status: 'success',
        tool_name: 'bash',
        tool_use_id: 'call_1',
        output: '/tmp\n',
        duration_ms: 9,
        message: '工具 bash 调用成功',
      }),
    ],
  }])

  assert.equal(timeline.length, 1)
  assert.equal(timeline[0].event.kind, 'tool_call')
  assert.equal(timeline[0].event.status, 'success')
  assert.deepEqual(timeline[0].event.input, { command: 'pwd' })
  assert.equal(timeline[0].event.output, '/tmp\n')
  assert.equal(timeline[0].event.duration_ms, 9)
  assert.equal(eventKindLabel(timeline[0].event), '工具调用完成')
})
