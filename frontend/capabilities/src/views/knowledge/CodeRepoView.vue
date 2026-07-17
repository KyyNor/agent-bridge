<script setup lang="ts">
import { Search, Plus, RotateCw, Trash2 } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { CodeRepoCategory, CodeRepository, ProjectProfile, TestCloneResult } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import PaginationBar from '../../components/PaginationBar.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'
import { confirm, alert } from '../../composables/useConfirm'
import CodeRepoDetailView from './CodeRepoDetailView.vue'

const props = defineProps<{ routeKey: string }>()
const mode = computed<'list' | 'detail'>(() => (props.routeKey ? 'detail' : 'list'))

const repos = ref<CodeRepository[]>([])
const loading = ref(true)
const searchQuery = ref('')
const filterCategory = ref('__all__')
const page = ref(1)
const pageSize = ref(10)

// Categories
const categories = ref<CodeRepoCategory[]>([])

// Repo form dialog
const showRepoForm = ref(false)
const repoFormMode = ref<'add' | 'edit'>('add')
const repoForm = ref({ repo_key: '', name: '', git_url: '', branch: 'main', description: '', category_key: '__none__', auto_understand: false })
const repoSaving = ref(false)
const repoError = ref('')
const syncingKey = ref('')

// Clone auth
const authType = ref<'none' | 'username_password' | 'token'>('none')
const authUsername = ref('')
const authPassword = ref('')
const authToken = ref('')
const testCloneResult = ref<TestCloneResult | null>(null)
const testCloning = ref(false)
const editingHasAuth = ref(false)

const detailRepo = computed(() => repos.value.find(r => r.repo_key === props.routeKey) || null)

// Plane assignment dialog
const showPlaneDialog = ref(false)
const planeRepo = ref<CodeRepository | null>(null)
const allProfiles = ref<ProjectProfile[]>([])
const selectedProfileKeys = ref<string[]>([])
const pendingProfileKeys = ref<string[]>([])
const planeSaving = ref(false)

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
  authType.value = 'none'
  authUsername.value = ''
  authPassword.value = ''
  authToken.value = ''
  testCloneResult.value = null
  editingHasAuth.value = false
  if (mode === 'edit' && r) {
    repoForm.value = {
      repo_key: r.repo_key,
      name: r.name,
      git_url: r.git_url,
      branch: r.branch,
      description: r.description || '',
      category_key: r.category_key || '__none__',
      auto_understand: Boolean(r.auto_understand),
    }
    editingHasAuth.value = r.has_auth_ref || false
  } else {
    repoForm.value = { repo_key: '', name: '', git_url: '', branch: 'main', description: '', category_key: '__none__', auto_understand: false }
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
    let authRef = ''
    if (authType.value === 'username_password' && authUsername.value) {
      authRef = JSON.stringify({
        type: 'username_password',
        username: authUsername.value,
        password: authPassword.value,
      })
    } else if (authType.value === 'token' && authToken.value) {
      authRef = JSON.stringify({ type: 'token', token: authToken.value })
    }
    const payload: any = {
      repo_key: repoForm.value.repo_key,
      name: repoForm.value.name,
      git_url: repoForm.value.git_url,
      branch: repoForm.value.branch || 'main',
      description: repoForm.value.description,
      category_key: repoForm.value.category_key === '__none__' ? '' : repoForm.value.category_key,
      auto_understand: repoForm.value.auto_understand,
    }
    if (authRef || repoFormMode.value === 'add') {
      payload.auth_ref = authRef
    }
    await api.upsertCodeRepo(payload)
    showRepoForm.value = false
    await loadRepos()
  } catch (e: any) {
    repoError.value = e.message || '保存失败'
  }
  repoSaving.value = false
}

async function testCloneConnection() {
  testCloneResult.value = null
  testCloning.value = true
  let authRef = ''
  if (authType.value === 'username_password' && authUsername.value) {
    authRef = JSON.stringify({
      type: 'username_password',
      username: authUsername.value,
      password: authPassword.value,
    })
  } else if (authType.value === 'token' && authToken.value) {
    authRef = JSON.stringify({ type: 'token', token: authToken.value })
  }
  try {
    testCloneResult.value = await api.testClone(repoForm.value.git_url, authRef)
  } catch (e: any) {
    testCloneResult.value = { success: false, message: e.message || '测试失败' }
  }
  testCloning.value = false
}

async function syncRepo(key: string) {
  syncingKey.value = key
  try {
    await api.syncCodeRepo(key)
    await loadRepos()
  } catch { /* ignore */ }
  syncingKey.value = ''
}

async function deleteRepo(r: CodeRepository) {
  if (!await confirm({ title: '删除代码知识库', description: `确定删除代码知识库「${r.name}」？将清除本地代码镜像、索引与知识图谱产物，且不可恢复。`, destructive: true, confirmText: '删除' })) return
  try {
    await api.deleteCodeRepo(r.repo_key)
    await loadRepos()
  } catch (e: any) {
    await alert({ title: '删除失败', description: e.message || '删除失败', destructive: true })
  }
}

function openDetail(r: CodeRepository) {
  window.location.hash = `code-repos/${r.repo_key}`
}

function backToList() {
  window.location.hash = 'code-repos'
}

const filteredRepos = computed(() => {
  let list = repos.value
  if (filterCategory.value && filterCategory.value !== '__all__') {
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
const pagedRepos = computed(() => paginate(filteredRepos.value, page.value, pageSize.value))

async function openPlaneDialog(r: CodeRepository) {
  planeRepo.value = r
  selectedProfileKeys.value = []
  pendingProfileKeys.value = []
  allProfiles.value = []
  try {
    const [profiles, rules] = await Promise.all([
      api.listProfiles(),
      api.getResourceProfiles('code_repo', r.repo_key),
    ])
    allProfiles.value = profiles
    selectedProfileKeys.value = rules.map((rule: any) => rule.profile_key)
    pendingProfileKeys.value = [...selectedProfileKeys.value]
  } catch { /* ignore */ }
  showPlaneDialog.value = true
}

function togglePlaneProfile(profileKey: string) {
  const idx = pendingProfileKeys.value.indexOf(profileKey)
  if (idx >= 0) {
    pendingProfileKeys.value.splice(idx, 1)
  } else {
    pendingProfileKeys.value.push(profileKey)
  }
}

async function savePlaneProfiles() {
  if (!planeRepo.value) return
  planeSaving.value = true
  try {
    await api.setResourceProfiles('code_repo', planeRepo.value.repo_key, [...pendingProfileKeys.value])
    selectedProfileKeys.value = [...pendingProfileKeys.value]
    showPlaneDialog.value = false
  } catch { /* ignore */ }
  planeSaving.value = false
}

function categoryName(key: string) {
  if (!key) return ''
  return categories.value.find(c => c.category_key === key)?.name || key
}

</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <CodeRepoDetailView
    v-else-if="mode === 'detail'"
    :repo-key="props.routeKey"
    :repo="detailRepo"
    @back="backToList"
  />
  <div v-else class="space-y-5">
    <!-- 页头操作：刷新 + 添加仓库进 #ph-actions（仅列表态） -->
    <Teleport v-if="mode === 'list'" to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="loadRepos()">
        <RotateCw :size="14" />
        刷新
      </Button>
      <Button size="lg" class="shadow-btn" @click="openRepoForm('add')">
        <Plus :size="14" />
        添加仓库
      </Button>
    </Teleport>

    <!-- 页头筛选：搜索 + 分类进 #ph-filters（仅列表态） -->
    <Teleport v-if="mode === 'list'" to="#ph-filters" defer>
      <div class="relative w-full max-w-[360px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-placeholder" />
        <Input v-model="searchQuery" placeholder="搜索仓库名称、标识或地址..." class="h-9 pl-8" />
      </div>
      <Select v-model="filterCategory" @update:model-value="page = 1">
        <SelectTrigger size="lg" class="w-[160px]">
          <SelectValue placeholder="全部分类" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部分类</SelectItem>
          <SelectItem v-for="c in categories" :key="c.category_key" :value="c.category_key">{{ c.name }}</SelectItem>
        </SelectContent>
      </Select>
    </Teleport>

    <!-- Code Repos Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="repos.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">暂无代码仓库，点击「添加仓库」开始</div>
        <div v-else-if="filteredRepos.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">没有匹配的仓库</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">仓库</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">分类</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Git URL</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">分支</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">最近同步</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in pagedRepos" :key="r.repo_key" class="border-b border-border/60">
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
                <Badge v-if="r.status === 'active'" variant="secondary" class="bg-success-soft text-success-soft-fg">正常</Badge>
                <Badge v-else-if="r.status === 'error'" variant="destructive">异常</Badge>
                <Badge v-else variant="secondary">{{ r.status }}</Badge>
              </td>
              <td class="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ formatLocalDatetime(r.last_synced_at) }}</td>
              <td class="px-4 py-3">
                <div class="flex gap-2">
                  <Button variant="ghost" size="sm" @click="syncRepo(r.repo_key)" :disabled="syncingKey === r.repo_key" class="h-8 text-xs">
                    {{ syncingKey === r.repo_key ? '同步中...' : '同步' }}
                  </Button>
                  <Button variant="outline" size="sm" @click="openRepoForm('edit', r)" class="h-8 text-xs">编辑</Button>
                  <Button variant="outline" size="sm" @click="openDetail(r)" class="h-8 text-xs">详情</Button>
                  <Button variant="outline" size="sm" @click="openPlaneDialog(r)" class="h-8 text-xs">能力平面</Button>
                  <Button variant="ghost" size="sm" class="h-8 gap-1.5 text-xs text-destructive" @click="deleteRepo(r)">
                    <Trash2 :size="14" />
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
      :total="filteredRepos.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />

    <!-- Add/Edit Repo Dialog -->
    <Dialog :open="showRepoForm" @update:open="showRepoForm = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>{{ repoFormMode === 'add' ? '添加代码仓库' : '编辑代码仓库' }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveRepo" class="space-y-4">
          <div v-if="repoError" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive-soft-fg">{{ repoError }}</div>
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
          <!-- Clone Credentials -->
          <div class="space-y-3 rounded-lg border border-border p-3">
            <div class="flex items-center justify-between">
              <label class="text-sm font-medium">Clone 凭证（可选）</label>
              <select v-model="authType" class="h-8 rounded-md border border-border bg-background px-2 text-xs">
                <option value="none">无需凭证</option>
                <option value="username_password">用户名+密码</option>
                <option value="token">Access Token</option>
              </select>
            </div>
            <div v-if="editingHasAuth && authType === 'none'" class="text-xs text-muted-foreground">
              已配置凭证，如需修改请选择凭证类型
            </div>
            <template v-if="authType === 'username_password'">
              <div class="grid grid-cols-2 gap-3">
                <div class="space-y-1.5">
                  <label class="text-xs text-muted-foreground">用户名</label>
                  <Input v-model="authUsername" placeholder="git 用户名" />
                </div>
                <div class="space-y-1.5">
                  <label class="text-xs text-muted-foreground">密码/Token</label>
                  <Input v-model="authPassword" type="password" placeholder="密码或 Personal Access Token" />
                </div>
              </div>
            </template>
            <template v-if="authType === 'token'">
              <div class="space-y-1.5">
                <label class="text-xs text-muted-foreground">Access Token</label>
                <Input v-model="authToken" type="password" placeholder="Personal Access Token" />
              </div>
            </template>
            <div class="flex items-center gap-3">
              <Button variant="outline" size="sm" @click="testCloneConnection" :disabled="testCloning || !repoForm.git_url">
                {{ testCloning ? '测试中...' : '测试连接' }}
              </Button>
              <div v-if="testCloneResult" :class="['text-xs', testCloneResult.success ? 'text-success' : 'text-destructive']">
                {{ testCloneResult.message }}
              </div>
            </div>
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
                  <SelectItem value="__none__">无分类</SelectItem>
                  <SelectItem v-for="c in categories" :key="c.category_key" :value="c.category_key">{{ c.name }}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="repoForm.description" placeholder="项目代码仓库" />
          </div>
          <div class="flex items-center gap-2">
            <input type="checkbox" v-model="repoForm.auto_understand" class="size-4 rounded-sm border-border" id="repo-auto-understand" />
            <label for="repo-auto-understand" class="text-sm">自动理解（定时运行 Understand Anything 分析）</label>
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveRepo" :disabled="repoSaving">{{ repoSaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Plane Assignment Dialog -->
    <Dialog :open="showPlaneDialog" @update:open="showPlaneDialog = $event">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ planeRepo?.name || '' }} — 归属能力平面</DialogTitle>
        </DialogHeader>
        <div class="space-y-2">
          <div class="text-xs text-muted-foreground">选择此仓库归属于哪些能力平面。</div>
          <div v-if="allProfiles.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无能力平面</div>
          <div v-else class="max-h-[320px] space-y-1 overflow-y-auto rounded-lg border border-border p-1">
            <label v-for="p in allProfiles" :key="p.profile_key"
              class="list-row-interactive flex cursor-pointer items-center gap-3 rounded-md px-3 py-2"
            >
              <input type="checkbox" :value="p.profile_key" :checked="pendingProfileKeys.includes(p.profile_key)"
                @change="togglePlaneProfile(p.profile_key)" class="size-4 rounded" />
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium truncate">{{ p.name || p.profile_key }}</div>
                <div class="text-xs text-muted-foreground">{{ p.profile_key }}</div>
              </div>
            </label>
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button @click="savePlaneProfiles" :disabled="planeSaving">{{ planeSaving ? '保存中...' : '确认' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

  </div>
</template>
