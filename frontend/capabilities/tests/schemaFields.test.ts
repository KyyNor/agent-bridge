import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from 'vue'

import { cloneSchemaValue, fieldsToSchema, isSimpleObjectSchema, parseSchemaObjectText, schemaToFields, validateSchemaFieldNames } from '../src/lib/schemaFields.ts'

test('schema cloning unwraps Vue reactive objects before structured cloning', () => {
  const schema = reactive({
    type: 'object',
    properties: { result: { type: 'string' } },
  })

  assert.deepEqual(cloneSchemaValue(schema), {
    type: 'object',
    properties: { result: { type: 'string' } },
  })
})

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

test('nested schema is available in fields mode without dropping data', () => {
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

  assert.equal(isSimpleObjectSchema(schema), true)
  assert.deepEqual(fieldsToSchema(schemaToFields(schema), schema), schema)
})

test('standard constraints and composition keywords stay editable and round trip', () => {
  const schema = {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    $defs: {
      resultId: { type: 'string', format: 'uuid' },
    },
    type: 'object',
    properties: {
      status: {
        type: 'string',
        enum: ['draft', 'published'],
        default: 'draft',
        minLength: 1,
        maxLength: 16,
      },
      tags: {
        type: 'array',
        items: { type: 'string', pattern: '^[a-z]+$' },
        minItems: 1,
        uniqueItems: true,
      },
      payload: {
        type: 'object',
        properties: {
          id: { $ref: '#/$defs/resultId' },
        },
        required: ['id'],
        additionalProperties: false,
      },
      optional: {
        type: ['string', 'null'],
      },
    },
    required: ['status'],
    additionalProperties: false,
  }

  assert.equal(isSimpleObjectSchema(schema), true)
  const fields = schemaToFields(schema)
  assert.equal(fields.find(field => field.name === 'status')?.type, 'string')
  assert.equal(fields.find(field => field.name === 'optional')?.type, 'union')
  assert.deepEqual(fieldsToSchema(fields, schema), schema)
})

test('object-like schemas can omit or union the root type without being flattened', () => {
  const withoutType = {
    properties: { value: { type: 'string' } },
    required: ['value'],
  }
  const nullableObject = {
    type: ['object', 'null'],
    properties: { value: { type: 'string' } },
    required: [],
  }

  assert.equal(isSimpleObjectSchema(withoutType), true)
  assert.equal(isSimpleObjectSchema(nullableObject), true)
  assert.deepEqual(fieldsToSchema(schemaToFields(withoutType), withoutType), withoutType)
  assert.deepEqual(fieldsToSchema(schemaToFields(nullableObject), nullableObject), nullableObject)
})

test('null is a supported compact field type', () => {
  const schema = {
    type: 'object',
    properties: { value: { type: 'null' } },
    required: [],
    additionalProperties: true,
  }

  assert.equal(isSimpleObjectSchema(schema), true)
  assert.deepEqual(schemaToFields(schema), [
    { name: 'value', type: 'null', required: false, description: '' },
  ])
  assert.deepEqual(fieldsToSchema(schemaToFields(schema), schema), schema)
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

test('field rows preserve unspecified additionalProperties and schema annotations', () => {
  const schema = {
    type: 'object',
    title: 'Runtime payload',
    description: 'Properties outside this list remain allowed by default.',
    properties: {
      count: { type: 'integer' },
    },
    required: [],
  }

  assert.equal(isSimpleObjectSchema(schema), true)
  assert.deepEqual(fieldsToSchema(schemaToFields(schema), schema), schema)
  assert.equal('additionalProperties' in fieldsToSchema(schemaToFields(schema), schema), false)
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
