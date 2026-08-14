import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const uploadDialog = () => readFileSync(resolve(root, 'src/components/knowledge/KnowledgeUploadDialog.vue'), 'utf-8')
const uploadQueue = () => readFileSync(resolve(root, 'src/composables/useKnowledgeUploadQueue.ts'), 'utf-8')

test('long upload paths stay inside a constrained filename column and remain inspectable', () => {
  const dialog = uploadDialog()
  assert.match(dialog, /class="[^\"]*grid-cols-\[auto_minmax\(0,1fr\)_auto\][^\"]*"/)
  assert.match(dialog, /<span class="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" :title="file\.relativePath">\{\{ file\.relativePath \}\}<\/span>/)
})

test('upload dialog provides extra horizontal room for document names', () => {
  assert.match(uploadDialog(), /<DialogContent[^>]*class="sm:max-w-\[640px\]">/)
})

test('upload dialog accepts zip archives and surfaces upload outcomes', () => {
  assert.match(uploadQueue(), /const ALLOWED_DOC_EXTENSIONS = \[[^\]]*'\.zip'/)
  assert.match(uploadDialog(), /accept="[^"]*\.zip[^"]*"/)
  assert.match(uploadQueue(), /const uploadError = ref\(''\)/)
  assert.match(uploadDialog(), /v-if="error"/)
  assert.match(uploadQueue(), /skipped_count/)
})

test('upload API types include the zip summary response', () => {
  const types = readFileSync(resolve(root, 'src/api/types.ts'), 'utf-8')
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf-8')
  assert.match(types, /export interface DocumentUploadSummary[\s\S]*uploaded_count: number[\s\S]*skipped_count: number/)
  assert.match(client, /postFormDataWithProgress<DocumentDetail \| DocumentUploadSummary>\('\/docs'/)
})

test('document upload API exposes typed XHR progress, uses session cookie, and preserves complete server error details', () => {
  const types = readFileSync(resolve(root, 'src/api/types.ts'), 'utf-8')
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf-8')
  assert.match(types, /export type UploadProgressCallback = \(loaded: number, total: number\) => void/)
  assert.match(client, /function postFormDataWithProgress<T>/)
  assert.match(client, /new XMLHttpRequest\(\)/)
  assert.match(client, /xhr\.upload\.onprogress = event => onProgress\?\.\(event\.loaded, event\.total\)/)
  assert.doesNotMatch(client, /setRequestHeader\('X-Agent-Bridge-User'/)
  assert.match(client, /JSON\.parse\(xhr\.responseText\)/)
  assert.match(client, /detail/)
  assert.match(client, /xhr\.responseText/)
  assert.match(client, /上传失败，请稍后重试/)
  assert.match(client, /onProgress\?: UploadProgressCallback/)
  assert.match(client, /postFormDataWithProgress<DocumentDetail \| DocumentUploadSummary>\('\/docs'/)
})

test('upload dialog renders per-file progress, processing stages, errors, and locks closing while uploading', () => {
  const queue = uploadQueue()
  const dialog = uploadDialog()
  assert.match(queue, /status: 'pending'/)
  assert.match(queue, /progress: 0/)
  assert.match(queue, /stage: '等待上传'/)
  assert.match(queue, /error: ''/)
  assert.match(dialog, /:show-close-button="!uploading"/)
  assert.match(dialog, /file\.progress/)
  assert.match(dialog, /file\.stage/)
  assert.match(dialog, /file\.error/)
  assert.match(queue, /正在解析/)
  assert.match(queue, /正在解压/)
  assert.match(queue, /正在入库/)
  assert.match(queue, /排队同步/)
  assert.match(dialog, /:disabled="uploading"/)
})

test('upload dialog uses a shared queue index with at most three async workers', () => {
  const queue = uploadQueue()
  assert.match(queue, /nextUploadIndex/)
  assert.match(queue, /Math\.min\(3,/)
  assert.match(queue, /async function uploadWorker/)
  assert.match(queue, /Promise\.all\(/)
})

test('batch upload starts one sync pass only when the knowledge base enables immediate sync', () => {
  const uploadFunction = uploadQueue().slice(uploadQueue().indexOf('async function uploadDocuments'))
  assert.match(uploadFunction, /uploadedCount > 0 && kb\.sync_on_upload/)
  assert.match(uploadFunction, /await api\.triggerKbSync\(kb\.slug\)/)
  assert.match(uploadFunction, /已入库，等待定时同步/)
})

test('knowledge sync badges translate backend states into user-facing labels', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  assert.match(file, /function syncBadgeLabel\(/)
  assert.match(file, /not_synced:\s*'未同步'/)
  assert.match(file, /sync_failed:\s*'同步失败'/)
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

test('knowledge browse API contract is typed and builds mutually exclusive context queries', () => {
  const types = readFileSync(resolve(root, 'src/api/types.ts'), 'utf-8')
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf-8')
  assert.match(types, /export type KnowledgeBrowseEntry = KnowledgeBrowseFolderEntry \| KnowledgeBrowseZipEntry \| KnowledgeBrowseDocumentEntry/)
  assert.match(types, /kind: 'folder'/)
  assert.match(types, /kind: 'zip'/)
  assert.match(types, /kind: 'document'/)
  assert.match(types, /export interface KnowledgeBrowseContext/)
  assert.match(types, /export interface KnowledgeBrowseResponse[\s\S]*entries: KnowledgeBrowseEntry\[\]/)
  assert.match(client, /listBrowse: \(kbSlug: string, folderId\?: number, archiveEntryId\?: number\)/)
  assert.match(client, /qs\.set\('folder_id', String\(folderId\)\)/)
  assert.match(client, /qs\.set\('archive_entry_id', String\(archiveEntryId\)\)/)
  assert.match(client, /get<KnowledgeBrowseResponse>\(`\/kbs\/\$\{kbSlug\}\/browse/)
})

test('knowledge browse renders ZIP and virtual folders with distinct icons and document-only actions', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const documentsTab = file.slice(file.indexOf('<!-- Documents Tab -->'))
  assert.match(file, /import \{[^}]*Archive[^}]*\} from '@lucide\/vue'/)
  assert.match(documentsTab, /entry\.kind === 'zip'/)
  assert.match(documentsTab, /<Archive/)
  assert.match(documentsTab, /<Folder/)
  assert.match(documentsTab, /entry\.kind === 'document'/)
  assert.match(documentsTab, /openDocumentDetail\(/)
  assert.match(documentsTab, /openPlaceDialog\(/)
  assert.match(documentsTab, /openAttachDialog\(/)
  assert.match(documentsTab, /deleteDoc\(/)
})

test('knowledge browse panes keep folder and document lists at a fixed height with internal scrolling', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  const documentsTab = file.slice(file.indexOf('<!-- Documents Tab -->'))
  assert.match(documentsTab, /h-\[calc\(100vh-280px\)\]/)
  assert.match(documentsTab, /min-h-\[/)
  assert.match(documentsTab, /max-h-\[/)
  assert.match(documentsTab, /overflow-y-auto/)
})

test('knowledge detail uses one refresh path for browse data and fresh sync jobs', () => {
  const file = readFileSync(resolve(root, 'src/views/knowledge/KnowledgeView.vue'), 'utf-8')
  assert.match(file, /async function refreshKnowledgeDetail\(/)
  const refreshFunction = file.slice(file.indexOf('async function refreshKnowledgeDetail'))
  assert.match(refreshFunction, /refreshDetailKbSummary\(/)
  assert.match(refreshFunction, /refreshCurrentDocs\(/)
  assert.match(refreshFunction, /api\.getKbSyncStatus\(detailKb\.value\.slug\)/)
  assert.match(refreshFunction, /detailSyncJobs\.value = syncStatusResult\.value\.jobs/)
  assert.match(file, /await refreshKnowledgeDetail\(\)/)
})
