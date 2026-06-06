<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { KnowledgeBaseSummary, Document, KbMember, SyncJob, SearchResultChunk } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'

const kbs = ref<KnowledgeBaseSummary[]>([])
const loading = ref(true)
const showCreate = ref(false)
const createForm = ref({ slug: '', name: '', description: '' })
const createSaving = ref(false)
const createError = ref('')

// Detail dialog
const showDetail = ref(false)
const detailKb = ref<KnowledgeBaseSummary | null>(null)
const detailTab = ref<'docs' | 'members' | 'sync' | 'search'>('docs')
const detailDocs = ref<Document[]>([])
const detailMembers = ref<KbMember[]>([])
const detailSyncJobs = ref<SyncJob[]>([])
const detailLoading = ref(false)
// Upload state
const uploadFile = ref<File | null>(null)
const uploadLater = ref(false)
const uploading = ref(false)
// Member grant
const memberUser = ref('')
const memberRole = ref('viewer')
const grantingMember = ref(false)
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

onMounted(async () => {
  await loadKbs()
  loading.value = false
})

async function loadKbs() {
  try { kbs.value = await api.listWikiKbs() } catch { kbs.value = [] }
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
  detailLoading.value = true
  searchResults.value = []
  askAnswer.value = ''
  askChunks.value = []
  try {
    const [docs, members, syncStatus] = await Promise.allSettled([
      api.listDocs(kb.slug),
      api.listKbMembers(kb.slug),
      api.getSyncStatus(),
    ])
    detailDocs.value = docs.status === 'fulfilled' ? docs.value : []
    detailMembers.value = members.status === 'fulfilled' ? members.value : []
    detailSyncJobs.value = syncStatus.status === 'fulfilled' ? syncStatus.value.jobs.filter((j: SyncJob) => j.kb_slug === kb.slug) : []
  } catch { /* ignore */ }
  detailLoading.value = false
}

async function uploadDocument() {
  if (!uploadFile.value || !detailKb.value) return
  uploading.value = true
  try {
    await api.addDocument(uploadFile.value, [detailKb.value.slug], uploadLater.value)
    uploadFile.value = null
    uploadLater.value = false
    detailDocs.value = await api.listDocs(detailKb.value.slug)
  } catch { /* ignore */ }
  uploading.value = false
}

async function deleteDoc(slug: string) {
  if (!detailKb.value) return
  try {
    await api.deleteDocument(slug)
    detailDocs.value = await api.listDocs(detailKb.value.slug)
  } catch { /* ignore */ }
}

async function grantMember() {
  if (!detailKb.value || !memberUser.value.trim()) return
  grantingMember.value = true
  try {
    await api.grantKbMember(detailKb.value.slug, { linux_user: memberUser.value.trim(), role: memberRole.value })
    memberUser.value = ''
    detailMembers.value = await api.listKbMembers(detailKb.value.slug)
  } catch { /* ignore */ }
  grantingMember.value = false
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

function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement
  uploadFile.value = target.files?.[0] || null
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
    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="kbs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无文档知识，点击「创建文档知识」开始</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">名称</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">标识</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">文档数</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">同步失败</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">后端状态</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">角色</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in kbs" :key="k.slug" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
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
                <div class="flex flex-wrap gap-1">
                  <Badge v-for="bt in k.backend_targets" :key="bt.slug" variant="secondary" class="text-[11px]"
                    :class="bt.status === 'active' ? 'bg-green-50 text-green-700' : 'text-muted-foreground'">
                    {{ bt.backend_type }}: {{ bt.slug }}
                  </Badge>
                  <span v-if="!k.backend_targets?.length" class="text-xs text-muted-foreground">—</span>
                </div>
              </td>
              <td class="px-4 py-3">
                <Badge variant="secondary" class="text-[11px]">{{ k.role }}</Badge>
              </td>
              <td class="px-4 py-3">
                <Button variant="outline" size="sm" @click="openDetail(k)" class="h-8 text-xs">详情</Button>
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

    <!-- KB Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[800px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{{ detailKb?.name || '' }}</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else class="space-y-4">
          <!-- Tabs -->
          <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
            <button v-for="t in [
              { key: 'docs', label: `文档 (${detailDocs.length})` },
              { key: 'members', label: `访问权限 (${detailMembers.length})` },
              { key: 'sync', label: `同步 (${detailSyncJobs.length})` },
              { key: 'search', label: '检索' },
            ]" :key="t.key"
              :class="['rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors', detailTab === t.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground']"
              @click="detailTab = t.key as any">{{ t.label }}</button>
          </div>

          <!-- Documents Tab -->
          <div v-if="detailTab === 'docs'" class="space-y-3">
            <div class="flex items-center gap-3">
              <input type="file" @change="onFileSelected" class="text-sm" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md" />
              <label class="flex items-center gap-1.5 text-sm text-muted-foreground">
                <input type="checkbox" v-model="uploadLater" /> 稍后同步
              </label>
              <Button size="sm" @click="uploadDocument" :disabled="!uploadFile || uploading">{{ uploading ? '上传中...' : '上传' }}</Button>
            </div>
            <div v-if="detailDocs.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无文档</div>
            <table v-else class="w-full">
              <thead><tr class="border-b border-border bg-secondary/50">
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">标题</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">上传者</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">版本</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">状态</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground"></th>
              </tr></thead>
              <tbody><tr v-for="d in detailDocs" :key="d.slug" class="border-b border-border/40 hover:bg-secondary/30">
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

          <!-- Members Tab -->
          <div v-if="detailTab === 'members'" class="space-y-3">
            <div class="text-xs text-muted-foreground">管理此知识库的访问权限，指定用户可查看、上传文档或管理知识库。</div>
            <div class="flex items-center gap-3">
              <Input v-model="memberUser" placeholder="用户名" class="w-40" />
              <select v-model="memberRole" class="h-9 rounded-md border border-border bg-background px-3 text-sm">
                <option value="viewer">查看者</option>
                <option value="contributor">贡献者</option>
                <option value="admin">管理员</option>
              </select>
              <Button size="sm" @click="grantMember" :disabled="!memberUser.trim() || grantingMember">{{ grantingMember ? '添加中...' : '授权' }}</Button>
            </div>
            <table class="w-full">
              <thead><tr class="border-b border-border bg-secondary/50">
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">用户</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">角色</th>
              </tr></thead>
              <tbody><tr v-for="m in detailMembers" :key="m.linux_user" class="border-b border-border/40">
                <td class="px-3 py-2 text-sm">{{ m.linux_user }}</td>
                <td class="px-3 py-2"><Badge variant="secondary" class="text-[11px]">{{ m.role }}</Badge></td>
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
              <thead><tr class="border-b border-border bg-secondary/50">
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">文档</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">操作</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">状态</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">后端</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">错误</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">时间</th>
              </tr></thead>
              <tbody><tr v-for="j in detailSyncJobs" :key="j.id" class="border-b border-border/40 hover:bg-secondary/30">
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
                <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ j.updated_at?.slice(0, 19) }}</td>
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
  </div>
</template>
