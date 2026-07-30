<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowLeft, Check, HelpCircle, Play, Plus, RotateCcw, Save, Trash2, WandSparkles } from '@lucide/vue'
import { api } from '../../api/client'
import type { DesignAgentResponse, ManagedScript, ProjectProfile, ScriptDesignResult, ScriptRun, SyntaxCheckResult, WorkflowDefinition, WorkflowRun } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import CodeMirror from '../../components/CodeMirror.vue'
import EditorActionBar from '../../components/EditorActionBar.vue'
import RevisionHistoryPanel from '../../components/version/RevisionHistoryPanel.vue'
import SchemaFieldEditor from '../../components/SchemaFieldEditor.vue'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Textarea } from '../../components/ui/textarea'
import StatusBadge from '../../components/StatusBadge.vue'
import { confirm } from '../../composables/useConfirm'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select'
import { schemaToFields, type SchemaField } from '../../lib/schemaFields'
import {
  canDeleteScript,
  canDisableScript,
  canEditScriptContract,
  canResetScript,
  DEFAULT_SCRIPT_CODE,
  isBuiltInScriptFamily,
  mergeScriptDesignDraft,
  toScriptFormState,
  toScriptUpsertPayload,
  type ScriptEditableFields,
} from '../../lib/scriptManagement'
import { formatLocalDatetime } from '../../lib/time'
import JsonViewer from '../../components/JsonViewer.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'
import { navigateTo, parseSubRoute, registerNavigationGuard, routeReturnTo } from '../../lib/navigation'

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
const form = ref<ScriptEditableFields>(emptyForm())
type SchemaFieldEditorHandle = { validate: () => boolean; getValidationMessage: () => string }
const inputSchemaEditor = ref<SchemaFieldEditorHandle | null>(null)
const outputSchemaEditor = ref<SchemaFieldEditorHandle | null>(null)
const outputSchemaEnabled = ref(false)
const formError = ref('')
const formLoading = ref(false)
const saving = ref(false)
const scriptNotFound = ref(false)
const formBaseline = ref('')
const expectedEditToken = ref<string | null>('')

// 运行状态
const runs = ref<ScriptRun[]>([])
const runPage = ref(1)
const runPageSize = ref(10)
const runsLoading = ref(false)
const runError = ref('')
const testing = ref(false)
// 测试运行参数：默认按「输入字段」逐个填写；原始 JSON 作为折叠的高级入口兜底。
const testParamsByField = ref<Record<string, string>>({})
const testParamsRaw = ref('{\n  "limit": 5\n}')
const showRawParams = ref(false)
const testTimeout = ref<number | undefined>(30)
const testProfileKey = ref('__default__')
const testWorkflowKey = ref('__none__')
const testWorkflowRunId = ref('')
const workflowRuns = ref<Record<string, WorkflowRun[]>>({})
const workflowRunsLoading = ref(false)
const workflowRunError = ref('')
let workflowRunRequestId = 0
const runDetail = ref<ScriptRun | null>(null)
const runDetailLoading = ref(false)
const showDesigner = ref(false)
const designMode = ref<'create' | 'modify'>('modify')
const designPrompt = ref('')
const designing = ref(false)
const designError = ref('')
const designResponse = ref<DesignAgentResponse<ScriptDesignResult> | null>(null)
const designRunKey = ref('')
const designStopRequested = ref(false)

const routeParts = computed(() => parseSubRoute(props.routeKey).segments)
const mode = computed<'list' | 'edit'>(() => (props.routeKey ? 'edit' : 'list'))
const isNew = computed(() => routeParts.value[0] === 'new')
const editingKey = computed(() => (isNew.value ? '' : routeParts.value[0] || ''))
const requestedRunId = computed(() => routeParts.value[1] === 'run' ? routeParts.value[2] || '' : '')
const returnToRoute = computed(() => routeReturnTo(props.routeKey))

function snapshotForm() {
  return JSON.stringify({ form: form.value, outputSchemaEnabled: outputSchemaEnabled.value })
}

const formDirty = computed(() =>
  mode.value === 'edit' && !formLoading.value && Boolean(formBaseline.value) && snapshotForm() !== formBaseline.value,
)

const removeNavigationGuard = registerNavigationGuard(() => {
  if (!formDirty.value) return true
  return confirm({
    title: '放弃未保存修改',
    description: '当前脚本有未保存修改，确定离开吗？',
    confirmText: '放弃并返回',
  })
})
onUnmounted(removeNavigationGuard)

const ownerKeyOptions = computed(() => {
  if (form.value.owner_type === 'profile') return profiles.value.map(p => ({ value: p.profile_key, label: p.name }))
  if (form.value.owner_type === 'workflow') return workflows.value.map(w => ({ value: w.workflow_key, label: w.name }))
  return []
})

// 当前编辑的脚本（已保存版本，用于判断归属/状态展示）
const editingScript = computed(() =>
  editingKey.value ? scripts.value.find(s => s.script_key === editingKey.value) || null : null,
)

// 版本历史面板开关
const showHistory = ref(false)
// 语法校验：lastSavedSyntax 来自最近一次保存结果；liveSyntax 来自实时校验
const lastSavedSyntax = ref<SyntaxCheckResult | null>(null)
const liveSyntax = ref<SyntaxCheckResult | null>(null)
const syntaxChecking = ref(false)
let syntaxTimer: ReturnType<typeof setTimeout> | null = null
let syntaxRequestId = 0
// 编辑中（代码与已保存版本不一致）时优先展示实时校验，否则展示已保存版本的结果
const syntaxResult = computed<SyntaxCheckResult | null>(() => {
  const savedCode = editingScript.value?.code
  const dirty = savedCode == null || savedCode !== form.value.code
  return dirty ? liveSyntax.value : (lastSavedSyntax.value ?? liveSyntax.value)
})
const scriptDesignDraft = computed(() => designResponse.value?.result?.script || null)
const inputSchemaFields = computed(() => schemaToFields(form.value.input_schema))
const isBuiltInScript = computed(() => editingScript.value ? isBuiltInScriptFamily(editingScript.value) : false)
const canEditContract = computed(() => editingScript.value ? canEditScriptContract(editingScript.value) : true)
const pagedScripts = computed(() => paginate(scripts.value, scriptPage.value, scriptPageSize.value))
const pagedRuns = computed(() => paginate(runs.value, runPage.value, runPageSize.value))
const workflowRunOptions = computed(() =>
  testWorkflowKey.value === '__none__' ? [] : workflowRuns.value[testWorkflowKey.value] || [],
)

onMounted(async () => {
  await loadAll()
})

// 实时语法校验：代码变化时 debounce 调用 /scripts/validate（不保存）。
watch(
  () => form.value.code,
  (code) => {
    if (syntaxTimer) clearTimeout(syntaxTimer)
    const requestId = ++syntaxRequestId
    if (!code || !code.trim()) {
      liveSyntax.value = null
      syntaxChecking.value = false
      return
    }
    syntaxTimer = setTimeout(async () => {
      if (requestId !== syntaxRequestId) return
      syntaxChecking.value = true
      try {
        const result = await api.validateScriptCode(code)
        if (requestId === syntaxRequestId && form.value.code === code) {
          liveSyntax.value = result
        }
      } catch {
        // 校验失败不阻塞编辑
        if (requestId === syntaxRequestId) liveSyntax.value = null
      } finally {
        if (requestId === syntaxRequestId) syntaxChecking.value = false
      }
    }, 400)
  },
)

// 进入/切换编辑页时加载脚本
watch(
  () => props.routeKey,
  async (key) => {
    if (!key) return
    if (syntaxTimer) clearTimeout(syntaxTimer)
    syntaxRequestId += 1
    liveSyntax.value = null
    lastSavedSyntax.value = null
    syntaxChecking.value = false
    formError.value = ''
    scriptNotFound.value = false
    if (isNew.value) {
      expectedEditToken.value = ''
      form.value = emptyForm()
      outputSchemaEnabled.value = false
      formBaseline.value = snapshotForm()
      runs.value = []
      runDetail.value = null
      testWorkflowKey.value = '__none__'
      testWorkflowRunId.value = ''
      return
    }
    formLoading.value = true
    try {
      const detail = await api.getScript(editingKey.value)
      expectedEditToken.value = detail.edit_token
      const state = toScriptFormState(detail, defaultInputSchema())
      form.value = state.form
      outputSchemaEnabled.value = state.outputSchemaEnabled
      formBaseline.value = snapshotForm()
      syncWorkflowTestContext(detail)
      await loadRuns()
      if (requestedRunId.value) await openRunDetail(requestedRunId.value)
      else if (runs.value[0]) await openRunDetail(runs.value[0].run_id)
      else runDetail.value = null
    } catch (e: unknown) {
      scriptNotFound.value = true
      expectedEditToken.value = null
      form.value = emptyForm()
      formBaseline.value = snapshotForm()
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
    if (editingScript.value) syncWorkflowTestContext(editingScript.value)
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

watch(testWorkflowKey, async (workflowKey) => {
  workflowRunError.value = ''
  if (workflowKey === '__none__') {
    testWorkflowRunId.value = ''
    return
  }
  const workflow = workflows.value.find(item => item.workflow_key === workflowKey)
  if (workflow) testProfileKey.value = workflow.profile_key
  await loadWorkflowTestRuns(workflowKey)
})

function syncWorkflowTestContext(script: Pick<ManagedScript, 'owner_type' | 'owner_key'> | null | undefined) {
  if (script?.owner_type !== 'workflow' || !script.owner_key) {
    testWorkflowKey.value = '__none__'
    testWorkflowRunId.value = ''
    return
  }
  const workflow = workflows.value.find(item => item.workflow_key === script.owner_key)
  if (!workflow) return
  testProfileKey.value = workflow.profile_key
  if (testWorkflowKey.value === workflow.workflow_key) {
    void loadWorkflowTestRuns(workflow.workflow_key)
  } else {
    testWorkflowKey.value = workflow.workflow_key
  }
}

async function loadWorkflowTestRuns(workflowKey: string) {
  const requestId = ++workflowRunRequestId
  workflowRunsLoading.value = true
  workflowRunError.value = ''
  try {
    const items = await api.listWorkflowRuns(workflowKey, 50)
    if (requestId !== workflowRunRequestId || testWorkflowKey.value !== workflowKey) return
    workflowRuns.value = { ...workflowRuns.value, [workflowKey]: items }
    if (!items.some(item => item.run_id === testWorkflowRunId.value)) {
      testWorkflowRunId.value = items.find(item => item.status === 'running')?.run_id || items[0]?.run_id || ''
    }
  } catch (e: unknown) {
    if (requestId !== workflowRunRequestId || testWorkflowKey.value !== workflowKey) return
    workflowRunError.value = errorMessage(e)
    workflowRuns.value = { ...workflowRuns.value, [workflowKey]: [] }
    testWorkflowRunId.value = ''
  } finally {
    if (requestId === workflowRunRequestId) workflowRunsLoading.value = false
  }
}

async function reloadScripts() {
  scripts.value = await api.listScripts()
}

function emptyForm(): ScriptEditableFields {
  return {
    script_key: '',
    name: '',
    description: '',
    language: 'python',
    code: DEFAULT_SCRIPT_CODE,
    status: 'active',
    owner_type: 'system',
    owner_key: '',
    input_schema: defaultInputSchema(),
    output_schema: null as Record<string, unknown> | null,
  }
}

function defaultInputSchema() {
  return { type: 'object', properties: {}, required: [], additionalProperties: false } as Record<string, unknown>
}

// 输入字段变化时同步测试运行的字段表单值：保留已填值，补齐新字段，移除已删字段。
watch(inputSchemaFields, (fields) => {
  const next: Record<string, string> = {}
  for (const field of fields) {
    const name = field.name.trim()
    if (!name) continue
    next[name] = name in testParamsByField.value ? testParamsByField.value[name] : ''
  }
  testParamsByField.value = next
}, { deep: true, immediate: true })

// 把字段表单的字符串值按 schema type 转成实际参数值；空字符串跳过（除非必填且未填，由调用方校验）。
function coerceParamValue(rawValue: string, type: string): unknown {
  const text = rawValue.trim()
  switch (type) {
    case 'integer': {
      if (text === '') return undefined
      const n = Number(text)
      return Number.isFinite(n) ? Math.trunc(n) : text
    }
    case 'number': {
      if (text === '') return undefined
      const n = Number(text)
      return Number.isFinite(n) ? n : text
    }
    case 'boolean':
      return text === 'true' || text === '1'
    case 'object':
    case 'array': {
      if (text === '') return undefined
      try { return JSON.parse(text) } catch { return text }
    }
    default:
      return text === '' ? undefined : text
  }
}

// 字段表单值 → 参数对象（跳过空值）。
function buildParamsFromFields(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const field of inputSchemaFields.value) {
    const name = field.name.trim()
    if (!name) continue
    const value = coerceParamValue(testParamsByField.value[name] ?? '', field.type)
    if (value !== undefined) params[name] = value
  }
  return params
}

// 切换到「原始 JSON」视图时，把当前字段表单的值序列化进去，避免两个视图数据割裂。
function syncFieldsToRaw() {
  const params = buildParamsFromFields()
  testParamsRaw.value = Object.keys(params).length ? JSON.stringify(params, null, 2) : '{\n  \n}'
}

// 切换到「字段表单」视图时，尝试把原始 JSON 回填到字段表单（仅回填已声明字段）。
function syncRawToFields() {
  let parsed: Record<string, unknown> = {}
  try {
    const v = testParamsRaw.value.trim() ? JSON.parse(testParamsRaw.value) : {}
    if (v && typeof v === 'object' && !Array.isArray(v)) parsed = v as Record<string, unknown>
  } catch { /* 解析失败就保留现有字段值 */ }
  const next: Record<string, string> = {}
  for (const field of inputSchemaFields.value) {
    const name = field.name.trim()
    if (!name) continue
    const value = parsed[name]
    next[name] = value === undefined ? (testParamsByField.value[name] ?? '') : (typeof value === 'object' ? JSON.stringify(value) : String(value))
  }
  testParamsByField.value = next
}

function toggleRawParams(show: boolean) {
  if (show) syncFieldsToRaw()
  else syncRawToFields()
  showRawParams.value = show
}

function fieldPlaceholder(field: SchemaField): string {
  if (field.type === 'integer') return '0'
  if (field.type === 'number') return '0.0'
  if (field.type === 'object') return '{ "key": "value" }'
  if (field.type === 'array') return '[ 1, 2 ]'
  return field.description || ''
}

function validateSchemaEditors(): boolean {
  if (!inputSchemaEditor.value?.validate()) {
    formError.value = inputSchemaEditor.value?.getValidationMessage() || '输入 Schema 不合法'
    return false
  }
  if (outputSchemaEnabled.value) {
    if (!outputSchemaEditor.value?.validate()) {
      formError.value = outputSchemaEditor.value?.getValidationMessage() || '输出 Schema 不合法'
      return false
    }
  } else {
    form.value.output_schema = null
  }
  return true
}

function toggleOutputSchema(enabled: boolean) {
  outputSchemaEnabled.value = enabled
  form.value.output_schema = enabled ? (form.value.output_schema || defaultInputSchema()) : null
}

function goList() {
  void navigateTo(returnToRoute.value || 'scripts', { replace: true })
}

function openCreate() {
  void navigateTo('scripts/new')
}

function openEdit(item: ManagedScript) {
  void navigateTo('scripts/' + item.script_key)
}

async function deleteScript(item: ManagedScript) {
  if (!canDeleteScript(item)) return
  if (!await confirm({ title: '删除脚本', description: `确定删除脚本「${item.name}」？已有运行历史的脚本应改为停用。`, destructive: true, confirmText: '删除' })) return
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
  if (!validateSchemaEditors()) return null
  if (!form.value.script_key || !form.value.name || !form.value.code.trim()) {
    formError.value = '请填写脚本标识、名称和代码'
    return null
  }
  saving.value = true
  try {
    const saved = await api.upsertScript({
      ...toScriptUpsertPayload(form.value, outputSchemaEnabled.value),
      expected_edit_token: expectedEditToken.value,
    })
    expectedEditToken.value = saved.edit_token
    syntaxRequestId += 1
    liveSyntax.value = null
    syntaxChecking.value = false
    lastSavedSyntax.value = saved.syntax_check ?? null
    await reloadScripts()
    formBaseline.value = snapshotForm()
    // 新建或设计 agent 生成了新 key 后同步 URL，避免后续保存落到旧路由上下文。
    if (isNew.value || saved.script_key !== editingKey.value) {
      void navigateTo('scripts/' + saved.script_key, { replace: true })
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

function createDesignRunKey() {
  const suffix = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `design-script-${suffix}`
}

function scriptDesignerCurrent() {
  validateSchemaEditors()
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
      input_schema: form.value.input_schema,
      output_schema: outputSchemaEnabled.value ? form.value.output_schema : null,
    }
  }
  return {
    language: 'python',
    status: 'active',
    owner_type: form.value.owner_type,
    owner_key: form.value.owner_key,
    output_schema: outputSchemaEnabled.value ? form.value.output_schema : null,
  }
}

async function runScriptDesigner() {
  designError.value = ''
  if (!designPrompt.value.trim()) {
    designError.value = '请输入提示词'
    return
  }
  const runKey = createDesignRunKey()
  designRunKey.value = runKey
  designStopRequested.value = false
  designResponse.value = null
  designing.value = true
  try {
    const response = await api.designScript({
      run_key: runKey,
      mode: designMode.value,
      prompt: designPrompt.value,
      current: scriptDesignerCurrent(),
      profile_key: form.value.owner_type === 'profile' ? form.value.owner_key : undefined,
    })
    if (designRunKey.value !== runKey || designStopRequested.value) return
    designResponse.value = response
    if (!designResponse.value.ok) {
      designError.value = designResponse.value.error || '设计 agent 执行失败'
    }
  } catch (e: unknown) {
    if (designRunKey.value !== runKey || designStopRequested.value) return
    designError.value = errorMessage(e)
  } finally {
    if (designRunKey.value === runKey) {
      if (designStopRequested.value && !designError.value) designError.value = '已停止'
      designing.value = false
      designStopRequested.value = false
    }
  }
}

async function stopScriptDesigner() {
  const runKey = designRunKey.value
  if (!designing.value || !runKey || designStopRequested.value) return
  designStopRequested.value = true
  designError.value = ''
  try {
    await api.stopAgentRun(runKey)
  } catch (e: unknown) {
    designError.value = errorMessage(e)
  }
}

async function acceptScriptDesign() {
  if (designing.value || designStopRequested.value) return
  const draft = scriptDesignDraft.value
  if (!draft) return
  const state = toScriptFormState(mergeScriptDesignDraft(form.value, draft), defaultInputSchema())
  form.value = state.form
  outputSchemaEnabled.value = state.outputSchemaEnabled
  const saved = await saveScript()
  if (saved) showDesigner.value = false
}

async function resetBuiltInScript() {
  const item = editingScript.value
  if (!item || !canResetScript(item)) return
  formError.value = ''
  try {
    const detail = await api.resetScript(item.script_key, expectedEditToken.value)
    expectedEditToken.value = detail.edit_token
    const state = toScriptFormState(detail, defaultInputSchema())
    form.value = state.form
    outputSchemaEnabled.value = state.outputSchemaEnabled
    formBaseline.value = snapshotForm()
    await reloadScripts()
  } catch (e: unknown) {
    formError.value = errorMessage(e)
  }
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
  if (showRawParams.value) {
    try {
      const parsed = testParamsRaw.value.trim() ? JSON.parse(testParamsRaw.value) : {}
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        runError.value = 'params 必须是 JSON 对象'
        return
      }
      params = parsed as Record<string, unknown>
    } catch {
      runError.value = 'params 不是合法 JSON'
      return
    }
  } else {
    params = buildParamsFromFields()
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
    // Failed executions are persisted before the API returns the validation
    // error. Reload the newest run so stdout/stderr remain available for
    // debugging instead of showing only the toast-like error text.
    await loadRuns()
    if (runs.value[0]) await openRunDetail(runs.value[0].run_id)
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

function sourceLabel(source: string | undefined) {
  if (source === 'default') return '默认内置'
  if (source === 'database') return '数据库覆盖'
  return source || '未知来源'
}

function sourceBadgeClass(source: string | undefined) {
  if (source === 'default') return 'bg-warning-soft text-warning-soft-fg'
  if (source === 'database') return 'bg-info-soft text-info-soft-fg'
  return ''
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
    <!-- 页头操作：Teleport 进全局 PageHeader 的 #ph-actions（仅列表态） -->
    <Teleport v-if="mode === 'list'" to="#ph-actions" defer>
      <Button variant="outline" size="lg" @click="showGuide = true">
        <HelpCircle :size="14" />
        使用指引
      </Button>
      <Button size="lg" class="shadow-btn" @click="openCreate">
        <Plus :size="14" />
        新建脚本
      </Button>
    </Teleport>

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
              <span class="font-mono text-foreground"> params (JSON 对象) </span> 作为对象传到
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

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
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
                <Badge v-if="isBuiltInScriptFamily(item)" variant="secondary" :class="sourceBadgeClass(item.source)">{{ sourceLabel(item.source) }}</Badge>
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
              <Button variant="ghost" size="sm" class="h-8 text-xs text-destructive" :disabled="!canDeleteScript(item)" @click="deleteScript(item)">
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
    <EditorActionBar class="-mx-7 px-7">
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
            <div class="mt-1 flex flex-wrap items-center gap-2">
              <p class="font-mono text-xs text-muted-foreground">
                {{ isNew ? '新建后将自动生成 script_key' : editingKey }}
              </p>
              <Badge v-if="editingScript && isBuiltInScript" variant="secondary" :class="sourceBadgeClass(editingScript.source)">{{ sourceLabel(editingScript.source) }}</Badge>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button v-if="isBuiltInScript" variant="outline" size="sm" :disabled="saving || testing" @click="resetBuiltInScript">
            <RotateCcw class="mr-1.5 h-4 w-4" />
            恢复默认
          </Button>
          <Button variant="outline" size="sm" :disabled="designing || !canEditContract" @click="openScriptDesigner('modify')">
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
          <Button v-if="editingKey" variant="outline" size="sm" @click="showHistory = true">
            版本历史
            <span v-if="editingScript?.revision_no" class="ml-1.5 rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px]">v{{ editingScript.revision_no }}</span>
          </Button>
        </div>
      </div>
    </EditorActionBar>

    <Dialog v-if="editingKey" v-model:open="showHistory">
      <DialogContent class="w-[96vw] max-w-[1100px] sm:max-w-[1100px]">
        <DialogHeader>
          <DialogTitle>脚本版本历史 · {{ editingScript?.name || editingKey }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[78vh] overflow-y-auto pr-1">
          <RevisionHistoryPanel
            :key="`script-${editingKey}`"
            entity-type="script"
            :entity-key="editingKey"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showHistory = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <div v-if="scriptNotFound" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-3 text-sm text-destructive-soft-fg">
      无法加载该脚本（可能已被删除或不存在）。请<a class="underline" href="#scripts" @click.prevent="goList">返回列表</a>。
    </div>

    <div v-else class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
      <!-- 左栏：编辑器 -->
      <Card>
        <CardContent class="space-y-4 p-4">
          <div v-if="formLoading" class="py-16 text-center text-sm text-muted-foreground">加载中</div>
          <template v-else>
            <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
              {{ formError }}
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">脚本标识 script_key</label>
                <Input v-model="form.script_key" placeholder="my_script" :disabled="!isNew" />
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">名称</label>
                <Input v-model="form.name" placeholder="我的脚本" :disabled="!canEditContract" />
              </div>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">描述</label>
              <Input v-model="form.description" placeholder="可选" :disabled="!canEditContract" />
            </div>
            <div class="grid gap-3 md:grid-cols-3">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">状态</label>
                <Select v-model="form.status" :disabled="!canDisableScript({ script_key: form.script_key, source: editingScript?.source, is_builtin: isBuiltInScript })">
                  <SelectTrigger class="w-full"><SelectValue placeholder="状态" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">启用</SelectItem>
                    <SelectItem value="disabled">停用</SelectItem>
                  </SelectContent>
                </Select>
                <p v-if="isBuiltInScript" class="mt-1 text-xs text-muted-foreground">内置脚本不能停用，可修改代码后按需恢复默认。</p>
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">归属类型</label>
                <Select v-model="form.owner_type" :disabled="!canEditContract">
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
                <Select v-if="ownerKeyOptions.length" v-model="form.owner_key" :disabled="!canEditContract">
                  <SelectTrigger class="w-full"><SelectValue placeholder="选择" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="opt in ownerKeyOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</SelectItem>
                  </SelectContent>
                </Select>
                <Input v-else v-model="form.owner_key" placeholder="owner_key" :disabled="!canEditContract" />
              </div>
            </div>
            <div>
              <label class="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                <span>代码（Python，main(envelope) -&gt; dict）</span>
                <span v-if="syntaxChecking" class="text-muted-foreground/70">语法检查中…</span>
                <span v-else-if="syntaxResult && syntaxResult.ok" class="text-success-soft-fg">语法正确</span>
              </label>
              <div
                v-if="syntaxResult && !syntaxResult.ok"
                class="mb-2 rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning-soft-fg"
              >
                <div class="mb-1 font-semibold">检测到 Python 语法错误（可保存，但运行前需修复）</div>
                <ul class="space-y-0.5 font-mono">
                  <li v-for="(err, i) in syntaxResult.errors" :key="i">
                    <span class="text-warning-soft-fg/70">[行 {{ err.line ?? '?' }}{{ err.col != null ? ':' + err.col : '' }}]</span>
                    {{ err.msg }}
                  </li>
                </ul>
              </div>
              <CodeMirror v-model="form.code" />
              <p class="mt-2 text-xs leading-5 text-muted-foreground">
                可直接使用 <span class="font-mono text-foreground">from agent_bridge_runtime import execute, workflow_get_task, workflow_set_task, workflow_run_log</span>。
              </p>
            </div>
            <div>
              <SchemaFieldEditor
                ref="inputSchemaEditor"
                v-model="form.input_schema"
                label="输入 Schema"
                :disabled="!canEditContract"
              />
              <p class="mt-2 text-xs text-muted-foreground">工作流会根据这些字段生成参数映射和运行前校验。</p>
            </div>
            <div class="space-y-3">
              <label class="flex items-center gap-2 text-xs text-muted-foreground">
                <input :checked="outputSchemaEnabled" type="checkbox" :disabled="!canEditContract" @change="toggleOutputSchema(($event.target as HTMLInputElement).checked)" />
                声明输出 Schema
              </label>
              <SchemaFieldEditor
                v-if="outputSchemaEnabled"
                ref="outputSchemaEditor"
                v-model="form.output_schema"
                label="输出 Schema"
                :disabled="!canEditContract"
              />
              <div v-else class="rounded-md border px-3 py-4 text-xs text-muted-foreground">
                未声明输出 Schema 时，脚本仍可运行，但下游只能把结果当作未建模对象处理。
              </div>
            </div>
          </template>

        </CardContent>
      </Card>

      <!-- 右栏：运行 + 结果（sticky） -->
      <div class="space-y-4 xl:sticky xl:top-4 xl:self-start xl:max-h-[calc(100vh-2rem)] xl:overflow-y-scroll xl:pr-1">
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="text-sm font-semibold text-foreground">测试运行</div>
            <div v-if="isNew" class="rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning-soft-fg">
              新建脚本：点击「保存并运行」将先保存再执行。
            </div>
            <div>
              <div class="mb-2 flex items-center justify-between gap-2">
                <label class="block text-xs text-muted-foreground">参数</label>
                <button type="button" class="text-xs text-primary hover:underline" @click="toggleRawParams(!showRawParams)">
                  {{ showRawParams ? '按输入字段填写' : '原始 JSON' }}
                </button>
              </div>
              <!-- 字段表单：按「输入字段」逐个填写 -->
              <div v-if="!showRawParams">
                <div v-if="inputSchemaFields.filter(f => f.name.trim()).length" class="space-y-2">
                  <div v-for="field in inputSchemaFields.filter(f => f.name.trim())" :key="field.name" class="grid gap-1">
                    <label class="flex items-center gap-1 text-xs text-muted-foreground">
                      <span class="font-mono">{{ field.name }}</span>
                      <span class="text-[10px] uppercase text-muted-foreground/70">{{ field.type }}</span>
                      <span v-if="field.required" class="text-destructive">*</span>
                      <span v-if="field.description" class="truncate text-muted-foreground/70">— {{ field.description }}</span>
                    </label>
                    <input
                      v-if="field.type === 'boolean'"
                      type="checkbox"
                      class="h-4 w-4"
                      :checked="testParamsByField[field.name] === 'true'"
                      @change="testParamsByField[field.name] = ($event.target as HTMLInputElement).checked ? 'true' : 'false'"
                    />
                    <Textarea
                      v-else-if="field.type === 'object' || field.type === 'array'"
                      v-model="testParamsByField[field.name]"
                      class="min-h-[64px] font-mono text-xs"
                      :placeholder="fieldPlaceholder(field)"
                      spellcheck="false"
                    />
                    <Input
                      v-else
                      v-model="testParamsByField[field.name]"
                      :type="field.type === 'integer' || field.type === 'number' ? 'number' : 'text'"
                      :placeholder="fieldPlaceholder(field)"
                    />
                  </div>
                </div>
                <div v-else class="rounded-md border px-3 py-3 text-xs text-muted-foreground">
                  此脚本未声明输入字段；切到「原始 JSON」可传任意参数。
                </div>
              </div>
              <!-- 折叠的原始 JSON（高级入口） -->
              <Textarea v-else v-model="testParamsRaw" class="min-h-[80px] font-mono text-xs" spellcheck="false" />
            </div>
            <div class="grid gap-3 grid-cols-2">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">超时 (秒)</label>
                <Input v-model.number="testTimeout" type="number" placeholder="30" />
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">能力平面</label>
                <Select v-model="testProfileKey" :disabled="testWorkflowKey !== '__none__'">
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
                  <Select v-model="testWorkflowRunId" :disabled="workflowRunsLoading || !workflowRunOptions.length">
                    <SelectTrigger class="w-full"><SelectValue placeholder="选择已有运行记录" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="run in workflowRunOptions" :key="run.run_id" :value="run.run_id">
                        <span class="font-mono text-xs">{{ run.run_id }}</span>
                        <span class="ml-2 text-xs text-muted-foreground">{{ run.status }} · {{ formatLocalDatetime(run.started_at) }}</span>
                      </SelectItem>
                    </SelectContent>
                  </Select>
                  <p v-if="workflowRunsLoading" class="mt-1 text-xs text-muted-foreground">加载工作流运行记录…</p>
                  <p v-else-if="workflowRunError" class="mt-1 text-xs text-destructive">{{ workflowRunError }}</p>
                  <p v-else-if="testWorkflowKey !== '__none__' && !workflowRunOptions.length" class="mt-1 text-xs text-warning-soft-fg">暂无可用运行记录，请先运行该工作流。</p>
                </div>
              </div>
              <p class="mt-2 text-xs leading-5 text-muted-foreground">
                页面只允许选择真实存在且属于该 workflow 的运行记录，避免 workflow run not found。
              </p>
              <p v-if="testWorkflowKey !== '__none__'" class="mt-1 text-xs text-muted-foreground">已自动使用该 workflow 关联的能力平面。</p>
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
                <div class="text-sm font-semibold text-foreground">运行详情 / 调试日志</div>
              <Button v-if="runDetail" variant="ghost" size="sm" class="h-7 px-2 text-xs" @click="runDetail = null">
                清除
              </Button>
            </div>
            <div v-if="runDetailLoading" class="py-6 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runDetail" class="py-6 text-center text-sm text-muted-foreground">暂无运行结果，点击「运行」或在下方记录中选择</div>
            <div v-else class="space-y-3">
              <div class="flex flex-wrap items-center gap-2">
                <StatusBadge :status="runDetail.status === 'success' ? 'success' : 'error'" />
                <Badge variant="outline">{{ runDetail.run_type }}</Badge>
                <span class="font-mono text-xs text-muted-foreground">{{ runDetail.run_id }}</span>
                <span class="text-xs text-muted-foreground">{{ runDetail.duration_ms }} ms</span>
                <span v-if="runDetail.exit_code !== null" class="text-xs text-muted-foreground">exit {{ runDetail.exit_code }}</span>
              </div>
              <div v-if="runDetail.error_message" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
                {{ runDetail.error_message }}
              </div>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">运行参数 / workflow context</div>
                <JsonViewer :value="runDetail.params" max-height="160px" />
              </section>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">result</div>
                <JsonViewer :value="runDetail.result" max-height="192px" />
              </section>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">stdout</div>
                <pre class="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-background p-2 text-xs">{{ runDetail.stdout || '（无 stdout 输出）' }}</pre>
              </section>
              <section class="rounded-md border bg-muted/20 p-3">
                <div class="mb-2 text-xs font-semibold text-foreground">stderr</div>
                <pre class="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-background p-2 text-xs">{{ runDetail.stderr || '（无 stderr 输出）' }}</pre>
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
                class="list-row-interactive rounded-md border px-3 py-2 text-left"
                :class="runDetail?.run_id === run.run_id ? 'border-primary/40 bg-primary/5' : ''"
                @click="openRunDetail(run.run_id)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate font-mono text-xs">{{ run.run_id }}</span>
                  <StatusBadge :status="run.status === 'success' ? 'success' : 'error'" />
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
        <Button class="w-full" :disabled="designStopRequested" @click="designing ? stopScriptDesigner() : runScriptDesigner()">
          <WandSparkles class="mr-1.5 h-4 w-4" />
          {{ designing ? (designStopRequested ? '停止中' : '立即停止') : '生成方案' }}
        </Button>

        <div v-if="designError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
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
        <Button :disabled="designing || !scriptDesignDraft || saving" @click="acceptScriptDesign">
          <Check class="mr-1.5 h-4 w-4" />
          {{ saving ? '保存中' : '采纳并保存' }}
        </Button>
      </div>
    </aside>
  </div>
</template>
