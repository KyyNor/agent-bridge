<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { ArrowLeft } from '@lucide/vue'
import { api } from '../../api/client'
import type { ModelEvaluationDataset, ModelEvaluationModel, ModelEvaluationRun } from '../../api/types'
import { queryClient, queryKeys } from '../../lib/query'
import { formatLocalDatetime } from '../../lib/time'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'
import StatusBadge from '../../components/StatusBadge.vue'
import EvaluationRadarChart from '../../components/EvaluationRadarChart.vue'
import { modelEvaluationRadarScores } from '../../lib/modelEvaluationRadar'

const props = defineProps<{ routeKey: string }>()

const router = useRouter()
const models = ref<ModelEvaluationModel[]>([])
const form = ref({
  base_url: '', api_key: '', model_name: '', datasets: ['gsm8k_chat_gen'] as string[], max_samples: 64,
  sampling_mode: 'head' as 'head' | 'random', sample_seed: 42,
})
const loadingModels = ref(false)
const starting = ref(false)
const actionError = ref('')

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message || fallback : fallback
}

const datasetsQuery = useQuery({
  queryKey: queryKeys.modelEvaluationDatasets(),
  queryFn: ({ signal }) => api.listModelEvaluationDatasets({ signal }),
  staleTime: 60_000,
})
const runtimeQuery = useQuery({
  queryKey: queryKeys.modelEvaluationRuntime(),
  queryFn: ({ signal }) => api.getModelEvaluationRuntime({ signal }),
})
const runsQuery = useQuery({
  queryKey: queryKeys.modelEvaluationRuns(),
  queryFn: ({ signal }) => api.listModelEvaluationRuns({ signal }),
  refetchInterval: query => query.state.data?.some(run => run.status === 'queued' || run.status === 'running') ? 4_000 : false,
})

const activeRunId = computed(() => props.routeKey.split('/')[0]?.split('?')[0] || '')
const runDetailQuery = useQuery({
  queryKey: computed(() => queryKeys.modelEvaluationRun(activeRunId.value)),
  queryFn: ({ signal }) => api.getModelEvaluationRun(activeRunId.value, { signal }),
  enabled: computed(() => Boolean(activeRunId.value)),
  refetchInterval: query => query.state.data?.status === 'queued' || query.state.data?.status === 'running' ? 4_000 : false,
})

const datasets = computed(() => datasetsQuery.data.value || [])
const runs = computed(() => runsQuery.data.value || [])
const detailRun = computed(() => runDetailQuery.data.value || runs.value.find(run => run.run_id === activeRunId.value) || null)
const runtime = computed(() => runtimeQuery.data.value || null)
const selectedRadarScores = computed(() => detailRun.value ? modelEvaluationRadarScores(detailRun.value, datasets.value) : [])
const loading = computed(() => datasetsQuery.isLoading.value || runtimeQuery.isLoading.value || runsQuery.isLoading.value)
const error = computed(() => {
  if (actionError.value) return actionError.value
  const queryError = datasetsQuery.error.value || runtimeQuery.error.value || runsQuery.error.value
  return queryError ? errorMessage(queryError, '加载模型评估失败') : ''
})
const datasetGroups = computed(() => {
  const grouped = new Map<string, ModelEvaluationDataset[]>()
  for (const dataset of datasets.value) {
    const current = grouped.get(dataset.dimension) || []
    current.push(dataset)
    grouped.set(dataset.dimension, current)
  }
  return [...grouped.entries()].map(([key, items]) => ({ key, label: items[0]?.dimension_label || key, items }))
})

function refreshRuns() {
  actionError.value = ''
  void runsQuery.refetch()
}

async function loadModels() {
  loadingModels.value = true
  actionError.value = ''
  try {
    models.value = await api.listEvaluationModels({ base_url: form.value.base_url, api_key: form.value.api_key })
    if (!models.value.some(item => item.id === form.value.model_name)) form.value.model_name = models.value[0]?.id || ''
    if (!models.value.length) actionError.value = '接口未返回可用模型'
  } catch (error: unknown) { actionError.value = errorMessage(error, '获取模型列表失败') } finally { loadingModels.value = false }
}

async function startEvaluation() {
  starting.value = true
  actionError.value = ''
  try {
    await api.startModelEvaluationRun({ ...form.value })
    form.value.api_key = ''
    await queryClient.invalidateQueries({ queryKey: queryKeys.modelEvaluationRuns() })
  } catch (error: unknown) { actionError.value = errorMessage(error, '启动评估失败') } finally { starting.value = false }
}

function toggleDataset(key: string, checked: boolean) {
  form.value.datasets = checked
    ? [...new Set([...form.value.datasets, key])]
    : form.value.datasets.filter(item => item !== key)
}

function statusLabel(status: ModelEvaluationRun['status']) {
  return ({ queued: '等待执行', running: '评估中', completed: '已完成', completed_with_warnings: '部分完成', failed: '失败', abandoned: '已中断' } as const)[status]
}

function statusTone(status: ModelEvaluationRun['status']) {
  return status === 'completed' ? 'enabled' : status === 'completed_with_warnings' ? 'blocked' : status === 'failed' ? 'error' : status === 'abandoned' ? 'disabled' : 'running'
}

function samplingModeLabel(mode: ModelEvaluationRun['sampling_mode']) {
  return mode === 'random' ? '随机抽样' : '固定前 N 条'
}

function scoreSummary(run: ModelEvaluationRun) {
  const rows = run.result.rows
  if (!rows?.length) return run.status === 'completed' ? '未找到汇总 CSV' : '—'
  const visibleScores = rows.slice(0, 3).map(row => scoreForRow(row))
  const remainingCount = rows.length - visibleScores.length
  return `共 ${rows.length} 项：${visibleScores.join(' · ')}${remainingCount > 0 ? ` 等 ${remainingCount} 项` : ''}`
}

function scoreForRow(row: Record<string, string>) {
  return Object.entries(row).find(([key]) => !['dataset', 'version', 'metric', 'mode'].includes(key))?.[1] || '—'
}

function openRunDetail(run: ModelEvaluationRun) {
  void router.push(`/model-evaluations/${encodeURIComponent(run.run_id)}`)
}

function backToList() {
  void router.replace('/model-evaluations')
}
</script>

<template>
  <div v-if="activeRunId" class="mx-auto max-w-6xl space-y-4">
    <div class="flex items-start gap-3">
      <Button variant="ghost" size="sm" class="-ml-2 mt-0.5" @click="backToList">
        <ArrowLeft :size="16" />
        返回模型评估
      </Button>
      <div class="min-w-0 border-l border-border pl-3">
        <h2 class="truncate text-lg font-semibold">模型评估详情</h2>
        <p class="truncate font-mono text-xs text-muted-foreground">{{ activeRunId }}</p>
      </div>
    </div>

    <div v-if="runDetailQuery.isLoading.value && !detailRun" class="py-8 text-center text-sm text-muted-foreground">正在加载评估详情…</div>
    <div v-else-if="runDetailQuery.error.value" class="rounded-lg border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive">
      {{ errorMessage(runDetailQuery.error.value, '加载评估详情失败') }}
    </div>
    <div v-else-if="detailRun" class="space-y-5">
      <Card>
        <CardContent class="p-5">
          <dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div class="min-w-0"><dt class="text-xs text-muted-foreground">模型</dt><dd class="mt-1 break-all font-mono text-xs">{{ detailRun.model_name }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">状态</dt><dd class="mt-1"><StatusBadge :status="statusTone(detailRun.status)" :label="statusLabel(detailRun.status)" /></dd></div>
            <div><dt class="text-xs text-muted-foreground">数据集</dt><dd class="mt-1">{{ detailRun.datasets.join('、') }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">每个数据集题数</dt><dd class="mt-1">{{ detailRun.max_samples }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">抽样方式</dt><dd class="mt-1">{{ samplingModeLabel(detailRun.sampling_mode) }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">随机种子</dt><dd class="mt-1">{{ detailRun.sampling_mode === 'random' ? detailRun.sample_seed : '—' }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">开始时间</dt><dd class="mt-1">{{ detailRun.started_at ? formatLocalDatetime(detailRun.started_at) : '—' }}</dd></div>
            <div><dt class="text-xs text-muted-foreground">结束时间</dt><dd class="mt-1">{{ detailRun.finished_at ? formatLocalDatetime(detailRun.finished_at) : '—' }}</dd></div>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardContent class="space-y-5 p-5">
          <div>
            <div class="mb-2 font-medium">评估结果</div>
            <div v-if="detailRun.result.rows?.length" class="overflow-x-auto rounded-md border border-border"><table class="w-full min-w-[560px] text-sm"><thead><tr class="border-b border-border text-left text-xs text-muted-foreground"><th class="px-3 py-2">数据集</th><th class="px-3 py-2">指标</th><th class="px-3 py-2">分数</th></tr></thead><tbody><tr v-for="row in detailRun.result.rows" :key="`${row.dataset}-${row.metric}`" class="border-b border-border/70 last:border-0"><td class="px-3 py-2">{{ row.dataset }}</td><td class="px-3 py-2">{{ row.metric || '—' }}</td><td class="px-3 py-2 font-medium">{{ scoreForRow(row) }}</td></tr></tbody></table></div>
            <p v-else-if="detailRun.error" class="whitespace-pre-line break-words text-destructive">{{ detailRun.error }}</p>
            <p v-else class="text-muted-foreground">{{ detailRun.status === 'completed' ? '未找到汇总结果。' : detailRun.progress_message }}</p>
          </div>
          <EvaluationRadarChart v-if="detailRun.result.rows?.length" :scores="selectedRadarScores" />
        </CardContent>
      </Card>

      <Card v-if="detailRun.executions.length || detailRun.result.sample_manifests?.length">
        <CardContent class="space-y-5 p-5">
          <div v-if="detailRun.executions.length">
            <div class="mb-2 font-medium">子执行</div>
            <div class="space-y-2">
              <div v-for="execution in detailRun.executions" :key="execution.execution_id" class="rounded-md border border-border p-3 text-xs">
                <div class="flex flex-wrap items-center gap-2"><span class="font-medium">{{ execution.datasets.join('、') }}</span><StatusBadge :status="execution.status === 'completed' ? 'enabled' : execution.status === 'failed' ? 'error' : execution.status === 'abandoned' ? 'disabled' : 'running'" :label="statusLabel(execution.status)" /></div>
                <div class="mt-1 text-muted-foreground">runner：{{ execution.runner_key }} · 镜像：<span class="font-mono">{{ execution.image }}</span></div>
                <div v-if="execution.container_id" class="mt-1 break-all font-mono text-muted-foreground">container：{{ execution.container_id }}</div>
                <div v-if="execution.error" class="mt-1 whitespace-pre-line break-words font-mono text-destructive">{{ execution.error }}</div>
                <div v-else-if="execution.status !== 'completed'" class="mt-1 text-muted-foreground">{{ execution.progress_message }}</div>
              </div>
            </div>
          </div>
          <details v-if="detailRun.result.sample_manifests?.length" class="rounded-md border border-border p-3 text-xs">
            <summary class="cursor-pointer font-medium">查看实际抽样题目索引</summary>
            <div v-for="manifest in detailRun.result.sample_manifests" :key="manifest.dataset" class="mt-3"><span class="font-medium">{{ manifest.dataset }}</span><span class="ml-2 text-muted-foreground">{{ manifest.mode === 'random' ? `seed ${manifest.seed}` : '固定顺序' }}</span><p class="mt-1 break-all text-muted-foreground">{{ manifest.source_indices.join(', ') }}</p></div>
          </details>
        </CardContent>
      </Card>
    </div>
  </div>
  <div v-else-if="loading" class="py-16 text-center text-sm text-muted-foreground">正在加载模型评估…</div>
  <div v-else class="mx-auto max-w-6xl space-y-5">
    <Card v-if="runtime && !runtime.configured" class="border-warning/40 bg-warning-soft/30">
      <CardContent class="space-y-2 p-5 text-sm">
        <div class="font-medium text-warning-soft-fg">{{ runtime.message }}</div>
        <p class="text-muted-foreground">模型评估仅支持本地 Docker。请启动 Docker daemon，并导入下列指定镜像后重启服务。</p>
        <div class="grid gap-2 sm:grid-cols-2">
          <div v-for="(image, key) in runtime.images" :key="key" class="rounded border border-border bg-background/70 p-2 text-xs">
            <div class="font-medium">{{ key === 'opencompass' ? 'OpenCompass runner' : 'Agent worker' }}</div>
            <div class="mt-1 break-all font-mono text-muted-foreground">{{ image.image }}</div>
            <div class="mt-1" :class="image.available ? 'text-success' : 'text-destructive'">{{ image.available ? '已就绪' : '缺失' }}</div>
          </div>
        </div>
      </CardContent>
    </Card>
    <Card>
      <CardContent class="space-y-5 p-5">
        <div>
          <div class="text-base font-medium">发起模型评估</div>
          <p class="mt-1 text-sm text-muted-foreground">通过本地 Docker 的隔离评估器，对 OpenAI 兼容模型进行可复现的五维能力评测。</p>
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
        <div class="space-y-3">
          <div class="text-sm font-medium">评估设置</div>
          <div class="flex flex-wrap gap-x-6 gap-y-4 rounded-md border border-border bg-muted/20 p-4">
            <label class="space-y-2 text-sm">
              <span>每个数据集最多题数</span>
              <Input v-model.number="form.max_samples" type="number" min="1" max="1000" class="w-36" />
            </label>
            <label class="space-y-2 text-sm">
              <span>抽样方式</span>
              <select v-model="form.sampling_mode" class="h-9 min-w-32 rounded-md border border-input bg-background px-3 text-sm"><option value="head">固定前 N 条</option><option value="random">随机抽样</option></select>
            </label>
            <label v-if="form.sampling_mode === 'random'" class="space-y-2 text-sm">
              <span>随机种子</span>
              <Input v-model.number="form.sample_seed" type="number" min="0" max="2147483647" class="w-40" />
            </label>
          </div>
        </div>
        <div class="space-y-3">
          <div class="text-sm font-medium">评估数据集</div>
          <div class="space-y-4">
            <section v-for="group in datasetGroups" :key="group.key" class="space-y-2">
              <div class="text-xs font-medium text-muted-foreground">{{ group.label }}</div>
              <div class="grid gap-2 md:grid-cols-2">
                <label v-for="dataset in group.items" :key="dataset.key" class="flex cursor-pointer gap-3 rounded-md border border-border p-3 text-sm">
                  <input type="checkbox" class="mt-1 h-4 w-4" :checked="form.datasets.includes(dataset.key)" @change="toggleDataset(dataset.key, ($event.target as HTMLInputElement).checked)" />
                  <span><span class="font-medium">{{ dataset.label }}</span><span class="mt-1 block text-xs text-muted-foreground">{{ dataset.description }}</span><span class="mt-1 block text-[11px] text-muted-foreground">指标：{{ dataset.metric }} · {{ dataset.runner === 'swebench' ? '独立 Agent testbed' : dataset.runner === 'code' ? '无网络代码沙箱' : 'OpenCompass runner' }}</span></span>
                </label>
              </div>
            </section>
          </div>
          <p class="text-xs text-muted-foreground">所有勾选的数据集统一最多运行此数量的题目；SWE-bench 中对应最多任务数。随机抽样使用固定种子，便于后续复跑与模型横向比较。</p>
        </div>
        <div class="flex items-center gap-3">
          <Button @click="startEvaluation" :disabled="starting || !runtime?.configured || !form.model_name || !form.datasets.length || form.max_samples < 1 || form.max_samples > 1000 || form.sample_seed < 0 || form.sample_seed > 2147483647">{{ starting ? '正在启动…' : '开始评估' }}</Button>
          <span v-if="error" class="text-sm text-destructive">{{ error }}</span>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardContent class="p-5">
        <div class="mb-4 flex items-center justify-between"><div><div class="text-base font-medium">评估记录</div><p class="mt-1 text-sm text-muted-foreground">每项能力使用独立 Docker runner；详情中可查看子执行、镜像与实际抽样题目。</p></div><Button size="sm" variant="outline" @click="refreshRuns">刷新</Button></div>
        <div v-if="!runs.length" class="py-10 text-center text-sm text-muted-foreground">尚未发起模型评估。</div>
        <div v-else class="overflow-x-auto"><table class="w-full min-w-[900px] text-sm"><thead><tr class="border-b border-border text-left text-xs text-muted-foreground"><th class="px-3 py-2">模型</th><th class="px-3 py-2">数据集</th><th class="px-3 py-2">每集题数</th><th class="px-3 py-2">状态</th><th class="px-3 py-2">结果</th><th class="px-3 py-2">创建时间</th><th class="px-3 py-2"></th></tr></thead><tbody><tr v-for="run in runs" :key="run.run_id" class="border-b border-border/70"><td class="px-3 py-3 font-mono text-xs">{{ run.model_name }}</td><td class="px-3 py-3 text-xs">{{ run.datasets.join('、') }}</td><td class="px-3 py-3 text-xs">{{ run.max_samples }}</td><td class="px-3 py-3"><StatusBadge :status="statusTone(run.status)" :label="statusLabel(run.status)" /><div v-if="run.error" class="mt-1 max-w-xs whitespace-pre-line break-words text-xs text-destructive">{{ run.error }}</div><div v-else-if="run.status !== 'completed'" class="mt-1 text-xs text-muted-foreground">{{ run.progress_message }}</div></td><td class="max-w-sm px-3 py-3 text-xs text-muted-foreground">{{ scoreSummary(run) }}</td><td class="px-3 py-3 text-xs text-muted-foreground">{{ formatLocalDatetime(run.created_at) }}</td><td class="px-3 py-3 text-right"><Button size="sm" variant="outline" @click="openRunDetail(run)">查看详情</Button></td></tr></tbody></table></div>
      </CardContent>
    </Card>

  </div>
</template>
