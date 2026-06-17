<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary, Document, SyncJob, SearchResultChunk, ProjectProfile, BackendInfo, BackendAgent } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'

const kbs = ref<KnowledgeBaseSummary[]>([])
const loading = ref(true)
const showCreate = ref(false)
const createForm = ref({ slug: '', name: '', description: '' })
const createSaving = ref(false)
const createError = ref('')

// Detail dialog
const showDetail = ref(false)
const detailKb = ref<KnowledgeBaseSummary | null>(null)
const detailTab = ref<'docs' | 'sync' | 'search'>('docs')
const detailDocs = ref<Document[]>([])
const detailSyncJobs = ref<SyncJob[]>([])
const detailLoading = ref(false)
// Sync
const syncing = ref(false)
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

// Plane assignment dialog
const showPlaneDialog = ref(false)
const planeKb = ref<KnowledgeBaseSummary | null>(null)

// 上传对话框
const showUploadDialog = ref(false)
const uploadKb = ref<KnowledgeBaseSummary | null>(null)
const uploadFiles = ref<File[]>([])
const uploading = ref(false)
const uploadDragOver = ref(false)
const ALLOWED_DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md']
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
})

async function loadKbs() {
  try { kbs.value = await api.listWikiKbs() } catch { kbs.value = [] }
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

async function openDetail(kb: KnowledgeBaseSummary) {
  detailKb.value = kb
  showDetail.value = true
  detailTab.value = 'docs'
  editingDefaultBackend.value = false
  defaultBackendSlug.value = kb.default_backend_slug || ''
  defaultAgentId.value = kb.default_agent_id || ''
  detailAgents.value = []
  detailLoading.value = true
  searchResults.value = []
  askAnswer.value = ''
  askChunks.value = []
  try {
    const [docs, syncStatus] = await Promise.allSettled([
      api.listDocs(kb.slug),
      api.getSyncStatus(),
    ])
    detailDocs.value = docs.status === 'fulfilled' ? docs.value : []
    detailSyncJobs.value = syncStatus.status === 'fulfilled' ? syncStatus.value.jobs.filter((j: SyncJob) => j.kb_slug === kb.slug) : []
    if (isWeknoraBackend(kb.default_backend_slug)) {
      detailAgents.value = await loadAgentsForBackend(kb.default_backend_slug as string)
    }
  } catch { /* ignore */ }
  detailLoading.value = false
}

async function deleteDoc(slug: string) {
  if (!detailKb.value) return
  try {
    await api.deleteDocument(slug)
    detailDocs.value = await api.listDocs(detailKb.value.slug)
  } catch { /* ignore */ }
}

async function triggerSync() {
  syncing.value = true
  try {
    await api.triggerSync()
    if (detailKb.value) {
      const status = await api.getSyncStatus()
      detailSyncJobs.value = status.jobs.filter((j: SyncJob) => j.kb_slug === detailKb.value!.slug)
    }
  } catch { /* ignore */ }
  syncing.value = false
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

function onUploadFilesSelected(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    const allowed = ALLOWED_DOC_EXTENSIONS
    for (let i = 0; i < target.files.length; i++) {
      const f = target.files[i]
      const ext = '.' + f.name.split('.').pop()?.toLowerCase()
      if (allowed.includes(ext)) uploadFiles.value.push(f)
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
      const ext = '.' + f.name.split('.').pop()?.toLowerCase()
      if (allowed.includes(ext)) uploadFiles.value.push(f)
    }
    return
  }
  entries.forEach(entry => traverseEntry(entry, allowed))
}

function traverseEntry(entry: FileSystemEntry, allowed: string[]) {
  if (entry.isFile) {
    const ext = '.' + entry.name.split('.').pop()?.toLowerCase()
    if (!allowed.includes(ext)) return
    ;(entry as FileSystemFileEntry).file(f => uploadFiles.value.push(f))
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    const readAll = () => {
      reader.readEntries(entries => {
        if (entries.length === 0) return
        entries.forEach(e => traverseEntry(e, allowed))
        readAll()
      })
    }
    readAll()
  }
}

function openUploadDialog(kb: KnowledgeBaseSummary) {
  uploadKb.value = kb
  uploadFiles.value = []
  showUploadDialog.value = true
}

async function uploadDocuments() {
  if (!uploadKb.value || uploadFiles.value.length === 0) return
  uploading.value = true
  try {
    for (const file of uploadFiles.value) {
      await api.addDocument(file, [uploadKb.value.slug], true)
    }
    showUploadDialog.value = false
    uploadFiles.value = []
    await loadKbs()
  } catch { /* ignore */ }
  uploading.value = false
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
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <Button @click="showCreate = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        创建文档知识
      </Button>
      <Button variant="outline" @click="loadKbs()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mr-1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </Button>
    </div>

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
            <tr v-for="k in kbs" :key="k.slug" class="border-b border-border/60 transition-colors hover:bg-muted/50">
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
                  <Button size="sm" @click="openUploadDialog(k)" class="h-8 text-xs">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    上传
                  </Button>
                  <Button variant="outline" size="sm" @click="openDetail(k)" class="h-8 text-xs">详情</Button>
                  <Button variant="outline" size="sm" @click="openPlaneDialog(k)" class="h-8 text-xs">能力平面</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
    <div class="text-sm text-muted-foreground">共 {{ kbs.length }} 个文档知识</div>

    <!-- Create KB Dialog -->
    <Dialog :open="showCreate" @update:open="showCreate = $event">
      <DialogContent class="sm:max-w-[450px]">
        <DialogHeader>
          <DialogTitle>创建文档知识</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createKb" class="space-y-4">
          <div v-if="createError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ createError }}</div>
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

    <!-- KB Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[800px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{{ detailKb?.name || '' }}</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else class="space-y-4">
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
              { key: 'docs', label: `文档 (${detailDocs.length})` },
              { key: 'sync', label: `同步 (${detailSyncJobs.length})` },
              { key: 'search', label: '检索' },
            ]" :key="t.key"
              :class="['rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors', detailTab === t.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground']"
              @click="detailTab = t.key as any">{{ t.label }}</button>
          </div>

          <!-- Documents Tab -->
          <div v-if="detailTab === 'docs'" class="space-y-3">
            <div class="text-xs text-muted-foreground">点击知识库列表中的「上传」按钮添加文档，上传后由定时任务自动同步</div>
            <div v-if="detailDocs.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无文档</div>
            <table v-else class="w-full">
              <thead><tr class="border-b border-border">
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标题</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">上传者</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">版本</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground"></th>
              </tr></thead>
              <tbody><tr v-for="d in detailDocs" :key="d.slug" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                <td class="px-3 py-2 text-sm font-medium">{{ d.title }}</td>
                <td class="px-3 py-2 text-xs text-muted-foreground">{{ d.owner_user }}</td>
                <td class="px-3 py-2 text-xs tabular-nums">v{{ d.current_version_no || 0 }}</td>
                <td class="px-3 py-2">
                  <Badge variant="secondary" class="text-[11px]"
                    :class="d.sync_status === 'synced' ? 'bg-green-50 text-green-700' : d.sync_status === 'sync_failed' ? 'bg-red-50 text-red-700' : ''">
                    {{ d.sync_status || d.status }}
                  </Badge>
                </td>
                <td class="px-3 py-2">
                  <Button variant="ghost" size="sm" class="h-7 text-xs text-red-600 hover:text-red-700" @click="deleteDoc(d.slug)">删除</Button>
                </td>
              </tr></tbody>
            </table>
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
                  <Badge variant="secondary" class="text-[11px]"
                    :class="j.status === 'succeeded' ? 'bg-green-50 text-green-700' : j.status === 'failed' ? 'bg-red-50 text-red-700' : ''">
                    {{ j.status }}
                  </Badge>
                </td>
                <td class="px-3 py-2 text-xs text-muted-foreground">{{ j.backend_slug }}</td>
                <td class="px-3 py-2 max-w-[200px] overflow-hidden text-ellipsis text-xs text-red-600" :title="j.error ?? ''">{{ j.error || '—' }}</td>
                <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ formatLocalDatetime(j.updated_at) }}</td>
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
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">关闭</Button></DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 上传文档对话框 -->
    <Dialog :open="showUploadDialog" @update:open="showUploadDialog = $event">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>上传文档 — {{ uploadKb?.name || '' }}</DialogTitle>
        </DialogHeader>
        <div class="space-y-4">
          <div class="text-xs text-muted-foreground">
            目标知识库：<span class="font-medium text-foreground">{{ uploadKb?.name }}</span>
            <span class="font-mono ml-1">({{ uploadKb?.slug }})</span>
          </div>

          <!-- 拖拽区域 -->
          <div v-if="uploadFiles.length === 0"
            :class="['rounded-lg border-2 border-dashed p-10 text-center transition-colors cursor-pointer',
              uploadDragOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/20']"
            @dragover="handleUploadDragOver"
            @dragleave="handleUploadDragLeave"
            @drop="handleUploadDrop"
          >
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="1.5" class="mx-auto mb-3"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            <div class="text-sm font-medium mb-1">拖拽文件或文件夹到此处</div>
            <div class="text-xs text-muted-foreground mb-4">支持 PDF、Word、Excel、PPT、TXT、Markdown — 上传后将由定时任务自动同步</div>
            <div class="flex items-center justify-center gap-3">
              <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm bg-primary text-primary-foreground text-sm font-medium cursor-pointer hover:bg-primary/80">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                选择文件
                <input type="file" multiple class="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md" @change="onUploadFilesSelected" />
              </label>
              <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm border border-border bg-background text-sm font-medium cursor-pointer hover:bg-muted">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                选择文件夹
                <input type="file" multiple webkitdirectory class="hidden" @change="onUploadFilesSelected" />
              </label>
            </div>
          </div>

          <!-- 文件列表 -->
          <div v-else class="rounded-lg border-2 border-green-200 bg-muted/20 p-4">
            <div class="flex items-center justify-between mb-3">
              <span class="text-sm font-medium">已选择 <span class="text-green-700">{{ uploadFiles.length }}</span> 个文件</span>
              <Button variant="ghost" size="xs" class="h-7 text-xs text-muted-foreground" @click="uploadFiles = []">清除</Button>
            </div>
            <div class="space-y-1.5 max-h-[240px] overflow-y-auto">
              <div v-for="(f, i) in uploadFiles" :key="i"
                class="flex items-center gap-2.5 px-3 py-2 rounded border border-border bg-background text-sm"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <span class="flex-1 truncate">{{ f.name }}</span>
                <span class="text-xs text-muted-foreground shrink-0">{{ getFileSizeLabel(f.size) }}</span>
              </div>
            </div>
            <label class="block mt-3 py-2 border border-dashed border-border rounded text-center text-xs text-muted-foreground cursor-pointer hover:bg-muted/50 transition-colors">
              + 继续添加文件
              <input type="file" multiple class="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md" @change="onUploadFilesSelected" />
            </label>
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button @click="uploadDocuments" :disabled="uploadFiles.length === 0 || uploading">
            {{ uploading ? '上传中...' : `上传 (${uploadFiles.length})` }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
