import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const timelinePath = new URL('../src/components/RunEventTimeline.vue', import.meta.url)
const viewerPath = new URL('../src/components/PayloadCodeViewer.vue', import.meta.url)

test('long payloads keep a preview and open in a typed modal', async () => {
  const source = await readFile(timelinePath, 'utf8')

  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'input'\)"/)
  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'output'\)"/)
  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'detail'\)"/)
  assert.match(source, /w-\[min\(1280px,calc\(100vw-2rem\)\)\]/)
  assert.match(source, /查看\n/)
  assert.match(source, /<Dialog :open="payloadModal !== null"/)
  assert.match(source, /v-html="renderMd\(payloadModal\.content\)"/)
  assert.match(source, /<PayloadCodeViewer/)
})

test('payload code viewer provides readonly syntax-highlighted editors', async () => {
  const source = await readFile(viewerPath, 'utf8')

  assert.match(source, /@codemirror\/lang-html/)
  assert.match(source, /@codemirror\/lang-javascript/)
  assert.match(source, /@codemirror\/lang-json/)
  assert.match(source, /@codemirror\/lang-python/)
  assert.match(source, /EditorState\.readOnly\.of\(true\)/)
  assert.match(source, /syntaxHighlighting\(defaultHighlightStyle\)/)
})
