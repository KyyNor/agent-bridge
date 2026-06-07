<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import type { BackendInfo, CodeRepoCategory, KnowledgeSyncConfig, SchedulerStatus } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'
import { Badge } from '../components/ui/badge'

const loading = ref(true)

// Sync config
const syncConfig = ref<KnowledgeSyncConfig>({ code_sync_enabled: false, code_sync_cron: '*/30 * * * *' })
const configSaving = ref(false)
const cronError = ref('')

// Categories
const categories = ref<CodeRepoCategory[]>([])
const showCategoryDialog = ref(false)
const categoryForm = ref({ category_key: '', name: '', description: '' })
const categorySaving = ref(false)
const editingCategory = ref(false)

// Scheduler status
const schedulerStatus = ref<SchedulerStatus | null>(null)

// Backends
const backends = ref<BackendInfo[]>([])
const showBackendDialog = ref(false)
const backendForm = ref({ slug: '', backend_type: 'ragflow', base_url: '', api_key: '', timeout: 120, embedding_model_id: '', summary_model_id: '' })
const backendSaving = ref(false)
const backendError = ref('')
const editingBackend = ref(false)

const backendTypes = [
  { value: 'ragflow', label: 'RagFlow' },
  { value: 'weknora', label: 'Weknora' },
  { value: 'mock', label: 'Mock (测试)' },
]

const isRagflowOrWeknora = computed(() => backendForm.value.backend_type === 'ragflow' || backendForm.value.backend_type === 'weknora')
const isWeknora = computed(() => backendForm.value.backend_type === 'weknora')

onMounted(async () => {
  await Promise.all([loadSyncConfig(), loadCategories(), loadSchedulerStatus(), loadBackends()])
  loading.value = false
})

async function loadSyncConfig() {
  try { syncConfig.value = await api.getSyncConfig() } catch { /* ignore */ }
}

async function loadCategories() {
  try { categories.value = await api.listCategories() } catch { categories.value = [] }
}

async function loadSchedulerStatus() {
  try { schedulerStatus.value = await api.getSchedulerStatus() } catch { schedulerStatus.value = null }
}

async function loadBackends() {
  try { backends.value = await api.listBackends() } catch { backends.value = [] }
}

function validateCron(expr: string): boolean {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) {
    cronError.value = 'Cron 表达式需要 5 段（分 时 日 月 周）'
    return false
  }
  cronError.value = ''
  return true
}

async function saveSyncConfig() {
  if (!validateCron(syncConfig.value.code_sync_cron)) return
  configSaving.value = true
  try {
    syncConfig.value = await api.saveSyncConfig(syncConfig.value)
    cronError.value = ''
    await loadSchedulerStatus()
  } catch { /* ignore */ }
  configSaving.value = false
}

function openAddCategory() {
  editingCategory.value = false
  categoryForm.value = { category_key: '', name: '', description: '' }
  showCategoryDialog.value = true
}

function openEditCategory(c: CodeRepoCategory) {
  editingCategory.value = true
  categoryForm.value = { category_key: c.category_key, name: c.name, description: c.description }
  showCategoryDialog.value = true
}

async function saveCategory() {
  categorySaving.value = true
  try {
    await api.upsertCategory(categoryForm.value)
    showCategoryDialog.value = false
    await loadCategories()
  } catch { /* ignore */ }
  categorySaving.value = false
}

async function deleteCategory(key: string) {
  try {
    await api.deleteCategory(key)
    await loadCategories()
  } catch { /* ignore */ }
}

// ── Backend CRUD ──

function openAddBackend() {
  editingBackend.value = false
  backendForm.value = { slug: '', backend_type: 'ragflow', base_url: '', api_key: '', timeout: 120, embedding_model_id: '', summary_model_id: '' }
  backendError.value = ''
  showBackendDialog.value = true
}

function openEditBackend(b: BackendInfo) {
  editingBackend.value = true
  backendForm.value = {
    slug: b.slug,
    backend_type: b.backend_type,
    base_url: b.base_url || '',
    api_key: '',
    timeout: b.timeout,
    embedding_model_id: b.embedding_model_id || '',
    summary_model_id: b.summary_model_id || '',
  }
  backendError.value = ''
  showBackendDialog.value = true
}

async function saveBackend() {
  backendError.value = ''
  if (!backendForm.value.slug) {
    backendError.value = '请填写后端标识'
    return
  }
  backendSaving.value = true
  try {
    const data: Record<string, unknown> = {
      slug: backendForm.value.slug,
      backend_type: backendForm.value.backend_type,
      timeout: backendForm.value.timeout,
    }
    if (isRagflowOrWeknora.value) {
      data.base_url = backendForm.value.base_url || null
      if (backendForm.value.api_key) data.api_key = backendForm.value.api_key
    }
    if (isWeknora.value) {
      data.embedding_model_id = backendForm.value.embedding_model_id || null
      data.summary_model_id = backendForm.value.summary_model_id || null
    }

    if (editingBackend.value) {
      await api.updateBackend(backendForm.value.slug, data)
    } else {
      await api.createBackend(data as any)
    }
    showBackendDialog.value = false
    await loadBackends()
  } catch (e: any) {
    backendError.value = e.message || '保存失败'
  }
  backendSaving.value = false
}

async function deleteBackend(slug: string) {
  try {
    await api.deleteBackend(slug)
    await loadBackends()
  } catch { /* ignore */ }
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <div class="flex items-center gap-4">
      <h2 class="text-lg font-semibold">知识处理配置</h2>
    </div>

    <!-- Backend Management -->
    <Card class="border-border">
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between">
          <div>
            <div class="text-sm font-semibold">后端管理</div>
            <div class="text-xs text-muted-foreground">文档知识同步与检索目标后端</div>
          </div>
          <div class="flex gap-2">
            <Button variant="outline" size="sm" @click="loadBackends()">刷新</Button>
            <Button size="sm" @click="openAddBackend()">添加后端</Button>
          </div>
        </div>
        <div v-if="backends.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无后端，点击「添加后端」开始配置</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">标识</th>
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">类型</th>
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">Base URL</th>
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">状态</th>
              <th class="px-3 py-2 text-right text-xs font-semibold text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in backends" :key="b.slug" class="border-b border-border/40 hover:bg-secondary/30">
              <td class="px-3 py-2 font-mono text-sm">{{ b.slug }}</td>
              <td class="px-3 py-2 text-sm">{{ b.backend_type }}</td>
              <td class="px-3 py-2 text-xs text-muted-foreground truncate max-w-[250px]">{{ b.base_url || '—' }}</td>
              <td class="px-3 py-2">
                <Badge variant="secondary" class="text-[11px]"
                  :class="b.runtime_status === 'active' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'">
                  {{ b.runtime_status === 'active' ? '运行中' : '未激活' }}
                </Badge>
              </td>
              <td class="px-3 py-2 text-right">
                <div class="flex justify-end gap-2">
                  <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openEditBackend(b)">编辑</Button>
                  <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive" @click="deleteBackend(b.slug)">删除</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <!-- Sync Config -->
    <Card class="border-border">
      <CardContent class="space-y-4 p-5">
        <div class="text-sm font-semibold">代码知识定时同步</div>
        <div class="flex items-center gap-4">
          <label class="flex items-center gap-2 text-sm">
            <input type="checkbox" v-model="syncConfig.code_sync_enabled" class="size-4 rounded-sm border-border" />
            启用定时同步
          </label>
          <div class="flex items-center gap-2 text-sm">
            <span class="text-muted-foreground">Cron 表达式</span>
            <Input v-model="syncConfig.code_sync_cron" placeholder="*/30 * * * *" class="w-40 font-mono text-xs" />
          </div>
          <Button @click="saveSyncConfig()" :disabled="configSaving" size="sm">
            {{ configSaving ? '保存中...' : '保存' }}
          </Button>
        </div>
        <div v-if="cronError" class="text-xs text-destructive">{{ cronError }}</div>
        <div class="text-xs text-muted-foreground">
          标准 5 段 cron 表达式：<code class="font-mono">分钟 小时 日 月 星期</code>。例如
          <code class="font-mono">*/30 * * * *</code>（每30分钟）、<code class="font-mono">0 */2 * * *</code>（每2小时）、<code class="font-mono">0 8 * * 1-5</code>（工作日早8点）。
        </div>
      </CardContent>
    </Card>

    <!-- Categories -->
    <Card class="border-border">
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between">
          <div class="text-sm font-semibold">代码仓库分类</div>
          <Button @click="openAddCategory()" size="sm">添加分类</Button>
        </div>
        <div v-if="categories.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无分类，点击「添加分类」开始</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">标识</th>
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">名称</th>
              <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">描述</th>
              <th class="px-3 py-2 text-right text-xs font-semibold text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in categories" :key="c.category_key" class="border-b border-border/40 hover:bg-secondary/30">
              <td class="px-3 py-2 font-mono text-xs">{{ c.category_key }}</td>
              <td class="px-3 py-2 text-sm">{{ c.name }}</td>
              <td class="px-3 py-2 text-xs text-muted-foreground">{{ c.description || '—' }}</td>
              <td class="px-3 py-2 text-right">
                <div class="flex justify-end gap-2">
                  <Button variant="ghost" size="sm" class="h-7 text-xs" @click="openEditCategory(c)">编辑</Button>
                  <Button variant="ghost" size="sm" class="h-7 text-xs text-destructive" @click="deleteCategory(c.category_key)">删除</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <!-- Scheduler Status -->
    <Card class="border-border">
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between">
          <div class="text-sm font-semibold">调度状态</div>
          <Button variant="outline" size="sm" @click="loadSchedulerStatus()">刷新</Button>
        </div>
        <div v-if="!schedulerStatus" class="py-4 text-center text-sm text-muted-foreground">无法获取调度状态</div>
        <div v-else>
          <div class="mb-3 flex items-center gap-3">
            <Badge :variant="schedulerStatus.running ? 'secondary' : 'outline'" :class="schedulerStatus.running ? 'bg-green-50 text-green-700' : ''">
              {{ schedulerStatus.running ? '运行中' : '已暂停' }}
            </Badge>
            <span v-if="schedulerStatus.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.cron }}</span>
          </div>
          <div v-if="schedulerStatus.jobs.length === 0" class="py-4 text-center text-sm text-muted-foreground">
            {{ schedulerStatus.running ? '没有活跃的代码仓库' : '—' }}
          </div>
          <table v-else class="w-full">
            <thead>
              <tr class="border-b border-border bg-secondary/50">
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">仓库</th>
                <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">下次执行</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="j in schedulerStatus.jobs" :key="j.repo_key" class="border-b border-border/40">
                <td class="px-3 py-2 text-sm font-mono">{{ j.repo_key }}</td>
                <td class="px-3 py-2 text-xs text-muted-foreground">{{ j.next_run_at?.replace('T', ' ').slice(0, 19) || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Backend Dialog -->
    <Dialog :open="showBackendDialog" @update:open="showBackendDialog = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>{{ editingBackend ? '编辑后端' : '添加后端' }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveBackend" class="space-y-4">
          <div v-if="backendError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ backendError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">后端标识 <span class="text-destructive">*</span></label>
            <Input v-model="backendForm.slug" placeholder="my-ragflow" :disabled="editingBackend" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">后端类型 <span class="text-destructive">*</span></label>
            <select v-model="backendForm.backend_type" :disabled="editingBackend" class="h-9 w-full rounded-md border border-border bg-background px-3 text-sm">
              <option v-for="t in backendTypes" :key="t.value" :value="t.value">{{ t.label }}</option>
            </select>
          </div>
          <div v-if="isRagflowOrWeknora" class="space-y-2">
            <label class="text-sm font-medium">Base URL <span class="text-destructive">*</span></label>
            <Input v-model="backendForm.base_url" placeholder="http://localhost:9380" />
          </div>
          <div v-if="isRagflowOrWeknora" class="space-y-2">
            <label class="text-sm font-medium">API Key{{ editingBackend ? '（留空保持不变）' : '' }}</label>
            <Input v-model="backendForm.api_key" type="password" placeholder="ragflow-xxxx" />
          </div>
          <div v-if="isWeknora" class="space-y-2">
            <label class="text-sm font-medium">Embedding Model ID</label>
            <Input v-model="backendForm.embedding_model_id" placeholder="emb-1" />
          </div>
          <div v-if="isWeknora" class="space-y-2">
            <label class="text-sm font-medium">Summary Model ID</label>
            <Input v-model="backendForm.summary_model_id" placeholder="chat-1" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">超时（秒）</label>
            <Input v-model.number="backendForm.timeout" type="number" :min="10" :max="600" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveBackend()" :disabled="backendSaving">{{ backendSaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Category Dialog -->
    <Dialog :open="showCategoryDialog" @update:open="showCategoryDialog = $event">
      <DialogContent class="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{{ editingCategory ? '编辑分类' : '添加分类' }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveCategory" class="space-y-4">
          <div class="space-y-2">
            <label class="text-sm font-medium">分类标识 <span class="text-destructive">*</span></label>
            <Input v-model="categoryForm.category_key" placeholder="backend-services" :disabled="editingCategory" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="categoryForm.name" placeholder="后端服务" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="categoryForm.description" placeholder="后端服务相关仓库" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveCategory()" :disabled="categorySaving">{{ categorySaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
