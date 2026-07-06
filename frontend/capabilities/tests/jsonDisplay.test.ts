import assert from 'node:assert/strict'
import test from 'node:test'

import { formatJsonValue, tokenizeJson } from '../src/lib/jsonDisplay.ts'

test('formatJsonValue parses JSON strings and pretty prints them', () => {
  assert.equal(formatJsonValue('{"b":2,"a":[true,null]}'), '{\n  "b": 2,\n  "a": [\n    true,\n    null\n  ]\n}')
})

test('formatJsonValue falls back to the original string when it is not JSON', () => {
  assert.equal(formatJsonValue('plain output'), 'plain output')
})

test('tokenizeJson classifies braces, keys, strings, numbers, booleans and nulls', () => {
  const tokens = tokenizeJson('{"ok":true,"n":12,"x":null,"s":"v"}')
  assert.ok(tokens.some(token => token.type === 'punctuation' && token.text === '{'))
  assert.ok(tokens.some(token => token.type === 'key' && token.text === '"ok"'))
  assert.ok(tokens.some(token => token.type === 'boolean' && token.text === 'true'))
  assert.ok(tokens.some(token => token.type === 'number' && token.text === '12'))
  assert.ok(tokens.some(token => token.type === 'null' && token.text === 'null'))
  assert.ok(tokens.some(token => token.type === 'string' && token.text === '"v"'))
})
