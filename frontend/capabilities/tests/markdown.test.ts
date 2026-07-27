import assert from 'node:assert/strict'
import test from 'node:test'

import { renderSafeMarkdown } from '../src/lib/markdown.ts'

test('renderSafeMarkdown renders Markdown but escapes raw HTML and blocks unsafe URLs', () => {
  const html = renderSafeMarkdown(
    '# Safe heading\n\n[trusted](https://example.com)\n\n[unsafe](javascript:alert(1))\n\n<img src=x onerror=alert(1)>',
  )

  assert.match(html, /<h1>Safe heading<\/h1>/)
  assert.match(html, /<a href="https:\/\/example\.com">trusted<\/a>/)
  assert.doesNotMatch(html, /javascript:/i)
  assert.doesNotMatch(html, /<img src=x onerror=/i)
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/)
})
