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
  assert.match(uploadDialog, /<DialogContent[^>]*class="sm:max-w-\[640px\]">/)
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
  assert.match(client, /postFormDataWithProgress<DocumentDetail \| DocumentUploadSummary>\('\/docs'/)
})

test('document upload API exposes typed XHR progress and preserves complete server error details', () => {
  const types = readFileSync(resolve(root, 'src/api/types.ts'), 'utf-8')
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf-8')
  assert.match(types, /export type UploadProgressCallback = \(loaded: number, total: number\) => void/)
  assert.match(client, /function postFormDataWithProgress<T>/)
  assert.match(client, /new XMLHttpRequest\(\)/)
  assert.match(client, /xhr\.upload\.onprogress = event => onProgress\?\.\(event\.loaded, event\.total\)/)
  assert.match(client, /setRequestHeader\('X-Agent-Bridge-User'/)
  assert.match(client, /JSON\.parse\(xhr\.responseText\)/)
  assert.match(client, /detail/)
  assert.match(client, /xhr\.responseText/)
  assert.match(client, /上传失败（HTTP \$\{xhr\.status\}）/)
  assert.match(client, /onProgress\?: UploadProgressCallback/)
  assert.match(client, /postFormDataWithProgress<DocumentDetail \| DocumentUploadSummary>\('\/docs'/)
})

test('upload dialog renders per-file progress, processing stages, errors, and locks closing while uploading', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const uploadDialog = file.slice(file.indexOf('<!-- 上传文档对话框 -->'))
  assert.match(file, /status: 'pending'/)
  assert.match(file, /progress: 0/)
  assert.match(file, /stage: '等待上传'/)
  assert.match(file, /error: ''/)
  assert.match(uploadDialog, /:show-close-button="!uploading"/)
  assert.match(uploadDialog, /f\.progress/)
  assert.match(uploadDialog, /f\.stage/)
  assert.match(uploadDialog, /f\.error/)
  assert.match(file, /正在解析/)
  assert.match(file, /正在解压/)
  assert.match(file, /正在入库/)
  assert.match(file, /排队同步/)
  assert.match(uploadDialog, /:disabled="uploading"/)
})

test('upload dialog uses a shared queue index with at most three async workers', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  assert.match(file, /nextUploadIndex/)
  assert.match(file, /Math\.min\(3,/)
  assert.match(file, /async function uploadWorker/)
  assert.match(file, /Promise\.all\(/)
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
