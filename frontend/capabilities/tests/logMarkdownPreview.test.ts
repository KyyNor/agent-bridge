import assert from 'node:assert/strict'
import test from 'node:test'

import { extractLogMarkdownPreview } from '../src/lib/logMarkdownPreview.ts'

function responseJson(value: unknown): string {
  return JSON.stringify(value)
}

test('extractLogMarkdownPreview reads Claude Hook additionalContext for session-start', () => {
  const preview = extractLogMarkdownPreview({
    tool_name: 'session-start',
    response_json: responseJson({
      stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: '# Agent Bridge Profile\n\nMemory context' } }),
    }),
  })

  assert.deepEqual(preview, { title: '会话启动', markdown: '# Agent Bridge Profile\n\nMemory context' })
})

test('extractLogMarkdownPreview reads Claude Hook additionalContext for full-probe aliases', () => {
  const response = responseJson({
    stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: '# Retrieval reminder' } }),
  })

  assert.deepEqual(extractLogMarkdownPreview({ tool_name: 'full-probe', response_json: response }), {
    title: '全量检索探测', markdown: '# Retrieval reminder',
  })
  assert.deepEqual(extractLogMarkdownPreview({ tool_name: 'full_probe', response_json: response }), {
    title: '全量检索探测', markdown: '# Retrieval reminder',
  })
})

test('extractLogMarkdownPreview reads the CodeGraph text result only from its designated content path', () => {
  const preview = extractLogMarkdownPreview({
    tool_name: 'codegraph_explore',
    response_json: responseJson({
      mcp_result: { content: [{ type: 'text', text: '# Result' }] },
    }),
  })

  assert.deepEqual(preview, { title: 'CodeGraph 代码探索', markdown: '# Result' })
})

test('extractLogMarkdownPreview rejects malformed, empty, and unsupported payloads', () => {
  assert.equal(extractLogMarkdownPreview({ tool_name: 'session-start', response_json: '{invalid' }), null)
  assert.equal(extractLogMarkdownPreview({
    tool_name: 'full-probe',
    response_json: responseJson({ stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: '' } }) }),
  }), null)
  assert.equal(extractLogMarkdownPreview({
    tool_name: 'lookup',
    response_json: responseJson({ markdown: '# Arbitrary JSON is not previewable' }),
  }), null)
})
