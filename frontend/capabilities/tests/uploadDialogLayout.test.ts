import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

test('long upload filenames stay within the dialog row and remain inspectable', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  assert.match(file, /<span class="min-w-0 flex-1 truncate" :title="f\.name">\{\{ f\.name \}\}<\/span>/)
})

test('upload dialog provides extra horizontal room for document names', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const uploadDialog = file.slice(file.indexOf('<!-- 上传文档对话框 -->'))
  assert.match(uploadDialog, /<DialogContent class="sm:max-w-\[640px\]">/)
})
