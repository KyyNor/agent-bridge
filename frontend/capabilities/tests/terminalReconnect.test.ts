import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  TERMINAL_MAX_RETRIES,
  terminalCloseAction,
  terminalCloseMessage,
  terminalRetryDelay,
} from '../src/lib/terminalReconnect'

test('Agent 结束后不再重连', () => {
  assert.equal(terminalCloseAction(4000, 0, true), 'ended')
  assert.equal(terminalCloseAction(4001, 0, true), 'ended')
})

test('业务性拒绝不自动重连', () => {
  assert.equal(terminalCloseAction(4009, 0, true), 'stop')
  assert.equal(terminalCloseAction(1008, 0, true), 'stop')
  assert.equal(terminalCloseAction(1013, 0, true), 'stop')
})

test('连上即断不会无限重连：计数不被重置，达到上限即停', () => {
  assert.equal(terminalCloseAction(1006, 0, true), 'retry')
  assert.equal(terminalCloseAction(1006, TERMINAL_MAX_RETRIES - 1, true), 'retry')
  assert.equal(terminalCloseAction(1006, TERMINAL_MAX_RETRIES, true), 'stop')
  assert.equal(terminalCloseAction(1011, TERMINAL_MAX_RETRIES + 5, true), 'stop')
})

test('从未连上过则不重试', () => {
  assert.equal(terminalCloseAction(1006, 0, false), 'stop')
})

test('退避有上限，且结束文案带退出码', () => {
  assert.equal(terminalRetryDelay(0), 500)
  assert.equal(terminalRetryDelay(1), 1000)
  assert.equal(terminalRetryDelay(10), 4000)
  assert.match(terminalCloseMessage(4000, 137), /Agent 已退出（137）/)
  assert.match(terminalCloseMessage(4001), /建议关闭页面/)
  assert.doesNotMatch(terminalCloseMessage(1013), /Agent 已退出/)
})
