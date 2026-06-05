<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import type { CodeRepository, KnowledgeBaseSummary } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'

const repos = ref<CodeRepository[]>([])
const kbs = ref<KnowledgeBaseSummary[]>([])
const loading = ref(true)
const tab = ref<'repos' | 'kbs'>('repos')

// Add repo dialog
const showAddRepo = ref(false)
const repoForm = ref({ repo_key: '', name: '', git_url: '', branch: 'main', description: '' })
const repoSaving = ref(false)
const repoError = ref('')
const syncingKey = ref('')

onMounted(async () => {
  await Promise.all([loadRepos(), loadKbs()])
  loading.value = false
})

async function loadRepos() {
  try { repos.value = await api.listCodeRepos() } catch { repos.value = [] }
}

async function loadKbs() {
  try { kbs.value = await api.listWikiKbs() } catch { kbs.value = [] }
}

const filterTabs = computed(() => [
  { key: 'repos' as const, label: '代码仓库', count: repos.value.length },
  { key: 'kbs' as const, label: '知识库', count: kbs.value.length },
])

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
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="t in filterTabs" :key="t.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            tab === t.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="tab = t.key"
        >{{ t.label }} <span class="font-normal text-muted-foreground">{{ t.count }}</span></button>
      </div>
      <Button v-if="tab === 'repos'" @click="showAddRepo = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加仓库
      </Button>
      <Button variant="outline" @click="tab === 'repos' ? loadRepos() : loadKbs()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mr-1.5"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </Button>
    </div>

    <!-- Code Repos Table -->
    <template v-if="tab === 'repos'">
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
                  <Button variant="ghost" size="sm" @click="syncRepo(r.repo_key)" :disabled="syncingKey === r.repo_key" class="h-8 text-xs">
                    {{ syncingKey === r.repo_key ? '同步中...' : '同步' }}
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>
      <div class="text-sm text-muted-foreground">共 {{ repos.length }} 个仓库</div>
    </template>

    <!-- KB Table -->
    <template v-if="tab === 'kbs'">
      <Card class="border-border">
        <CardContent class="p-0">
          <div v-if="kbs.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无知识库</div>
          <table v-else class="w-full">
            <thead>
              <tr class="border-b border-border bg-secondary/50">
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">知识库</th>
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">文档数</th>
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">成员数</th>
                <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">后端状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="k in kbs" :key="k.slug" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
                <td class="px-4 py-3">
                  <div class="text-sm font-medium">{{ k.name }}</div>
                  <div class="text-xs text-muted-foreground">{{ k.slug }}</div>
                </td>
                <td class="px-4 py-3 tabular-nums">{{ k.doc_count }}</td>
                <td class="px-4 py-3 tabular-nums">{{ k.member_count }}</td>
                <td class="px-4 py-3">
                  <div class="flex flex-wrap gap-1">
                    <Badge v-for="bt in k.backend_targets" :key="bt.slug" variant="secondary" class="text-[11px]"
                      :class="bt.status === 'active' ? 'bg-green-50 text-green-700' : 'text-muted-foreground'">
                      {{ bt.type }}: {{ bt.slug }}
                    </Badge>
                    <span v-if="!k.backend_targets?.length" class="text-xs text-muted-foreground">—</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>
      <div class="text-sm text-muted-foreground">共 {{ kbs.length }} 个知识库</div>
    </template>

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
  </div>
</template>
