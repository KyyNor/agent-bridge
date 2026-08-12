<script setup lang="ts">
import { Plus, RotateCw, Upload, File, Folder, Archive, Trash2, ArrowLeft } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import type { AccessActorContext, KnowledgeBaseSummary, Document, KnowledgeFolder, SyncJob, BackendInfo, KnowledgeBrowseDocumentEntry, KnowledgeBrowseEntry, KnowledgeBrowseResponse, ResourceVisibility } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import StatusBadge from '../../components/StatusBadge.vue'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import PaginationBar from '../../components/PaginationBar.vue'
import TourReplayButton from '../../components/TourReplayButton.vue'
import FolderTree from '../../components/knowledge/FolderTree.vue'
import KnowledgeDefaultBackendPanel from '../../components/knowledge/KnowledgeDefaultBackendPanel.vue'
import KnowledgeDocumentDetailDialog from '../../components/knowledge/KnowledgeDocumentDetailDialog.vue'
import KnowledgePlaneDialog from '../../components/knowledge/KnowledgePlaneDialog.vue'
import KnowledgeRepoSourcesPanel from '../../components/knowledge/KnowledgeRepoSourcesPanel.vue'
import KnowledgeSearchPanel from '../../components/knowledge/KnowledgeSearchPanel.vue'
import KnowledgeSyncJobsPanel from '../../components/knowledge/KnowledgeSyncJobsPanel.vue'
import KnowledgeUploadDialog from '../../components/knowledge/KnowledgeUploadDialog.vue'
import { confirm, alert } from '../../composables/useConfirm'
import { useKnowledgeUploadQueue } from '../../composables/useKnowledgeUploadQueue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'
const router = useRouter()
import { queryClient, queryKeys } from '../../lib/query'
import { knowledgeFirstUseTour } from '../../lib/onboardingTours'
import { useOnboardingTour } from '../../composables/useOnboardingTour'
import { isSharedResourceReadOnly, SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'
const props = defineProps<{ routeKey: string }>()
const { maybeStartTour, startTour } = useOnboardingTour()
const mode = computed<'list' | 'detail'>(() => (props.routeKey ? 'detail' : 'list'))
const pagedKbs = computed(() => paginate(kbs.value, page.value, pageSize.value))
const kbs = ref<KnowledgeBaseSummary[]>([])
const page = ref(1)
const pageSize = ref(10)
const loading = ref(true)
const actorContext = ref<AccessActorContext | null>(null)
const showCreate = ref(false)
const createForm = ref<{ slug: string; name: string; description: string; visibility: ResourceVisibility }>({ slug: '', name: '', description: '', visibility: 'group' })
const createSaving = ref(false)
const createError = ref('')
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
const detailRepoSourceCount = ref(0)
const detailLoading = ref(false)
const routeError = ref('')
const selectedDocSlugs = ref<Set<string>>(new Set())
const allDocsSelected = computed(() => detailDocs.value.length > 0 && detailDocs.value.every(d => selectedDocSlugs.value.has(d.slug)))
const someDocsSelected = computed(() => detailDocs.value.some(d => selectedDocSlugs.value.has(d.slug)))
const detailTotalDocumentCount = computed(() => {
  const slug = detailKb.value?.slug
  return kbs.value.find(kb => kb.slug === slug)?.document_count ?? detailKb.value?.document_count ?? 0
})
const detailReadOnly = computed(() => isSharedResourceReadOnly(actorContext.value, detailKb.value))
function kbReadOnly(kb: KnowledgeBaseSummary | null | undefined) {
  return isSharedResourceReadOnly(actorContext.value, kb)
}
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
const showDocDetail = ref(false)
const docDetailSlug = ref('')
const showPlaneDialog = ref(false)
const planeKb = ref<KnowledgeBaseSummary | null>(null)
const backends = ref<BackendInfo[]>([])
const {
  showUploadDialog,
  uploadKb,
  uploadFiles,
  uploadFolderId,
  uploading,
  uploadDragOver,
  uploadError,
  failedUploadCount,
  retryableUploadCount,
  openUploadDialog,
  updateUploadDialogOpen,
  clearUploadFiles,
  onUploadFilesSelected,
  handleUploadDragOver,
  handleUploadDragLeave,
  handleUploadDrop,
  uploadDocuments,
} = useKnowledgeUploadQueue({
  detailKb,
  selectedFolderId,
  rootFolder,
  showingAllDocuments,
  onUploaded: async (kb) => {
    if (detailKb.value?.slug === kb.slug) await refreshKnowledgeDetail(selectedFolderId.value)
    else await refreshDetailKbSummary()
  },
})
onMounted(async () => {
  const [, , actor] = await Promise.all([loadKbs(), loadBackends(), api.getAccessContext()])
  actorContext.value = actor
  loading.value = false
  await loadDetail()
  await maybeStartKnowledgeTour()
})
// Route-driven detail loading: entering /knowledge/<slug> loads that kb's data.
watch(() => props.routeKey, async () => {
  await loadDetail()
  await maybeStartKnowledgeTour()
})
watch(() => detailTab.value, () => {
  if (detailTab.value !== 'docs') selectedDocSlugs.value = new Set()
})
async function loadKbs(options: { fresh?: boolean } = {}) {
  if (options.fresh) await queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeBases() })
  try {
    kbs.value = await queryClient.fetchQuery({
      queryKey: queryKeys.knowledgeBases(),
      queryFn: ({ signal }) => api.listWikiKbs({ signal }),
    })
  } catch { kbs.value = [] }
}
async function maybeStartKnowledgeTour() {
  if (mode.value !== 'list' || loading.value) return
  await maybeStartTour(knowledgeFirstUseTour)
}
async function refreshDetailKbSummary() {
  await loadKbs({ fresh: true })
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
    await loadKbs({ fresh: true })
  } catch (e: any) {
    await alert({ title: '删除失败', description: e.message || '删除失败', destructive: true })
  }
}
async function loadBackends(options: { fresh?: boolean } = {}) {
  if (options.fresh) await queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeBackends() })
  try {
    backends.value = await queryClient.fetchQuery({
      queryKey: queryKeys.knowledgeBackends(),
      queryFn: ({ signal }) => api.listBackends({ signal }),
    })
  } catch { backends.value = [] }
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
      visibility: createForm.value.visibility,
    })
    showCreate.value = false
    createForm.value = { slug: '', name: '', description: '', visibility: 'group' }
    await loadKbs({ fresh: true })
    const newKb = kbs.value.find(k => k.slug === slug)
    if (newKb) openDetail(newKb)
  } catch (e: any) {
    createError.value = e.message || '创建失败'
  }
  createSaving.value = false
}
function goList() {
  void router.replace('/knowledge')
}
async function openDetail(kb: KnowledgeBaseSummary) {
  void router.push('/knowledge/' + kb.slug)
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
    api.getKbSyncStatus(detailKb.value.slug),
  ])
  if (syncStatusResult.status === 'fulfilled' && detailKb.value) {
    detailSyncJobs.value = syncStatusResult.value.jobs
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
  detailLoading.value = true
  detailRepoSourceCount.value = 0
  detailFolders.value = []
  detailBrowse.value = null
  browseError.value = ''
  selectedFolderId.value = null
  showingAllDocuments.value = false
  selectedDocSlugs.value = new Set()
  try {
    await refreshKnowledgeDetail()
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

function openDocumentDetail(slug: string) {
  showDocDetail.value = true
  docDetailSlug.value = slug
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

function openPlaneDialog(k: KnowledgeBaseSummary) {
  planeKb.value = k
  showPlaneDialog.value = true
}

async function updateKnowledgeDefaults(defaults: { default_backend_slug: string | null; default_agent_id: string | null }) {
  if (!detailKb.value) return
  detailKb.value = { ...detailKb.value, ...defaults }
  await loadKbs({ fresh: true })
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
      <TourReplayButton :tour="knowledgeFirstUseTour" @start="startTour" />
      <Button data-tour="knowledge-refresh" variant="outline" size="lg" @click="loadKbs({ fresh: true })">
        <RotateCw :size="14" />
        刷新
      </Button>
      <Button data-tour="knowledge-create" size="lg" class="shadow-btn" @click="showCreate = true">
        <Plus :size="14" />
        创建文档知识
      </Button>
    </Teleport>

    <!-- KB Table -->
    <Card data-tour="knowledge-list">
      <CardContent class="p-0">
        <div v-if="kbs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无文档知识，点击「创建文档知识」开始</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">标识</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">范围</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">文档数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">同步失败</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in pagedKbs" :key="k.slug" class="border-b border-border/60">
              <td class="px-4 py-3">
                <div class="text-sm font-medium">{{ k.name }}</div>
                <div class="text-xs text-muted-foreground">{{ k.description }}</div>
              </td>
              <td class="px-4 py-3 font-mono text-xs text-muted-foreground">{{ k.slug }}</td>
              <td class="px-4 py-3">
                <div v-if="k.visibility === 'shared'">
                  <Badge variant="secondary">共享</Badge>
                  <div v-if="kbReadOnly(k)" class="mt-1 text-[10px] text-muted-foreground">{{ SHARED_RESOURCE_READ_ONLY_HINT }}</div>
                </div>
                <div v-else>
                  <Badge variant="outline">组内</Badge>
                  <div class="mt-1 font-mono text-[10px] text-muted-foreground">{{ k.owner_group_key }}</div>
                </div>
              </td>
              <td class="px-4 py-3 tabular-nums text-sm">{{ k.document_count }}</td>
              <td class="px-4 py-3 tabular-nums text-sm">
                <Badge v-if="k.sync_failed_count > 0" variant="destructive">{{ k.sync_failed_count }}</Badge>
                <span v-else class="text-muted-foreground">0</span>
              </td>
              <td class="px-4 py-3">
                <div class="flex gap-2">
                  <Button variant="outline" size="sm" @click="openDetail(k)" class="h-8 text-xs">详情</Button>
                  <Button variant="outline" size="sm" @click="openPlaneDialog(k)" :disabled="kbReadOnly(k)" :title="kbReadOnly(k) ? SHARED_RESOURCE_READ_ONLY_HINT : undefined" class="h-8 text-xs">能力平面</Button>
                  <Button variant="ghost" size="sm" class="h-8 gap-1.5 text-xs text-destructive" @click="deleteKb(k)" :disabled="kbReadOnly(k)" :title="kbReadOnly(k) ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">
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
      <div v-if="routeError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-3 text-sm text-destructive-soft-fg">
        {{ routeError }}。请<button type="button" class="underline" @click="goList">返回列表</button>。
      </div>

      <!-- Back button -->
      <div v-else class="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
          <ArrowLeft :size="14" class="mr-1.5" />
          返回
        </Button>
        <div class="flex flex-wrap gap-2">
          <Button v-if="detailKb" variant="outline" size="sm" class="h-8 text-xs" @click="openUploadDialog(detailKb)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">
            <Upload :size="12" class="mr-1" />
            上传
          </Button>
          <Button v-if="detailKb" variant="outline" size="sm" class="h-8 text-xs" @click="openPlaneDialog(detailKb)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">能力平面</Button>
        </div>
      </div>

      <!-- KB name header -->
      <div v-if="detailKb && !routeError">
        <h2 class="text-lg font-semibold text-foreground">{{ detailKb.name }}</h2>
        <p class="font-mono text-xs text-muted-foreground">{{ detailKb.slug }}</p>
      </div>

      <div v-if="detailKb && detailReadOnly" class="rounded-md border border-warning/30 bg-warning-soft px-3 py-3 text-sm text-warning-soft-fg">
        {{ SHARED_RESOURCE_READ_ONLY_HINT }}
      </div>

      <div v-if="detailKb" v-show="!detailLoading" class="space-y-4">
        <KnowledgeDefaultBackendPanel :kb="detailKb" :backends="backends" :read-only="detailReadOnly" @saved="updateKnowledgeDefaults" />
        <!-- Tabs -->
        <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
          <button v-for="t in [
            { key: 'docs', label: `文档 (${detailTotalDocumentCount})` },
            { key: 'sync', label: `同步 (${detailSyncJobs.length})` },
            { key: 'sources', label: `Git 数据源 (${detailRepoSourceCount})` },
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
              :actions-enabled="!detailReadOnly"
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
              <Button variant="destructive" size="sm" class="h-7 text-xs" @click="batchDeleteDocs" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">
                <Trash2 :size="12" class="mr-1" />批量删除
              </Button>
              <Button variant="ghost" size="sm" class="h-7 text-xs text-muted-foreground" @click="selectedDocSlugs = new Set()">取消选择</Button>
            </div>
            <div v-if="browseLoading" class="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">目录加载中...</div>
            <div v-else-if="browseError && !showingAllDocuments" class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-3 text-sm text-destructive-soft-fg">{{ browseError }}</div>
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
                    <tr v-if="entry.kind !== 'document'" class="border-b border-border/60">
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
                    <tr v-else-if="entry.kind === 'document'" class="border-b border-border/60">
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
                          <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openPlaceDialog(documentFromBrowseEntry(entry))" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">移动</Button>
                          <Button v-if="otherKnowledgeBases.length > 0" variant="ghost" size="sm" class="h-7 text-xs" @click="openAttachDialog(documentFromBrowseEntry(entry))" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">关联</Button>
                          <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteDoc(entry.slug, entry.title)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">删除</Button>
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
                <tbody><tr v-for="d in detailDocs" :key="d.slug" class="border-b border-border/60">
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
                      <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openPlaceDialog(d)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">移动</Button>
                      <Button v-if="otherKnowledgeBases.length > 0" variant="ghost" size="sm" class="h-7 text-xs" @click="openAttachDialog(d)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">关联</Button>
                      <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteDoc(d.slug, d.title)" :disabled="detailReadOnly" :title="detailReadOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">删除</Button>
                    </div>
                  </td>
                </tr></tbody>
              </table>
            </div>
          </section>
        </div>

        <KnowledgeSyncJobsPanel v-if="detailTab === 'sync'" :kb-slug="detailKb.slug" :jobs="detailSyncJobs" :on-synced="refreshKnowledgeDetail" :read-only="detailReadOnly" />

        <!-- Git 数据源由独立面板加载和维护，避免详情视图承载仓库同步状态。 -->
        <KnowledgeRepoSourcesPanel
          v-show="detailTab === 'sources'"
          :kb="detailKb"
          :read-only="detailReadOnly"
          @sources-change="detailRepoSourceCount = $event.length"
          @refresh-detail="refreshKnowledgeDetail()"
        />

        <KnowledgeSearchPanel v-if="detailTab === 'search'" :kb="detailKb" />
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
          <div class="space-y-2">
            <label class="text-sm font-medium">数据可见范围</label>
            <Select v-model="createForm.visibility"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>
              <SelectItem value="group">仅本小组</SelectItem><SelectItem value="shared">共享给所有小组</SelectItem>
            </SelectContent></Select>
            <p class="text-xs text-muted-foreground">共享后所有用户都可使用，维护仍只允许归属小组。</p>
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="createKb" :disabled="createSaving">{{ createSaving ? '创建中...' : '创建' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <KnowledgePlaneDialog v-model:open="showPlaneDialog" :kb="planeKb" :backends="backends" />

    <KnowledgeDocumentDetailDialog v-model:open="showDocDetail" :slug="docDetailSlug" />

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
          <div v-if="folderDialogError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ folderDialogError }}</div>
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
          <div v-if="placementError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ placementError }}</div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button type="button" @click="submitPlacement" :disabled="placementSaving || placementTargetFolderId == null">
            {{ placementSaving ? '保存中...' : '确定' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 上传文档对话框由独立队列与组件维护，页面仅装配目标知识库和刷新回调。 -->
    <KnowledgeUploadDialog
      :open="showUploadDialog"
      :kb="uploadKb"
      :folder-label="uploadKb?.slug === detailKb?.slug ? detailFolders.find(folder => folder.id === uploadFolderId)?.path || detailKb?.name || '根目录' : ''"
      :files="uploadFiles"
      :uploading="uploading"
      :drag-over="uploadDragOver"
      :error="uploadError"
      :failed-count="failedUploadCount"
      :retryable-count="retryableUploadCount"
      @update:open="updateUploadDialogOpen"
      @files-selected="onUploadFilesSelected"
      @drag-over="handleUploadDragOver"
      @drag-leave="handleUploadDragLeave"
      @drop="handleUploadDrop"
      @clear="clearUploadFiles"
      @upload="uploadDocuments"
    />
  </div>
</template>
