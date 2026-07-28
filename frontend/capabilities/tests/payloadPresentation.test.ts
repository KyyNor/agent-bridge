import assert from 'node:assert/strict'
import test from 'node:test'

import {
  detectPayloadLanguage,
  extractMcpStructuredPayload,
  preparePayloadPresentation,
} from '../src/lib/payloadPresentation.ts'

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

test('extractMcpStructuredPayload unwraps a JSON string only inside the MCP response envelope', () => {
  const result = extractMcpStructuredPayload(JSON.stringify({
    service: 'codegraph',
    tool: 'explore',
    tool_name: 'explore',
    success: true,
    result: {
      structured: JSON.stringify({ matches: [{ path: 'src/app.py' }] }),
      content: [],
    },
  }))

  assert.deepEqual(result, {
    service: 'codegraph',
    toolName: 'explore',
    success: true,
    structured: { matches: [{ path: 'src/app.py' }] },
  })
})

test('extractMcpStructuredPayload rejects non-MCP and non-JSON structured strings', () => {
  assert.equal(extractMcpStructuredPayload('{"result":{"structured":"plain text"}}'), null)
  assert.equal(extractMcpStructuredPayload(JSON.stringify({
    service: 'codegraph',
    tool: 'explore',
    result: { structured: 'plain text' },
  })), null)
})
