<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Play, RefreshCw, Wrench } from '@lucide/vue'
import { api } from '../../api/client'
import type {
  CapabilityTool,
  CapabilityToolSummary,
  CatalogSource,
  ExecuteCapabilityResult,
  OpenApiTool,
  ProjectProfile,
} from '../../api/types'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { Textarea } from '../../components/ui/textarea'
import CategoryBadge from '../../components/CategoryBadge.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import { toolDebugFirstUseTour } from '../../lib/onboardingTours'
import { useOnboardingTour } from '../../composables/useOnboardingTour'

type ToolDebugSourceType = 'mcp_service' | 'openapi_service'

type ToolDebugService = CatalogSource & {
  source_type: ToolDebugSourceType
}

type ToolDebugTool = CapabilityTool & {
  source_type: ToolDebugSourceType
  service_name: string
}

const router = useRouter()
const profiles = ref<ProjectProfile[]>([])
const services = ref<ToolDebugService[]>([])
const tools = ref<ToolDebugTool[]>([])
const toolOptions = ref<(CapabilityToolSummary & { source_type: ToolDebugSourceType; service_name: string })[]>([])

const loading = ref(true)
const serviceLoading = ref(false)
const toolLoading = ref(false)
const executing = ref(false)

const error = ref('')
const executionError = ref('')
const result = ref<ExecuteCapabilityResult | null>(null)

const selectedProfileKey = ref('')
const selectedServiceId = ref('')
const selectedToolName = ref('')
const paramsText = ref('{\n  \n}')

const activeProfiles = computed(() => profiles.value.filter(profile => profile.status === 'active'))
const { maybeStartTour } = useOnboardingTour()

const selectedService = computed(() => {
  return services.value.find(service => serviceId(service) === selectedServiceId.value) || null
})

const selectedTool = computed(() => {
  return tools.value.find(tool => tool.tool_name === selectedToolName.value) || null
})

const selectedToolSchemaText = computed(() => prettyJson(selectedTool.value?.input_schema || {}))
const selectedToolExamplesText = computed(() => prettyJson(selectedTool.value?.examples || []))
const selectedToolRequestText = computed(() =>
  selectedTool.value && isOpenApiTool(selectedTool.value)
    ? `${selectedTool.value.method} ${selectedTool.value.path}`
    : '',
)

onMounted(async () => {
  await loadProfiles()
  if (!loading.value && !error.value) await maybeStartTour(toolDebugFirstUseTour)
})

watch(selectedProfileKey, async (profileKey, previous) => {
  if (!profileKey || profileKey === previous) return
  await loadServices(profileKey)
})

watch(selectedServiceId, async (serviceIdValue, previous) => {
  if (!serviceIdValue || serviceIdValue === previous) return
  await loadTools(serviceIdValue)
})

watch(selectedToolName, (toolName, previous) => {
  if (!toolName || toolName === previous) return
  executionError.value = ''
  result.value = null
  void loadToolDetail(toolName)
})

async function loadProfiles() {
  loading.value = true
  error.value = ''
  try {
    const list = await api.listProfiles()
    profiles.value = list
    selectedProfileKey.value = activeProfiles.value[0]?.profile_key || list[0]?.profile_key || ''
    if (!selectedProfileKey.value) {
      services.value = []
      tools.value = []
      toolOptions.value = []
    }
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function loadServices(profileKey: string) {
  serviceLoading.value = true
  error.value = ''
  executionError.value = ''
  result.value = null
  tools.value = []
  toolOptions.value = []
  selectedToolName.value = ''
  try {
    const catalog = await api.catalog(profileKey)
    services.value = catalog.sources
      .filter(source => source.source_type === 'mcp_service' || source.source_type === 'openapi_service')
      .map(source => ({ ...source, source_type: source.source_type as ToolDebugSourceType }))
    const nextServiceId = services.value[0] ? serviceId(services.value[0]) : ''
    if (nextServiceId !== selectedServiceId.value) {
      selectedServiceId.value = nextServiceId
    } else if (nextServiceId) {
      await loadTools(nextServiceId)
    }
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    serviceLoading.value = false
  }
}

async function loadTools(serviceIdValue: string) {
  const service = services.value.find(item => serviceId(item) === serviceIdValue)
  if (!service) {
    tools.value = []
    toolOptions.value = []
    selectedToolName.value = ''
    return
  }
  toolLoading.value = true
  executionError.value = ''
  result.value = null
  try {
    const list = service.source_type === 'openapi_service'
      ? await api.listOpenApiTools(service.source_key, true)
      : await api.listTools(service.source_key, true)
    toolOptions.value = (list as unknown as CapabilityToolSummary[]).map(tool => ({
      ...tool,
      source_type: service.source_type,
      service_name: service.name,
    }))
    tools.value = []
    const nextToolName = toolOptions.value[0]?.tool_name || ''
    if (nextToolName !== selectedToolName.value) {
      selectedToolName.value = nextToolName
    } else {
      paramsText.value = '{\n  \n}'
    }
  } catch (e: unknown) {
    tools.value = []
    selectedToolName.value = ''
    executionError.value = errorMessage(e)
  } finally {
    toolLoading.value = false
  }
}

async function loadToolDetail(toolName: string) {
  const service = selectedService.value
  if (!service || !toolName) return
  toolLoading.value = true
  try {
    const detail = service.source_type === 'openapi_service'
      ? await api.getOpenApiTool(service.source_key, toolName)
      : await api.getTool(service.source_key, toolName)
    tools.value = [{
      ...detail,
      source_type: service.source_type,
      service_name: service.name,
    }]
    paramsText.value = buildParamsTemplate(detail.input_schema || {})
  } catch (e: unknown) {
    tools.value = []
    executionError.value = errorMessage(e)
  } finally {
    toolLoading.value = false
  }
}

async function refreshCurrentProfile() {
  if (!selectedProfileKey.value) return
  await loadServices(selectedProfileKey.value)
}

async function executeTool() {
  if (!selectedProfileKey.value || !selectedService.value || !selectedTool.value || executing.value) return

  let params: Record<string, unknown> = {}
  try {
    const parsed = paramsText.value.trim() ? JSON.parse(paramsText.value) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      executionError.value = 'params 必须是 JSON 对象'
      return
    }
    params = parsed as Record<string, unknown>
  } catch {
    executionError.value = 'params 不是合法 JSON'
    return
  }

  executing.value = true
  executionError.value = ''
  result.value = null
  try {
    result.value = await api.executeCapability(
      {
        service: selectedService.value.source_key,
        tool_name: selectedTool.value.tool_name,
        params,
        profile_key: selectedProfileKey.value,
      },
      {
        profile_key: selectedProfileKey.value,
      },
    )
  } catch (e: unknown) {
    executionError.value = errorMessage(e)
  } finally {
    executing.value = false
  }
}

function serviceId(service: ToolDebugService) {
  return `${service.source_type}:${service.source_key}`
}

function sourceLabel(sourceType: ToolDebugSourceType) {
  return sourceType === 'openapi_service' ? 'OpenAPI' : 'MCP'
}

function profileLabel(profile: ProjectProfile) {
  return `${profile.name}${profile.status !== 'active' ? '（停用）' : ''}`
}

function isOpenApiTool(tool: ToolDebugTool): tool is ToolDebugTool & OpenApiTool {
  return tool.source_type === 'openapi_service' && 'method' in tool && 'path' in tool
}

function buildParamsTemplate(schema: Record<string, unknown>) {
  const example = schemaExample(schema)
  return prettyJson(example)
}

function schemaExample(schema: Record<string, unknown>) {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return {}
  return Object.fromEntries(
    Object.entries(properties).map(([name, definition]) => [name, exampleValue(definition)]),
  )
}

function exampleValue(definition: unknown): unknown {
  if (!definition || typeof definition !== 'object' || Array.isArray(definition)) return null
  const spec = definition as Record<string, unknown>
  if ('default' in spec) return spec.default
  if ('example' in spec) return spec.example
  if (spec.type === 'string') return '<string>'
  if (spec.type === 'integer' || spec.type === 'number') return 0
  if (spec.type === 'boolean') return false
  if (spec.type === 'array') return []
  if (spec.type === 'object') return {}
  return null
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
  <Button variant="ghost" size="sm" class="-ml-2 mb-4 h-8 px-2" @click="router.push('/services')">
    <ArrowLeft :size="14" />
    返回能力接入
  </Button>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
      {{ error }}
    </div>

    <Card data-tour="tool-debug-selection">
      <CardContent class="grid gap-4 p-5 lg:grid-cols-[220px_260px_minmax(0,1fr)_auto]">
        <div class="space-y-2">
          <div class="text-xs font-medium text-muted-foreground">能力平面</div>
          <Select v-model="selectedProfileKey">
            <SelectTrigger>
              <SelectValue placeholder="选择能力平面" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem v-for="profile in profiles" :key="profile.profile_key" :value="profile.profile_key">
                {{ profileLabel(profile) }}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div class="space-y-2">
          <div class="text-xs font-medium text-muted-foreground">服务</div>
          <Select v-model="selectedServiceId" :disabled="serviceLoading || !services.length">
            <SelectTrigger>
              <SelectValue :placeholder="serviceLoading ? '加载服务中...' : '选择服务'" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem v-for="service in services" :key="serviceId(service)" :value="serviceId(service)">
                {{ service.name }} · {{ sourceLabel(service.source_type) }}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div class="space-y-2">
          <div class="text-xs font-medium text-muted-foreground">工具</div>
          <Select v-model="selectedToolName" :disabled="toolLoading || !tools.length">
            <SelectTrigger>
              <SelectValue :placeholder="toolLoading ? '加载工具中...' : '选择工具'" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem v-for="tool in toolOptions" :key="tool.tool_name" :value="tool.tool_name">
                {{ tool.tool_name }}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div class="flex items-end gap-2">
          <Button variant="outline" :disabled="serviceLoading || toolLoading || !selectedProfileKey" @click="refreshCurrentProfile">
            <RefreshCw class="mr-1.5 h-4 w-4" />
            刷新
          </Button>
        </div>
      </CardContent>
    </Card>

    <div v-if="selectedTool" class="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
      <div class="space-y-4">
        <Card>
          <CardContent class="space-y-4 p-5">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-mono text-sm font-semibold text-foreground">{{ selectedTool.tool_name }}</span>
                  <CategoryBadge kind="toolType" :value="selectedTool.tool_type" />
                  <CategoryBadge kind="source" :value="selectedTool.source_type" />
                </div>
                <div class="mt-1 text-xs text-muted-foreground">
                  {{ selectedTool.service_name }}
                  <span v-if="selectedToolRequestText"> · {{ selectedToolRequestText }}</span>
                </div>
              </div>
            </div>

            <div class="rounded-md border bg-muted/20 p-3 text-sm text-muted-foreground">
              {{ selectedTool.description || '暂无描述' }}
            </div>

            <div v-if="selectedTool.tags?.length" class="flex flex-wrap gap-1.5">
              <span
                v-for="tag in selectedTool.tags"
                :key="tag"
                class="inline-flex items-center rounded-md bg-secondary px-2 py-0.5 text-[11px] font-medium text-card-foreground"
              >
                {{ tag }}
              </span>
            </div>

            <div data-tour="tool-debug-params" class="space-y-2">
              <div class="text-xs font-medium text-muted-foreground">params (JSON 对象)</div>
              <Textarea v-model="paramsText" class="min-h-[320px] font-mono text-xs" />
            </div>

            <div v-if="executionError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
              {{ executionError }}
            </div>

            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="text-xs text-muted-foreground">
                调试上下文：{{ profiles.find(p => p.profile_key === selectedProfileKey)?.name || selectedProfileKey }}
              </div>
              <Button data-tour="tool-debug-run" :disabled="executing" @click="executeTool">
                <Play class="mr-1.5 h-4 w-4" />
                {{ executing ? '执行中...' : '执行工具' }}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent class="space-y-2 p-5">
            <div class="flex items-center gap-2 text-sm font-medium text-foreground">
              <Wrench class="h-4 w-4" />
              调试结果
            </div>
            <div v-if="!result" class="rounded-md border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
              执行后会在这里展示返回结果
            </div>
            <div v-else class="space-y-3">
              <div class="grid gap-2 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground md:grid-cols-2">
                <div>能力平面：<span class="font-mono text-foreground">{{ result.profile_key || selectedProfileKey }}</span></div>
                <div>服务：<span class="font-mono text-foreground">{{ result.service }}</span></div>
                <div>工具：<span class="font-mono text-foreground">{{ result.tool_name }}</span></div>
                <div>来源：<span class="font-mono text-foreground">{{ sourceLabel(selectedTool.source_type) }}</span></div>
              </div>
              <JsonViewer :value="result.result" max-height="420px" />
            </div>
          </CardContent>
        </Card>
      </div>

      <div class="space-y-4">
        <Card>
          <CardContent class="space-y-3 p-5">
            <div class="text-sm font-medium text-foreground">输入 Schema</div>
            <JsonViewer :value="selectedTool.input_schema" max-height="420px" />
          </CardContent>
        </Card>

        <Card>
          <CardContent class="space-y-3 p-5">
            <div class="text-sm font-medium text-foreground">示例参数</div>
            <JsonViewer :value="selectedTool.examples" max-height="420px" />
          </CardContent>
        </Card>
      </div>
    </div>

    <Card v-else>
      <CardContent class="px-5 py-12 text-center text-sm text-muted-foreground">
        {{ selectedProfileKey ? '当前能力平面下暂无可调试工具，请先确认服务已启用且已被该能力平面放行。' : '请先选择能力平面。' }}
      </CardContent>
    </Card>
  </div>
</template>
