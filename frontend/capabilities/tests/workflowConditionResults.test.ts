import assert from 'node:assert/strict'
import test from 'node:test'

import { formatWorkflowConditionActual } from '../src/lib/workflowConditionResults.ts'

test('workflow condition labels serialize structured actual values as compact JSON', () => {
  assert.equal(formatWorkflowConditionActual({ status: 'ready', count: 2 }), '{"status":"ready","count":2}')
  assert.equal(formatWorkflowConditionActual(['ready', 2]), '["ready",2]')
})

test('workflow condition labels preserve scalar actual values', () => {
  assert.equal(formatWorkflowConditionActual('ready'), 'ready')
  assert.equal(formatWorkflowConditionActual(false), 'false')
  assert.equal(formatWorkflowConditionActual(null), '')
})
