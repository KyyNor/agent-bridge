<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api/client'
import type { ModelEvaluationDataset, ModelEvaluationModel, ModelEvaluationRun, ModelEvaluationRuntimeStatus } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'
import StatusBadge from '../../components/StatusBadge.vue'

const datasets = ref<ModelEvaluationDataset[]>([])
const runs = ref<ModelEvaluationRun[]>([])
const models = ref<ModelEvaluationModel[]>([])
const runtime = ref<ModelEvaluationRuntimeStatus | null>(null)
const form = ref({ base_url: '', api_key: '', model_name: '', datasets: ['demo_gsm8k_chat_gen'] as string[] })
const loading = ref(true)
const loadingModels = ref(false)
const starting = ref(false)
const error = ref('')
let refreshTimer: number | undefined

const activeRunExists = computed(() => runs.value.some(run => run.status === 'queued' || run.status === 'running'))

onMounted(async () => {
  await Promise.all([loadDatasets(), loadRuns(), loadRuntime()])
  loading.value = false
  refreshTimer = window.setInterval(() => { if (activeRunExists.value) void loadRuns() }, 4_000)
})

onUnmounted(() => { if (refreshTimer !== undefined) window.clearInterval(refreshTimer) })

async function loadDatasets() {
  try { datasets.value = await api.listModelEvaluationDatasets() } catch (e: any) { error.value = e.message || '无法加载可用数据集' }
}

async function loadRuns() {
  try { runs.value = await api.listModelEvaluationRuns() } catch (e: any) { error.value = e.message || '无法加载评估记录' }
}

async function loadRuntime() {
  try { runtime.value = await api.getModelEvaluationRuntime() } catch (e: any) { error.value = e.message || '无法获取评估运行时状态' }
}

async function loadModels() {
  loadingModels.value = true
  error.value = ''
  try {
    models.value = await api.listEvaluationModels({ base_url: form.value.base_url, api_key: form.value.api_key })
    if (!models.value.some(item => item.id === form.value.model_name)) form.value.model_name = models.value[0]?.id || ''
    if (!models.value.length) error.value = '接口未返回可用模型'
  } catch (e: any) { error.value = e.message || '获取模型列表失败' } finally { loadingModels.value = false }
}

async function startEvaluation() {
  starting.value = true
  error.value = ''
  try {
    await api.startModelEvaluationRun({ ...form.value })
    form.value.api_key = ''
    await loadRuns()
  } catch (e: any) { error.value = e.message || '启动评估失败' } finally { starting.value = false }
}

function toggleDataset(key: string, checked: boolean) {
  form.value.datasets = checked
    ? [...new Set([...form.value.datasets, key])]
    : form.value.datasets.filter(item => item !== key)
}

function statusLabel(status: ModelEvaluationRun['status']) {
  return ({ queued: '等待执行', running: '评估中', completed: '已完成', failed: '失败', abandoned: '已中断' } as const)[status]
}

function scoreSummary(run: ModelEvaluationRun) {
  const row = run.result.rows?.[0]
  if (!row) return run.status === 'completed' ? '未找到汇总 CSV' : '—'
  return Object.entries(row).filter(([key]) => !/model|dataset/i.test(key)).slice(0, 3).map(([key, value]) => `${key}: ${value}`).join(' · ') || '已生成结果'
}
</script>

<template>
  <div v-if="loading" class="py-16 text-center text-sm text-muted-foreground">正在加载模型评估…</div>
  <div v-else class="mx-auto max-w-6xl space-y-5">
    <Card v-if="runtime && !runtime.configured" class="border-warning/40 bg-warning-soft/30">
      <CardContent class="space-y-2 p-5 text-sm">
        <div class="font-medium text-warning-soft-fg">{{ runtime.message }}</div>
        <p class="text-muted-foreground">请在部署机为评估单独创建 venv 或 Docker runner，再设置环境变量并重启服务：</p>
        <code v-if="runtime.install_command" class="block rounded bg-background/80 p-2 text-xs">{{ runtime.install_command }}</code>
        <code v-if="runtime.configure_command" class="block rounded bg-background/80 p-2 text-xs">{{ runtime.configure_command }}</code>
      </CardContent>
    </Card>
    <Card>
      <CardContent class="space-y-5 p-5">
        <div>
          <div class="text-base font-medium">发起模型评估</div>
          <p class="mt-1 text-sm text-muted-foreground">通过 OpenCompass 对 OpenAI 兼容接口中的模型进行小规模基准评测。</p>
        </div>
        <div class="grid gap-4 md:grid-cols-2">
          <label class="space-y-2 text-sm">
            <span>Base URL <span class="text-muted-foreground">（可选）</span></span>
            <Input v-model="form.base_url" placeholder="https://api.example.com/v1" class="font-mono text-xs" />
          </label>
          <label class="space-y-2 text-sm">
            <span>API Key <span class="text-muted-foreground">（可选）</span></span>
            <Input v-model="form.api_key" type="password" placeholder="留空时与 URL 一起使用公共模型配置" class="font-mono text-xs" />
          </label>
        </div>
        <p class="-mt-2 text-xs text-muted-foreground">URL 和 API Key 同时留空时，默认使用「系统管理 → 公共模型配置」中的全量探测关键词模型连接；API Key 不会保存到评估记录。</p>
        <div class="flex flex-wrap items-end gap-3">
          <label class="min-w-72 flex-1 space-y-2 text-sm">
            <span>待评估模型</span>
            <select v-model="form.model_name" class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm" :disabled="loadingModels || !models.length">
              <option value="">请先获取模型列表</option>
              <option v-for="model in models" :key="model.id" :value="model.id">{{ model.label }}</option>
            </select>
          </label>
          <Button variant="outline" @click="loadModels" :disabled="loadingModels">{{ loadingModels ? '获取中…' : '获取模型列表' }}</Button>
        </div>
        <div class="space-y-2">
          <div class="text-sm">简单数据集</div>
          <div class="grid gap-2 md:grid-cols-2">
            <label v-for="dataset in datasets" :key="dataset.key" class="flex cursor-pointer gap-3 rounded-md border border-border p-3 text-sm">
              <input type="checkbox" class="mt-1 h-4 w-4" :checked="form.datasets.includes(dataset.key)" @change="toggleDataset(dataset.key, ($event.target as HTMLInputElement).checked)" />
              <span><span class="font-medium">{{ dataset.label }}</span><span class="mt-1 block text-xs text-muted-foreground">{{ dataset.description }}</span></span>
            </label>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <Button @click="startEvaluation" :disabled="starting || !runtime?.configured || !form.model_name || !form.datasets.length">{{ starting ? '正在启动…' : '开始评估' }}</Button>
          <span v-if="error" class="text-sm text-destructive">{{ error }}</span>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardContent class="p-5">
        <div class="mb-4 flex items-center justify-between"><div><div class="text-base font-medium">评估记录</div><p class="mt-1 text-sm text-muted-foreground">完成后自动读取 OpenCompass 生成的汇总结果。</p></div><Button size="sm" variant="outline" @click="loadRuns">刷新</Button></div>
        <div v-if="!runs.length" class="py-10 text-center text-sm text-muted-foreground">尚未发起模型评估。</div>
        <div v-else class="overflow-x-auto"><table class="w-full min-w-[760px] text-sm"><thead><tr class="border-b border-border text-left text-xs text-muted-foreground"><th class="px-3 py-2">模型</th><th class="px-3 py-2">数据集</th><th class="px-3 py-2">状态</th><th class="px-3 py-2">结果</th><th class="px-3 py-2">创建时间</th></tr></thead><tbody><tr v-for="run in runs" :key="run.run_id" class="border-b border-border/70"><td class="px-3 py-3 font-mono text-xs">{{ run.model_name }}</td><td class="px-3 py-3 text-xs">{{ run.datasets.join('、') }}</td><td class="px-3 py-3"><StatusBadge :status="run.status === 'completed' ? 'enabled' : run.status === 'failed' ? 'error' : run.status === 'abandoned' ? 'disabled' : 'running'" :label="statusLabel(run.status)" /><div v-if="run.error" class="mt-1 max-w-xs text-xs text-destructive">{{ run.error }}</div><div v-else-if="run.status !== 'completed'" class="mt-1 text-xs text-muted-foreground">{{ run.progress_message }}</div></td><td class="max-w-sm px-3 py-3 text-xs text-muted-foreground">{{ scoreSummary(run) }}</td><td class="px-3 py-3 text-xs text-muted-foreground">{{ formatLocalDatetime(run.created_at) }}</td></tr></tbody></table></div>
      </CardContent>
    </Card>
  </div>
</template>
