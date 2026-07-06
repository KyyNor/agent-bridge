<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Check, HelpCircle, Play, Plus, RotateCcw, Save, Trash2, WandSparkles } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { DesignAgentResponse, ManagedScript, ProjectProfile, ScriptDesignResult, ScriptRun, WorkflowDefinition } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import CodeMirror from '../../components/CodeMirror.vue'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Textarea } from '../../components/ui/textarea'
import { confirm } from '../../composables/useConfirm'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select'
import { formatLocalDatetime } from '../../lib/time'
import JsonViewer from '../../components/JsonViewer.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

const props = defineProps<{ routeKey: string }>()

const scripts = ref<ManagedScript[]>([])
const profiles = ref<ProjectProfile[]>([])
const workflows = ref<WorkflowDefinition[]>([])
const loading = ref(true)
const error = ref('')
const showGuide = ref(false)
const scriptPage = ref(1)
const scriptPageSize = ref(10)

// 编辑模式表单状态
const form = ref(emptyForm())
const formError = ref('')
const formLoading = ref(false)
const saving = ref(false)
const scriptNotFound = ref(false)

// 运行状态
const runs = ref<ScriptRun[]>([])
const runPage = ref(1)
const runPageSize = ref(10)
const runsLoading = ref(false)
const runError = ref('')
const testing = ref(false)
const testParams = ref('{\n  "limit": 5\n}')
const testTimeout = ref<number | undefined>(30)
const testProfileKey = ref('__default__')
const testWorkflowKey = ref('__none__')
const testWorkflowRunId = ref('')
const runDetail = ref<ScriptRun | null>(null)
const runDetailLoading = ref(false)
const showDesigner = ref(false)
const designMode = ref<'create' | 'modify'>('modify')
const designPrompt = ref('')
const designing = ref(false)
const designError = ref('')
const designResponse = ref<DesignAgentResponse<ScriptDesignResult> | null>(null)

const mode = computed<'list' | 'edit'>(() => (props.routeKey ? 'edit' : 'list'))
const isNew = computed(() => props.routeKey === 'new')
const editingKey = computed(() => (isNew.value ? '' : props.routeKey))

const ownerKeyOptions = computed(() => {
  if (form.value.owner_type === 'profile') return profiles.value.map(p => ({ value: p.profile_key, label: p.name }))
  if (form.value.owner_type === 'workflow') return workflows.value.map(w => ({ value: w.workflow_key, label: w.name }))
  return []
})

// 当前编辑的脚本（已保存版本，用于判断归属/状态展示）
const editingScript = computed(() =>
  editingKey.value ? scripts.value.find(s => s.script_key === editingKey.value) || null : null,
)
const scriptDesignDraft = computed(() => designResponse.value?.result?.script || null)
const pagedScripts = computed(() => paginate(scripts.value, scriptPage.value, scriptPageSize.value))
const pagedRuns = computed(() => paginate(runs.value, runPage.value, runPageSize.value))

onMounted(async () => {
  await loadAll()
})

// 进入/切换编辑页时加载脚本
watch(
  () => props.routeKey,
  async (key) => {
    if (!key) return
    formError.value = ''
    scriptNotFound.value = false
    if (key === 'new') {
      form.value = emptyForm()
      runs.value = []
      runDetail.value = null
      return
    }
    formLoading.value = true
    try {
      const detail = await api.getScript(key)
      form.value = {
        script_key: detail.script_key,
        name: detail.name,
        description: detail.description,
        language: detail.language,
        code: detail.code || '',
        status: detail.status,
        owner_type: detail.owner_type,
        owner_key: detail.owner_key,
      }
      await loadRuns()
      runDetail.value = runs.value[0] || null
    } catch (e: unknown) {
      scriptNotFound.value = true
      form.value = emptyForm()
      formError.value = errorMessage(e)
    } finally {
      formLoading.value = false
    }
  },
  { immediate: true },
)

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [scriptList, profileList, workflowList] = await Promise.all([
      api.listScripts(),
      api.listProfiles(),
      api.listWorkflows(),
    ])
    scripts.value = scriptList
    profiles.value = profileList
    workflows.value = workflowList
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function reloadScripts() {
  scripts.value = await api.listScripts()
}

function emptyForm() {
  return {
    script_key: '',
    name: '',
    description: '',
    language: 'python',
    code: '',
    status: 'active',
    owner_type: 'system',
    owner_key: '',
  }
}

function goList() {
  window.location.hash = 'scripts'
}

function openCreate() {
  window.location.hash = 'scripts/new'
}

function openEdit(item: ManagedScript) {
  window.location.hash = 'scripts/' + item.script_key
}

async function deleteScript(item: ManagedScript) {
  if (!await confirm({ title: '删除脚本', description: `确定删除脚本「${item.name}」？其运行记录将一并清除。`, destructive: true, confirmText: '删除' })) return
  error.value = ''
  try {
    await api.deleteScript(item.script_key)
    await reloadScripts()
    if (editingKey.value === item.script_key) goList()
  } catch (e: unknown) {
    error.value = errorMessage(e)
  }
}

async function saveScript(): Promise<ManagedScript | null> {
  formError.value = ''
  if (!form.value.script_key || !form.value.name || !form.value.code.trim()) {
    formError.value = '请填写脚本标识、名称和代码'
    return null
  }
  saving.value = true
  try {
    const saved = await api.upsertScript({
      script_key: form.value.script_key,
      name: form.value.name,
      description: form.value.description,
      language: form.value.language,
      code: form.value.code,
      status: form.value.status,
      owner_type: form.value.owner_type,
      owner_key: form.value.owner_type === 'system' ? '' : form.value.owner_key,
    })
    await reloadScripts()
    // 新建或设计 agent 生成了新 key 后同步 URL，避免后续保存落到旧路由上下文。
    if (isNew.value || saved.script_key !== editingKey.value) {
      window.location.hash = 'scripts/' + saved.script_key
    }
    return saved
  } catch (e: unknown) {
    formError.value = errorMessage(e)
    return null
  } finally {
    saving.value = false
  }
}

function openScriptDesigner(mode: 'create' | 'modify' = 'modify') {
  designMode.value = mode
  showDesigner.value = true
  designError.value = ''
}

function scriptDesignerCurrent() {
  if (designMode.value === 'modify') {
    return {
      script_key: form.value.script_key,
      name: form.value.name,
      description: form.value.description,
      language: form.value.language,
      code: form.value.code,
      status: form.value.status,
      owner_type: form.value.owner_type,
      owner_key: form.value.owner_key,
    }
  }
  return {
    language: 'python',
    status: 'active',
    owner_type: form.value.owner_type,
    owner_key: form.value.owner_key,
  }
}

async function runScriptDesigner() {
  designError.value = ''
  if (!designPrompt.value.trim()) {
    designError.value = '请输入提示词'
    return
  }
  designing.value = true
  try {
    designResponse.value = await api.designScript({
      mode: designMode.value,
      prompt: designPrompt.value,
      current: scriptDesignerCurrent(),
      profile_key: form.value.owner_type === 'profile' ? form.value.owner_key : undefined,
    })
    if (!designResponse.value.ok) {
      designError.value = designResponse.value.error || '设计 agent 执行失败'
    }
  } catch (e: unknown) {
    designError.value = errorMessage(e)
  } finally {
    designing.value = false
  }
}

async function acceptScriptDesign() {
  const draft = scriptDesignDraft.value
  if (!draft) return
  form.value = {
    script_key: draft.script_key,
    name: draft.name,
    description: draft.description,
    language: draft.language,
    code: draft.code,
    status: draft.status,
    owner_type: draft.owner_type,
    owner_key: draft.owner_key,
  }
  const saved = await saveScript()
  if (saved) showDesigner.value = false
}

async function runScript() {
  if (testing.value) return
  const key = editingKey.value
  if (!key) {
    // 新建脚本必须先保存
    const saved = await saveScript()
    if (!saved) return
    await doRun(saved.script_key)
  } else {
    await doRun(key)
  }
}

async function doRun(scriptKey: string) {
  let params: Record<string, unknown> = {}
  try {
    const parsed = testParams.value.trim() ? JSON.parse(testParams.value) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      runError.value = 'params 必须是 JSON 对象'
      return
    }
    params = parsed as Record<string, unknown>
  } catch {
    runError.value = 'params 不是合法 JSON'
    return
  }
  const workflowEnabled = testWorkflowKey.value !== '__none__' || !!testWorkflowRunId.value.trim()
  if (workflowEnabled && (testWorkflowKey.value === '__none__' || !testWorkflowRunId.value.trim())) {
    runError.value = '启用 workflow header 时必须同时填写 workflow_key 和 run_id'
    return
  }
  testing.value = true
  runError.value = ''
  try {
    const run = await api.testScript(scriptKey, {
      params,
      timeout_seconds: testTimeout.value,
    }, {
      profile_key: testProfileKey.value && testProfileKey.value !== '__default__' ? testProfileKey.value : undefined,
      workflow_enabled: workflowEnabled,
      workflow_key: testWorkflowKey.value !== '__none__' ? testWorkflowKey.value : undefined,
      workflow_run_id: testWorkflowRunId.value.trim() || undefined,
    })
    await loadRuns()
    runDetail.value = run
  } catch (e: unknown) {
    runError.value = errorMessage(e)
  } finally {
    testing.value = false
  }
}

async function loadRuns() {
  const key = editingKey.value
  if (!key) return
  runsLoading.value = true
  runError.value = ''
  try {
    const result = await api.listScriptRuns(key, 20)
    runs.value = result.runs
  } catch (e: unknown) {
    runError.value = errorMessage(e)
    runs.value = []
  } finally {
    runsLoading.value = false
  }
}

async function openRunDetail(runId: string) {
  runDetailLoading.value = true
  runDetail.value = null
  try {
    runDetail.value = await api.getScriptRun(runId)
  } catch (e: unknown) {
    runError.value = errorMessage(e)
  } finally {
    runDetailLoading.value = false
  }
}

function statusLabel(status: string) {
  if (status === 'active') return '启用'
  if (status === 'disabled') return '停用'
  return status
}

function ownerLabel(item: ManagedScript) {
  if (item.owner_type === 'system') return '系统'
  const key = item.owner_key || item.owner_type
  if (item.owner_type === 'profile') return profileName(key)
  if (item.owner_type === 'workflow') return workflowName(key)
  return key
}

function profileName(key: string) {
  const p = profiles.value.find(i => i.profile_key === key)
  return p ? `${p.name}` : key
}

function workflowName(key: string) {
  const w = workflows.value.find(i => i.workflow_key === key)
  return w ? w.name : key
}

function runStatusLabel(status: string) {
  if (status === 'success') return '成功'
  if (status === 'failed') return '失败'
  return status
}

function runBadgeClass(status: string) {
  if (status === 'success') return 'bg-green-50 text-green-700'
  if (status === 'failed') return 'bg-red-50 text-red-700'
  return ''
}

function prettyJson(value: unknown) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}
</script>

<template>
  <!-- 列表模式 -->
  <div v-if="mode === 'list'" class="space-y-5">
    <div class="flex flex-wrap items-center gap-2">
      <Button variant="outline" @click="showGuide = true">
        <HelpCircle class="mr-1.5 h-4 w-4" />
        使用指引
      </Button>
      <Button @click="openCreate">
        <Plus class="mr-1.5 h-4 w-4" />
        新建脚本
      </Button>
    </div>

    <Dialog v-model:open="showGuide">
      <DialogContent class="w-[96vw] max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>脚本使用指引</DialogTitle>
        </DialogHeader>
        <div class="max-h-[74vh] space-y-4 overflow-auto pr-1 text-sm leading-6 text-muted-foreground">
          <section class="rounded-md border p-4">
            <h3 class="text-sm font-semibold text-foreground">入参与出参</h3>
            <p class="mt-2">
              脚本入口必须实现 <span class="font-mono text-foreground">main(envelope)</span>。页面里的测试运行会把自定义
              <span class="font-mono text-foreground"> params </span> 作为对象传到
              <span class="font-mono text-foreground">envelope["script_params"]</span>，并把 profile / workflow 信息放进
              <span class="font-mono text-foreground">envelope["profile_key"]</span> 与
              <span class="font-mono text-foreground">envelope["workflow"]</span>。
            </p>
            <pre class="mt-3 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">def main(envelope):
    params = envelope["script_params"]
    return {
        "ok": True,
        "received": params,
        "profile": envelope["profile_key"],
        "workflow": envelope["workflow"],
    }</pre>
            <p class="mt-2">
              业务结果必须通过 <span class="font-mono text-foreground">return dict</span> 返回；<span class="font-mono text-foreground">print</span>
              / stdout 只用于日志，不再承担结果协议。
            </p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="text-sm font-semibold text-foreground">execute MCP</h3>
            <p class="mt-2">
              脚本里调用其他能力时，用 runtime helper 暴露的
              <span class="font-mono text-foreground">execute(service, tool_name, params)</span>。这里不能手工传
              <span class="font-mono text-foreground">profile_key</span>，调用一律继承本次执行请求头里的 profile。
            </p>
            <pre class="mt-3 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">from agent_bridge_runtime import execute

def main(envelope):
    result = execute("built-in", "load_skill", {"skill_name": "design_workflow"})
    return {"skill": result["result"]}</pre>
            <p class="mt-2">
              这意味着脚本可以复用能力中心的统一权限校验，调用其他 MCP 时仍然按来源 profile 检查可见性与可执行性。
            </p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="text-sm font-semibold text-foreground">workflow MCP</h3>
            <p class="mt-2">
              如果脚本需要领取任务、写任务或记运行日志，可直接从 helper 引入
              <span class="font-mono text-foreground">workflow_get_task</span>、
              <span class="font-mono text-foreground">workflow_set_task</span>、
              <span class="font-mono text-foreground">workflow_run_log</span>。
              这些 helper 会把来源 workflow headers 透传给顶级 workflow 工具。
            </p>
            <pre class="mt-3 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">from agent_bridge_runtime import workflow_get_task, workflow_run_log

def main(envelope):
    leased = workflow_get_task()
    task = leased["task"]
    workflow_run_log(stage="lease", message="task leased", task_key=task["task_key"] if task else None)
    return {"task": task}</pre>
            <p class="mt-2">
              没有完整 workflow context 时，这些 helper 会直接失败并报
              <span class="font-mono text-foreground">workflow context is required</span>。
            </p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="text-sm font-semibold text-foreground">权限与测试 Header</h3>
            <p class="mt-2">
              脚本管理页的“测试运行”支持传递 profile 与 workflow 相关 headers。profile 会进入
              <span class="font-mono text-foreground">X-Agent-Bridge-MetaMCP-Profile</span>；workflow 测试会带上
              <span class="font-mono text-foreground">X-Agent-Bridge-Workflow</span>、
              <span class="font-mono text-foreground">X-Agent-Bridge-Workflow-Key</span>、
              <span class="font-mono text-foreground">X-Agent-Bridge-Workflow-Run-Id</span>。
            </p>
            <p class="mt-2">权限控制始终以来源 header 为准，而不是脚本代码内部自报。</p>
          </section>

          <section class="rounded-md border p-4">
            <h3 class="text-sm font-semibold text-foreground">让智能体协助编写</h3>
            <p class="mt-2">
              先让智能体读取内置技能，再基于用户需求生成 <span class="font-mono text-foreground">script.py</span>。
            </p>
            <pre class="mt-3 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">execute service='built-in' tool_name='load_skill' params={"skill_name":"design_script"}</pre>
            <p class="mt-2">
              随后要求智能体参照技能内容完成开发，并检查 <span class="font-mono text-foreground">main(envelope)</span>、
              参数读取、返回值格式、MCP 调用、workflow 上下文和权限约束。
            </p>
          </section>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showGuide = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {{ error }}
    </div>

    <Card>
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-medium text-foreground">脚本</div>
            <div class="text-xs text-muted-foreground">{{ scripts.length }} 个</div>
          </div>
        </div>
        <div v-if="loading" class="px-4 py-8 text-sm text-muted-foreground">加载中</div>
        <div v-else-if="!scripts.length" class="px-4 py-8 text-sm text-muted-foreground">暂无脚本</div>
        <div v-else class="divide-y">
          <div v-for="item in pagedScripts" :key="item.script_key" class="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_240px_220px] lg:items-center">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-medium text-foreground">{{ item.name }}</span>
                <Badge variant="outline">{{ statusLabel(item.status) }}</Badge>
                <Badge variant="outline">{{ item.language }}</Badge>
              </div>
              <div class="mt-1 truncate font-mono text-xs text-muted-foreground">{{ item.script_key }}</div>
              <p class="mt-1 line-clamp-2 text-xs text-muted-foreground">{{ item.description || item.code_preview || '无描述' }}</p>
            </div>
            <div class="text-xs text-muted-foreground">
              <div>归属：{{ ownerLabel(item) }}</div>
              <div class="mt-1">更新 {{ formatLocalDatetime(item.updated_at) }}</div>
            </div>
            <div class="flex flex-wrap justify-start gap-2 lg:justify-end">
              <Button variant="outline" size="sm" class="h-8 text-xs" :disabled="item.status !== 'active'" @click="openEdit(item)">
                <Play class="mr-1 h-3.5 w-3.5" />
                编辑/运行
              </Button>
              <Button variant="ghost" size="sm" class="h-8 text-xs text-destructive" @click="deleteScript(item)">
                <Trash2 class="mr-1 h-3.5 w-3.5" />
                删除
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
    <PaginationBar
      v-if="scripts.length"
      v-model:page="scriptPage"
      v-model:page-size="scriptPageSize"
      :total="scripts.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />
  </div>

  <!-- 编辑/运行二级页面 -->
  <div v-else class="space-y-4">
    <!-- 顶栏 -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <Button variant="ghost" size="sm" class="h-8 px-2" @click="goList">
          <ArrowLeft class="mr-1 h-4 w-4" />
          返回
        </Button>
        <div>
          <h2 class="text-lg font-semibold text-foreground">
            {{ isNew ? '新建脚本' : (editingScript?.name || form.name || '编辑脚本') }}
          </h2>
          <p class="font-mono text-xs text-muted-foreground">
            {{ isNew ? '新建后将自动生成 script_key' : editingKey }}
          </p>
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" :disabled="designing" @click="openScriptDesigner('modify')">
          <WandSparkles class="mr-1.5 h-4 w-4" />
          AI 设计
        </Button>
        <Button variant="outline" size="sm" :disabled="saving" @click="saveScript">
          <Save class="mr-1.5 h-4 w-4" />
          {{ saving ? '保存中' : '保存' }}
        </Button>
        <Button size="sm" :disabled="testing || form.status !== 'active'" @click="runScript">
          <Play class="mr-1.5 h-4 w-4" />
          {{ testing ? '运行中' : (isNew ? '保存并运行' : '运行') }}
        </Button>
      </div>
    </div>

    <div v-if="scriptNotFound" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
      无法加载该脚本（可能已被删除或不存在）。请<a class="underline" href="#scripts" @click.prevent="goList">返回列表</a>。
    </div>

    <div v-else class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
      <!-- 左栏：编辑器 -->
      <Card>
        <CardContent class="space-y-4 p-4">
          <div v-if="formLoading" class="py-16 text-center text-sm text-muted-foreground">加载中</div>
          <template v-else>
            <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {{ formError }}
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">脚本标识 script_key</label>
                <Input v-model="form.script_key" placeholder="my_script" :disabled="!isNew" />
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">名称</label>
                <Input v-model="form.name" placeholder="我的脚本" />
              </div>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">描述</label>
              <Input v-model="form.description" placeholder="可选" />
            </div>
            <div class="grid gap-3 md:grid-cols-3">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">状态</label>
                <Select v-model="form.status">
                  <SelectTrigger class="w-full"><SelectValue placeholder="状态" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">启用</SelectItem>
                    <SelectItem value="disabled">停用</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">归属类型</label>
                <Select v-model="form.owner_type">
                  <SelectTrigger class="w-full"><SelectValue placeholder="归属" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="system">系统</SelectItem>
                    <SelectItem value="profile">能力平面</SelectItem>
                    <SelectItem value="workflow">工作流</SelectItem>
                    <SelectItem value="skill">技能</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div v-if="form.owner_type !== 'system'">
                <label class="mb-1 block text-xs text-muted-foreground">归属 key</label>
                <Select v-if="ownerKeyOptions.length" v-model="form.owner_key">
                  <SelectTrigger class="w-full"><SelectValue placeholder="选择" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="opt in ownerKeyOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</SelectItem>
                  </SelectContent>
                </Select>
                <Input v-else v-model="form.owner_key" placeholder="owner_key" />
              </div>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">代码（Python，main(envelope) -&gt; dict）</label>
              <CodeMirror v-model="form.code" />
              <p class="mt-2 text-xs leading-5 text-muted-foreground">
                可直接使用 <span class="font-mono text-foreground">from agent_bridge_runtime import execute, workflow_get_task, workflow_set_task, workflow_run_log</span>。
              </p>
            </div>
          </template>
        </CardContent>
      </Card>

      <!-- 右栏：运行 + 结果（sticky） -->
      <div class="space-y-4 xl:sticky xl:top-4 xl:self-start xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto xl:pr-1">
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="text-sm font-semibold text-foreground">测试运行</div>
            <div v-if="isNew" class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              新建脚本：点击「保存并运行」将先保存再执行。
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">params (JSON 对象)</label>
              <Textarea v-model="testParams" class="min-h-[80px] font-mono text-xs" spellcheck="false" />
            </div>
            <div class="grid gap-3 grid-cols-2">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">超时 (秒)</label>
                <Input v-model.number="testTimeout" type="number" placeholder="30" />
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">能力平面</label>
                <Select v-model="testProfileKey">
                  <SelectTrigger class="w-full"><SelectValue placeholder="默认" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">默认</SelectItem>
                    <SelectItem v-for="p in profiles" :key="p.profile_key" :value="p.profile_key">{{ p.name }}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div class="rounded-md border bg-muted/20 p-3">
              <div class="mb-3 text-xs font-semibold tracking-wide text-foreground">Workflow Headers</div>
              <div class="grid gap-3">
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">workflow_key</label>
                  <Select v-model="testWorkflowKey">
                    <SelectTrigger class="w-full"><SelectValue placeholder="不传递" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">不传递</SelectItem>
                      <SelectItem v-for="w in workflows" :key="w.workflow_key" :value="w.workflow_key">{{ w.name }}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">run_id</label>
                  <Input v-model="testWorkflowRunId" placeholder="例如 run_1" />
                </div>
              </div>
              <p class="mt-2 text-xs leading-5 text-muted-foreground">
                填写完整后会透传 workflow 运行上下文；仅填写一部分时，页面会先阻止提交。
              </p>
            </div>
            <div class="flex items-center gap-3">
              <Button size="sm" :disabled="testing || form.status !== 'active'" @click="runScript">
                <Play class="mr-1.5 h-4 w-4" />
                {{ testing ? '运行中' : (isNew ? '保存并运行' : '运行') }}
              </Button>
              <div v-if="runError" class="text-xs text-destructive">{{ runError }}</div>
            </div>
          </CardContent>
        </Card>

        <!-- 运行结果（内联，取代弹窗） -->
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="flex items-center justify-between gap-2">
              <div class="text-sm font-semibold text-foreground">运行结果</div>
              <Button v-if="runDetail" variant="ghost" size="sm" class="h-7 px-2 text-xs" @click="runDetail = null">
                清除
              </Button>
            </div>
            <div v-if="runDetailLoading" class="py-6 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runDetail" class="py-6 text-center text-sm text-muted-foreground">暂无运行结果，点击「运行」或在下方记录中选择</div>
            <div v-else class="space-y-3">
              <div class="flex flex-wrap items-center gap-2">
                <Badge variant="outline" :class="runBadgeClass(runDetail.status)">{{ runStatusLabel(runDetail.status) }}</Badge>
                <Badge variant="outline">{{ runDetail.run_type }}</Badge>
                <span class="font-mono text-xs text-muted-foreground">{{ runDetail.run_id }}</span>
                <span class="text-xs text-muted-foreground">{{ runDetail.duration_ms }} ms</span>
                <span v-if="runDetail.exit_code !== null" class="text-xs text-muted-foreground">exit {{ runDetail.exit_code }}</span>
              </div>
              <div v-if="runDetail.error_message" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {{ runDetail.error_message }}
              </div>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">result</div>
                <JsonViewer :value="runDetail.result" max-height="192px" />
              </section>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">stdout</div>
                <pre class="max-h-48 overflow-auto rounded bg-background p-2 text-xs">{{ runDetail.stdout }}</pre>
              </section>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">stderr</div>
                <pre class="max-h-48 overflow-auto rounded bg-background p-2 text-xs">{{ runDetail.stderr }}</pre>
              </section>
            </div>
          </CardContent>
        </Card>

        <!-- 运行记录 -->
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold">运行记录</h3>
              <Button variant="outline" size="sm" class="h-7 text-xs" :disabled="runsLoading || isNew" @click="loadRuns">
                <RotateCcw class="mr-1 h-3 w-3" />
                {{ runsLoading ? '刷新中' : '刷新' }}
              </Button>
            </div>
            <div v-if="isNew" class="rounded-md border px-3 py-4 text-xs text-muted-foreground">保存脚本后将显示运行记录</div>
            <div v-else-if="runsLoading && !runs.length" class="py-4 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runs.length" class="rounded-md border px-3 py-4 text-sm text-muted-foreground">暂无运行记录</div>
            <div v-else class="grid gap-2">
              <button
                v-for="run in pagedRuns"
                :key="run.run_id"
                class="rounded-md border px-3 py-2 text-left transition hover:bg-muted/50"
                :class="runDetail?.run_id === run.run_id ? 'border-primary/40 bg-primary/5' : ''"
                @click="openRunDetail(run.run_id)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate font-mono text-xs">{{ run.run_id }}</span>
                  <Badge variant="outline" :class="runBadgeClass(run.status)">{{ runStatusLabel(run.status) }}</Badge>
                </div>
                <div class="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                  <span>{{ run.run_type }}</span>
                  <span>{{ run.duration_ms }} ms</span>
                  <span v-if="run.exit_code !== null">exit {{ run.exit_code }}</span>
                </div>
                <div class="mt-1 text-xs text-muted-foreground">{{ formatLocalDatetime(run.created_at) }}</div>
              </button>
            </div>
            <PaginationBar
              v-if="runs.length"
              v-model:page="runPage"
              v-model:page-size="runPageSize"
              :total="runs.length"
              :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
            />
          </CardContent>
        </Card>
      </div>
    </div>

    <aside
      v-if="showDesigner"
      class="fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l bg-background shadow-xl"
    >
      <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
        <div>
          <div class="text-sm font-semibold text-foreground">脚本设计 Agent</div>
          <div class="font-mono text-xs text-muted-foreground">design_script</div>
        </div>
        <Button variant="ghost" size="sm" class="h-8 px-2" :disabled="designing" @click="showDesigner = false">关闭</Button>
      </div>
      <div class="flex-1 space-y-4 overflow-auto p-4">
        <div class="grid grid-cols-2 gap-2 rounded-md border bg-muted/20 p-1">
          <button
            class="rounded px-3 py-2 text-sm transition"
            :class="designMode === 'modify' ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground'"
            @click="designMode = 'modify'"
          >
            修改
          </button>
          <button
            class="rounded px-3 py-2 text-sm transition"
            :class="designMode === 'create' ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground'"
            @click="designMode = 'create'"
          >
            新建
          </button>
        </div>

        <div>
          <label class="mb-1 block text-xs text-muted-foreground">提示词</label>
          <Textarea
            v-model="designPrompt"
            class="min-h-32 text-sm"
            placeholder="描述希望 agent 设计或修改的脚本目标"
          />
        </div>
        <Button class="w-full" :disabled="designing" @click="runScriptDesigner">
          <WandSparkles class="mr-1.5 h-4 w-4" />
          {{ designing ? '生成中' : '生成方案' }}
        </Button>

        <div v-if="designError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {{ designError }}
        </div>

        <section v-if="designResponse?.result" class="space-y-3 rounded-md border p-3">
          <div class="flex items-center justify-between gap-2">
            <div class="text-sm font-semibold">生成结果</div>
            <Badge v-if="designResponse.run_key" variant="outline">{{ designResponse.run_key }}</Badge>
          </div>
          <p class="text-sm text-muted-foreground">{{ designResponse.result.summary }}</p>
          <div v-if="designResponse.result.notes?.length" class="space-y-1 text-xs text-muted-foreground">
            <div v-for="note in designResponse.result.notes" :key="note">· {{ note }}</div>
          </div>
          <div v-if="scriptDesignDraft" class="grid gap-2 text-xs">
            <div class="rounded-md border bg-muted/20 p-2">
              <div class="font-mono font-medium text-foreground">{{ scriptDesignDraft.script_key }}</div>
              <div class="mt-1 text-muted-foreground">{{ scriptDesignDraft.name }}</div>
            </div>
            <pre class="max-h-96 overflow-auto rounded-md border bg-muted/20 p-3 text-xs">{{ scriptDesignDraft.code }}</pre>
          </div>
        </section>
      </div>
      <div class="flex items-center justify-end gap-2 border-t p-4">
        <Button variant="outline" :disabled="designing" @click="showDesigner = false">取消</Button>
        <Button :disabled="!scriptDesignDraft || saving" @click="acceptScriptDesign">
          <Check class="mr-1.5 h-4 w-4" />
          {{ saving ? '保存中' : '采纳并保存' }}
        </Button>
      </div>
    </aside>
  </div>
</template>
