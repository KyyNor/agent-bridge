<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { CronExpressionParser } from 'cron-parser'
import { api } from '../../api/client'
import type { AgentRuntimeConfig, BackendInfo, ClaudeMemConfig, CodeRepoCategory, KnowledgeSyncConfig, RetrievalProbeLlmConfig, SchedulerStatus, TopLevelMcpTool } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import { Badge } from '../../components/ui/badge'
import StatusBadge from '../../components/StatusBadge.vue'
import { confirm } from '../../composables/useConfirm'

const loading = ref(true)

const topLevelMcpTools = ref<TopLevelMcpTool[]>([])
const topLevelMcpToolsError = ref('')
const topLevelMcpToolsUpdating = ref<string | null>(null)

// Sync config
const syncConfig = ref<KnowledgeSyncConfig>({
  code_sync_cron: '0 * * * *',
  ua_git_url: '',
  ua_plugin_update_cron: '0 3 * * 0',
  claude_mem_git_url: '',
  claude_mem_plugin_update_cron: '30 3 * * 0',
  understand_cron: '0 2 * * *',
  doc_sync_cron: '*/30 * * * *',
  workflow_start_time: '22:00',
  workflow_stop_time: '07:00',
  workflow_max_runs: 0,
  workflow_max_concurrent_runs: 4,
  workflow_max_concurrent_runs_per_workflow: 2,
  workflow_max_runtime_minutes: 30,
  workflow_task_rerun_days: 30,
  log_retention_days: 180,
  mcp_timeout_seconds: 150,
  understand_timeout_minutes: 120,
})
const configSaving = ref(false)
const cronError = ref('')

const claudeMemConfig = ref<ClaudeMemConfig | null>(null)
const claudeMemForm = ref({ base_url: '', model: '', auth_token: '', api_key: '', clear_auth_token: false, clear_api_key: false })
const claudeMemSaving = ref(false)
const claudeMemError = ref('')

const retrievalProbeLlmConfig = ref<RetrievalProbeLlmConfig | null>(null)
const retrievalProbeLlmForm = ref({ base_url: '', model: '', api_key: '', clear_api_key: false })
const retrievalProbeLlmSaving = ref(false)
const retrievalProbeLlmError = ref('')

const agentRuntimeConfig = ref<AgentRuntimeConfig>({ default_backend: 'claude', backends: [] })
const agentRuntimeSaving = ref(false)
const agentRuntimeError = ref('')
const agentRuntimeMessage = ref('')
const fixedAgentBackendDefs = [
  { slug: 'claude', type: 'claude', command: null as string | null, model: null as string | null },
  { slug: 'opencode', type: 'opencode', command: 'opencode', model: null as string | null },
  { slug: 'codex', type: 'codex', command: 'codex', model: null as string | null },
]

// Categories
const categories = ref<CodeRepoCategory[]>([])
const showCategoryDialog = ref(false)
const categoryForm = ref({ category_key: '', name: '', description: '' })
const categorySaving = ref(false)
const editingCategory = ref(false)
const categoryExpectedEditToken = ref<string | null>('')

// Scheduler status
const schedulerStatus = ref<SchedulerStatus | null>(null)

// Backends
const backends = ref<BackendInfo[]>([])
const showBackendDialog = ref(false)
const backendForm = ref({ slug: '', backend_type: 'ragflow', base_url: '', api_key: '', timeout: 120, embedding_model_id: '', summary_model_id: '', rerank_model_id: '' })
const backendSaving = ref(false)
const backendError = ref('')
const editingBackend = ref(false)
const backendExpectedEditToken = ref<string | null>('')

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
  await Promise.all([loadSyncConfig(), loadTopLevelMcpTools(), loadClaudeMemConfig(), loadRetrievalProbeLlmConfig(), loadAgentRuntimeConfig(), loadCategories(), loadSchedulerStatus(), loadBackends()])
  loading.value = false
})

async function loadSyncConfig() {
  try { syncConfig.value = await api.getSyncConfig() } catch { /* ignore */ }
}

async function loadTopLevelMcpTools() {
  try {
    topLevelMcpTools.value = await api.listTopLevelMcpTools()
    topLevelMcpToolsError.value = ''
  } catch (e: any) {
    topLevelMcpToolsError.value = e.message || '无法加载顶层 MCP 工具'
  }
}

async function toggleTopLevelMcpTool(tool: TopLevelMcpTool) {
  const nextStatus = tool.status === 'enabled' ? 'disabled' : 'enabled'
  topLevelMcpToolsUpdating.value = tool.name
  try {
    const saved = await api.updateTopLevelMcpToolStatus(tool.name, nextStatus)
    const index = topLevelMcpTools.value.findIndex(item => item.name === tool.name)
    if (index >= 0) topLevelMcpTools.value[index] = saved
    topLevelMcpToolsError.value = ''
  } catch (e: any) {
    topLevelMcpToolsError.value = e.message || '更新顶层 MCP 工具状态失败'
  }
  topLevelMcpToolsUpdating.value = null
}

async function loadClaudeMemConfig() {
  try {
    const config = await api.getClaudeMemConfig()
    claudeMemConfig.value = config
    claudeMemForm.value = {
      base_url: config.base_url || '',
      model: config.model || '',
      auth_token: '',
      api_key: '',
      clear_auth_token: false,
      clear_api_key: false,
    }
  } catch {
    claudeMemConfig.value = null
  }
}

async function loadRetrievalProbeLlmConfig() {
  try {
    const config = await api.getRetrievalProbeLlmConfig()
    retrievalProbeLlmConfig.value = config
    retrievalProbeLlmForm.value = { base_url: config.base_url, model: config.model, api_key: '', clear_api_key: false }
    retrievalProbeLlmError.value = ''
  } catch (e: any) {
    retrievalProbeLlmConfig.value = null
    retrievalProbeLlmError.value = e.message || '无法加载全量探测关键词模型配置'
  }
}

async function loadAgentRuntimeConfig() {
  try {
    agentRuntimeConfig.value = normalizeFixedAgentRuntimeConfig(await api.getAgentRuntimeConfig())
    agentRuntimeError.value = ''
  } catch (e: any) {
    agentRuntimeError.value = e.message || '无法加载 Coding Agent 配置'
  }
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
const uaPluginUpdateNextRuns = computed(() => formatNextRuns(syncConfig.value.ua_plugin_update_cron || '0 3 * * 0'))
const claudeMemPluginUpdateNextRuns = computed(() => formatNextRuns(syncConfig.value.claude_mem_plugin_update_cron || '30 3 * * 0'))
const docSyncNextRuns = computed(() => formatNextRuns(syncConfig.value.doc_sync_cron || '*/30 * * * *'))
const HHMM = /^([01]?\d|2[0-3]):[0-5]\d$/
const workflowTimesValid = computed(() =>
  HHMM.test(syncConfig.value.workflow_start_time.trim())
  && HHMM.test(syncConfig.value.workflow_stop_time.trim()),
)
const maxRunsValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_max_runs) && syncConfig.value.workflow_max_runs >= 0,
)
const maxConcurrentRunsValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_max_concurrent_runs) && syncConfig.value.workflow_max_concurrent_runs > 0,
)
const maxConcurrentRunsPerWorkflowValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_max_concurrent_runs_per_workflow)
  && syncConfig.value.workflow_max_concurrent_runs_per_workflow > 0,
)
const taskRerunDaysValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_task_rerun_days) && syncConfig.value.workflow_task_rerun_days >= 0,
)
const workflowRuntimeValid = computed(() =>
  Number.isInteger(syncConfig.value.workflow_max_runtime_minutes) && syncConfig.value.workflow_max_runtime_minutes >= 0,
)
const logRetentionValid = computed(() =>
  Number.isInteger(syncConfig.value.log_retention_days) && syncConfig.value.log_retention_days > 0,
)
const mcpTimeoutValid = computed(() =>
  Number.isInteger(syncConfig.value.mcp_timeout_seconds) && syncConfig.value.mcp_timeout_seconds > 0,
)
const understandTimeoutValid = computed(() =>
  Number.isInteger(syncConfig.value.understand_timeout_minutes) && syncConfig.value.understand_timeout_minutes > 0,
)
const cronValid = computed(() =>
  codeSyncNextRuns.value !== null
  && understandNextRuns.value !== null
  && uaPluginUpdateNextRuns.value !== null
  && claudeMemPluginUpdateNextRuns.value !== null
  && docSyncNextRuns.value !== null
  && workflowTimesValid.value
  && maxRunsValid.value
  && maxConcurrentRunsValid.value
  && maxConcurrentRunsPerWorkflowValid.value
  && taskRerunDaysValid.value
  && workflowRuntimeValid.value
  && logRetentionValid.value
  && mcpTimeoutValid.value
  && understandTimeoutValid.value,
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
const runningWorkflowText = computed(() => {
  const workflow = schedulerStatus.value?.workflow
  if (!workflow?.running_workflows?.length) return '无'
  return workflow.running_workflows
    .map(key => `${key} ×${workflow.running_run_counts?.[key] || 1}`)
    .join(', ')
})

// 任务运行 status → StatusBadge 语义状态
function runBadgeStatus(status?: string | null): 'success' | 'error' | 'running' | 'disabled' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'running') return 'running'
  return 'disabled'
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

async function saveClaudeMemConfig() {
  claudeMemSaving.value = true
  claudeMemError.value = ''
  try {
    const saved = await api.saveClaudeMemConfig({
      base_url: claudeMemForm.value.base_url,
      model: claudeMemForm.value.model,
      auth_token: claudeMemForm.value.auth_token || null,
      api_key: claudeMemForm.value.api_key || null,
      clear_auth_token: claudeMemForm.value.clear_auth_token,
      clear_api_key: claudeMemForm.value.clear_api_key,
      expected_edit_token: claudeMemConfig.value?.edit_token,
    })
    claudeMemConfig.value = saved
    claudeMemForm.value = {
      base_url: saved.base_url || '',
      model: saved.model || '',
      auth_token: '',
      api_key: '',
      clear_auth_token: false,
      clear_api_key: false,
    }
  } catch (e: any) {
    claudeMemError.value = e.message || '保存失败'
  }
  claudeMemSaving.value = false
}

async function saveRetrievalProbeLlmConfig() {
  retrievalProbeLlmSaving.value = true
  retrievalProbeLlmError.value = ''
  try {
    const saved = await api.saveRetrievalProbeLlmConfig({
      base_url: retrievalProbeLlmForm.value.base_url,
      model: retrievalProbeLlmForm.value.model,
      api_key: retrievalProbeLlmForm.value.api_key || null,
      clear_api_key: retrievalProbeLlmForm.value.clear_api_key,
      expected_edit_token: retrievalProbeLlmConfig.value?.edit_token,
    })
    retrievalProbeLlmConfig.value = saved
    retrievalProbeLlmForm.value = { base_url: saved.base_url, model: saved.model, api_key: '', clear_api_key: false }
  } catch (e: any) {
    retrievalProbeLlmError.value = e.message || '保存失败'
  }
  retrievalProbeLlmSaving.value = false
}

function normalizeFixedAgentRuntimeConfig(config: AgentRuntimeConfig): AgentRuntimeConfig {
  const bySlug = new Map(config.backends.map(item => [item.slug, item]))
  const backends = fixedAgentBackendDefs.map(def => {
    const current = bySlug.get(def.slug)
    return {
      slug: def.slug,
      type: def.type,
      command: current?.command ?? def.command,
      model: current?.model ?? def.model,
    }
  })
  const allowed = new Set(fixedAgentBackendDefs.map(item => item.slug))
  return {
    default_backend: allowed.has(config.default_backend) ? config.default_backend : 'claude',
    backends,
    edit_token: config.edit_token,
  }
}

async function saveAgentRuntimeConfig() {
  agentRuntimeSaving.value = true
  agentRuntimeError.value = ''
  agentRuntimeMessage.value = ''
  try {
    const normalized = normalizeFixedAgentRuntimeConfig(agentRuntimeConfig.value)
    const saved = await api.saveAgentRuntimeConfig({
      default_backend: normalized.default_backend,
      edit_token: normalized.edit_token,
      backends: normalized.backends.map(item => ({
        slug: item.slug.trim(),
        type: item.type,
        command: item.command?.trim() || null,
        model: item.model?.trim() || null,
      })),
    })
    agentRuntimeConfig.value = normalizeFixedAgentRuntimeConfig(saved)
    agentRuntimeMessage.value = '已保存并刷新运行时配置'
  } catch (e: any) {
    agentRuntimeError.value = e.message || '保存失败'
  }
  agentRuntimeSaving.value = false
}

function openAddCategory() {
  editingCategory.value = false
  categoryExpectedEditToken.value = ''
  categoryForm.value = { category_key: '', name: '', description: '' }
  showCategoryDialog.value = true
}

async function openEditCategory(c: CodeRepoCategory) {
  try {
    categories.value = await api.listCategories()
  } catch {
    return
  }
  const latest = categories.value.find(item => item.category_key === c.category_key)
  if (!latest) return
  c = latest
  editingCategory.value = true
  categoryExpectedEditToken.value = c.edit_token ?? null
  categoryForm.value = { category_key: c.category_key, name: c.name, description: c.description }
  showCategoryDialog.value = true
}

async function saveCategory() {
  categorySaving.value = true
  try {
    await api.upsertCategory({
      ...categoryForm.value,
      expected_edit_token: categoryExpectedEditToken.value,
    })
    showCategoryDialog.value = false
    await loadCategories()
  } catch { /* ignore */ }
  categorySaving.value = false
}

async function deleteCategory(key: string) {
  if (!await confirm({ title: '删除分类', description: `确定删除代码仓库分类「${key}」？已归类仓库可能需要重新整理。`, destructive: true, confirmText: '删除' })) return
  try {
    await api.deleteCategory(key)
    await loadCategories()
  } catch { /* ignore */ }
}

// ── Backend CRUD ──

function openAddBackend() {
  editingBackend.value = false
  backendExpectedEditToken.value = ''
  backendForm.value = { slug: '', backend_type: 'ragflow', base_url: '', api_key: '', timeout: 120, embedding_model_id: '', summary_model_id: '', rerank_model_id: '' }
  backendError.value = ''
  showBackendDialog.value = true
}

async function openEditBackend(b: BackendInfo) {
  try {
    backends.value = await api.listBackends()
  } catch {
    return
  }
  const latest = backends.value.find(item => item.slug === b.slug)
  if (!latest) return
  b = latest
  editingBackend.value = true
  backendExpectedEditToken.value = b.edit_token ?? null
  backendForm.value = {
    slug: b.slug,
    backend_type: b.backend_type,
    base_url: b.base_url || '',
    api_key: '',
    timeout: b.timeout,
    embedding_model_id: b.embedding_model_id || '',
    summary_model_id: b.summary_model_id || '',
    rerank_model_id: b.rerank_model_id || '',
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
      expected_edit_token: backendExpectedEditToken.value,
    }
    if (needsBackendConnection.value) {
      data.base_url = backendForm.value.base_url || null
      if (backendForm.value.api_key) data.api_key = backendForm.value.api_key
    }
    if (supportsModelConfig.value) {
      data.embedding_model_id = backendForm.value.embedding_model_id || null
      data.summary_model_id = backendForm.value.summary_model_id || null
    }
    if (isWeknora.value) {
      data.rerank_model_id = backendForm.value.rerank_model_id || null
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
  if (!await confirm({ title: '删除知识后端', description: `确定删除知识后端「${slug}」？相关知识库后续将无法继续使用该后端。`, destructive: true, confirmText: '删除' })) return
  try {
    await api.deleteBackend(slug)
    await loadBackends()
  } catch { /* ignore */ }
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="flex items-start justify-between gap-4">
          <div>
            <div class="text-sm font-medium">顶层 MCP 工具</div>
            <div class="mt-1 text-xs text-muted-foreground">管理 /mcp 对外直接提供的工具。固定入口 <code>search</code> 和 <code>execute</code> 始终保留；关闭后工具不会出现在 tools/list 或能力目录中，也无法调用。</div>
          </div>
          <Button variant="outline" size="sm" @click="loadTopLevelMcpTools" :disabled="topLevelMcpToolsUpdating !== null">刷新</Button>
        </div>
        <div v-if="topLevelMcpToolsError" class="rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ topLevelMcpToolsError }}</div>
        <div v-else-if="topLevelMcpTools.length === 0" class="py-4 text-center text-sm text-muted-foreground">暂无可配置的顶层 MCP 工具</div>
        <div v-else class="overflow-hidden rounded-md border border-border">
          <table class="w-full">
            <thead>
              <tr class="border-b border-border bg-muted/30">
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">工具</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">说明</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">状态</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-muted-foreground">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="tool in topLevelMcpTools" :key="tool.name" class="border-b border-border/60 last:border-b-0">
                <td class="px-3 py-2 align-top">
                  <div class="font-mono text-sm">{{ tool.name }}</div>
                  <div class="mt-0.5 text-xs text-muted-foreground">{{ tool.title }}</div>
                </td>
                <td class="max-w-xl px-3 py-2 text-xs text-muted-foreground">{{ tool.description }}</td>
                <td class="px-3 py-2"><StatusBadge :status="tool.status" :label="tool.status === 'enabled' ? '已启用' : '已关闭'" /></td>
                <td class="px-3 py-2 text-right">
                  <Button
                    :variant="tool.status === 'enabled' ? 'outline' : 'default'"
                    size="sm"
                    :disabled="topLevelMcpToolsUpdating === tool.name"
                    @click="toggleTopLevelMcpTool(tool)"
                  >{{ topLevelMcpToolsUpdating === tool.name ? '更新中...' : tool.status === 'enabled' ? '临时关闭' : '重新启用' }}</Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

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
          <div class="text-sm shrink-0 whitespace-nowrap">UA 插件更新 <span class="text-xs text-muted-foreground">(每周默认)</span></div>
          <Input v-model="syncConfig.ua_plugin_update_cron" placeholder="0 3 * * 0" class="w-40 font-mono text-xs" />
          <span v-if="uaPluginUpdateNextRuns" class="text-xs text-muted-foreground font-mono">{{ uaPluginUpdateNextRuns }}</span>
          <span v-else class="text-xs text-destructive">表达式无效</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,10rem)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">Memory 插件更新 <span class="text-xs text-muted-foreground">(claude-mem)</span></div>
          <Input v-model="syncConfig.claude_mem_plugin_update_cron" placeholder="30 3 * * 0" class="w-40 font-mono text-xs" />
          <span v-if="claudeMemPluginUpdateNextRuns" class="text-xs text-muted-foreground font-mono">{{ claudeMemPluginUpdateNextRuns }}</span>
          <span v-else class="text-xs text-destructive">表达式无效</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">MCP 超时 <span class="text-xs text-muted-foreground">(秒)</span></div>
          <Input v-model.number="syncConfig.mcp_timeout_seconds" type="number" min="1" placeholder="150" class="w-32 font-mono text-sm" />
          <span v-if="mcpTimeoutValid" class="text-xs text-muted-foreground">HTTP MCP 与 CodeGraph MCP 的统一超时上限（默认 150）</span>
          <span v-else class="text-xs text-destructive">请输入正整数</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">代码理解超时 <span class="text-xs text-muted-foreground">(分钟)</span></div>
          <Input v-model.number="syncConfig.understand_timeout_minutes" type="number" min="1" placeholder="120" class="w-32 font-mono text-sm" />
          <span v-if="understandTimeoutValid" class="text-xs text-muted-foreground">单次 Understand Anything 分析的墙钟上限，超时即终止（默认 120）</span>
          <span v-else class="text-xs text-destructive">请输入正整数</span>
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
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">工作流全局并发 <span class="text-xs text-muted-foreground">(运行数)</span></div>
          <Input v-model.number="syncConfig.workflow_max_concurrent_runs" type="number" min="1" placeholder="4" class="w-32 font-mono text-sm" />
          <span v-if="maxConcurrentRunsValid" class="text-xs text-muted-foreground">自动调度中，所有工作流合计最多同时运行 {{ syncConfig.workflow_max_concurrent_runs }} 个</span>
          <span v-else class="text-xs text-destructive">请输入正整数</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">单工作流并发 <span class="text-xs text-muted-foreground">(运行数)</span></div>
          <Input v-model.number="syncConfig.workflow_max_concurrent_runs_per_workflow" type="number" min="1" placeholder="2" class="w-32 font-mono text-sm" />
          <span v-if="maxConcurrentRunsPerWorkflowValid" class="text-xs text-muted-foreground">自动调度中，同一工作流最多同时运行 {{ syncConfig.workflow_max_concurrent_runs_per_workflow }} 个</span>
          <span v-else class="text-xs text-destructive">请输入正整数</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">工作流运行时长上限 <span class="text-xs text-muted-foreground">(分钟)</span></div>
          <Input v-model.number="syncConfig.workflow_max_runtime_minutes" type="number" min="0" placeholder="30" class="w-32 font-mono text-sm" />
          <span v-if="workflowRuntimeValid" class="text-xs text-muted-foreground">{{ syncConfig.workflow_max_runtime_minutes > 0 ? `单次工作流运行超过 ${syncConfig.workflow_max_runtime_minutes} 分钟即强制终止` : '不限（0，回退默认上限）' }}</span>
          <span v-else class="text-xs text-destructive">请输入非负整数</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">任务重开期限 <span class="text-xs text-muted-foreground">(天)</span></div>
          <Input v-model.number="syncConfig.workflow_task_rerun_days" type="number" min="0" placeholder="30" class="w-32 font-mono text-sm" />
          <span v-if="taskRerunDaysValid" class="text-xs text-muted-foreground">同 workflow/task/version 完成后超过 {{ syncConfig.workflow_task_rerun_days }} 天，set task 会重新置为待处理</span>
          <span v-else class="text-xs text-destructive">请输入非负整数</span>
        </div>
        <div class="grid grid-cols-[12rem_minmax(0,auto)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">运行日志保留 <span class="text-xs text-muted-foreground">(天)</span></div>
          <Input v-model.number="syncConfig.log_retention_days" type="number" min="1" placeholder="180" class="w-32 font-mono text-sm" />
          <span v-if="logRetentionValid" class="text-xs text-muted-foreground">结构化调用日志与 Agent 运行记录仅保留最近 {{ syncConfig.log_retention_days }} 天</span>
          <span v-else class="text-xs text-destructive">请输入正整数</span>
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
                <StatusBadge :status="schedulerStatus.code_sync.running ? 'running' : 'disabled'" :label="schedulerStatus.code_sync.running ? '运行中' : '已暂停'" />
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
                  <tr v-for="j in schedulerStatus.code_sync.jobs" :key="j.repo_key" class="border-b border-border/60">
                    <td class="px-3 py-2 text-sm font-mono">{{ j.repo_key }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(j.next_run_at) }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">
                      <StatusBadge v-if="j.progress" class="mr-2" :status="runBadgeStatus(j.progress.status)" :label="runLabel(j.progress.status)" />
                      <span>{{ j.progress?.message || '暂无执行记录' }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">代码理解</span>
                <StatusBadge :status="schedulerStatus.understand.running ? 'running' : 'disabled'" :label="schedulerStatus.understand.running ? '运行中' : '已暂停'" />
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
                  <tr v-for="j in schedulerStatus.understand.jobs" :key="j.repo_key" class="border-b border-border/60">
                    <td class="px-3 py-2 text-sm font-mono">{{ j.repo_key }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(j.next_run_at) }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">
                      <StatusBadge v-if="j.progress" class="mr-2" :status="runBadgeStatus(j.progress.status)" :label="runLabel(j.progress.status)" />
                      <span>{{ j.progress?.message || '暂无执行记录' }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">插件更新</span>
                <StatusBadge :status="schedulerStatus.plugin_update.running ? 'running' : 'disabled'" :label="schedulerStatus.plugin_update.running ? '运行中' : '已暂停'" />
              </div>
              <div v-if="schedulerStatus.plugin_update.jobs.length === 0" class="py-2 text-center text-xs text-muted-foreground">
                {{ schedulerStatus.plugin_update.running ? '没有配置插件 Git URL' : '—' }}
              </div>
              <table v-else class="w-full">
                <thead>
                  <tr class="border-b border-border">
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">插件</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">下次执行</th>
                    <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">最近进度</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in schedulerStatus.plugin_update.jobs" :key="j.plugin_key || j.repo_key" class="border-b border-border/60">
                    <td class="px-3 py-2 text-sm font-mono">{{ j.plugin_key || j.repo_key }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(j.next_run_at) }}</td>
                    <td class="px-3 py-2 text-xs text-muted-foreground">
                      <StatusBadge v-if="j.progress" class="mr-2" :status="runBadgeStatus(j.progress.status)" :label="runLabel(j.progress.status)" />
                      <span>{{ j.progress?.message || '暂无执行记录' }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-3">
                <span class="text-xs text-muted-foreground">知识同步</span>
                <StatusBadge :status="schedulerStatus.doc_sync?.running ? 'running' : 'disabled'" :label="schedulerStatus.doc_sync?.running ? '运行中' : '已暂停'" />
                <span v-if="schedulerStatus.doc_sync?.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.doc_sync.cron }}</span>
              </div>
              <div class="space-y-2 rounded-md border border-border bg-muted/20 p-3">
                <div class="flex items-center gap-2 text-xs">
                  <span class="text-muted-foreground">当前执行</span>
                  <StatusBadge v-if="schedulerStatus.doc_sync?.current_run" :status="runBadgeStatus(schedulerStatus.doc_sync.current_run.status)" :label="runLabel(schedulerStatus.doc_sync.current_run.status)" />
                  <span class="text-muted-foreground">{{ docRunText(schedulerStatus.doc_sync?.current_run) }}</span>
                </div>
                <div class="flex items-center gap-2 text-xs">
                  <span class="text-muted-foreground">最近一次</span>
                  <StatusBadge v-if="schedulerStatus.doc_sync?.last_run" :status="runBadgeStatus(schedulerStatus.doc_sync.last_run.status)" :label="runLabel(schedulerStatus.doc_sync.last_run.status)" />
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
                <StatusBadge :status="schedulerStatus.workflow?.running ? 'running' : 'disabled'" :label="schedulerStatus.workflow?.running ? '运行中' : '已暂停'" />
                <span class="font-mono text-xs text-muted-foreground">
                  每日 {{ schedulerStatus.workflow?.start_time || '--' }} → {{ schedulerStatus.workflow?.stop_time || '--' }}
                </span>
                <Badge v-if="schedulerStatus.workflow?.in_window" variant="secondary" class="bg-info-soft text-info-soft-fg">窗口内</Badge>
                <span class="text-xs text-muted-foreground">全局并发 {{ schedulerStatus.workflow?.max_concurrent_runs || 4 }}，单工作流 {{ schedulerStatus.workflow?.max_concurrent_runs_per_workflow || 2 }}</span>
              </div>
              <div class="space-y-2 rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
                <div>正在执行：{{ runningWorkflowText }}</div>
                <div>今日结束：{{ schedulerStatus.workflow?.finished_today?.length ? schedulerStatus.workflow.finished_today.join(', ') : '无' }}</div>
                <div>运行计数：{{ runCountText }}</div>
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- Coding Agent 运行配置 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between gap-4">
          <div>
            <div class="text-sm font-medium">Coding Agent 运行配置</div>
            <div class="mt-1 text-xs text-muted-foreground">普通 Agent 运行三选一；工作流和 Understand Anything 当前仍固定使用 Claude</div>
          </div>
          <div class="flex gap-2">
            <Button variant="outline" size="sm" @click="loadAgentRuntimeConfig()">刷新</Button>
          </div>
        </div>

        <div class="grid grid-cols-[12rem_minmax(0,20rem)_1fr] items-center gap-4">
          <div class="text-sm shrink-0 whitespace-nowrap">默认后端</div>
          <select v-model="agentRuntimeConfig.default_backend" class="h-9 rounded-md border border-input bg-background px-3 text-sm">
            <option v-for="backend in agentRuntimeConfig.backends" :key="backend.slug" :value="backend.slug">
              {{ backend.slug }}
            </option>
          </select>
          <span class="text-xs text-muted-foreground">保存后立即影响普通 Agent 运行</span>
        </div>

        <div class="rounded-md border border-border">
          <table class="w-full">
            <thead>
              <tr class="border-b border-border bg-muted/30">
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">标识</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">类型</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">命令</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">模型</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="backend in agentRuntimeConfig.backends" :key="backend.slug" class="border-b border-border/60">
                <td class="px-3 py-2 font-mono text-sm">{{ backend.slug }}</td>
                <td class="px-3 py-2 text-sm">{{ backend.type }}</td>
                <td class="px-3 py-2">
                  <Input
                    v-if="backend.type !== 'claude'"
                    :model-value="backend.command || ''"
                    :placeholder="backend.type"
                    class="h-8 font-mono text-xs"
                    @update:model-value="backend.command = String($event || '')"
                  />
                  <span v-else class="text-xs text-muted-foreground">内置</span>
                </td>
                <td class="px-3 py-2">
                  <Input :model-value="backend.model || ''" placeholder="默认模型" class="h-8 font-mono text-xs" @update:model-value="backend.model = String($event || '')" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="flex items-center gap-3">
          <Button @click="saveAgentRuntimeConfig()" :disabled="agentRuntimeSaving" size="sm">
            {{ agentRuntimeSaving ? '保存中...' : '保存配置' }}
          </Button>
          <span v-if="agentRuntimeError" class="text-xs text-destructive">{{ agentRuntimeError }}</span>
          <span v-else-if="agentRuntimeMessage" class="text-xs text-success">{{ agentRuntimeMessage }}</span>
          <span v-else class="text-xs text-muted-foreground">保存到 server.toml 的 [agents] 区块</span>
        </div>
      </CardContent>
    </Card>

    <!-- Claude Mem 运行配置 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between gap-4">
          <div>
            <div class="text-sm font-medium">Claude Mem 运行配置</div>
            <div class="mt-1 text-xs text-muted-foreground">所有 Memory Block 共享这份配置，首次缺省会从 ~/.claude/settings.json 生成</div>
          </div>
          <Button variant="outline" size="sm" @click="loadClaudeMemConfig()">刷新</Button>
        </div>

        <div v-if="!claudeMemConfig" class="py-4 text-center text-sm text-muted-foreground">无法获取 Claude Mem 配置</div>
        <div v-else class="space-y-4">
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">共享 .env</div>
            <div class="flex min-w-0 items-center gap-2">
              <span class="truncate font-mono text-xs text-muted-foreground">{{ claudeMemConfig.env_file_path }}</span>
              <StatusBadge :status="claudeMemConfig.env_file_exists ? 'enabled' : 'disabled'" :label="claudeMemConfig.env_file_exists ? '已生成' : '未生成'" />
            </div>
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">Provider</div>
            <div class="flex items-center gap-2 text-sm">
              <Badge variant="outline" class="font-mono text-[11px]">{{ claudeMemConfig.provider }}</Badge>
              <span class="text-xs text-muted-foreground">auth: {{ claudeMemConfig.auth_method }}</span>
              <span class="text-xs text-muted-foreground">mode: {{ claudeMemConfig.mode }}</span>
            </div>
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">Base URL</div>
            <Input v-model="claudeMemForm.base_url" placeholder="https://open.bigmodel.cn/api/anthropic" class="font-mono text-xs" />
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">Model</div>
            <Input v-model="claudeMemForm.model" placeholder="glm-5.2" class="font-mono text-xs" />
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">Auth Token</div>
            <div class="flex items-center gap-3">
              <Input v-model="claudeMemForm.auth_token" type="password" placeholder="留空保持不变" class="font-mono text-xs" :disabled="claudeMemForm.clear_auth_token" />
              <StatusBadge :status="claudeMemConfig.has_auth_token ? 'enabled' : 'disabled'" :label="claudeMemConfig.has_auth_token ? '已配置' : '未配置'" />
              <label class="flex items-center gap-2 text-xs text-muted-foreground">
                <input v-model="claudeMemForm.clear_auth_token" type="checkbox" class="h-4 w-4" />
                清除
              </label>
            </div>
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm shrink-0 whitespace-nowrap">API Key</div>
            <div class="flex items-center gap-3">
              <Input v-model="claudeMemForm.api_key" type="password" placeholder="留空保持不变" class="font-mono text-xs" :disabled="claudeMemForm.clear_api_key" />
              <StatusBadge :status="claudeMemConfig.has_api_key ? 'enabled' : 'disabled'" :label="claudeMemConfig.has_api_key ? '已配置' : '未配置'" />
              <label class="flex items-center gap-2 text-xs text-muted-foreground">
                <input v-model="claudeMemForm.clear_api_key" type="checkbox" class="h-4 w-4" />
                清除
              </label>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <Button @click="saveClaudeMemConfig()" :disabled="claudeMemSaving" size="sm">
              {{ claudeMemSaving ? '保存中...' : '保存配置' }}
            </Button>
            <span v-if="claudeMemError" class="text-xs text-destructive">{{ claudeMemError }}</span>
            <span v-else class="text-xs text-muted-foreground">新配置会在下一次 worker 启动或 hook 调用时生效</span>
          </div>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between gap-4">
          <div>
            <div class="text-sm font-medium">公共模型配置</div>
            <div class="mt-1 text-xs text-muted-foreground">全局共用的小模型连接，用于把问题提炼为工作流产物检索短句</div>
          </div>
          <Button variant="outline" size="sm" @click="loadRetrievalProbeLlmConfig">刷新</Button>
        </div>
        <div v-if="!retrievalProbeLlmConfig" class="py-4 text-center text-sm text-muted-foreground">{{ retrievalProbeLlmError || '无法获取配置' }}</div>
        <div v-else class="space-y-4">
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm">Base URL</div>
            <Input v-model="retrievalProbeLlmForm.base_url" placeholder="http://127.0.0.1:8000/v1" class="font-mono text-xs" />
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm">全量探测关键词模型</div>
            <Input v-model="retrievalProbeLlmForm.model" placeholder="qwen2.5-3b-instruct" class="font-mono text-xs" />
          </div>
          <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
            <div class="text-sm">API Key</div>
            <div class="flex items-center gap-3">
              <Input v-model="retrievalProbeLlmForm.api_key" type="password" placeholder="留空保持不变" class="font-mono text-xs" :disabled="retrievalProbeLlmForm.clear_api_key" />
              <StatusBadge :status="retrievalProbeLlmConfig.api_key_set ? 'enabled' : 'disabled'" :label="retrievalProbeLlmConfig.api_key_set ? '已配置' : '未配置'" />
              <label class="flex items-center gap-2 text-xs text-muted-foreground"><input v-model="retrievalProbeLlmForm.clear_api_key" type="checkbox" class="h-4 w-4" />清除</label>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <Button @click="saveRetrievalProbeLlmConfig" :disabled="retrievalProbeLlmSaving" size="sm">{{ retrievalProbeLlmSaving ? '保存中...' : '保存配置' }}</Button>
            <span v-if="retrievalProbeLlmError" class="text-xs text-destructive">{{ retrievalProbeLlmError }}</span>
            <span v-else class="text-xs text-muted-foreground">模型抽取最多 10 秒，完整探测最多 20 秒；当前仅搜索工作流产物。</span>
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
              <tr v-for="b in backends" :key="b.slug" class="border-b border-border/60">
                <td class="px-3 py-2 font-mono text-sm">{{ b.slug }}</td>
                <td class="px-3 py-2 text-sm">{{ b.backend_type }}</td>
                <td class="px-3 py-2 text-xs text-muted-foreground truncate max-w-[250px]">{{ b.base_url || '—' }}</td>
                <td class="px-3 py-2">
                  <StatusBadge
                    :status="b.runtime_status === 'active' ? 'running' : 'error'"
                    :label="b.runtime_status === 'active' ? '运行中' : '未激活'" />
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
        <div class="flex items-center gap-3">
          <div class="text-sm shrink-0 whitespace-nowrap">Memory Git URL <span class="text-xs text-muted-foreground">(claude-mem)</span></div>
          <Input v-model="syncConfig.claude_mem_git_url" placeholder="https://github.com/thedotmack/claude-mem.git" class="font-mono text-xs flex-1" />
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
              <tr v-for="c in categories" :key="c.category_key" class="border-b border-border/60">
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
          <div v-if="backendError" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ backendError }}</div>
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
          <div v-if="isWeknora" class="space-y-2">
            <label class="text-sm font-medium">Rerank Model ID<span class="text-muted-foreground font-normal">（可选，hybrid 类 agent 需要）</span></label>
            <Input v-model="backendForm.rerank_model_id" placeholder="留空则不自动配置 rerank" />
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
