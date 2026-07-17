<script setup lang="ts">
import { Plus, RotateCw, Upload, File, Folder, Archive, Trash2, GitBranch, ArrowLeft } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary, Document, DocumentDetail, DocumentUploadSummary, KnowledgeFolder, SyncJob, SearchResultChunk, ProjectProfile, BackendInfo, BackendAgent, CodeRepository, KbRepoSource, KnowledgeBrowseDocumentEntry, KnowledgeBrowseEntry, KnowledgeBrowseResponse } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import StatusBadge from '../../components/StatusBadge.vue'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import PaginationBar from '../../components/PaginationBar.vue'
import FolderTree from '../../components/knowledge/FolderTree.vue'
import { confirm, alert } from '../../composables/useConfirm'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

const props = defineProps<{ routeKey: string }>()

const mode = computed<'list' | 'detail'>(() => (props.routeKey ? 'detail' : 'list'))
const pagedKbs = computed(() => paginate(kbs.value, page.value, pageSize.value))

const kbs = ref<KnowledgeBaseSummary[]>([])
const page = ref(1)
const pageSize = ref(10)
const loading = ref(true)
const showCreate = ref(false)
const createForm = ref({ slug: '', name: '', description: '' })
const createSaving = ref(false)
const createError = ref('')

// Detail (secondary page driven by hash route)
const detailKb = ref<KnowledgeBaseSummary | null>(null)
const detailTab = ref<'docs' | 'sync' | 'sources' | 'search'>('docs')
const detailDocs = ref<Document[]>([])
const detailBrowse = ref<KnowledgeBrowseResponse | null>(null)
const browseLoading = ref(false)
const browseError = ref('')
const detailFolders = ref<KnowledgeFolder[]>([])
const selectedFolderId = ref<number | null>(null)
const showingAllDocuments = ref(false)
const folderTreeLoading = ref(false)
const folderDialogOpen = ref(false)
const folderDialogMode = ref<'create' | 'rename' | 'move'>('create')
const folderDialogFolder = ref<KnowledgeFolder | null>(null)
const folderDialogParentId = ref<number | null>(null)
const folderDialogName = ref('')
const folderDialogTargetId = ref<number | null>(null)
const folderDialogError = ref('')
const folderDialogSaving = ref(false)
const FOLDER_PANE_DEFAULT_WIDTH = 300
const FOLDER_PANE_MIN_WIDTH = 240
const FOLDER_PANE_MAX_WIDTH = 420
const folderPaneWidth = ref(FOLDER_PANE_DEFAULT_WIDTH)
const isResizingFolderPane = ref(false)
let folderPaneResizeStartX = 0
let folderPaneResizeStartWidth = FOLDER_PANE_DEFAULT_WIDTH
let previousBodyCursor = ''
let previousBodyUserSelect = ''
const placementDialogOpen = ref(false)
const placementDialogMode = ref<'place' | 'attach'>('place')
const placementDoc = ref<Document | null>(null)
const placementFolders = ref<KnowledgeFolder[]>([])
const placementKbSlug = ref('')
const placementTargetFolderId = ref<number | null>(null)
const placementError = ref('')
const placementSaving = ref(false)
const detailSyncJobs = ref<SyncJob[]>([])
const detailRepoSources = ref<KbRepoSource[]>([])
const detailLoading = ref(false)
const routeError = ref('')
// Batch document delete
const selectedDocSlugs = ref<Set<string>>(new Set())
const allDocsSelected = computed(() => detailDocs.value.length > 0 && detailDocs.value.every(d => selectedDocSlugs.value.has(d.slug)))
const someDocsSelected = computed(() => detailDocs.value.some(d => selectedDocSlugs.value.has(d.slug)))
const detailTotalDocumentCount = computed(() => {
  const slug = detailKb.value?.slug
  return kbs.value.find(kb => kb.slug === slug)?.document_count ?? detailKb.value?.document_count ?? 0
})
const rootFolder = computed(() => detailFolders.value.find(folder => folder.is_root) || detailFolders.value[0] || null)
const currentFolder = computed(() => detailFolders.value.find(folder => folder.id === selectedFolderId.value) || null)
const browseContext = computed(() => detailBrowse.value?.context || null)
const browseParent = computed(() => detailBrowse.value?.parent || null)
const browseEntries = computed(() => detailBrowse.value?.entries || [])
const currentFolderBreadcrumbs = computed(() => {
  if (showingAllDocuments.value) return ['全部文档']
  if (browseContext.value) {
    const path = browseContext.value.relative_path.split('/').filter(Boolean)
    return [detailKb.value?.name || '根目录', ...path]
  }
  if (!currentFolder.value || !currentFolder.value.path) return [detailKb.value?.name || '根目录']
  return [detailKb.value?.name || '根目录', ...currentFolder.value.path.split('/').filter(Boolean)]
})
const browseContextLabel = computed(() => {
  if (showingAllDocuments.value) return '全部文档'
  if (browseContext.value?.kind === 'zip') return 'ZIP 压缩包'
  if (browseContext.value?.archive_entry_id != null) return 'ZIP 内部目录'
  return '真实目录'
})
const activeBackendType = computed(() => {
  const slug = detailKb.value?.default_backend_slug
  return backends.value.find(backend => backend.slug === slug)?.backend_type
    || detailKb.value?.backend_targets.find(target => target.status === 'active')?.backend_type
    || ''
})
const folderCapabilityLabel = computed(() => activeBackendType.value === 'weknora' ? '目录同步' : '本地分目录，后端平铺')
const folderDialogTitle = computed(() => {
  if (folderDialogMode.value === 'create') return '新建目录'
  if (folderDialogMode.value === 'rename') return `重命名目录「${folderDialogFolder.value?.name || ''}」`
  return `移动目录「${folderDialogFolder.value?.name || ''}」`
})
const folderDialogTargets = computed(() => {
  const folder = folderDialogFolder.value
  if (!folder || folderDialogMode.value !== 'move') return detailFolders.value
  const prefix = folder.path ? `${folder.path}/` : ''
  return detailFolders.value.filter(candidate => candidate.id !== folder.id && !candidate.path.startsWith(prefix))
})
const placementFoldersForDialog = computed(() => placementDialogMode.value === 'place' ? detailFolders.value : placementFolders.value)
const placementRootLabel = computed(() => {
  if (placementDialogMode.value === 'place') return detailKb.value?.name || '根目录'
  return kbs.value.find(kb => kb.slug === placementKbSlug.value)?.name || '根目录'
})

function clampFolderPaneWidth(width: number) {
  return Math.min(FOLDER_PANE_MAX_WIDTH, Math.max(FOLDER_PANE_MIN_WIDTH, width))
}

function setFolderPaneWidth(width: number) {
  folderPaneWidth.value = clampFolderPaneWidth(width)
}

function handleFolderPanePointerMove(event: PointerEvent) {
  if (!isResizingFolderPane.value) return
  setFolderPaneWidth(folderPaneResizeStartWidth + event.clientX - folderPaneResizeStartX)
}

function stopFolderPaneResize() {
  if (!isResizingFolderPane.value) return
  isResizingFolderPane.value = false
  window.removeEventListener('pointermove', handleFolderPanePointerMove)
  window.removeEventListener('pointerup', stopFolderPaneResize)
  window.removeEventListener('pointercancel', stopFolderPaneResize)
  document.body.style.cursor = previousBodyCursor
  document.body.style.userSelect = previousBodyUserSelect
}

function startFolderPaneResize(event: PointerEvent) {
  if (event.button !== 0 || isResizingFolderPane.value) return
  event.preventDefault()
  isResizingFolderPane.value = true
  folderPaneResizeStartX = event.clientX
  folderPaneResizeStartWidth = folderPaneWidth.value
  previousBodyCursor = document.body.style.cursor
  previousBodyUserSelect = document.body.style.userSelect
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', handleFolderPanePointerMove)
  window.addEventListener('pointerup', stopFolderPaneResize)
  window.addEventListener('pointercancel', stopFolderPaneResize)
}

function handleFolderPaneKeydown(event: KeyboardEvent) {
  const step = event.shiftKey ? 40 : 10
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    setFolderPaneWidth(folderPaneWidth.value - step)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    setFolderPaneWidth(folderPaneWidth.value + step)
  }
}

onBeforeUnmount(stopFolderPaneResize)

// Sync
const syncing = ref(false)
// Git repo sources
const codeRepos = ref<CodeRepository[]>([])
const repoSourceForm = ref({ repo_key: '', include_suffixes: '.md, .txt' })
const repoSourceSaving = ref(false)
const repoSourceSyncing = ref<Record<string, boolean>>({})
const repoSourceDeleting = ref<Record<string, boolean>>({})
const repoSourceError = ref('')
const repoSourceMessage = ref('')
// Search/Q&A
const searchQuery = ref('')
const searchResults = ref<SearchResultChunk[]>([])
const searchSearching = ref(false)
const askQuestion = ref('')
const askAnswer = ref('')
const askChunks = ref<SearchResultChunk[]>([])
const askSessionId = ref<string | null>(null)
const asking = ref(false)
// Document detail
const showDocDetail = ref(false)
const docDetailSlug = ref('')
const docDetailLoading = ref(false)
const docDetail = ref<DocumentDetail | null>(null)
const docDetailError = ref('')

// Plane assignment dialog
const showPlaneDialog = ref(false)
const planeKb = ref<KnowledgeBaseSummary | null>(null)

// 上传对话框
const showUploadDialog = ref(false)
const uploadKb = ref<KnowledgeBaseSummary | null>(null)
type UploadItemStatus = 'pending' | 'uploading' | 'processing' | 'success' | 'error'
interface UploadItem {
  file: File
  relativePath: string
  status: UploadItemStatus
  progress: number
  stage: string
  error: string
}
const uploadFiles = ref<UploadItem[]>([])
const uploadFolderId = ref<number | null>(null)
const uploading = ref(false)
const uploadDragOver = ref(false)
const uploadError = ref('')
const failedUploadCount = computed(() => uploadFiles.value.filter(item => item.status === 'error').length)
const retryableUploadCount = computed(() => uploadFiles.value.filter(item => item.status !== 'success').length)
let nextUploadIndex = 0
const ALLOWED_DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.zip']
const allProfiles = ref<ProjectProfile[]>([])
const selectedProfileKeys = ref<string[]>([])
const pendingProfileKeys = ref<string[]>([])
const planeSaving = ref(false)
// Per-profile retrieval backend overrides: profileKey -> backendSlug (empty string = auto)
const profileBackendOverrides = ref<Record<string, string>>({})

// Backends
const backends = ref<BackendInfo[]>([])

// Default backend editing in detail dialog
const editingDefaultBackend = ref(false)
const defaultBackendSlug = ref<string>('')
const savingDefaultBackend = ref(false)
// Default agent editing (KB detail) — only meaningful for weknora backends
const defaultAgentId = ref<string>('')
const detailAgents = ref<BackendAgent[]>([])
const detailAgentsLoading = ref(false)
const detailAgentLabel = computed(() => {
  const id = detailKb.value?.default_agent_id
  if (!id) return ''
  const found = detailAgents.value.find(a => a.agent_id === id)
  return found ? `${found.name}${found.agent_type ? ' · ' + found.agent_type : ''}` : id
})
// Per-profile agent overrides (capability plane): profileKey -> agentId
const profileAgentOverrides = ref<Record<string, string>>({})
// Agent list cache by backend slug (populated lazily for weknora backends)
const agentsByBackend = ref<Record<string, BackendAgent[]>>({})

onMounted(async () => {
  await Promise.all([loadKbs(), loadBackends()])
  loading.value = false
  await loadDetail()
})

// Route-driven detail loading: entering #knowledge/<slug> loads that kb's data.
watch(() => props.routeKey, () => { void loadDetail() })

watch(() => detailTab.value, () => {
  if (detailTab.value !== 'docs') selectedDocSlugs.value = new Set()
})

async function loadKbs() {
  try { kbs.value = await api.listWikiKbs() } catch { kbs.value = [] }
}

async function refreshDetailKbSummary() {
  await loadKbs()
  if (!detailKb.value) return
  const latest = kbs.value.find(kb => kb.slug === detailKb.value?.slug)
  if (latest) detailKb.value = latest
}

async function deleteKb(kb: KnowledgeBaseSummary) {
  const ok = await confirm({
    title: '删除文档知识库',
    description: `确定删除文档知识库「${kb.name}」？若其下仍有文档将被拒绝，请先清空文档。`,
    destructive: true,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await api.deleteKnowledgeBase(kb.slug)
    await loadKbs()
  } catch (e: any) {
    await alert({ title: '删除失败', description: e.message || '删除失败', destructive: true })
  }
}

async function loadBackends() {
  try { backends.value = await api.listBackends() } catch { backends.value = [] }
}

function isWeknoraBackend(slug: string | null | undefined): boolean {
  if (!slug) return false
  const b = backends.value.find(x => x.slug === slug)
  return !!b && b.backend_type === 'weknora'
}

async function loadAgentsForBackend(slug: string): Promise<BackendAgent[]> {
  if (!slug || !isWeknoraBackend(slug)) return []
  if (slug in agentsByBackend.value) return agentsByBackend.value[slug]
  try {
    const list = await api.listBackendAgents(slug)
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: list }
    return list
  } catch {
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: [] }
    return []
  }
}

async function createKb() {
  createError.value = ''
  if (!createForm.value.slug || !createForm.value.name) {
    createError.value = '请填写标识和名称'
    return
  }
  createSaving.value = true
  try {
    const slug = createForm.value.slug
    await api.createKb({
      slug,
      name: createForm.value.name,
      description: createForm.value.description || undefined,
    })
    showCreate.value = false
    createForm.value = { slug: '', name: '', description: '' }
    await loadKbs()
    const newKb = kbs.value.find(k => k.slug === slug)
    if (newKb) openDetail(newKb)
  } catch (e: any) {
    createError.value = e.message || '创建失败'
  }
  createSaving.value = false
}

function goList() {
  window.location.hash = 'knowledge'
}

async function openDetail(kb: KnowledgeBaseSummary) {
  window.location.hash = 'knowledge/' + kb.slug
}

async function loadFolderTree(preferredFolderId?: number | null) {
  if (!detailKb.value) return
  folderTreeLoading.value = true
  const previousSelectedId = selectedFolderId.value
  try {
    detailFolders.value = await api.listFolders(detailKb.value.slug)
    const preferred = preferredFolderId != null
      ? detailFolders.value.find(folder => folder.id === preferredFolderId)
      : previousSelectedId != null
        ? detailFolders.value.find(folder => folder.id === previousSelectedId)
        : null
    const fallback = preferred || rootFolder.value
    if (!showingAllDocuments.value) selectedFolderId.value = fallback?.id ?? null
  } catch (e: unknown) {
    detailFolders.value = []
    if (!showingAllDocuments.value) selectedFolderId.value = null
    throw e
  } finally {
    folderTreeLoading.value = false
  }
}

function documentFromBrowseEntry(entry: KnowledgeBrowseDocumentEntry): Document {
  return {
    id: entry.doc_id,
    slug: entry.slug,
    title: entry.title,
    owner_user: '',
    status: entry.status,
    current_version_no: entry.version_no,
    sync_status: entry.sync_status,
    folder_id: browseContext.value?.archive_entry_id == null ? selectedFolderId.value : null,
    folder_path: browseContext.value?.archive_entry_id == null ? currentFolder.value?.path || null : null,
  }
}

function browseContextQuery() {
  const context = detailBrowse.value?.context
  if (context?.archive_entry_id != null) return { archiveEntryId: context.archive_entry_id }
  return { folderId: context?.kind === 'folder' ? context.id : selectedFolderId.value ?? undefined }
}

async function loadBrowse(folderId?: number, archiveEntryId?: number) {
  if (!detailKb.value) return
  browseLoading.value = true
  browseError.value = ''
  showingAllDocuments.value = false
  try {
    const response = await api.listBrowse(detailKb.value.slug, folderId, archiveEntryId)
    detailBrowse.value = response
    detailDocs.value = response.entries
      .filter((entry): entry is KnowledgeBrowseDocumentEntry => entry.kind === 'document')
      .map(documentFromBrowseEntry)
    selectedDocSlugs.value = new Set()
    if (folderId != null) selectedFolderId.value = folderId
  } catch (e: any) {
    detailBrowse.value = null
    detailDocs.value = []
    browseError.value = e.message || '浏览目录失败'
    throw e
  } finally {
    browseLoading.value = false
  }
}

async function refreshCurrentDocs() {
  if (!detailKb.value) return
  if (showingAllDocuments.value) {
    detailBrowse.value = null
    detailDocs.value = await api.listDocs(detailKb.value.slug)
  } else {
    const query = browseContextQuery()
    const response = await api.listBrowse(detailKb.value.slug, query.folderId, query.archiveEntryId)
    detailBrowse.value = response
    detailDocs.value = response.entries
      .filter((entry): entry is KnowledgeBrowseDocumentEntry => entry.kind === 'document')
      .map(documentFromBrowseEntry)
    browseError.value = ''
  }
  selectedDocSlugs.value = new Set()
}

async function refreshKnowledgeDetail(preferredFolderId?: number | null) {
  if (!detailKb.value) return
  const [summaryResult, foldersResult, syncStatusResult] = await Promise.allSettled([
    refreshDetailKbSummary(),
    loadFolderTree(preferredFolderId),
    api.getSyncStatus(),
  ])
  if (syncStatusResult.status === 'fulfilled' && detailKb.value) {
    detailSyncJobs.value = syncStatusResult.value.jobs.filter((job: SyncJob) => job.kb_slug === detailKb.value!.slug)
  }
  try {
    await refreshCurrentDocs()
  } catch (e: any) {
    detailDocs.value = []
    browseError.value = e.message || '浏览目录失败'
  }
  if (foldersResult.status === 'rejected') {
    detailBrowse.value = null
    detailDocs.value = []
    routeError.value = foldersResult.reason?.message || '目录加载失败'
  } else if (summaryResult.status === 'fulfilled') {
    routeError.value = ''
  }
}

async function selectFolder(folderId: number | null) {
  if (!detailKb.value) return
  showingAllDocuments.value = folderId === null
  if (folderId !== null) selectedFolderId.value = folderId
  detailBrowse.value = null
  browseError.value = ''
  try {
    await refreshCurrentDocs()
  } catch (e: any) {
    await alert({ title: '加载文档失败', description: e.message || '加载文档失败', destructive: true })
  }
}

async function enterBrowseEntry(entry: KnowledgeBrowseEntry) {
  if (entry.kind === 'document') return
  const isVirtualFolder = entry.kind === 'folder' && (
    entry.archive_entry_id != null || browseContext.value?.archive_entry_id != null
  )
  if (entry.kind === 'folder' && !isVirtualFolder) {
    await selectFolder(entry.id)
    return
  }
  await loadBrowse(undefined, entry.archive_entry_id ?? entry.id)
}

async function goBrowseParent() {
  const parent = browseParent.value
  if (!parent) return
  if (parent.archive_entry_id != null) {
    await loadBrowse(undefined, parent.archive_entry_id)
  } else {
    await loadBrowse(parent.id)
  }
}

function openCreateFolder(parentFolderId: number) {
  folderDialogMode.value = 'create'
  folderDialogFolder.value = null
  folderDialogParentId.value = parentFolderId
  folderDialogName.value = ''
  folderDialogTargetId.value = parentFolderId
  folderDialogError.value = ''
  folderDialogOpen.value = true
}

function openRenameFolder(folder: KnowledgeFolder) {
  if (folder.is_root) return
  folderDialogMode.value = 'rename'
  folderDialogFolder.value = folder
  folderDialogParentId.value = folder.parent_id
  folderDialogName.value = folder.name
  folderDialogTargetId.value = folder.parent_id ?? rootFolder.value?.id ?? null
  folderDialogError.value = ''
  folderDialogOpen.value = true
}

function openMoveFolder(folder: KnowledgeFolder) {
  if (folder.is_root) return
  folderDialogMode.value = 'move'
  folderDialogFolder.value = folder
  folderDialogParentId.value = folder.parent_id
  folderDialogName.value = ''
  folderDialogTargetId.value = folder.parent_id ?? rootFolder.value?.id ?? null
  folderDialogError.value = ''
  folderDialogOpen.value = true
}

async function submitFolderDialog() {
  if (!detailKb.value) return
  folderDialogError.value = ''
  const name = folderDialogName.value.trim()
  if ((folderDialogMode.value === 'create' || folderDialogMode.value === 'rename') && !name) {
    folderDialogError.value = '请输入目录名称'
    return
  }
  if (folderDialogMode.value === 'move' && folderDialogTargetId.value == null) {
    folderDialogError.value = '请选择目标目录'
    return
  }
  folderDialogSaving.value = true
  try {
    if (folderDialogMode.value === 'create') {
      await api.createFolder(detailKb.value.slug, folderDialogParentId.value, name)
    } else if (folderDialogFolder.value) {
      await api.updateFolder(
        detailKb.value.slug,
        folderDialogFolder.value.id,
        folderDialogMode.value === 'rename' ? { name } : { parent_folder_id: folderDialogTargetId.value },
      )
    }
    folderDialogOpen.value = false
    await refreshKnowledgeDetail(selectedFolderId.value)
  } catch (e: any) {
    folderDialogError.value = e.message || '保存目录失败'
  } finally {
    folderDialogSaving.value = false
  }
}

function isInFolderSubtree(folder: KnowledgeFolder, candidate: KnowledgeFolder | null) {
  if (!candidate) return false
  return candidate.id === folder.id || (!!folder.path && candidate.path.startsWith(`${folder.path}/`))
}

async function removeFolder(folder: KnowledgeFolder) {
  if (!detailKb.value || folder.is_root) return
  const parentId = folder.parent_id ?? rootFolder.value?.id ?? null
  try {
    const preview = await api.deleteFolder(detailKb.value.slug, folder.id, false)
    const ok = await confirm({
      title: '删除目录',
      description: `你删除的目录下有 ${preview.directory_count ?? preview.folder_count ?? 0} 个目录、${preview.file_count ?? preview.descendant_file_count ?? 0} 个文件，确认删除后都会删除且不能恢复。`,
      destructive: true,
      confirmText: '确认删除',
    })
    if (!ok) return
    const result = await api.deleteFolder(detailKb.value.slug, folder.id, true)
    const removedCurrent = !showingAllDocuments.value && isInFolderSubtree(folder, currentFolder.value)
    if (removedCurrent) {
      showingAllDocuments.value = false
      selectedFolderId.value = parentId
    }
    await refreshKnowledgeDetail(removedCurrent ? parentId : selectedFolderId.value)
    await alert({
      title: '目录已删除',
      description: `已删除 ${result.directory_count ?? result.folder_count ?? 0} 个目录、${result.file_count ?? result.descendant_file_count ?? 0} 个文件。`,
    })
  } catch (e: any) {
    await alert({ title: '删除目录失败', description: e.message || '删除目录失败', destructive: true })
  }
}

async function openPlaceDialog(document: Document) {
  if (!detailKb.value) return
  placementDialogMode.value = 'place'
  placementDoc.value = document
  placementKbSlug.value = detailKb.value.slug
  placementFolders.value = detailFolders.value
  placementTargetFolderId.value = document.folder_id ?? rootFolder.value?.id ?? null
  placementError.value = ''
  placementDialogOpen.value = true
}

const otherKnowledgeBases = computed(() => kbs.value.filter(kb => kb.slug !== detailKb.value?.slug))

async function openAttachDialog(document: Document) {
  if (!detailKb.value || otherKnowledgeBases.value.length === 0) return
  placementDialogMode.value = 'attach'
  placementDoc.value = document
  placementKbSlug.value = otherKnowledgeBases.value[0].slug
  placementFolders.value = []
  placementTargetFolderId.value = null
  placementError.value = ''
  placementDialogOpen.value = true
  await loadPlacementFolders(placementKbSlug.value)
}

async function loadPlacementFolders(kbSlug: string) {
  placementFolders.value = []
  if (!kbSlug) return
  try {
    placementFolders.value = await api.listFolders(kbSlug)
    placementTargetFolderId.value = placementFolders.value.find(folder => folder.is_root)?.id || placementFolders.value[0]?.id || null
  } catch (e: any) {
    placementError.value = e.message || '加载目标目录失败'
  }
}

async function changePlacementKb(kbSlug: string) {
  placementKbSlug.value = kbSlug
  await loadPlacementFolders(kbSlug)
}

function selectPlacementFolder(folderId: number | null) {
  if (folderId != null) placementTargetFolderId.value = folderId
}

function selectFolderDialogTarget(folderId: number | null) {
  if (folderId != null) folderDialogTargetId.value = folderId
}

async function submitPlacement() {
  if (!placementDoc.value || !placementKbSlug.value || placementTargetFolderId.value == null) {
    placementError.value = '请选择目标目录'
    return
  }
  placementSaving.value = true
  placementError.value = ''
  try {
    if (placementDialogMode.value === 'place' && detailKb.value) {
      await api.placeDocument(placementDoc.value.slug, detailKb.value.slug, placementTargetFolderId.value)
    } else {
      await api.attachDocument(placementDoc.value.slug, placementKbSlug.value, placementTargetFolderId.value)
    }
    placementDialogOpen.value = false
    await refreshKnowledgeDetail(selectedFolderId.value)
  } catch (e: any) {
    placementError.value = e.message || '保存文档目录失败'
  } finally {
    placementSaving.value = false
  }
}

// Load detail data for the kb referenced by the current route (props.routeKey).
async function loadDetail() {
  if (!props.routeKey) {
    detailKb.value = null
    routeError.value = ''
    detailFolders.value = []
    detailBrowse.value = null
    selectedFolderId.value = null
    showingAllDocuments.value = false
    selectedDocSlugs.value = new Set()
    return
  }
  const kb = kbs.value.find(k => k.slug === props.routeKey) || null
  if (!kb) {
    detailKb.value = null
    routeError.value = '无法加载该知识库（可能已被删除或不存在）'
    detailFolders.value = []
    detailBrowse.value = null
    selectedFolderId.value = null
    showingAllDocuments.value = false
    selectedDocSlugs.value = new Set()
    return
  }
  detailKb.value = kb
  routeError.value = ''
  detailTab.value = 'docs'
  editingDefaultBackend.value = false
  defaultBackendSlug.value = kb.default_backend_slug || ''
  defaultAgentId.value = kb.default_agent_id || ''
  detailAgents.value = []
  detailLoading.value = true
  searchResults.value = []
  askAnswer.value = ''
  askChunks.value = []
  detailRepoSources.value = []
  repoSourceError.value = ''
  repoSourceMessage.value = ''
  detailFolders.value = []
  detailBrowse.value = null
  browseError.value = ''
  selectedFolderId.value = null
  showingAllDocuments.value = false
  selectedDocSlugs.value = new Set()
  try {
    const [repoSources, repos] = await Promise.allSettled([
      api.listKbRepoSources(kb.slug),
      api.listCodeRepos(),
    ])
    detailRepoSources.value = repoSources.status === 'fulfilled' ? repoSources.value : []
    codeRepos.value = repos.status === 'fulfilled' ? repos.value : []
    resetRepoSourceForm()
    await refreshKnowledgeDetail()
    if (isWeknoraBackend(kb.default_backend_slug)) {
      detailAgents.value = await loadAgentsForBackend(kb.default_backend_slug as string)
    }
  } catch { /* ignore */ }
  finally { detailLoading.value = false }
}

function toggleDocSelected(slug: string) {
  const next = new Set(selectedDocSlugs.value)
  if (next.has(slug)) next.delete(slug)
  else next.add(slug)
  selectedDocSlugs.value = next
}

function toggleAllDocs() {
  if (allDocsSelected.value) {
    selectedDocSlugs.value = new Set()
  } else {
    selectedDocSlugs.value = new Set(detailDocs.value.map(d => d.slug))
  }
}

async function openDocumentDetail(slug: string) {
  showDocDetail.value = true
  docDetailSlug.value = slug
  docDetail.value = null
  docDetailError.value = ''
  docDetailLoading.value = true
  try {
    docDetail.value = await api.getDoc(slug)
  } catch (e: any) {
    docDetailError.value = e.message || '加载文档详情失败'
  } finally {
    docDetailLoading.value = false
  }
}

async function deleteDoc(slug: string, docTitle: string) {
  if (!detailKb.value) return
  const ok = await confirm({
    title: '删除文档',
    description: `确定删除文档「${docTitle}」？删除后将从当前知识库中移除，并等待后端同步删除。`,
    destructive: true,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await api.deleteDocumentFromKb(detailKb.value.slug, slug)
    await refreshKnowledgeDetail()
  } catch (e: any) {
    await alert({ title: '删除失败', description: e.message || '删除失败', destructive: true })
  }
}

async function batchDeleteDocs() {
  if (!detailKb.value) return
  const slugs = [...selectedDocSlugs.value]
  if (slugs.length === 0) return
  const ok = await confirm({
    title: '批量删除文档',
    description: `确定删除选中的 ${slugs.length} 个文档？删除后将从当前知识库中移除，并等待后端同步删除。`,
    destructive: true,
    confirmText: '删除',
  })
  if (!ok) return
  const results = await Promise.allSettled(slugs.map(slug => api.deleteDocumentFromKb(detailKb.value!.slug, slug)))
  const failed = results.filter(r => r.status === 'rejected').length
  const succeeded = results.length - failed
  await refreshKnowledgeDetail()
  selectedDocSlugs.value = new Set()
  if (failed > 0) {
    await alert({
      title: '部分删除失败',
      description: `成功删除 ${succeeded} 个，失败 ${failed} 个。`,
      destructive: true,
    })
  } else {
    await alert({ title: '删除完成', description: `已删除 ${succeeded} 个文档。` })
  }
}

async function triggerSync() {
  syncing.value = true
  try {
    await api.triggerSync()
    await refreshKnowledgeDetail()
  } catch { /* ignore */ }
  syncing.value = false
}

function resetRepoSourceForm() {
  if (codeRepos.value.length === 0) {
    repoSourceForm.value = { repo_key: '', include_suffixes: '.md, .txt' }
    return
  }
  const repo = codeRepos.value.find(r => !detailRepoSources.value.some(source => source.repo_key === r.repo_key)) || codeRepos.value[0]
  repoSourceForm.value.repo_key = repo.repo_key
  onRepoSourceSelect()
}

function normalizeSuffixInput(value: string): string[] {
  const suffixes: string[] = []
  for (const raw of value.split(/[\s,，]+/)) {
    const trimmed = raw.trim().toLowerCase()
    if (!trimmed) continue
    const suffix = trimmed.startsWith('.') ? trimmed : `.${trimmed}`
    if (!suffixes.includes(suffix)) suffixes.push(suffix)
  }
  return suffixes
}

function onRepoSourceSelect() {
  const existing = detailRepoSources.value.find(source => source.repo_key === repoSourceForm.value.repo_key)
  repoSourceForm.value.include_suffixes = existing?.include_suffixes.join(', ') || '.md, .txt'
  repoSourceError.value = ''
  repoSourceMessage.value = ''
}

async function saveRepoSource() {
  if (!detailKb.value) return
  repoSourceError.value = ''
  repoSourceMessage.value = ''
  const include_suffixes = normalizeSuffixInput(repoSourceForm.value.include_suffixes)
  if (!repoSourceForm.value.repo_key) {
    repoSourceError.value = '请选择代码仓库'
    return
  }
  if (include_suffixes.length === 0) {
    repoSourceError.value = '请至少填写一个文件后缀'
    return
  }
  repoSourceSaving.value = true
  try {
    await api.saveKbRepoSource(detailKb.value.slug, { repo_key: repoSourceForm.value.repo_key, include_suffixes })
    detailRepoSources.value = await api.listKbRepoSources(detailKb.value.slug)
    repoSourceForm.value.include_suffixes = include_suffixes.join(', ')
    repoSourceMessage.value = '已保存 Git 数据源'
  } catch (e: any) {
    repoSourceError.value = e.message || '保存失败'
  }
  repoSourceSaving.value = false
}

async function syncRepoSource(source: KbRepoSource) {
  if (!detailKb.value) return
  repoSourceError.value = ''
  repoSourceMessage.value = ''
  repoSourceSyncing.value = { ...repoSourceSyncing.value, [source.repo_key]: true }
  try {
    const result = await api.syncKbRepoSource(detailKb.value.slug, source.repo_key)
    detailRepoSources.value = await api.listKbRepoSources(detailKb.value.slug)
    await refreshKnowledgeDetail()
    repoSourceMessage.value = `已同步：新增 ${result.added}，删除 ${result.removed}，更新 ${result.updated}`
  } catch (e: any) {
    repoSourceError.value = e.message || '同步失败'
  }
  repoSourceSyncing.value = { ...repoSourceSyncing.value, [source.repo_key]: false }
}

async function deleteRepoSource(source: KbRepoSource) {
  if (!detailKb.value) return
  const ok = await confirm({
    title: '移除数据源',
    description: `确定移除数据源「${source.repo_name || source.repo_key}」？将从该知识库删除 ${source.doc_count} 个由它提供的文档，并在后端同步删除。此操作不会删除 git 仓库本身。`,
    destructive: true,
    confirmText: '移除',
  })
  if (!ok) return
  repoSourceError.value = ''
  repoSourceMessage.value = ''
  repoSourceDeleting.value = { ...repoSourceDeleting.value, [source.repo_key]: true }
  try {
    await api.deleteKbRepoSource(detailKb.value.slug, source.repo_key)
    detailRepoSources.value = await api.listKbRepoSources(detailKb.value.slug)
    await refreshKnowledgeDetail()
    repoSourceMessage.value = '已移除数据源'
  } catch (e: any) {
    repoSourceError.value = e.message || '删除失败'
  }
  repoSourceDeleting.value = { ...repoSourceDeleting.value, [source.repo_key]: false }
}

async function doSearch() {
  if (!detailKb.value || !searchQuery.value.trim()) return
  searchSearching.value = true
  try {
    const result = await api.search(detailKb.value.slug, searchQuery.value.trim())
    searchResults.value = result.results
  } catch { searchResults.value = [] }
  searchSearching.value = false
}

async function doAsk() {
  if (!detailKb.value || !askQuestion.value.trim()) return
  asking.value = true
  try {
    const result = await api.ask({
      kb: detailKb.value.slug,
      question: askQuestion.value.trim(),
      session_id: askSessionId.value || undefined,
    })
    askAnswer.value = result.answer
    askChunks.value = result.chunks
    askSessionId.value = result.session_id
  } catch { askAnswer.value = '问答失败'; askChunks.value = [] }
  asking.value = false
}

async function onDetailBackendChange() {
  // Backend switched in edit mode: reload its agents, drop an agent that no longer applies.
  detailAgents.value = []
  defaultAgentId.value = ''
  const slug = defaultBackendSlug.value
  if (slug && isWeknoraBackend(slug)) {
    detailAgentsLoading.value = true
    detailAgents.value = await loadAgentsForBackend(slug)
    detailAgentsLoading.value = false
  }
}

async function saveDefaultBackend() {
  if (!detailKb.value) return
  savingDefaultBackend.value = true
  try {
    const slug = defaultBackendSlug.value || null
    // agent_id only applies when the chosen backend is weknora
    const agent = isWeknoraBackend(defaultBackendSlug.value) ? (defaultAgentId.value || null) : null
    await api.updateKbDefaults(detailKb.value.slug, { default_backend_slug: slug, default_agent_id: agent })
    detailKb.value.default_backend_slug = slug
    detailKb.value.default_agent_id = agent
    editingDefaultBackend.value = false
    await loadKbs()
  } catch { /* ignore */ }
  savingDefaultBackend.value = false
}

function normalizeRelativePath(path: string) {
  return path.replace(/\\/g, '/').replace(/^\/+/, '') || path
}

function addUploadItem(file: File, relativePath?: string) {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!ALLOWED_DOC_EXTENSIONS.includes(ext)) return
  uploadFiles.value.push({
    file,
    relativePath: normalizeRelativePath(relativePath || file.name),
    status: 'pending',
    progress: 0,
    stage: '等待上传',
    error: '',
  })
}

function onUploadFilesSelected(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    for (let i = 0; i < target.files.length; i++) {
      const f = target.files[i]
      addUploadItem(f, f.webkitRelativePath || f.name)
    }
  }
  target.value = ''
}

function handleUploadDragOver(e: DragEvent) {
  e.preventDefault()
  uploadDragOver.value = true
}

function handleUploadDragLeave(e: DragEvent) {
  const el = e.currentTarget as HTMLElement | null
  if (e.relatedTarget && el?.contains(e.relatedTarget as Node)) return
  uploadDragOver.value = false
}

function handleUploadDrop(e: DragEvent) {
  e.preventDefault()
  uploadDragOver.value = false
  if (!e.dataTransfer) return
  addFilesFromDataTransfer(e.dataTransfer)
}

function addFilesFromDataTransfer(dt: DataTransfer) {
  const allowed = ALLOWED_DOC_EXTENSIONS
  const entries: FileSystemEntry[] = []
  for (let i = 0; i < dt.items.length; i++) {
    const entry = dt.items[i].webkitGetAsEntry()
    if (entry) entries.push(entry)
  }
  if (entries.length === 0) {
    for (let i = 0; i < dt.files.length; i++) {
      const f = dt.files[i]
      addUploadItem(f, f.webkitRelativePath || f.name)
    }
    return
  }
  entries.forEach(entry => traverseEntry(entry, allowed))
}

function traverseEntry(entry: FileSystemEntry, allowed: string[], parentPath = '') {
  const relativePath = parentPath ? `${parentPath}/${entry.name}` : entry.name
  if (entry.isFile) {
    const ext = '.' + entry.name.split('.').pop()?.toLowerCase()
    if (!allowed.includes(ext)) return
    ;(entry as FileSystemFileEntry).file(f => addUploadItem(f, relativePath))
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    const readAll = () => {
      reader.readEntries(entries => {
        if (entries.length === 0) return
        entries.forEach(e => traverseEntry(e, allowed, relativePath))
        readAll()
      })
    }
    readAll()
  }
}

function openUploadDialog(kb: KnowledgeBaseSummary) {
  uploadKb.value = kb
  uploadFiles.value = []
  uploadFolderId.value = kb.slug === detailKb.value?.slug
    ? (showingAllDocuments.value ? rootFolder.value?.id ?? null : selectedFolderId.value)
    : null
  uploadError.value = ''
  showUploadDialog.value = true
}

function updateUploadDialogOpen(open: boolean) {
  if (!open && uploading.value) return
  showUploadDialog.value = open
}

function isUploadSummary(result: DocumentDetail | DocumentUploadSummary): result is DocumentUploadSummary {
  return 'uploaded_count' in result && 'skipped_count' in result
}

function updateUploadItem(index: number, patch: Partial<UploadItem>) {
  const item = uploadFiles.value[index]
  if (!item) return
  uploadFiles.value[index] = { ...item, ...patch }
}

function uploadProcessingStage(file: File) {
  return file.name.toLowerCase().endsWith('.zip')
    ? '正在解析 / 正在解压 / 正在入库 / 排队同步'
    : '正在解析 / 正在入库 / 排队同步'
}

function uploadErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

async function uploadOne(index: number, kbSlug: string, folderId: number | null) {
  const item = uploadFiles.value[index]
  if (!item) return null
  updateUploadItem(index, { status: 'uploading', progress: 0, stage: '正在上传', error: '' })
  try {
    const result = await api.addDocument(
      item.file,
      [kbSlug],
      true,
      folderId,
      item.relativePath,
      (loaded, total) => {
        const progress = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
        const uploadFinished = total > 0 && loaded >= total
        updateUploadItem(index, {
          status: uploadFinished ? 'processing' : 'uploading',
          progress,
          stage: uploadFinished ? uploadProcessingStage(item.file) : `正在上传 ${progress}%`,
        })
      },
    )
    updateUploadItem(index, { status: 'success', progress: 100, stage: '上传成功', error: '' })
    return result
  } catch (error: unknown) {
    updateUploadItem(index, { status: 'error', stage: '上传失败', error: uploadErrorMessage(error) })
    return null
  }
}

async function uploadWorker(
  uploadIndexes: number[],
  results: Array<DocumentDetail | DocumentUploadSummary | null>,
  kbSlug: string,
  folderId: number | null,
) {
  while (true) {
    const queueIndex = nextUploadIndex++
    const itemIndex = uploadIndexes[queueIndex]
    if (itemIndex == null) return
    results[itemIndex] = await uploadOne(itemIndex, kbSlug, folderId)
  }
}

async function uploadDocuments() {
  const kb = uploadKb.value
  if (!kb || uploadFiles.value.length === 0 || uploading.value) return
  const uploadIndexes = uploadFiles.value
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.status !== 'success')
    .map(({ index }) => index)
  if (uploadIndexes.length === 0) return
  uploading.value = true
  uploadError.value = ''
  try {
    uploadIndexes.forEach(index => updateUploadItem(index, { status: 'pending', progress: 0, stage: '等待上传', error: '' }))
    const results: Array<DocumentDetail | DocumentUploadSummary | null> = []
    nextUploadIndex = 0
    const workerCount = Math.min(3, uploadIndexes.length)
    await Promise.all(
      Array.from({ length: workerCount }, () => uploadWorker(uploadIndexes, results, kb.slug, uploadFolderId.value)),
    )
    const completedResults = results.filter(
      (result): result is DocumentDetail | DocumentUploadSummary => result !== null,
    )
    const failedCount = uploadIndexes.filter(index => uploadFiles.value[index]?.status === 'error').length
    const uploadedCount = completedResults.reduce((count, result) => count + (isUploadSummary(result) ? result.uploaded_count : 1), 0)
    const skippedCount = completedResults.reduce((count, result) => count + (isUploadSummary(result) ? result.skipped_count : 0), 0)
    if (failedCount > 0) {
      uploadError.value = `${failedCount} 个文件上传失败，请查看下方具体错误并重试。`
      return
    }
    let syncStartError = ''
    if (uploadedCount > 0) {
      uploadIndexes.forEach(index => {
        if (uploadFiles.value[index]?.status === 'success') {
          updateUploadItem(index, { stage: '正在同步' })
        }
      })
      try {
        await api.triggerSync()
      } catch {
        syncStartError = '文件已入库，但同步未启动，请稍后点击“立即同步”。'
      }
    }
    if (skippedCount > 0) {
      await alert({
        title: '上传完成',
        description: `成功入库 ${uploadedCount} 个文件，跳过 ${skippedCount} 个重复文件。`,
      })
    }
    showUploadDialog.value = false
    uploadFiles.value = []
    if (detailKb.value?.slug === kb.slug) await refreshKnowledgeDetail(selectedFolderId.value)
    else await refreshDetailKbSummary()
    if (syncStartError) {
      await alert({ title: '同步未启动', description: syncStartError, destructive: true })
    }
  } catch (e: unknown) {
    uploadError.value = uploadErrorMessage(e) || '上传失败'
  } finally {
    uploading.value = false
  }
}

function getFileSizeLabel(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function openPlaneDialog(k: KnowledgeBaseSummary) {
  planeKb.value = k
  selectedProfileKeys.value = []
  pendingProfileKeys.value = []
  allProfiles.value = []
  profileBackendOverrides.value = {}
  profileAgentOverrides.value = {}
  agentsByBackend.value = {}
  try {
    const [profiles, rules] = await Promise.all([
      api.listProfiles(),
      api.getResourceProfiles('wiki_kb', k.slug),
    ])
    allProfiles.value = profiles
    selectedProfileKeys.value = rules.map((rule: any) => rule.profile_key)
    pendingProfileKeys.value = [...selectedProfileKeys.value]
    for (const rule of rules as any[]) {
      profileBackendOverrides.value[rule.profile_key] = rule.retrieval_backend_slug || ''
      profileAgentOverrides.value[rule.profile_key] = rule.retrieval_agent_id || ''
    }
    // Preload agent lists for weknora backends already referenced by an override
    const weknoraSlugs = new Set<string>()
    for (const pk of pendingProfileKeys.value) {
      const slug = profileBackendOverrides.value[pk]
      if (slug && isWeknoraBackend(slug)) weknoraSlugs.add(slug)
    }
    await Promise.all([...weknoraSlugs].map(s => loadAgentsForBackend(s)))
  } catch { /* ignore */ }
  showPlaneDialog.value = true
}

function togglePlaneProfile(profileKey: string) {
  const idx = pendingProfileKeys.value.indexOf(profileKey)
  if (idx >= 0) {
    pendingProfileKeys.value.splice(idx, 1)
    delete profileBackendOverrides.value[profileKey]
    delete profileAgentOverrides.value[profileKey]
  } else {
    pendingProfileKeys.value.push(profileKey)
    if (!(profileKey in profileBackendOverrides.value)) {
      profileBackendOverrides.value[profileKey] = ''
    }
    if (!(profileKey in profileAgentOverrides.value)) {
      profileAgentOverrides.value[profileKey] = ''
    }
  }
}

async function onProfileBackendChange(profileKey: string) {
  // Backend switched for a profile: load its agents, clear agent override if backend is not weknora.
  const slug = profileBackendOverrides.value[profileKey]
  if (slug && isWeknoraBackend(slug)) {
    await loadAgentsForBackend(slug)
  } else {
    profileAgentOverrides.value[profileKey] = ''
  }
}

async function savePlaneProfiles() {
  if (!planeKb.value) return
  planeSaving.value = true
  try {
    const overrides: Record<string, { retrieval_backend_slug: string | null; retrieval_agent_id: string | null }> = {}
    for (const pk of pendingProfileKeys.value) {
      const slug = profileBackendOverrides.value[pk] ?? ''
      // agent_id only persists when the profile targets a weknora backend
      const agent = slug && isWeknoraBackend(slug) ? (profileAgentOverrides.value[pk] || null) : null
      overrides[pk] = { retrieval_backend_slug: slug || null, retrieval_agent_id: agent }
    }
    await api.setResourceProfiles('wiki_kb', planeKb.value.slug, [...pendingProfileKeys.value], Object.keys(overrides).length > 0 ? overrides : undefined)
    selectedProfileKeys.value = [...pendingProfileKeys.value]
    showPlaneDialog.value = false
  } catch { /* ignore */ }
  planeSaving.value = false
}

// 同步/任务 status → StatusBadge 语义状态
function syncBadgeStatus(status?: string | null): 'success' | 'error' | 'disabled' {
  if (status === 'synced' || status === 'succeeded' || status === 'success') return 'success'
  if (status === 'sync_failed' || status === 'failed' || status === 'error') return 'error'
  return 'disabled'
}

function syncBadgeLabel(status?: string | null) {
  const labels: Record<string, string> = {
    not_synced: '未同步',
    synced: '已同步',
    sync_failed: '同步失败',
    delete_pending: '待删除',
    delete_failed: '删除失败',
    pending: '待处理',
    running: '同步中',
    succeeded: '已完成',
    failed: '失败',
    success: '成功',
    error: '错误',
  }
  return labels[status || ''] || status || '未知'
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- LIST MODE -->
    <template v-if="mode === 'list'">
    <!-- 页头操作：Teleport 进全局 PageHeader 的 #ph-actions（仅列表态） -->
    <Teleport v-if="mode === 'list'" to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="loadKbs()">
        <RotateCw :size="14" />
        刷新
      </Button>
      <Button size="lg" class="shadow-btn" @click="showCreate = true">
        <Plus :size="14" />
        创建文档知识
      </Button>
    </Teleport>

    <!-- KB Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="kbs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无文档知识，点击「创建文档知识」开始</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">标识</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">文档数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">同步失败</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in pagedKbs" :key="k.slug" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <div class="text-sm font-medium">{{ k.name }}</div>
                <div class="text-xs text-muted-foreground">{{ k.description }}</div>
              </td>
              <td class="px-4 py-3 font-mono text-xs text-muted-foreground">{{ k.slug }}</td>
              <td class="px-4 py-3 tabular-nums text-sm">{{ k.document_count }}</td>
              <td class="px-4 py-3 tabular-nums text-sm">
                <Badge v-if="k.sync_failed_count > 0" variant="destructive">{{ k.sync_failed_count }}</Badge>
                <span v-else class="text-muted-foreground">0</span>
              </td>
              <td class="px-4 py-3">
                <div class="flex gap-2">
                  <Button variant="outline" size="sm" @click="openDetail(k)" class="h-8 text-xs">详情</Button>
                  <Button variant="outline" size="sm" @click="openPlaneDialog(k)" class="h-8 text-xs">能力平面</Button>
                  <Button variant="ghost" size="sm" class="h-8 gap-1.5 text-xs text-destructive" @click="deleteKb(k)">
                    <Trash2 :size="12" />
                    删除
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
    <PaginationBar
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="kbs.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />
    </template>

    <!-- DETAIL MODE (secondary page) -->
    <template v-else>
      <!-- Route error -->
      <div v-if="routeError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
        {{ routeError }}。请<a class="underline" href="#knowledge" @click.prevent="goList">返回列表</a>。
      </div>

      <!-- Back button -->
      <div v-else class="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
          <ArrowLeft :size="14" class="mr-1.5" />
          返回
        </Button>
        <div class="flex flex-wrap gap-2">
          <Button v-if="detailKb" variant="outline" size="sm" class="h-8 text-xs" @click="openUploadDialog(detailKb)">
            <Upload :size="12" class="mr-1" />
            上传
          </Button>
          <Button v-if="detailKb" variant="outline" size="sm" class="h-8 text-xs" @click="openPlaneDialog(detailKb)">能力平面</Button>
        </div>
      </div>

      <!-- KB name header -->
      <div v-if="detailKb && !routeError">
        <h2 class="text-lg font-semibold text-foreground">{{ detailKb.name }}</h2>
        <p class="font-mono text-xs text-muted-foreground">{{ detailKb.slug }}</p>
      </div>

      <div v-if="detailKb" v-show="!detailLoading" class="space-y-4">
        <!-- Default Backend & Agent -->
        <div class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-2.5">
          <span class="text-xs text-muted-foreground shrink-0">默认检索后端</span>
          <template v-if="!editingDefaultBackend">
            <span class="text-sm font-medium">{{ detailKb?.default_backend_slug || '自动（跟随系统）' }}</span>
            <template v-if="detailKb?.default_agent_id">
              <span class="text-xs text-muted-foreground">· 默认 Agent:</span>
              <span class="text-sm font-medium">{{ detailAgentLabel }}</span>
            </template>
            <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="editingDefaultBackend = true">修改</Button>
          </template>
          <template v-else>
            <select v-model="defaultBackendSlug" @change="onDetailBackendChange" class="h-8 rounded-md border border-border bg-background px-2 text-sm flex-1 min-w-[180px]">
              <option value="">自动（跟随系统）</option>
              <option v-for="b in backends" :key="b.slug" :value="b.slug">{{ b.slug }} ({{ b.backend_type }})</option>
            </select>
            <select v-if="isWeknoraBackend(defaultBackendSlug)" v-model="defaultAgentId" :disabled="detailAgentsLoading" class="h-8 rounded-md border border-border bg-background px-2 text-sm flex-1 min-w-[180px]">
              <option value="">无（使用后端默认问答）</option>
              <option v-for="a in detailAgents" :key="a.agent_id" :value="a.agent_id">{{ a.name }}{{ a.agent_type ? ' · ' + a.agent_type : '' }}</option>
            </select>
            <Button variant="ghost" size="sm" class="h-6 text-xs" @click="editingDefaultBackend = false">取消</Button>
            <Button size="sm" class="h-6 text-xs" @click="saveDefaultBackend" :disabled="savingDefaultBackend">
              {{ savingDefaultBackend ? '保存中...' : '保存' }}
            </Button>
          </template>
        </div>
        <!-- Tabs -->
        <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
          <button v-for="t in [
            { key: 'docs', label: `文档 (${detailTotalDocumentCount})` },
            { key: 'sync', label: `同步 (${detailSyncJobs.length})` },
            { key: 'sources', label: `Git 数据源 (${detailRepoSources.length})` },
            { key: 'search', label: '检索' },
          ]" :key="t.key"
            :class="['rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors', detailTab === t.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground']"
            @click="detailTab = t.key as any">{{ t.label }}</button>
        </div>

        <!-- Documents Tab -->
        <div
          v-if="detailTab === 'docs'"
          class="grid min-h-0 gap-4 lg:flex lg:items-stretch lg:gap-0"
          :style="{ '--folder-pane-width': folderPaneWidth + 'px' }"
        >
          <aside class="h-[calc(100vh-280px)] min-h-[240px] max-h-[calc(100vh-280px)] min-w-0 overflow-y-auto rounded-lg border border-border bg-card p-2 lg:w-[var(--folder-pane-width)] lg:shrink-0">
            <div class="mb-2 flex items-center justify-between px-2">
              <span class="text-xs font-semibold text-muted-foreground">目录</span>
              <span class="text-[11px] text-muted-foreground">{{ detailFolders.length }} 项</span>
            </div>
            <FolderTree
              :folders="detailFolders"
              :selected-id="selectedFolderId"
              :all-selected="showingAllDocuments"
              :root-label="detailKb.name"
              :all-count="detailTotalDocumentCount"
              :loading="folderTreeLoading"
              @select="selectFolder"
              @create="openCreateFolder"
              @rename="openRenameFolder"
              @move="openMoveFolder"
              @remove="removeFolder"
            />
          </aside>

          <div
            data-testid="knowledge-folder-resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="调整目录宽度"
            :aria-valuenow="folderPaneWidth"
            :aria-valuemin="FOLDER_PANE_MIN_WIDTH"
            :aria-valuemax="FOLDER_PANE_MAX_WIDTH"
            tabindex="0"
            class="group hidden w-4 shrink-0 cursor-col-resize touch-none items-center justify-center lg:flex"
            @pointerdown="startFolderPaneResize"
            @keydown="handleFolderPaneKeydown"
          >
            <span class="h-12 w-1 rounded-full bg-border transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary" />
          </div>

          <section class="min-h-0 min-w-0 flex-1 space-y-3 lg:h-[calc(100vh-280px)] lg:min-h-[240px] lg:max-h-[calc(100vh-280px)] lg:overflow-y-auto">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-1 text-sm font-medium">
                  <template v-for="(crumb, index) in currentFolderBreadcrumbs" :key="`${crumb}-${index}`">
                    <span v-if="index > 0" class="text-muted-foreground">/</span>
                    <span class="truncate">{{ crumb }}</span>
                  </template>
                </div>
                <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{{ showingAllDocuments ? '全部文档是快捷查询，不作为目录同步路径' : '当前上下文仅显示直接子条目' }}</span>
                  <span v-if="browseContext" class="text-muted-foreground">· {{ browseContext.name }}</span>
                  <Badge variant="secondary" class="text-[10px]">{{ folderCapabilityLabel }}</Badge>
                  <Badge variant="outline" class="text-[10px]">{{ browseContextLabel }}</Badge>
                </div>
              </div>
              <Button v-if="browseParent && !showingAllDocuments" variant="outline" size="sm" class="h-8 shrink-0 text-xs" @click="goBrowseParent">
                <ArrowLeft :size="12" class="mr-1" />返回上一级
              </Button>
            </div>

            <!-- Batch toolbar -->
            <div v-if="selectedDocSlugs.size > 0" class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-3 py-2">
              <span class="text-sm font-medium">已选 {{ selectedDocSlugs.size }} 项</span>
              <Button variant="destructive" size="sm" class="h-7 text-xs" @click="batchDeleteDocs">
                <Trash2 :size="12" class="mr-1" />批量删除
              </Button>
              <Button variant="ghost" size="sm" class="h-7 text-xs text-muted-foreground" @click="selectedDocSlugs = new Set()">取消选择</Button>
            </div>
            <div v-if="browseLoading" class="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">目录加载中...</div>
            <div v-else-if="browseError && !showingAllDocuments" class="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-3 text-sm text-destructive">{{ browseError }}</div>
            <div v-else-if="!showingAllDocuments && browseEntries.length === 0" class="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">暂无子条目</div>
            <div v-else-if="!showingAllDocuments" class="min-h-0 max-h-[calc(100vh-420px)] overflow-x-auto overflow-y-auto rounded-lg border border-border">
              <table class="w-full">
                <thead><tr class="border-b border-border">
                  <th class="px-3 py-2 text-left" style="width: 28px;">
                    <input type="checkbox" class="size-4 rounded" :checked="allDocsSelected"
                      :indeterminate.prop="someDocsSelected && !allDocsSelected" @change="toggleAllDocs" />
                  </th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标题</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">目录</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">版本</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground"></th>
                </tr></thead>
                <tbody>
                  <template v-for="entry in browseEntries" :key="`${entry.kind}-${entry.id}`">
                    <tr v-if="entry.kind !== 'document'" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                      <td colspan="6" class="px-3 py-2">
                        <button type="button" class="flex w-full items-center gap-2 text-left" @click="enterBrowseEntry(entry)">
                          <Archive v-if="entry.kind === 'zip'" :size="16" class="shrink-0 text-warning" />
                          <Folder v-else :size="16" class="shrink-0 text-primary/80" />
                          <span class="min-w-0 flex-1 truncate text-sm font-medium" :title="entry.relative_path">{{ entry.name }}</span>
                          <Badge variant="outline" class="shrink-0 text-[10px]">{{ entry.kind === 'zip' ? 'ZIP' : '文件夹' }}</Badge>
                          <span class="shrink-0 text-xs text-muted-foreground">{{ entry.child_count }} 项</span>
                        </button>
                      </td>
                    </tr>
                    <tr v-else-if="entry.kind === 'document'" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                      <td class="px-3 py-2">
                        <input type="checkbox" class="size-4 rounded" :value="entry.slug"
                          :checked="selectedDocSlugs.has(entry.slug)" @change="toggleDocSelected(entry.slug)" />
                      </td>
                      <td class="px-3 py-2 text-sm font-medium" :title="entry.relative_path">{{ entry.title }}</td>
                      <td class="max-w-[180px] truncate px-3 py-2 text-xs text-muted-foreground" :title="entry.relative_path">{{ entry.name }}</td>
                      <td class="px-3 py-2 text-xs tabular-nums">v{{ entry.version_no || 0 }}</td>
                      <td class="px-3 py-2">
                        <StatusBadge
                          :status="syncBadgeStatus(entry.sync_status)"
                          :label="syncBadgeLabel(entry.sync_status || entry.status)" />
                      </td>
                      <td class="px-3 py-2">
                        <div class="flex justify-end gap-1">
                          <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openDocumentDetail(entry.slug)">详情</Button>
                          <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openPlaceDialog(documentFromBrowseEntry(entry))">移动</Button>
                          <Button v-if="otherKnowledgeBases.length > 0" variant="ghost" size="sm" class="h-7 text-xs" @click="openAttachDialog(documentFromBrowseEntry(entry))">关联</Button>
                          <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteDoc(entry.slug, entry.title)">删除</Button>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
            <div v-else-if="detailDocs.length === 0" class="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">暂无文档</div>
            <div v-else class="min-h-0 max-h-[calc(100vh-420px)] overflow-x-auto overflow-y-auto rounded-lg border border-border">
              <table class="w-full">
                <thead><tr class="border-b border-border">
                  <th class="px-3 py-2 text-left" style="width: 28px;">
                    <input type="checkbox" class="size-4 rounded" :checked="allDocsSelected"
                      :indeterminate.prop="someDocsSelected && !allDocsSelected" @change="toggleAllDocs" />
                  </th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标题</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">目录</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">版本</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground"></th>
                </tr></thead>
                <tbody><tr v-for="d in detailDocs" :key="d.slug" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                  <td class="px-3 py-2">
                    <input type="checkbox" class="size-4 rounded" :value="d.slug"
                      :checked="selectedDocSlugs.has(d.slug)" @change="toggleDocSelected(d.slug)" />
                  </td>
                  <td class="px-3 py-2 text-sm font-medium">{{ d.title }}</td>
                  <td class="max-w-[180px] truncate px-3 py-2 text-xs text-muted-foreground" :title="d.folder_path || '根目录'">{{ d.folder_path || '根目录' }}</td>
                  <td class="px-3 py-2 text-xs tabular-nums">v{{ d.current_version_no || 0 }}</td>
                  <td class="px-3 py-2">
                    <StatusBadge
                      :status="syncBadgeStatus(d.sync_status)"
                      :label="syncBadgeLabel(d.sync_status || d.status)" />
                  </td>
                  <td class="px-3 py-2">
                    <div class="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openDocumentDetail(d.slug)">详情</Button>
                      <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openPlaceDialog(d)">移动</Button>
                      <Button v-if="otherKnowledgeBases.length > 0" variant="ghost" size="sm" class="h-7 text-xs" @click="openAttachDialog(d)">关联</Button>
                      <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteDoc(d.slug, d.title)">删除</Button>
                    </div>
                  </td>
                </tr></tbody>
              </table>
            </div>
          </section>
        </div>

        <!-- Sync Tab -->
        <div v-if="detailTab === 'sync'" class="space-y-3">
          <div class="flex items-center gap-3">
            <Button size="sm" @click="triggerSync" :disabled="syncing">{{ syncing ? '同步中...' : '立即同步' }}</Button>
            <span class="text-sm text-muted-foreground">处理所有待处理和失败的同步任务</span>
          </div>
          <div v-if="detailSyncJobs.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无同步任务</div>
          <table v-else class="w-full">
            <thead><tr class="border-b border-border">
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">文档</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">操作</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">后端</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">错误</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">时间</th>
            </tr></thead>
            <tbody><tr v-for="j in detailSyncJobs" :key="j.id" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-3 py-2 text-sm">{{ j.doc_title }}</td>
              <td class="px-3 py-2 text-xs">{{ j.operation }}</td>
              <td class="px-3 py-2">
                <StatusBadge :status="syncBadgeStatus(j.status)" :label="syncBadgeLabel(j.status)" />
              </td>
              <td class="px-3 py-2 text-xs text-muted-foreground">{{ j.backend_slug }}</td>
              <td class="px-3 py-2 max-w-[200px] overflow-hidden text-ellipsis text-xs text-destructive" :title="j.error ?? ''">{{ j.error || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ formatLocalDatetime(j.updated_at) }}</td>
            </tr></tbody>
          </table>
        </div>

        <!-- Git Sources Tab -->
        <div v-if="detailTab === 'sources'" class="space-y-4">
          <div class="rounded-lg border border-border p-4">
            <div class="mb-3 flex items-center gap-2">
              <GitBranch :size="15" class="text-muted-foreground" />
              <h4 class="text-sm font-medium">Git 数据源</h4>
            </div>
            <div v-if="codeRepos.length === 0" class="py-4 text-sm text-muted-foreground">暂无已登记的代码仓库，请先在代码知识中添加仓库。</div>
            <div v-else class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-muted-foreground">代码仓库</label>
                <select v-model="repoSourceForm.repo_key" @change="onRepoSourceSelect" class="h-9 w-full rounded-md border border-border bg-background px-2 text-sm">
                  <option v-for="repo in codeRepos" :key="repo.repo_key" :value="repo.repo_key">{{ repo.name || repo.repo_key }}</option>
                </select>
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-muted-foreground">后缀过滤</label>
                <Input v-model="repoSourceForm.include_suffixes" placeholder=".md, .txt" />
              </div>
              <div class="flex items-end">
                <Button class="h-9" @click="saveRepoSource" :disabled="repoSourceSaving || !repoSourceForm.repo_key">
                  {{ repoSourceSaving ? '保存中...' : '保存' }}
                </Button>
              </div>
            </div>
            <div v-if="repoSourceError" class="mt-3 rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ repoSourceError }}</div>
            <div v-if="repoSourceMessage" class="mt-3 rounded-md bg-success-soft px-3 py-2 text-xs text-success-soft-fg">{{ repoSourceMessage }}</div>
          </div>

          <div v-if="detailRepoSources.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无 Git 数据源</div>
          <table v-else class="w-full">
            <thead><tr class="border-b border-border">
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">仓库</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">include_suffixes</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">最近同步</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">错误</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground"></th>
            </tr></thead>
            <tbody><tr v-for="source in detailRepoSources" :key="source.repo_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-3 py-2">
                <div class="text-sm font-medium">{{ source.repo_name || source.repo_key }}</div>
                <div class="font-mono text-xs text-muted-foreground">{{ source.repo_key }}</div>
              </td>
              <td class="px-3 py-2">
                <div class="flex flex-wrap gap-1">
                  <Badge v-for="suffix in source.include_suffixes" :key="suffix" variant="secondary" class="font-mono text-[11px]">{{ suffix }}</Badge>
                </div>
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ source.last_synced_at ? formatLocalDatetime(source.last_synced_at) : '未同步' }}</td>
              <td class="px-3 py-2 max-w-[180px] overflow-hidden text-ellipsis text-xs text-destructive" :title="source.last_error ?? ''">{{ source.last_error || '—' }}</td>
              <td class="px-3 py-2 text-right">
                <div class="flex justify-end gap-2">
                  <Button variant="outline" size="sm" class="h-7 text-xs" @click="syncRepoSource(source)" :disabled="repoSourceSyncing[source.repo_key]">
                    {{ repoSourceSyncing[source.repo_key] ? '同步中...' : '立即同步' }}
                  </Button>
                  <Button variant="outline" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteRepoSource(source)" :disabled="repoSourceDeleting[source.repo_key]">
                    {{ repoSourceDeleting[source.repo_key] ? '删除中...' : '删除' }}
                  </Button>
                </div>
              </td>
            </tr></tbody>
          </table>
        </div>

        <!-- Search/Q&A Tab -->
        <div v-if="detailTab === 'search'" class="space-y-4">
          <!-- Search -->
          <div class="space-y-2">
            <h4 class="text-sm font-medium">检索</h4>
            <div class="flex gap-2">
              <Input v-model="searchQuery" placeholder="输入检索关键词" class="flex-1" @keydown.enter="doSearch" />
              <Button size="sm" @click="doSearch" :disabled="searchSearching || !searchQuery.trim()">{{ searchSearching ? '搜索中...' : '搜索' }}</Button>
            </div>
            <div v-if="searchResults.length > 0" class="space-y-2">
              <div v-for="(chunk, i) in searchResults" :key="i" class="rounded-lg border border-border p-3">
                <div class="mb-1 text-xs text-muted-foreground">{{ chunk.document_name }} · 相似度 {{ (chunk.similarity * 100).toFixed(1) }}%</div>
                <div class="text-sm whitespace-pre-wrap">{{ chunk.content }}</div>
              </div>
            </div>
          </div>
          <hr class="border-border" />
          <!-- Ask -->
          <div class="space-y-2">
            <h4 class="text-sm font-medium">问答</h4>
            <div class="flex gap-2">
              <Input v-model="askQuestion" placeholder="输入问题" class="flex-1" @keydown.enter="doAsk" />
              <Button size="sm" @click="doAsk" :disabled="asking || !askQuestion.trim()">{{ asking ? '思考中...' : '提问' }}</Button>
            </div>
            <div v-if="askAnswer" class="rounded-lg border border-border bg-secondary/30 p-4">
              <div class="text-sm whitespace-pre-wrap">{{ askAnswer }}</div>
            </div>
            <div v-if="askChunks.length > 0" class="space-y-1">
              <div class="text-xs text-muted-foreground">引用 ({{ askChunks.length }})</div>
              <div v-for="(chunk, i) in askChunks" :key="i" class="rounded border border-border/60 p-2 text-xs text-muted-foreground">
                {{ chunk.document_name }}: {{ chunk.content.slice(0, 100) }}...
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
    </template>

    <!-- Create KB Dialog -->
    <Dialog :open="showCreate" @update:open="showCreate = $event">
      <DialogContent class="sm:max-w-[450px]">
        <DialogHeader>
          <DialogTitle>创建文档知识</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createKb" class="space-y-4">
          <div v-if="createError" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ createError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">标识 <span class="text-destructive">*</span></label>
            <Input v-model="createForm.slug" placeholder="my-kb" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="createForm.name" placeholder="我的文档知识" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="createForm.description" placeholder="文档知识描述" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="createKb" :disabled="createSaving">{{ createSaving ? '创建中...' : '创建' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Plane Assignment Dialog -->
    <Dialog :open="showPlaneDialog" @update:open="showPlaneDialog = $event">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{{ planeKb?.name || '' }} — 归属能力平面</DialogTitle>
        </DialogHeader>
        <div class="space-y-2">
          <div class="text-xs text-muted-foreground">选择此知识库归属于哪些能力平面，可为每个平面单独指定检索后端。</div>
          <div v-if="allProfiles.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无能力平面</div>
          <div v-else class="max-h-[400px] space-y-1 overflow-y-auto rounded-lg border border-border p-1">
            <div v-for="p in allProfiles" :key="p.profile_key"
              class="rounded-md px-3 py-2 transition-colors hover:bg-muted/50"
            >
              <label class="flex cursor-pointer items-center gap-3">
                <input type="checkbox" :value="p.profile_key" :checked="pendingProfileKeys.includes(p.profile_key)"
                  @change="togglePlaneProfile(p.profile_key)" class="size-4 rounded" />
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium truncate">{{ p.name || p.profile_key }}</div>
                  <div class="text-xs text-muted-foreground">{{ p.profile_key }}</div>
                </div>
              </label>
              <div v-if="pendingProfileKeys.includes(p.profile_key) && backends.length > 0" class="mt-2 ml-7 space-y-1.5">
                <div class="flex items-center gap-2">
                  <span class="text-xs text-muted-foreground shrink-0 w-14">检索后端</span>
                  <select v-model="profileBackendOverrides[p.profile_key]" @change="onProfileBackendChange(p.profile_key)" class="h-7 rounded border border-border bg-background px-2 text-xs flex-1">
                    <option value="">跟随默认</option>
                    <option v-for="b in backends" :key="b.slug" :value="b.slug">{{ b.slug }} ({{ b.backend_type }})</option>
                  </select>
                </div>
                <div v-if="isWeknoraBackend(profileBackendOverrides[p.profile_key])" class="flex items-center gap-2">
                  <span class="text-xs text-muted-foreground shrink-0 w-14">Agent</span>
                  <select v-model="profileAgentOverrides[p.profile_key]" class="h-7 rounded border border-border bg-background px-2 text-xs flex-1">
                    <option value="">跟随默认</option>
                    <option v-for="a in (agentsByBackend[profileBackendOverrides[p.profile_key]] || [])" :key="a.agent_id" :value="a.agent_id">{{ a.name }}{{ a.agent_type ? ' · ' + a.agent_type : '' }}</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button @click="savePlaneProfiles" :disabled="planeSaving">{{ planeSaving ? '保存中...' : '确认' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 文档详情对话框 -->
    <Dialog :open="showDocDetail" @update:open="showDocDetail = $event">
      <DialogContent class="sm:max-w-[640px]">
        <DialogHeader><DialogTitle>文档详情</DialogTitle></DialogHeader>
        <div v-if="docDetailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="docDetailError" class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{{ docDetailError }}</div>
        <div v-else-if="docDetail" class="space-y-4 text-sm">
          <div class="grid gap-2 sm:grid-cols-2">
            <div><span class="text-xs text-muted-foreground">标题</span><div class="font-medium">{{ docDetail.title }}</div></div>
            <div><span class="text-xs text-muted-foreground">标识</span><div class="font-mono text-xs">{{ docDetail.slug }}</div></div>
            <div><span class="text-xs text-muted-foreground">状态</span><div>{{ docDetail.status }}</div></div>
            <div><span class="text-xs text-muted-foreground">知识库</span><div>{{ docDetail.kb_slugs.join('、') || '—' }}</div></div>
          </div>
          <div>
            <div class="mb-2 text-xs font-medium text-muted-foreground">版本</div>
            <div class="max-h-48 overflow-y-auto rounded-md border border-border">
              <div v-for="version in docDetail.versions" :key="version.id" class="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 last:border-b-0">
                <span>v{{ version.version_no }} · {{ version.original_filename }}</span>
                <span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(version.created_at) }}</span>
              </div>
              <div v-if="docDetail.versions.length === 0" class="px-3 py-4 text-center text-xs text-muted-foreground">暂无版本</div>
            </div>
          </div>
        </div>
        <DialogFooter><DialogClose as-child><Button variant="outline">关闭</Button></DialogClose></DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 目录操作对话框 -->
    <Dialog :open="folderDialogOpen" @update:open="folderDialogOpen = $event">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader><DialogTitle>{{ folderDialogTitle }}</DialogTitle></DialogHeader>
        <form class="space-y-4" @submit.prevent="submitFolderDialog">
          <div v-if="folderDialogMode === 'create'" class="text-xs text-muted-foreground">
            将在「{{ detailFolders.find(folder => folder.id === folderDialogParentId)?.path || detailKb?.name || '根目录' }}」下创建目录
          </div>
          <div v-if="folderDialogMode !== 'move'" class="space-y-1.5">
            <label class="text-sm font-medium">目录名称 <span class="text-destructive">*</span></label>
            <Input v-model="folderDialogName" placeholder="例如：产品文档" autofocus />
          </div>
          <div v-else class="space-y-1.5">
            <label class="text-sm font-medium">目标目录</label>
            <div class="max-h-64 overflow-y-auto rounded-md border border-border p-1">
              <FolderTree
                :folders="folderDialogTargets"
                :selected-id="folderDialogTargetId"
                :root-label="detailKb?.name || '根目录'"
                :show-all="false"
                :actions-enabled="false"
                :compact="true"
                @select="selectFolderDialogTarget"
              />
            </div>
          </div>
          <div v-if="folderDialogError" class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{{ folderDialogError }}</div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button type="button" @click="submitFolderDialog" :disabled="folderDialogSaving">
            {{ folderDialogSaving ? '保存中...' : (folderDialogMode === 'create' ? '创建' : '确定') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 文档移动/关联对话框 -->
    <Dialog :open="placementDialogOpen" @update:open="placementDialogOpen = $event">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader><DialogTitle>{{ placementDialogMode === 'place' ? '移动文档' : '关联文档' }}</DialogTitle></DialogHeader>
        <div class="space-y-4">
          <div class="text-sm font-medium truncate" :title="placementDoc?.title">{{ placementDoc?.title }}</div>
          <div v-if="placementDialogMode === 'attach'" class="space-y-1.5">
            <label class="text-xs font-medium text-muted-foreground">目标知识库</label>
            <select v-model="placementKbSlug" class="h-9 w-full rounded-md border border-border bg-background px-2 text-sm" @change="changePlacementKb(($event.target as HTMLSelectElement).value)">
              <option v-for="kb in otherKnowledgeBases" :key="kb.slug" :value="kb.slug">{{ kb.name }}（{{ kb.slug }}）</option>
            </select>
          </div>
          <div class="space-y-1.5">
            <label class="text-xs font-medium text-muted-foreground">目标目录</label>
            <div class="max-h-64 overflow-y-auto rounded-md border border-border p-1">
              <FolderTree
                :folders="placementFoldersForDialog"
                :selected-id="placementTargetFolderId"
                :root-label="placementRootLabel"
                :show-all="false"
                :actions-enabled="false"
                :compact="true"
                @select="selectPlacementFolder"
              />
            </div>
          </div>
          <div v-if="placementError" class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{{ placementError }}</div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button type="button" @click="submitPlacement" :disabled="placementSaving || placementTargetFolderId == null">
            {{ placementSaving ? '保存中...' : '确定' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 上传文档对话框 -->
    <Dialog :open="showUploadDialog" @update:open="updateUploadDialogOpen">
      <DialogContent :show-close-button="!uploading" class="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>上传文档 — {{ uploadKb?.name || '' }}</DialogTitle>
        </DialogHeader>
        <div class="space-y-4">
          <div class="text-xs text-muted-foreground">
            目标知识库：<span class="font-medium text-foreground">{{ uploadKb?.name }}</span>
            <span class="font-mono ml-1">({{ uploadKb?.slug }})</span>
            <span v-if="uploadKb?.slug === detailKb?.slug" class="ml-2">· 目标目录：{{ detailFolders.find(folder => folder.id === uploadFolderId)?.path || detailKb?.name || '根目录' }}</span>
          </div>

          <!-- 拖拽区域 -->
          <div v-if="uploadFiles.length === 0"
            :class="['rounded-lg border-2 border-dashed p-10 text-center transition-colors cursor-pointer',
              uploadDragOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/20']"
            @dragover="handleUploadDragOver"
            @dragleave="handleUploadDragLeave"
            @drop="handleUploadDrop"
          >
            <Upload :size="40" stroke="#9ca3af" stroke-width="1.5" class="mx-auto mb-3" />
            <div class="text-sm font-medium mb-1">拖拽文件或文件夹到此处</div>
            <div class="text-xs text-muted-foreground mb-4">支持 PDF、Word、Excel、PPT、TXT、Markdown、ZIP — 压缩包将自动识别其中的文档</div>
            <div class="flex items-center justify-center gap-3">
              <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm bg-primary text-primary-foreground text-sm font-medium cursor-pointer hover:bg-primary/80">
                <File :size="14" />
                选择文件
                <input type="file" multiple class="hidden" :disabled="uploading" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.zip" @change="onUploadFilesSelected" />
              </label>
              <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm border border-border bg-background text-sm font-medium cursor-pointer hover:bg-muted">
                <Folder :size="14" />
                选择文件夹
                <input type="file" multiple webkitdirectory class="hidden" :disabled="uploading" @change="onUploadFilesSelected" />
              </label>
            </div>
          </div>

          <!-- 文件列表 -->
          <div v-else class="rounded-lg border-2 border-success/30 bg-muted/20 p-4">
            <div class="flex items-center justify-between mb-3">
              <div class="text-sm font-medium">
                已选择 <span class="text-success">{{ uploadFiles.length }}</span> 个文件
                <span v-if="failedUploadCount > 0" class="ml-2 text-destructive">失败 {{ failedUploadCount }} 个</span>
              </div>
              <Button variant="ghost" size="xs" class="h-7 text-xs text-muted-foreground" :disabled="uploading" @click="uploadFiles = []">清除</Button>
            </div>
            <div class="min-w-0 space-y-1.5 max-h-[240px] overflow-y-auto">
              <div v-for="(f, i) in uploadFiles" :key="i"
                class="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 overflow-hidden rounded border border-border bg-background px-3 py-2 text-sm"
              >
                <File :size="14" stroke="#9ca3af" class="shrink-0" />
                <span class="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" :title="f.relativePath">{{ f.relativePath }}</span>
                <div class="flex shrink-0 flex-col items-end gap-0.5 text-xs">
                  <span class="text-muted-foreground">{{ getFileSizeLabel(f.file.size) }}</span>
                  <span :class="f.status === 'error' ? 'text-destructive' : f.status === 'success' ? 'text-success' : 'text-muted-foreground'">
                    {{ f.status === 'success' ? '成功' : f.status === 'error' ? '失败' : f.status === 'processing' ? '处理中' : f.status === 'uploading' ? '上传中' : '等待中' }}
                  </span>
                </div>
                <div class="col-span-full min-w-0 space-y-1">
                  <div class="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span class="min-w-0 truncate">{{ f.stage }}</span>
                    <span class="shrink-0">{{ f.progress }}%</span>
                  </div>
                  <div class="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div class="h-full rounded-full bg-primary transition-[width]" :style="{ width: `${f.progress}%` }"></div>
                  </div>
                  <div v-if="f.error" class="max-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded border border-destructive/20 bg-destructive/5 px-2 py-1 text-xs text-destructive">
                    {{ f.error }}
                  </div>
                </div>
              </div>
            </div>
            <label class="block mt-3 py-2 border border-dashed border-border rounded text-center text-xs text-muted-foreground cursor-pointer hover:bg-muted/50 transition-colors">
              + 继续添加文件
              <input type="file" multiple class="hidden" :disabled="uploading" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.zip" @change="onUploadFilesSelected" />
            </label>
          </div>
          <div v-if="uploadError" class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {{ uploadError }}
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" :disabled="uploading">取消</Button></DialogClose>
          <Button @click="uploadDocuments" :disabled="retryableUploadCount === 0 || uploading">
            {{ uploading ? '上传中...' : failedUploadCount > 0 ? `重试失败 (${failedUploadCount})` : `上传 (${uploadFiles.length})` }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
