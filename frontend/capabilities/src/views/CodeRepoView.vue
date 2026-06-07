<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { api } from '../api/client'
import type { CodeRepository, CodeGraphStatus, CodeGraphNode, CodeGraphExploreResult, CodeRepoCategory, RepoOverview, UAStatus, UASummary, UAAvailability } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'

const repos = ref<CodeRepository[]>([])
const loading = ref(true)
const searchQuery = ref('')
const filterCategory = ref('')

// Categories
const categories = ref<CodeRepoCategory[]>([])

// Repo form dialog
const showRepoForm = ref(false)
const repoFormMode = ref<'add' | 'edit'>('add')
const repoForm = ref({ repo_key: '', name: '', git_url: '', branch: 'main', description: '', category_key: '' })
const repoSaving = ref(false)
const repoError = ref('')
const syncingKey = ref('')

// Repo detail dialog
const showDetail = ref(false)
const detailRepo = ref<CodeRepository | null>(null)
const detailLoading = ref(false)
const detailOverview = ref<RepoOverview | null>(null)
const detailStatus = ref<CodeGraphStatus | null>(null)
const detailQuery = ref('')
const detailResults = ref<CodeGraphNode[]>([])
const detailExploreQuery = ref('')
const detailExploreResult = ref<CodeGraphExploreResult | null>(null)
const detailExploreError = ref('')
const detailTab = ref<'overview' | 'query' | 'explore' | 'understand'>('overview')
const detailSearching = ref(false)
const detailExploring = ref(false)

// UA (Understand Anything) state
const uaStatus = ref<UAStatus | null>(null)
const uaSummary = ref<UASummary | null>(null)
const uaLoading = ref(false)
const uaAvailability = ref<UAAvailability | null>(null)
const uaAnalyzing = ref(false)
const uaAnalyzeError = ref('')
const uaAnalyzeSuccess = ref('')

onMounted(async () => {
  await Promise.all([loadRepos(), loadCategories()])
  loading.value = false
})

async function loadRepos() {
  try { repos.value = await api.listCodeRepos() } catch { repos.value = [] }
}

async function loadCategories() {
  try { categories.value = await api.listCategories() } catch { categories.value = [] }
}

function openRepoForm(mode: 'add' | 'edit', r?: CodeRepository) {
  repoFormMode.value = mode
  repoError.value = ''
  if (mode === 'edit' && r) {
    repoForm.value = {
      repo_key: r.repo_key,
      name: r.name,
      git_url: r.git_url,
      branch: r.branch,
      description: r.description || '',
      category_key: r.category_key || '',
    }
  } else {
    repoForm.value = { repo_key: '', name: '', git_url: '', branch: 'main', description: '', category_key: '' }
  }
  showRepoForm.value = true
}

async function saveRepo() {
  repoError.value = ''
  if (!repoForm.value.repo_key || !repoForm.value.name || !repoForm.value.git_url) {
    repoError.value = '请填写仓库标识、名称和 Git URL'
    return
  }
  repoSaving.value = true
  try {
    await api.upsertCodeRepo({
      repo_key: repoForm.value.repo_key,
      name: repoForm.value.name,
      git_url: repoForm.value.git_url,
      branch: repoForm.value.branch || 'main',
      description: repoForm.value.description,
      category_key: repoForm.value.category_key,
    })
    showRepoForm.value = false
    await loadRepos()
  } catch (e: any) {
    repoError.value = e.message || '保存失败'
  }
  repoSaving.value = false
}

async function syncRepo(key: string) {
  syncingKey.value = key
  try {
    await api.syncCodeRepo(key)
    await loadRepos()
  } catch { /* ignore */ }
  syncingKey.value = ''
}

async function openDetail(r: CodeRepository) {
  detailRepo.value = r
  showDetail.value = true
  detailLoading.value = true
  detailTab.value = 'overview'
  detailResults.value = []
  detailQuery.value = ''
  detailExploreQuery.value = ''
  detailExploreResult.value = null
  detailExploreError.value = ''
  uaStatus.value = null
  uaSummary.value = null
  uaAvailability.value = null
  uaAnalyzing.value = false
  uaAnalyzeError.value = ''
  uaAnalyzeSuccess.value = ''
  try {
    const [status, overview] = await Promise.allSettled([
      api.getCodeGraphStatus(),
      api.getRepoOverview(r.repo_key),
    ])
    detailStatus.value = status.status === 'fulfilled' ? status.value : null
    detailOverview.value = overview.status === 'fulfilled' ? overview.value : null
  } catch { /* ignore */ }
  detailLoading.value = false
}

async function searchInRepo() {
  const term = detailQuery.value.trim()
  if (!term || !detailRepo.value) return
  detailSearching.value = true
  try {
    const key = detailRepo.value.repo_key
    const result = await api.queryRepo(key, term)
    detailResults.value = result.matches
  } catch { detailResults.value = [] }
  detailSearching.value = false
}

async function exploreRepo() {
  const term = detailExploreQuery.value.trim()
  if (!term || !detailRepo.value) return
  detailExploring.value = true
  detailExploreError.value = ''
  detailExploreResult.value = null
  try {
    const result = await api.exploreRepo(detailRepo.value.repo_key, term)
    detailExploreResult.value = result
  } catch (e: any) {
    detailExploreError.value = e.message || 'Explore 执行失败'
  }
  detailExploring.value = false
}

const exploreMarkdownHtml = computed(() => {
  const content = detailExploreResult.value?.mcp_result?.content
  if (!Array.isArray(content)) return ''
  const textItem = content.find((c: any) => c.type === 'text' && c.text) as { text: string } | undefined
  if (!textItem) return ''
  return marked.parse(textItem.text, { async: false }) as string
})

const filteredRepos = computed(() => {
  let list = repos.value
  if (filterCategory.value) {
    list = list.filter(r => r.category_key === filterCategory.value)
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(r =>
      r.name.toLowerCase().includes(q) ||
      r.repo_key.toLowerCase().includes(q) ||
      r.git_url.toLowerCase().includes(q) ||
      (r.description || '').toLowerCase().includes(q)
    )
  }
  return list
})

function categoryName(key: string) {
  if (!key) return ''
  return categories.value.find(c => c.category_key === key)?.name || key
}

async function loadUAData() {
  if (!detailRepo.value) return
  uaLoading.value = true
  try {
    const [avail, statusResult, summaryResult] = await Promise.allSettled([
      api.checkUAAvailability(detailRepo.value.repo_key),
      api.getUAStatus(detailRepo.value.repo_key),
      api.getUASummary(detailRepo.value.repo_key),
    ])
    uaAvailability.value = avail.status === 'fulfilled' ? avail.value : null
    uaStatus.value = statusResult.status === 'fulfilled' ? statusResult.value : null
    uaSummary.value = summaryResult.status === 'fulfilled' ? summaryResult.value : null
  } catch { /* ignore */ }
  uaLoading.value = false
}

async function triggerAnalyze() {
  if (!detailRepo.value) return
  uaAnalyzing.value = true
  uaAnalyzeError.value = ''
  uaAnalyzeSuccess.value = ''
  try {
    const result = await api.triggerUAAnalyze(detailRepo.value.repo_key)
    if (result.success) {
      uaAnalyzeSuccess.value = `分析完成：${result.node_count} 节点、${result.edge_count} 边，耗时 ${(result.duration_ms / 1000).toFixed(1)}s`
      await loadUAData()
    } else {
      uaAnalyzeError.value = result.error || '分析失败'
    }
  } catch (e: any) {
    uaAnalyzeError.value = e.message || '分析失败'
  }
  uaAnalyzing.value = false
}

watch(detailTab, (tab) => {
  if (tab === 'understand' && !uaStatus.value && !uaLoading.value) {
    loadUAData()
  }
})
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <Input v-model="searchQuery" placeholder="搜索仓库名称、标识或地址..." class="pl-8" />
      </div>
      <Select v-model="filterCategory">
        <SelectTrigger class="w-[160px]">
          <SelectValue placeholder="全部分类" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">全部分类</SelectItem>
          <SelectItem v-for="c in categories" :key="c.category_key" :value="c.category_key">{{ c.name }}</SelectItem>
        </SelectContent>
      </Select>
      <Button @click="openRepoForm('add')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加仓库
      </Button>
      <Button variant="outline" @click="loadRepos()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mr-1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </Button>
    </div>

    <!-- Code Repos Table -->
    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="repos.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无代码仓库，点击「添加仓库」开始</div>
        <div v-else-if="filteredRepos.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">没有匹配的仓库</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">仓库</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">分类</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Git URL</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">分支</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">最近同步</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filteredRepos" :key="r.repo_key" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3">
                <div class="text-sm font-medium">{{ r.name }}</div>
                <div class="text-xs text-muted-foreground">{{ r.repo_key }}</div>
              </td>
              <td class="px-4 py-3">
                <Badge v-if="r.category_key" variant="secondary">{{ categoryName(r.category_key) }}</Badge>
                <span v-else class="text-xs text-muted-foreground">—</span>
              </td>
              <td class="max-w-[240px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 font-mono text-xs text-muted-foreground">{{ r.git_url }}</td>
              <td class="px-4 py-3 text-sm">{{ r.branch }}</td>
              <td class="px-4 py-3">
                <Badge v-if="r.status === 'active'" variant="secondary" class="bg-green-50 text-green-700">正常</Badge>
                <Badge v-else-if="r.status === 'error'" variant="destructive">异常</Badge>
                <Badge v-else variant="secondary">{{ r.status }}</Badge>
              </td>
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ r.last_synced_at?.slice(0, 19) || '—' }}</td>
              <td class="px-4 py-3">
                <div class="flex gap-2">
                  <Button variant="ghost" size="sm" @click="syncRepo(r.repo_key)" :disabled="syncingKey === r.repo_key" class="h-8 text-xs">
                    {{ syncingKey === r.repo_key ? '同步中...' : '同步' }}
                  </Button>
                  <Button variant="outline" size="sm" @click="openRepoForm('edit', r)" class="h-8 text-xs">编辑</Button>
                  <Button variant="outline" size="sm" @click="openDetail(r)" class="h-8 text-xs">详情</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
    <div class="text-sm text-muted-foreground">共 {{ filteredRepos.length }} / {{ repos.length }} 个仓库</div>

    <!-- Add/Edit Repo Dialog -->
    <Dialog :open="showRepoForm" @update:open="showRepoForm = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>{{ repoFormMode === 'add' ? '添加代码仓库' : '编辑代码仓库' }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveRepo" class="space-y-4">
          <div v-if="repoError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ repoError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">仓库标识 <span v-if="repoFormMode === 'add'" class="text-destructive">*</span></label>
            <Input v-if="repoFormMode === 'add'" v-model="repoForm.repo_key" placeholder="my-project" required />
            <Input v-else :model-value="repoForm.repo_key" disabled class="bg-secondary" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="repoForm.name" placeholder="我的项目" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Git URL <span v-if="repoFormMode === 'add'" class="text-destructive">*</span></label>
            <Input v-if="repoFormMode === 'add'" v-model="repoForm.git_url" placeholder="https://github.com/org/repo.git" required />
            <Input v-else :model-value="repoForm.git_url" disabled class="bg-secondary" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-2">
              <label class="text-sm font-medium">分支</label>
              <Input v-model="repoForm.branch" placeholder="main" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">分类</label>
              <Select v-model="repoForm.category_key">
                <SelectTrigger>
                  <SelectValue placeholder="无分类" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">无分类</SelectItem>
                  <SelectItem v-for="c in categories" :key="c.category_key" :value="c.category_key">{{ c.name }}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="repoForm.description" placeholder="项目代码仓库" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveRepo" :disabled="repoSaving">{{ repoSaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Repo Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[900px] max-h-[85vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <DialogTitle>{{ detailRepo?.name || '' }} 详情</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else class="space-y-4">
          <!-- Status Banner -->
          <div v-if="detailStatus && !detailStatus.codegraph_installed" class="rounded-lg bg-amber-50 p-3 text-sm text-amber-700">
            {{ detailStatus.message }}
          </div>

          <!-- Overview -->
          <div v-if="detailOverview" class="grid grid-cols-3 gap-3">
            <div class="rounded-lg border border-border p-3 text-center">
              <div class="text-2xl font-semibold tabular-nums">{{ detailOverview.file_count }}</div>
              <div class="text-xs text-muted-foreground">文件数</div>
            </div>
            <div class="rounded-lg border border-border p-3 text-center">
              <div class="text-2xl font-semibold tabular-nums">{{ detailOverview.symbol_count }}</div>
              <div class="text-xs text-muted-foreground">符号数</div>
            </div>
            <div class="rounded-lg border border-border p-3 text-center">
              <div class="text-xs text-muted-foreground">最近同步</div>
              <div class="text-sm font-medium">{{ detailOverview.last_synced_at?.slice(0, 19) || '—' }}</div>
            </div>
          </div>

          <!-- Tabs -->
          <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
            <button v-for="t in [
              { key: 'overview', label: '概览' },
              { key: 'query', label: '查询' },
              { key: 'explore', label: 'Explore' },
              { key: 'understand', label: 'Understand' },
            ]" :key="t.key"
              :class="['rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors', detailTab === t.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground']"
              @click="detailTab = t.key as any">{{ t.label }}</button>
          </div>

          <!-- Overview Tab -->
          <div v-if="detailTab === 'overview'" class="space-y-3">
            <div class="rounded-lg border border-border p-4">
              <div class="mb-3 text-sm font-semibold">仓库信息</div>
              <div class="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <div class="text-xs text-muted-foreground">仓库标识</div>
                  <div class="font-mono text-xs">{{ detailRepo?.repo_key }}</div>
                </div>
                <div>
                  <div class="text-xs text-muted-foreground">分支</div>
                  <div>{{ detailRepo?.branch }}</div>
                </div>
                <div class="sm:col-span-2">
                  <div class="text-xs text-muted-foreground">Git URL</div>
                  <div class="break-all font-mono text-xs">{{ detailRepo?.git_url }}</div>
                </div>
                <div v-if="detailRepo?.description" class="sm:col-span-2">
                  <div class="text-xs text-muted-foreground">描述</div>
                  <div>{{ detailRepo.description }}</div>
                </div>
              </div>
            </div>
            <div v-if="detailRepo?.last_error" class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              <div class="mb-1 font-semibold">同步错误</div>
              <div class="whitespace-pre-wrap break-words">{{ detailRepo.last_error }}</div>
            </div>
          </div>

          <!-- Query Tab -->
          <div v-if="detailTab === 'query'" class="space-y-3">
            <div class="flex gap-2">
              <Input v-model="detailQuery" placeholder="输入符号名或搜索词" class="flex-1" @keydown.enter="searchInRepo()" />
              <Button @click="searchInRepo()" :disabled="detailSearching || !detailQuery.trim()" size="sm">搜索</Button>
            </div>
            <div v-if="detailSearching" class="py-4 text-center text-sm text-muted-foreground">查询中...</div>
            <div v-else-if="detailResults.length > 0" class="max-h-[300px] overflow-y-auto rounded-lg border border-border">
              <table class="w-full table-fixed">
                <thead><tr class="border-b border-border bg-secondary/50">
                  <th class="w-[20%] px-3 py-2 text-left text-xs font-semibold text-muted-foreground">符号</th>
                  <th class="w-[12%] px-3 py-2 text-left text-xs font-semibold text-muted-foreground">类型</th>
                  <th class="w-[55%] px-3 py-2 text-left text-xs font-semibold text-muted-foreground">文件</th>
                  <th class="w-[13%] px-3 py-2 text-left text-xs font-semibold text-muted-foreground">行号</th>
                </tr></thead>
                <tbody><tr v-for="r in detailResults" :key="r.symbol + r.path" class="border-b border-border/40 hover:bg-secondary/30">
                  <td class="px-3 py-1.5 text-sm font-medium truncate" :title="r.symbol">{{ r.symbol }}</td>
                  <td class="px-3 py-1.5"><Badge variant="secondary" class="text-[11px]">{{ r.kind }}</Badge></td>
                  <td class="px-3 py-1.5 font-mono text-xs text-muted-foreground truncate" :title="r.path">{{ r.path }}</td>
                  <td class="px-3 py-1.5 text-xs tabular-nums">{{ r.line_start || '—' }}</td>
                </tr></tbody>
              </table>
            </div>
          </div>

          <!-- Explore Tab -->
          <div v-if="detailTab === 'explore'" class="space-y-3">
            <div class="flex gap-2">
              <Input v-model="detailExploreQuery" placeholder="输入要交给 CodeGraph Explore 的问题" class="flex-1" @keydown.enter="exploreRepo()" />
              <Button @click="exploreRepo()" :disabled="detailExploring || !detailExploreQuery.trim()" size="sm">
                {{ detailExploring ? '执行中...' : '执行' }}
              </Button>
            </div>
            <div v-if="detailExploreError" class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {{ detailExploreError }}
            </div>
            <div v-if="detailExploring" class="py-4 text-center text-sm text-muted-foreground">执行中...</div>
            <div v-else-if="detailExploreResult" class="space-y-3">
              <div v-if="detailExploreResult.mcp_result.structured" class="rounded-lg border border-border p-3">
                <div class="mb-2 text-xs font-medium text-muted-foreground">Structured</div>
                <pre class="max-h-[260px] overflow-auto rounded-md bg-secondary p-3 text-xs leading-5">{{ JSON.stringify(detailExploreResult.mcp_result.structured, null, 2) }}</pre>
              </div>
              <div class="rounded-lg border border-border p-3">
                <div class="mb-2 text-xs font-medium text-muted-foreground">Content</div>
                <div v-if="exploreMarkdownHtml" class="prose prose-sm max-w-none max-h-[500px] overflow-y-auto" v-html="exploreMarkdownHtml"></div>
                <pre v-else class="max-h-[260px] overflow-auto rounded-md bg-secondary p-3 text-xs leading-5">{{ JSON.stringify(detailExploreResult.mcp_result.content, null, 2) }}</pre>
              </div>
            </div>
          </div>

          <!-- Understand Tab -->
          <div v-if="detailTab === 'understand'" class="space-y-3">
            <div v-if="uaLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
            <template v-else>
              <!-- Availability Check -->
              <div v-if="uaAvailability && !uaAvailability.ua_skill_available && !uaAvailability.ua_git_url_configured" class="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700">
                <div class="font-medium">Understand Anything 不可用</div>
                <div class="mt-1">请在「知识处理配置」页面填写 UA Git URL 以启用自动安装。</div>
              </div>
              <div v-else-if="uaAvailability && !uaAvailability.ua_skill_available && uaAvailability.ua_git_url_configured" class="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-700 flex items-center justify-between">
                <span>UA 技能未安装，将在运行分析时自动安装</span>
                <Button size="sm" @click="triggerAnalyze" :disabled="uaAnalyzing">
                  {{ uaAnalyzing ? '安装并分析中...' : '安装并分析' }}
                </Button>
              </div>
              <div v-else-if="uaAvailability && uaAvailability.ua_skill_available" class="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-700 flex items-center justify-between">
                <span>Understand Anything 技能已就绪</span>
                <Button size="sm" @click="triggerAnalyze" :disabled="uaAnalyzing">
                  {{ uaAnalyzing ? '分析中...' : '运行分析' }}
                </Button>
              </div>

              <!-- Analyze Result -->
              <div v-if="uaAnalyzeSuccess" class="rounded-lg bg-green-50 p-3 text-sm text-green-700">{{ uaAnalyzeSuccess }}</div>
              <div v-if="uaAnalyzeError" class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{{ uaAnalyzeError }}</div>

              <!-- Status Banner -->
              <div v-if="!uaStatus?.graph_exists" class="rounded-lg border border-border bg-secondary/50 p-4 text-center">
                <div class="text-sm text-muted-foreground">暂无知识图谱</div>
                <div class="mt-1 text-xs text-muted-foreground">可通过 Understand Anything 技能生成</div>
              </div>
              <template v-else>
                <div v-if="uaStatus?.stale" class="rounded-lg bg-amber-50 p-3 text-sm text-amber-700">
                  图谱可能已过期（commit 不匹配）
                </div>
                <div v-else class="rounded-lg bg-green-50 p-3 text-sm text-green-700">
                  知识图谱可用
                </div>
                <div class="grid grid-cols-4 gap-2">
                  <div class="rounded-lg border border-border p-2.5 text-center">
                    <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.node_count || 0 }}</div>
                    <div class="text-[11px] text-muted-foreground">节点</div>
                  </div>
                  <div class="rounded-lg border border-border p-2.5 text-center">
                    <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.edge_count || 0 }}</div>
                    <div class="text-[11px] text-muted-foreground">边</div>
                  </div>
                  <div class="rounded-lg border border-border p-2.5 text-center">
                    <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.layer_count || 0 }}</div>
                    <div class="text-[11px] text-muted-foreground">层</div>
                  </div>
                  <div class="rounded-lg border border-border p-2.5 text-center">
                    <div class="text-lg font-semibold tabular-nums">{{ uaStatus?.tour_count || 0 }}</div>
                    <div class="text-[11px] text-muted-foreground">导览</div>
                  </div>
                </div>

                <!-- Summary -->
                <div v-if="uaSummary" class="space-y-3">
                  <div v-if="uaSummary.description" class="rounded-lg border border-border p-4">
                    <div class="text-sm text-muted-foreground">{{ uaSummary.description }}</div>
                  </div>
                  <div v-if="uaSummary.languages.length || uaSummary.frameworks.length" class="flex flex-wrap gap-1.5">
                    <Badge v-for="lang in uaSummary.languages" :key="lang" variant="secondary" class="bg-blue-50 text-blue-700">{{ lang }}</Badge>
                    <Badge v-for="fw in uaSummary.frameworks" :key="fw" variant="secondary" class="bg-green-50 text-green-700">{{ fw }}</Badge>
                  </div>
                  <div v-if="uaSummary.modules.length" class="rounded-lg border border-border">
                    <div class="border-b border-border bg-secondary/50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">主要模块</div>
                    <div v-for="m in uaSummary.modules" :key="m.name" class="border-b border-border/40 px-4 py-2.5 last:border-b-0">
                      <div class="text-sm font-medium">{{ m.name }}</div>
                      <div v-if="m.summary" class="text-xs text-muted-foreground">{{ m.summary }}</div>
                    </div>
                  </div>
                  <div v-if="uaSummary.tours.length" class="rounded-lg border border-border">
                    <div class="border-b border-border bg-secondary/50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">导览</div>
                    <div v-for="t in uaSummary.tours" :key="t.title" class="px-4 py-2.5">
                      <div class="text-sm font-medium">{{ t.title }}</div>
                      <div class="text-xs text-muted-foreground">{{ t.step_count }} 步 · {{ t.description }}</div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Diagnostics -->
              <details v-if="uaStatus?.graph_exists" class="rounded-lg border border-border">
                <summary class="cursor-pointer px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">诊断信息</summary>
                <div class="border-t border-border px-4 py-3 text-xs text-muted-foreground space-y-1">
                  <div v-if="uaStatus?.analyzed_at">分析时间: {{ uaStatus.analyzed_at }}</div>
                  <div v-if="uaStatus?.git_commit">分析 commit: <span class="font-mono">{{ uaStatus.git_commit?.slice(0, 12) }}</span></div>
                  <div v-if="uaStatus?.analyzed_files != null">分析文件数: {{ uaStatus.analyzed_files }}</div>
                  <div v-if="uaStatus?.graph_path">图谱路径: <span class="font-mono text-[11px]">{{ uaStatus.graph_path }}</span></div>
                  <div v-if="uaStatus?.error" class="text-red-600">错误: {{ uaStatus.error }}</div>
                </div>
              </details>
            </template>
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">关闭</Button></DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
