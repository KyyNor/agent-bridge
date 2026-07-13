import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatWorkflowValue,
  parseWorkflowValue,
  workflowValueTypeForReference,
} from '../src/lib/workflowValues.ts'

test('typed workflow values preserve integer number boolean object and array types', () => {
  assert.deepEqual(parseWorkflowValue('42', 'integer'), { ok: true, value: 42 })
  assert.deepEqual(parseWorkflowValue('3.5', 'number'), { ok: true, value: 3.5 })
  assert.deepEqual(parseWorkflowValue('false', 'boolean'), { ok: true, value: false })
  assert.deepEqual(parseWorkflowValue('{"enabled":true}', 'object'), { ok: true, value: { enabled: true } })
  assert.deepEqual(parseWorkflowValue('[1,false]', 'array'), { ok: true, value: [1, false] })
})

test('typed workflow values keep exact template references and reject invalid literals', () => {
  assert.deepEqual(parseWorkflowValue('{{ input.limit }}', 'integer'), {
    ok: true,
    value: '{{ input.limit }}',
  })
  assert.equal(parseWorkflowValue('3.5', 'integer').ok, false)
  assert.equal(parseWorkflowValue('truthy', 'boolean').ok, false)
  assert.equal(parseWorkflowValue('{bad}', 'object').ok, false)
})

test('condition value type follows the selected reference and round trips without string coercion', () => {
  const items = [
    { path: 'nodes.classify.output.score', label: '', type: 'number', description: '' },
    { path: 'nodes.classify.output.accepted', label: '', type: 'boolean', description: '' },
  ]

  assert.equal(workflowValueTypeForReference(items, 'nodes.classify.output.score'), 'number')
  assert.equal(workflowValueTypeForReference(items, 'nodes.classify.output.accepted'), 'boolean')
  assert.equal(parseWorkflowValue(formatWorkflowValue(7, 'number'), 'number').value, 7)
  assert.equal(parseWorkflowValue(formatWorkflowValue(true, 'boolean'), 'boolean').value, true)
})
