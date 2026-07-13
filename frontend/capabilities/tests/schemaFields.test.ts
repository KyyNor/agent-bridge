import assert from 'node:assert/strict'
import test from 'node:test'

import { fieldsToSchema, isSimpleObjectSchema, schemaToFields } from '../src/lib/schemaFields.ts'

test('simple object schema round trips through field rows', () => {
  const schema = {
    type: 'object',
    properties: {
      summary: { type: 'string', description: '说明' },
      count: { type: 'integer' },
    },
    required: ['summary'],
    additionalProperties: false,
  }

  const fields = schemaToFields(schema)

  assert.deepEqual(fields, [
    { name: 'summary', type: 'string', required: true, description: '说明' },
    { name: 'count', type: 'integer', required: false, description: '' },
  ])
  assert.deepEqual(fieldsToSchema(fields), schema)
})

test('nested schema stays in advanced mode without dropping data', () => {
  const schema = {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        properties: {
          value: { type: 'string' },
        },
      },
    },
  }

  assert.equal(isSimpleObjectSchema(schema), false)
})
