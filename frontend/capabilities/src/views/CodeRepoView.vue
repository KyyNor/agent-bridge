<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { CodeRepository, CodeGraphStatus, CodeGraphNode, RepoOverview } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'

const repos = ref<CodeRepository[]>([])
const loading = ref(true)

// Add repo dialog
const showAddRepo = ref(false)
const repoForm = ref({ repo_key: '', name: '', git_url: '', branch: 'main', description: '' })
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
const detailTab = ref<'overview' | 'query'>('overview')
const detailSearching = ref(false)

onMounted(async () => {
  await loadRepos()
  loading.value = false
})

async function loadRepos() {
  try { repos.value = await api.listCodeRepos() } catch { repos.value = [] }
}

async function addRepo() {
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
    })
    showAddRepo.value = false
    repoForm.value = { repo_key: '', name: '', git_url: '', branch: 'main', description: '' }
    await loadRepos()
  } catch (e: any) {
    repoError.value = e.message || '添加失败'
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
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <Button @click="showAddRepo = true">
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
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">仓库</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Git URL</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">分支</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">最近同步</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in repos" :key="r.repo_key" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3">
                <div class="text-sm font-medium">{{ r.name }}</div>
                <div class="text-xs text-muted-foreground">{{ r.repo_key }}</div>
              </td>
              <td class="max-w-[280px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 font-mono text-xs text-muted-foreground">{{ r.git_url }}</td>
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
                  <Button variant="outline" size="sm" @click="openDetail(r)" class="h-8 text-xs">详情</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
    <div class="text-sm text-muted-foreground">共 {{ repos.length }} 个仓库</div>

    <!-- Add Repo Dialog -->
    <Dialog :open="showAddRepo" @update:open="showAddRepo = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>添加代码仓库</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="addRepo" class="space-y-4">
          <div v-if="repoError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ repoError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">仓库标识 <span class="text-destructive">*</span></label>
            <Input v-model="repoForm.repo_key" placeholder="my-project" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="repoForm.name" placeholder="我的项目" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Git URL <span class="text-destructive">*</span></label>
            <Input v-model="repoForm.git_url" placeholder="https://github.com/org/repo.git" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">分支</label>
            <Input v-model="repoForm.branch" placeholder="main" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="repoForm.description" placeholder="项目代码仓库" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="addRepo" :disabled="repoSaving">{{ repoSaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Repo Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[700px] max-h-[85vh] overflow-y-auto">
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
              <table class="w-full">
                <thead><tr class="border-b border-border bg-secondary/50">
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">符号</th>
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">类型</th>
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">文件</th>
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">行号</th>
                </tr></thead>
                <tbody><tr v-for="r in detailResults" :key="r.symbol + r.path" class="border-b border-border/40 hover:bg-secondary/30">
                  <td class="px-3 py-1.5 text-sm font-medium">{{ r.symbol }}</td>
                  <td class="px-3 py-1.5"><Badge variant="secondary" class="text-[11px]">{{ r.kind }}</Badge></td>
                  <td class="px-3 py-1.5 font-mono text-xs text-muted-foreground">{{ r.path }}</td>
                  <td class="px-3 py-1.5 text-xs tabular-nums">{{ r.line_start || '—' }}</td>
                </tr></tbody>
              </table>
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
