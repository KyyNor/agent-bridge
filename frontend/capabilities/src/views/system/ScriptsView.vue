<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Play, Plus, RotateCcw, Save, Trash2 } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { ManagedScript, ProjectProfile, ScriptRun, WorkflowDefinition } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'
import { Textarea } from '../../components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select'

const props = defineProps<{ routeKey: string }>()

const scripts = ref<ManagedScript[]>([])
const profiles = ref<ProjectProfile[]>([])
const workflows = ref<WorkflowDefinition[]>([])
const loading = ref(true)
const error = ref('')

// 编辑模式表单状态
const form = ref(emptyForm())
const formError = ref('')
const formLoading = ref(false)
const saving = ref(false)
const scriptNotFound = ref(false)

// 运行状态
const runs = ref<ScriptRun[]>([])
const runsLoading = ref(false)
const runError = ref('')
const testing = ref(false)
const testParams = ref('{\n  "limit": 5\n}')
const testTimeout = ref<number | undefined>(30)
const testProfileKey = ref('__default__')
const runDetail = ref<ScriptRun | null>(null)
const runDetailLoading = ref(false)

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
  if (!confirm(`确定删除脚本「${item.name}」？其运行记录将一并清除。`)) return
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
    // 新建成功后同步 URL，避免再次保存时被当作新建
    if (isNew.value) {
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
    params = testParams.value.trim() ? JSON.parse(testParams.value) : {}
  } catch {
    runError.value = 'script_params 不是合法 JSON'
    return
  }
  testing.value = true
  runError.value = ''
  try {
    const run = await api.testScript(scriptKey, {
      script_params: params,
      timeout_seconds: testTimeout.value,
      profile_key: testProfileKey.value && testProfileKey.value !== '__default__' ? testProfileKey.value : undefined,
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
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-foreground">脚本管理</h2>
        <p class="text-sm text-muted-foreground">管理受控 Python 脚本，在线测试与查看运行记录</p>
      </div>
      <Button @click="openCreate">
        <Plus class="mr-1.5 h-4 w-4" />
        新建脚本
      </Button>
    </div>

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
          <div v-for="item in scripts" :key="item.script_key" class="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_240px_220px] lg:items-center">
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
              <div class="mt-1">更新 {{ item.updated_at }}</div>
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
              <label class="mb-1 block text-xs text-muted-foreground">代码（Python，stdout 输出 JSON）</label>
              <Textarea v-model="form.code" class="min-h-[58vh] font-mono text-xs leading-5" spellcheck="false" placeholder="import json&#10;print(json.dumps({'ok': True}))" />
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
              <label class="mb-1 block text-xs text-muted-foreground">script_params (JSON)</label>
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
                <pre class="max-h-48 overflow-auto rounded bg-background p-2 text-xs">{{ prettyJson(runDetail.result) }}</pre>
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
                v-for="run in runs"
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
                <div class="mt-1 text-xs text-muted-foreground">{{ run.created_at }}</div>
              </button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  </div>
</template>
