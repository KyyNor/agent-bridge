import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const logsView = () => readFileSync(resolve(root, 'src/views/monitoring/LogsView.vue'), 'utf-8')
const markdownPreview = () => readFileSync(resolve(root, 'src/components/LogMarkdownPreview.vue'), 'utf-8')

test('LogsView renders source-aware tool labels while exposing raw names as native titles', () => {
  const source = logsView()
  assert.match(source, /import \{ toolCallDisplayName \} from '\.\.\/\.\.\/lib\/toolCallDisplay'/)
  assert.match(source, /:title="l\.tool_name \|\| ''"[^>]*>\{\{ toolCallDisplayName\(l\) \}\}/)
  assert.match(source, /:title="detailLog\.tool_name \|\| ''"[^>]*>\{\{ toolCallDisplayName\(detailLog\) \}\}/)
})

test('LogsView conditionally opens only an extracted Markdown preview', () => {
  const source = logsView()
  assert.match(source, /import \{ extractLogMarkdownPreview \} from '\.\.\/\.\.\/lib\/logMarkdownPreview'/)
  assert.match(source, /import LogMarkdownPreview from '\.\.\/\.\.\/components\/LogMarkdownPreview\.vue'/)
  assert.match(source, /const detailMarkdownPreview = computed\(\(\) => detailLog\.value \? extractLogMarkdownPreview\(detailLog\.value\) : null\)/)
  assert.match(source, /<Button v-if="detailMarkdownPreview"[^>]*>预览<\/Button>/)
  assert.match(source, /<LogMarkdownPreview[^>]*v-model:open="previewOpen"[^>]*:title="detailMarkdownPreview\.title"[^>]*:markdown="detailMarkdownPreview\.markdown"/)
})

test('LogMarkdownPreview uses the shared dialog primitives and markdown renderer', () => {
  const source = markdownPreview()
  assert.match(source, /import \{ renderMarkdown \} from '\.\.\/lib\/markdown'/)
  assert.match(source, /import \{ Dialog, DialogContent, DialogHeader, DialogTitle \} from '\.\/ui\/dialog'/)
  assert.match(source, /@update:open="\$emit\('update:open', \$event\)"/)
  assert.match(source, /v-html="renderMarkdown\(markdown\)"/)
})
