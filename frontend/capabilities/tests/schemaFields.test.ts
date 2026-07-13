import assert from 'node:assert/strict'
import test from 'node:test'

import { fieldsToSchema, isSimpleObjectSchema, parseSchemaObjectText, schemaToFields, validateSchemaFieldNames } from '../src/lib/schemaFields.ts'

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

test('field rows preserve supported top-level schema metadata', () => {
  const schema = {
    type: 'object',
    description: 'Script parameters',
    properties: {
      repo: { type: 'string', description: 'Repository' },
    },
    required: ['repo'],
    additionalProperties: false,
  }

  const roundTripped = fieldsToSchema(schemaToFields(schema), schema)

  assert.deepEqual(roundTripped, schema)
})

test('unknown top-level metadata keeps schema in advanced mode', () => {
  assert.equal(isSimpleObjectSchema({
    type: 'object',
    properties: {},
    required: [],
    additionalProperties: false,
    'x-vendor': 'keep-me',
  }), false)
})

test('schema text parsing rejects invalid JSON and non-object roots without replacing the model', () => {
  assert.deepEqual(parseSchemaObjectText('{ invalid'), {
    ok: false,
    message: '高级 JSON 不是合法对象',
  })
  assert.deepEqual(parseSchemaObjectText('[]'), {
    ok: false,
    message: 'Schema 必须是 JSON 对象',
  })
})

test('field validation rejects blank and duplicate names', () => {
  assert.equal(validateSchemaFieldNames([
    { name: ' ', type: 'string', required: false, description: '' },
  ], '输入 Schema'), '输入 Schema字段名不能为空')
  assert.equal(validateSchemaFieldNames([
    { name: 'repo', type: 'string', required: false, description: '' },
    { name: 'repo', type: 'integer', required: true, description: '' },
  ], '输入 Schema'), '输入 Schema字段名不能重复')
})
