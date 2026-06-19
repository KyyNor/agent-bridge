<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Play, Plus, Trash2 } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { ManagedScript, ProjectProfile, ScriptRun, WorkflowDefinition } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Textarea } from '../../components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select'

const scripts = ref<ManagedScript[]>([])
const profiles = ref<ProjectProfile[]>([])
const workflows = ref<WorkflowDefinition[]>([])
const selectedKey = ref('')
const loading = ref(true)
const error = ref('')

const showEditor = ref(false)
const saving = ref(false)
const formError = ref('')
const form = ref(emptyForm())

const showRuns = ref(false)
const runs = ref<ScriptRun[]>([])
const runsLoading = ref(false)
const runError = ref('')

const testing = ref(false)
const testError = ref('')
const testParams = ref('{\n  "limit": 5\n}')
const testTimeout = ref<number | undefined>(30)
const testProfileKey = ref('__default__')

const runDetail = ref<ScriptRun | null>(null)
const runDetailLoading = ref(false)

const selected = computed(() =>
  scripts.value.find(item => item.script_key === selectedKey.value) || scripts.value[0] || null,
)

const ownerKeyOptions = computed(() => {
  if (form.value.owner_type === 'profile') return profiles.value.map(p => ({ value: p.profile_key, label: p.name }))
  if (form.value.owner_type === 'workflow') return workflows.value.map(w => ({ value: w.workflow_key, label: w.name }))
  return []
})

onMounted(async () => {
  await loadAll()
})

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
    selectedKey.value = selectedKey.value || scriptList[0]?.script_key || ''
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

function openCreate() {
  form.value = emptyForm()
  formError.value = ''
  showEditor.value = true
}

function openEdit(item: ManagedScript) {
  selectedKey.value = item.script_key
  form.value = {
    script_key: item.script_key,
    name: item.name,
    description: item.description,
    language: item.language,
    code: '',
    status: item.status,
    owner_type: item.owner_type,
    owner_key: item.owner_key,
  }
  formError.value = ''
  showEditor.value = true
  void loadCode(item.script_key)
}

async function loadCode(scriptKey: string) {
  try {
    const detail = await api.getScript(scriptKey)
    form.value.code = detail.code || ''
  } catch (e: unknown) {
    formError.value = errorMessage(e)
  }
}

async function saveScript() {
  formError.value = ''
  if (!form.value.script_key || !form.value.name || !form.value.code.trim()) {
    formError.value = '请填写脚本标识、名称和代码'
    return
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
    selectedKey.value = saved.script_key
    showEditor.value = false
    await reloadScripts()
  } catch (e: unknown) {
    formError.value = errorMessage(e)
  } finally {
    saving.value = false
  }
}

async function deleteScript(item: ManagedScript) {
  if (!confirm(`确定删除脚本「${item.name}」？其运行记录将一并清除。`)) return
  error.value = ''
  try {
    await api.deleteScript(item.script_key)
    await reloadScripts()
    if (selectedKey.value === item.script_key) selectedKey.value = scripts.value[0]?.script_key || ''
  } catch (e: unknown) {
    error.value = errorMessage(e)
  }
}

async function openRuns(item: ManagedScript) {
  selectedKey.value = item.script_key
  showRuns.value = true
  await loadRuns()
}

async function loadRuns() {
  if (!selected.value) return
  runsLoading.value = true
  runError.value = ''
  try {
    const result = await api.listScriptRuns(selected.value.script_key, 20)
    runs.value = result.runs
  } catch (e: unknown) {
    runError.value = errorMessage(e)
    runs.value = []
  } finally {
    runsLoading.value = false
  }
}

async function runTest() {
  if (!selected.value || testing.value) return
  let params: Record<string, unknown> = {}
  try {
    params = testParams.value.trim() ? JSON.parse(testParams.value) : {}
  } catch {
    testError.value = 'script_params 不是合法 JSON'
    return
  }
  testing.value = true
  testError.value = ''
  try {
    const run = await api.testScript(selected.value.script_key, {
      script_params: params,
      timeout_seconds: testTimeout.value,
      profile_key: testProfileKey.value && testProfileKey.value !== '__default__' ? testProfileKey.value : undefined,
    })
    await loadRuns()
    runDetail.value = run
  } catch (e: unknown) {
    testError.value = errorMessage(e)
  } finally {
    testing.value = false
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
  <div class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-foreground">脚本运行时</h2>
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
          <div v-for="item in scripts" :key="item.script_key" class="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_240px_300px] lg:items-center">
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
              <Button variant="outline" size="sm" class="h-8 text-xs" @click="openEdit(item)">编辑</Button>
              <Button variant="outline" size="sm" class="h-8 text-xs" :disabled="item.status !== 'active'" @click="openRuns(item)">
                <Play class="mr-1 h-3.5 w-3.5" />
                运行
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

    <Dialog v-model:open="showEditor">
      <DialogContent class="w-[96vw] max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>{{ form.script_key && scripts.some(s => s.script_key === form.script_key) ? '编辑脚本' : '新建脚本' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[78vh] space-y-4 overflow-auto pr-1">
          <div v-if="formError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {{ formError }}
          </div>
          <div class="grid gap-3 md:grid-cols-2">
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">脚本标识 script_key</label>
              <Input v-model="form.script_key" placeholder="my_script" :disabled="scripts.some(s => s.script_key === form.script_key)" />
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
            <Textarea v-model="form.code" class="min-h-[40vh] font-mono text-xs leading-5" spellcheck="false" placeholder="import json&#10;print(json.dumps({'ok': True}))" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showEditor = false">取消</Button>
          <Button :disabled="saving" @click="saveScript">{{ saving ? '保存中' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="showRuns">
      <DialogContent class="w-[96vw] max-w-[1100px] sm:max-w-[1100px]">
        <DialogHeader>
          <DialogTitle>{{ selected?.name || '脚本运行' }}</DialogTitle>
        </DialogHeader>
        <div class="max-h-[78vh] space-y-4 overflow-auto pr-1">
          <section class="rounded-md border p-4">
            <div class="mb-3 text-sm font-semibold text-foreground">测试运行</div>
            <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_200px] md:items-end">
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">script_params (JSON)</label>
                <Textarea v-model="testParams" class="min-h-[80px] font-mono text-xs" spellcheck="false" />
              </div>
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
            <div class="mt-3 flex items-center gap-3">
              <Button size="sm" :disabled="testing || selected?.status !== 'active'" @click="runTest">
                <Play class="mr-1.5 h-4 w-4" />
                {{ testing ? '运行中' : '运行' }}
              </Button>
              <div v-if="testError" class="text-xs text-destructive">{{ testError }}</div>
            </div>
          </section>

          <section class="space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold">运行记录</h3>
              <Button variant="outline" size="sm" :disabled="runsLoading" @click="loadRuns">{{ runsLoading ? '刷新中' : '刷新' }}</Button>
            </div>
            <div v-if="runsLoading" class="py-4 text-center text-sm text-muted-foreground">加载中</div>
            <div v-else-if="!runs.length" class="rounded-md border px-4 py-6 text-sm text-muted-foreground">暂无运行记录</div>
            <div v-else class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              <button
                v-for="run in runs"
                :key="run.run_id"
                class="rounded-md border px-3 py-2 text-left transition hover:bg-muted/50"
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
          </section>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showRuns = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="runDetail !== null" @update:open="(v: boolean) => { if (!v) runDetail = null }">
      <DialogContent class="w-[96vw] max-w-[980px] sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle>运行详情</DialogTitle>
        </DialogHeader>
        <div v-if="runDetailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
        <div v-else-if="runDetail" class="max-h-[76vh] space-y-4 overflow-auto pr-1">
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
            <pre class="max-h-60 overflow-auto rounded bg-background p-2 text-xs">{{ prettyJson(runDetail.result) }}</pre>
          </section>
          <section class="rounded-md border bg-muted/20 p-3">
            <div class="mb-2 text-xs font-semibold text-foreground">stdout</div>
            <pre class="max-h-60 overflow-auto rounded bg-background p-2 text-xs">{{ runDetail.stdout }}</pre>
          </section>
          <section class="rounded-md border bg-muted/20 p-3">
            <div class="mb-2 text-xs font-semibold text-foreground">stderr</div>
            <pre class="max-h-60 overflow-auto rounded bg-background p-2 text-xs">{{ runDetail.stderr }}</pre>
          </section>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="runDetail = null">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
