import assert from 'node:assert/strict'
import test from 'node:test'

import { detectPayloadLanguage, preparePayloadPresentation } from '../src/lib/payloadPresentation.ts'

test('detectPayloadLanguage honors content metadata before content inference', () => {
  assert.equal(detectPayloadLanguage('# heading', { contentType: 'text/markdown' }), 'markdown')
  assert.equal(detectPayloadLanguage('<html></html>', { ref: 'report.html' }), 'html')
  assert.equal(detectPayloadLanguage('{"ok":true}'), 'json')
  assert.equal(detectPayloadLanguage('def run(): pass'), 'python')
  assert.equal(detectPayloadLanguage('const run = () => {}'), 'javascript')
  assert.equal(detectPayloadLanguage('plain output'), 'text')
})

test('preparePayloadPresentation formats structured JSON before opening it', () => {
  assert.deepEqual(preparePayloadPresentation({ ok: true }), {
    content: '{\n  "ok": true\n}',
    language: 'json',
  })
})
