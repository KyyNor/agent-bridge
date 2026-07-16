import assert from 'node:assert/strict'
import test from 'node:test'

import {
  agentRunStatusLabel,
  agentRunBadgeVariant,
  agentRunOkFilterParam,
  agentRunStatusFilterParam,
} from '../src/lib/agentRunStatus.ts'

test('agent run status distinguishes running from failed even when ok is false', () => {
  const running = { ok: false, status: 'running' }

  assert.equal(agentRunStatusLabel(running), '执行中')
  assert.equal(agentRunBadgeVariant(running), 'running')
})

test('agent run status labels stopped runs and uses a stopped badge', () => {
  const stopped = { ok: false, status: 'stopped' }

  assert.equal(agentRunStatusLabel(stopped), '已停止')
  assert.equal(agentRunBadgeVariant(stopped), 'stopped')
})

test('agent run ok filter excludes running rows from failed filter', () => {
  assert.equal(agentRunOkFilterParam(''), undefined)
  assert.equal(agentRunOkFilterParam('success'), true)
  assert.equal(agentRunOkFilterParam('failed'), false)
  assert.equal(agentRunOkFilterParam('running'), undefined)
  assert.equal(agentRunStatusFilterParam('success'), undefined)
  assert.equal(agentRunStatusFilterParam('failed'), 'failed')
  assert.equal(agentRunStatusFilterParam('running'), 'running')
  assert.equal(agentRunStatusFilterParam('stopped'), 'stopped')
})
