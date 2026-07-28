import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const timelinePath = new URL('../src/components/RunEventTimeline.vue', import.meta.url)
const viewerPath = new URL('../src/components/PayloadCodeViewer.vue', import.meta.url)
const dialogPath = new URL('../src/components/PayloadDetailDialog.vue', import.meta.url)
const jsonViewerPath = new URL('../src/components/JsonViewer.vue', import.meta.url)

test('long payloads keep a preview and open in a typed modal', async () => {
  const source = await readFile(timelinePath, 'utf8')

  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'input'\)"/)
  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'output'\)"/)
  assert.match(source, /@click\.stop="openPayload\(entry\.event, 'detail'\)"/)
  assert.match(source, /import PayloadDetailDialog from '\.\/PayloadDetailDialog\.vue'/)
  assert.match(source, /<PayloadDetailDialog/)
  assert.doesNotMatch(source, /<Dialog :open="payloadModal !== null"/)
})

test('payload detail dialog shares Markdown and syntax-highlighted rendering', async () => {
  const source = await readFile(dialogPath, 'utf8')

  assert.match(source, /import \{ renderMarkdown \} from '\.\.\/lib\/markdown'/)
  assert.match(source, /import PayloadCodeViewer from '\.\/PayloadCodeViewer\.vue'/)
  assert.match(source, /import JsonViewer from '\.\/JsonViewer\.vue'/)
  assert.match(source, /import \{ Dialog, DialogContent, DialogHeader, DialogTitle \} from '\.\/ui\/dialog'/)
  assert.match(source, /v-html="renderMarkdown\(content\)"/)
  assert.match(source, /v-else-if="language === 'json'"/)
  assert.match(source, /extractMcpStructuredPayload/)
  assert.match(source, /完整响应/)
  assert.match(source, />structured<\/Button>/)
  assert.match(source, /<JsonViewer/)
  assert.match(source, /<PayloadCodeViewer/)
  assert.match(source, /\.payload-markdown\{/)
  assert.doesNotMatch(await readFile(timelinePath, 'utf8'), /\.payload-markdown\{/)
})

test('workflow payload previews share the JSON presentation used by log details', async () => {
  const timeline = await readFile(timelinePath, 'utf8')
  const viewer = await readFile(jsonViewerPath, 'utf8')

  assert.match(timeline, /import JsonViewer from '\.\/JsonViewer\.vue'/)
  assert.match(timeline, /function payloadIsJson/)
  assert.match(timeline, /density="compact"/)
  assert.match(viewer, /json-viewer-compact/)
})

test('payload code viewer provides readonly syntax-highlighted editors in the shared JSON visual language', async () => {
  const source = await readFile(viewerPath, 'utf8')

  assert.match(source, /@codemirror\/lang-html/)
  assert.match(source, /@codemirror\/lang-javascript/)
  assert.match(source, /@codemirror\/lang-json/)
  assert.match(source, /@codemirror\/lang-python/)
  assert.match(source, /HighlightStyle\.define/)
  assert.match(source, /var\(--json-key\)/)
  assert.match(source, /var\(--json-string\)/)
  assert.match(source, /\.cm-gutters': \{ display: 'none' \}/)
  assert.match(source, /EditorState\.readOnly\.of\(true\)/)
  assert.match(source, /syntaxHighlighting\(payloadCodeHighlight\)/)
})
