<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import CronExpressionParser from 'cron-parser'
import { api } from '../../api/client'
import type { BackendInfo, CodeRepoCategory, KnowledgeSyncConfig, SchedulerStatus } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import { Badge } from '../../components/ui/badge'

const loading = ref(true)

// Sync config
const syncConfig = ref<KnowledgeSyncConfig>({
  code_sync_cron: '0 * * * *',
  ua_git_url: '',
  understand_cron: '0 2 * * *',
  doc_sync_cron: '*/30 * * * *',
  workflow_start_time: '22:00',
  workflow_stop_time: '07:00',
  workflow_max_runs: 0,
})
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
  { value: 'pageindex', label: 'PageIndex (内置)' },
  { value: 'mock', label: 'Mock (测试)' },
]

const needsBackendConnection = computed(() => ['ragflow', 'weknora', 'pageindex'].includes(backendForm.value.backend_type))
const isWeknora = computed(() => backendForm.value.backend_type === 'weknora')
const isPageIndex = computed(() => backendForm.value.backend_type === 'pageindex')
const supportsModelConfig = computed(() => isWeknora.value || isPageIndex.value)

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

function getNextRuns(expr: string): Date[] | null {
  try {
    const interval = CronExpressionParser.parse(expr.trim())
    return [interval.next().toDate(), interval.next().toDate()]
  } catch {
    return null
  }
}

function formatNextRuns(expr: string): string | null {
  const runs = getNextRuns(expr)
  if (!runs) return null
  return runs.map(d => {
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${mm}/${dd} ${hh}:${min}`
  }).join('  →  ')
}

const codeSyncNextRuns = computed(() => formatNextRuns(syncConfig.value.code_sync_cron))
const understandNextRuns = computed(() => formatNextRuns(syncConfig.value.understand_cron))
const docSyncNextRuns = computed(() => formatNextRuns(syncConfig.value.doc_sync_cron || '*/30 * * * *'))
const HHMM = /^([01]?\d|2[0-3]):[0-5]\d$/
const workflowTimesValid = computed(() =>
  HHMM.test(syncConfig.value.workflow_start_time.trim())
  && HHMM.test(syncConfig.value.workflow_stop_time.trim()),
)
const maxRunsValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_max_runs) && syncConfig.value.workflow_max_runs >= 0,
)
const cronValid = computed(() =>
  codeSyncNextRuns.value !== null
  && understandNextRuns.value !== null
  && docSyncNextRuns.value !== null
  && workflowTimesValid.value
  && maxRunsValid.value,
)
const runCountText = computed(() => {
  const wf = schedulerStatus.value?.workflow
  if (!wf) return '—'
  const cap = wf.max_runs ?? 0
  if (cap <= 0) return '不限'
  const entries = Object.entries(wf.run_counts ?? {})
  if (!entries.length) return `0/${cap}（暂无运行）`
  return entries.map(([k, v]) => `${k} ${v}/${cap}`).join('、')
})

function runBadgeClass(status?: string | null): string {
  if (status === 'succeeded') return 'bg-green-50 text-green-700'
  if (status === 'failed') return 'bg-red-50 text-red-700'
  if (status === 'running') return 'bg-blue-50 text-blue-700'
  return ''
}

function runLabel(status?: string | null): string {
  if (status === 'succeeded') return '成功'
  if (status === 'failed') return '失败'
  if (status === 'running') return '执行中'
  return '未执行'
}

function docRunText(run: any): string {
  if (!run) return '暂无执行记录'
  const total = run.total ?? 0
  const processed = run.processed ?? 0
  const succeeded = run.succeeded ?? 0
  const failed = run.failed ?? 0
  const current = run.current_job?.doc_title || run.current_job?.doc_slug
  const base = total ? `${processed}/${total}，成功 ${succeeded}，失败 ${failed}` : `成功 ${succeeded}，失败 ${failed}`
  return current ? `${base}，当前：${current}` : base
}

async function saveSyncConfig() {
  if (!cronValid.value) {
    cronError.value = 'Cron 表达式无效，请检查后重试'
    return
  }
  configSaving.value = true
  try {
    const saved = await api.saveSyncConfig(syncConfig.value)
    syncConfig.value = { ...syncConfig.value, ...saved }
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
    if (needsBackendConnection.value) {
      data.base_url = backendForm.value.base_url || null
      if (backendForm.value.api_key) data.api_key = backendForm.value.api_key
    }
    if (supportsModelConfig.value) {
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
    <!-- 定时任务管理 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="text-sm font-medium">定时任务管理</div>

        <div class="grid grid-cols-[12rem_minmax(0,10rem)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">代码同步 <span class="text-xs text-muted-foreground">(CodeGraph)</span></div>
          <Input v-model="syncConfig.code_sync_cron" placeholder="0 * * * *" class="w-40 font-mono text-xs" />
          <span v-if="codeSyncNextRuns" class="text-xs text-muted-foreground font-mono">{{ codeSyncNextRuns }}</span>
          <span v-else class="text-xs text-destructive">表达式无效</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,10rem)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">代码理解 <span class="text-xs text-muted-foreground">(Understand Anything)</span></div>
          <Input v-model="syncConfig.understand_cron" placeholder="0 2 * * *" class="w-40 font-mono text-xs" />
          <span v-if="understandNextRuns" class="text-xs text-muted-foreground font-mono">{{ understandNextRuns }}</span>
          <span v-else class="text-xs text-destructive">表达式无效</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,10rem)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">知识同步 <span class="text-xs text-muted-foreground">(文档知识同步)</span></div>
          <Input v-model="syncConfig.doc_sync_cron" placeholder="*/30 * * * *" class="w-40 font-mono text-xs" />
          <span v-if="docSyncNextRuns" class="text-xs text-muted-foreground font-mono">{{ docSyncNextRuns }}</span>
          <span v-else class="text-xs text-destructive">表达式无效</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">工作流调度 <span class="text-xs text-muted-foreground">(Workflow)</span></div>
          <div class="flex items-center gap-2">
            <Input v-model="syncConfig.workflow_start_time" placeholder="22:00" class="w-32 font-mono text-sm" />
            <span class="text-muted-foreground">→</span>
            <Input v-model="syncConfig.workflow_stop_time" placeholder="07:00" class="w-32 font-mono text-sm" />
          </div>
          <span v-if="workflowTimesValid" class="text-xs text-muted-foreground">每日窗口内持续轮转，跨夜自动续跑</span>
          <span v-else class="text-xs text-destructive">请输入 HH:MM 时间</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">单工作流运行上限 <span class="text-xs text-muted-foreground">(次/窗口)</span></div>
          <Input v-model.number="syncConfig.workflow_max_runs" type="number" min="0" placeholder="0" class="w-32 font-mono text-sm" />
          <span v-if="maxRunsValid" class="text-xs text-muted-foreground">{{ syncConfig.workflow_max_runs > 0 ? `每个工作流每窗口最多自动运行 ${syncConfig.workflow_max_runs} 次（手动测试运行不计入）` : '不限（0）' }}</span>
          <span v-else class="text-xs text-destructive">请输入非负整数</span>
        </div>

        <div class="flex items-center gap-3">
          <Button @click="saveSyncConfig()" :disabled="configSaving || !cronValid" size="sm">
            {{ configSaving ? '保存中...' : '保存配置' }}
          </Button>
          <span v-if="cronError" class="text-xs text-destructive">{{ cronError }}</span>
        </div>

        <!-- 调度状态 -->
        <div class="border-t border-border pt-4">
          <div class="flex items-center justify-between mb-3">
            <div class="text-xs font-medium text-muted-foreground">调度状态</div>
            <Button variant="outline" size="sm" @click="loadSchedulerStatus()">刷新</Button>
          </div>
          <div v-if="!schedulerStatus" class="py-4 text-center text-sm text-muted-foreground">无法获取调度状态</div>
          <div v-else class="space-y-3">
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">代码同步</span>
                <Badge :variant="schedulerStatus.code_sync.running ? 'secondary' : 'outline'" :class="schedulerStatus.code_sync.running ? 'bg-green-50 text-green-700' : ''">
                  {{ schedulerStatus.code_sync.running ? '运行中' : '已暂停' }}
                </Badge>
                <span v-if="schedulerStatus.code_sync.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.code_sync.cron }}</span>
              </div>
              <div v-if="schedulerStatus.code_sync.jobs.length === 0" class="py-2 text-center text-xs text-muted-foreground">
                {{ schedulerStatus.code_sync.running ? '没有活跃的代码仓库' : '—' }}
              </div>
              <table v-else class="w-full">
                <thead>
                  <tr class="border-b border-border">
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">仓库</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">下次执行</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">最近进度</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in schedulerStatus.code_sync.jobs" :key="j.repo_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                    <td class="px-3 py-2 text-sm font-mono">{{ j.repo_key }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(j.next_run_at) }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">
                      <Badge v-if="j.progress" variant="secondary" class="mr-2 text-[11px]" :class="runBadgeClass(j.progress.status)">
                        {{ runLabel(j.progress.status) }}
                      </Badge>
                      <span>{{ j.progress?.message || '暂无执行记录' }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">代码理解</span>
                <Badge :variant="schedulerStatus.understand.running ? 'secondary' : 'outline'" :class="schedulerStatus.understand.running ? 'bg-green-50 text-green-700' : ''">
                  {{ schedulerStatus.understand.running ? '运行中' : '已暂停' }}
                </Badge>
                <span v-if="schedulerStatus.understand.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.understand.cron }}</span>
              </div>
              <div v-if="schedulerStatus.understand.jobs.length === 0" class="py-2 text-center text-xs text-muted-foreground">
                {{ schedulerStatus.understand.running ? '没有开启自动理解的仓库' : '—' }}
              </div>
              <table v-else class="w-full">
                <thead>
                  <tr class="border-b border-border">
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">仓库</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">下次执行</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">最近进度</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in schedulerStatus.understand.jobs" :key="j.repo_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
                    <td class="px-3 py-2 text-sm font-mono">{{ j.repo_key }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(j.next_run_at) }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">
                      <Badge v-if="j.progress" variant="secondary" class="mr-2 text-[11px]" :class="runBadgeClass(j.progress.status)">
                        {{ runLabel(j.progress.status) }}
                      </Badge>
                      <span>{{ j.progress?.message || '暂无执行记录' }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">知识同步</span>
                <Badge :variant="schedulerStatus.doc_sync?.running ? 'secondary' : 'outline'" :class="schedulerStatus.doc_sync?.running ? 'bg-green-50 text-green-700' : ''">
                  {{ schedulerStatus.doc_sync?.running ? '运行中' : '已暂停' }}
                </Badge>
                <span v-if="schedulerStatus.doc_sync?.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.doc_sync.cron }}</span>
              </div>
              <div class="space-y-2 rounded-md border border-border bg-muted/20 p-3">
                <div class="flex items-center gap-2 text-xs">
                  <span class="text-muted-foreground">当前执行</span>
                  <Badge v-if="schedulerStatus.doc_sync?.current_run" variant="secondary" class="text-[11px]" :class="runBadgeClass(schedulerStatus.doc_sync.current_run.status)">
                    {{ runLabel(schedulerStatus.doc_sync.current_run.status) }}
                  </Badge>
                  <span class="text-muted-foreground">{{ docRunText(schedulerStatus.doc_sync?.current_run) }}</span>
                </div>
                <div class="flex items-center gap-2 text-xs">
                  <span class="text-muted-foreground">最近一次</span>
                  <Badge v-if="schedulerStatus.doc_sync?.last_run" variant="secondary" class="text-[11px]" :class="runBadgeClass(schedulerStatus.doc_sync.last_run.status)">
                    {{ runLabel(schedulerStatus.doc_sync.last_run.status) }}
                  </Badge>
                  <span class="text-muted-foreground">{{ docRunText(schedulerStatus.doc_sync?.last_run) }}</span>
                  <span v-if="schedulerStatus.doc_sync?.last_run?.finished_at" class="font-mono text-muted-foreground">
                    {{ formatLocalDatetime(schedulerStatus.doc_sync.last_run.finished_at) }}
                  </span>
                </div>
              </div>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">工作流调度</span>
                <Badge :variant="schedulerStatus.workflow?.running ? 'secondary' : 'outline'" :class="schedulerStatus.workflow?.running ? 'bg-green-50 text-green-700' : ''">
                  {{ schedulerStatus.workflow?.running ? '运行中' : '已暂停' }}
                </Badge>
                <span class="font-mono text-xs text-muted-foreground">
                  每日 {{ schedulerStatus.workflow?.start_time || '--' }} → {{ schedulerStatus.workflow?.stop_time || '--' }}
                </span>
                <Badge v-if="schedulerStatus.workflow?.in_window" variant="secondary" class="bg-blue-50 text-blue-700">窗口内</Badge>
                <span class="text-xs text-muted-foreground">最多并发 {{ schedulerStatus.workflow?.max_concurrent_workflows || 2 }} 个工作流</span>
              </div>
              <div class="space-y-2 rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
                <div>正在执行：{{ schedulerStatus.workflow?.running_workflows?.length ? schedulerStatus.workflow.running_workflows.join(', ') : '无' }}</div>
                <div>今日结束：{{ schedulerStatus.workflow?.finished_today?.length ? schedulerStatus.workflow.finished_today.join(', ') : '无' }}</div>
                <div>运行计数：{{ runCountText }}</div>
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- 知识库管理 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="text-sm font-medium">知识库管理</div>

        <!-- Backend Management -->
        <div class="border-t border-border pt-4">
          <div class="flex items-center justify-between mb-3">
            <div>
              <div class="text-xs font-medium text-muted-foreground">知识库后端</div>
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
              <tr class="border-b border-border">
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标识</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">类型</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Base URL</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-muted-foreground"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="b in backends" :key="b.slug" class="border-b border-border/60 transition-colors hover:bg-muted/50">
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
        </div>
      </CardContent>
    </Card>

    <!-- 代码仓库管理 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="text-sm font-medium">代码仓库管理</div>
        <div class="flex items-center gap-3">
          <div class="text-sm shrink-0 whitespace-nowrap">UA Git URL <span class="text-xs text-muted-foreground">(Understand Anything)</span></div>
          <Input v-model="syncConfig.ua_git_url" placeholder="https://github.com/Lum1104/Understand-Anything.git" class="font-mono text-xs flex-1" />
          <Button @click="saveSyncConfig()" :disabled="configSaving" size="sm">保存</Button>
        </div>
        <div class="flex items-center justify-between border-t border-border pt-4">
          <div class="text-xs font-medium text-muted-foreground">仓库分类</div>
          <Button @click="openAddCategory()" size="sm">添加分类</Button>
        </div>
        <div v-if="categories.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无分类，点击「添加分类」开始</div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标识</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">名称</th>
              <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">描述</th>
              <th class="px-3 py-2 text-right text-xs font-medium text-muted-foreground"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in categories" :key="c.category_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
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
          <div v-if="needsBackendConnection" class="space-y-2">
            <label class="text-sm font-medium">{{ isPageIndex ? 'LiteLLM Base URL' : 'Base URL' }} <span class="text-destructive">*</span></label>
            <Input v-model="backendForm.base_url" :placeholder="isPageIndex ? 'http://litellm.internal:4000/v1' : 'http://localhost:9380'" />
          </div>
          <div v-if="needsBackendConnection" class="space-y-2">
            <label class="text-sm font-medium">API Key{{ editingBackend ? '（留空保持不变）' : '' }}</label>
            <Input v-model="backendForm.api_key" type="password" :placeholder="isPageIndex ? 'internal-litellm-key' : 'ragflow-xxxx'" />
          </div>
          <div v-if="supportsModelConfig" class="space-y-2">
            <label class="text-sm font-medium">{{ isPageIndex ? 'Index Model' : 'Embedding Model ID' }}</label>
            <Input v-model="backendForm.embedding_model_id" :placeholder="isPageIndex ? 'openai/qwen-long' : 'emb-1'" />
          </div>
          <div v-if="supportsModelConfig" class="space-y-2">
            <label class="text-sm font-medium">{{ isPageIndex ? 'Retrieve / Ask Model' : 'Summary Model ID' }}</label>
            <Input v-model="backendForm.summary_model_id" :placeholder="isPageIndex ? 'openai/qwen-long' : 'chat-1'" />
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
