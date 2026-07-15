import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

test('long upload paths stay inside a constrained filename column and remain inspectable', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const uploadDialog = file.slice(file.indexOf('<!-- 上传文档对话框 -->'))
  assert.match(uploadDialog, /class="[^\"]*grid-cols-\[auto_minmax\(0,1fr\)_auto\][^\"]*"/)
  assert.match(uploadDialog, /<span class="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" :title="f\.relativePath">\{\{ f\.relativePath \}\}<\/span>/)
})

test('upload dialog provides extra horizontal room for document names', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const uploadDialog = file.slice(file.indexOf('<!-- 上传文档对话框 -->'))
  assert.match(uploadDialog, /<DialogContent class="sm:max-w-\[640px\]">/)
})

test('upload dialog accepts zip archives and surfaces upload outcomes', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  assert.match(file, /const ALLOWED_DOC_EXTENSIONS = \[[^\]]*'\.zip'/)
  assert.match(file, /accept="[^"]*\.zip[^"]*"/)
  assert.match(file, /const uploadError = ref\(''\)/)
  assert.match(file, /v-if="uploadError"/)
  assert.match(file, /skipped_count/)
})

test('upload API types include the zip summary response', () => {
  const types = readFileSync(resolve(root, 'src/api/types.ts'), 'utf-8')
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf-8')
  assert.match(types, /export interface DocumentUploadSummary[\s\S]*uploaded_count: number[\s\S]*skipped_count: number/)
  assert.match(client, /postFormData<DocumentDetail \| DocumentUploadSummary>\('\/docs'/)
})

test('knowledge folder pane exposes a bounded desktop resizer and keeps mobile layout responsive', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const documentsTab = file.slice(file.indexOf('<!-- Documents Tab -->'))
  assert.match(file, /const FOLDER_PANE_DEFAULT_WIDTH = 300/)
  assert.match(file, /const FOLDER_PANE_MIN_WIDTH = 240/)
  assert.match(file, /const FOLDER_PANE_MAX_WIDTH = 420/)
  assert.match(file, /function startFolderPaneResize/)
  assert.match(documentsTab, /data-testid="knowledge-folder-resizer"/)
  assert.match(documentsTab, /role="separator"/)
  assert.match(documentsTab, /:style="\{ '--folder-pane-width': folderPaneWidth \+ 'px' \}"/)
  assert.match(documentsTab, /lg:w-\[var\(--folder-pane-width\)\]/)
})
