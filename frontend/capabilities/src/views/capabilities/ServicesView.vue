<script setup lang="ts">
import { Globe2, PlugZap, Plus, RotateCw, Search } from 'lucide-vue-next'
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import type { CapabilityServiceSource, McpService, OpenApiService, OpenApiTool } from '../../api/types'
import { Card, CardContent } from '../../components/ui/card'
import { timeAgo } from '../../lib/time'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import {
  buildOpenApiServicePayload,
  buildServicePayload,
  defaultOpenApiServiceForm,
  defaultServiceForm,
  openApiServiceToForm,
  serviceToForm,
  type ServiceFormMode,
  type ServiceSourceType,
} from './serviceForm'
import {
  buildOpenApiToolPayload,
  defaultOpenApiImportState,
  selectedOperations,
  toggleOperationSelection,
  type OpenApiImportState,
} from './openapiImport'

const mcpServices = ref<McpService[]>([])
const openApiServices = ref<OpenApiService[]>([])
const toolCounts = ref<Record<string, number>>({})
const loading = ref(true)
const search = ref('')
const statusFilter = ref('all')
const sourceFilter = ref<'all' | ServiceSourceType>('all')

const showForm = ref(false)
const formMode = ref<ServiceFormMode>('create')
const sourceType = ref<ServiceSourceType>('mcp_service')
const mcpForm = ref(defaultServiceForm())
const openApiForm = ref(defaultOpenApiServiceForm())
const saving = ref(false)
const formError = ref('')

const showImport = ref(false)
const importService = ref<OpenApiService | null>(null)
const importState = ref<OpenApiImportState>(defaultOpenApiImportState())
const importing = ref(false)
const importSaving = ref(false)
const importError = ref('')

const services = computed<CapabilityServiceSource[]>(() => [
  ...mcpServices.value.map(service => ({ ...service, source_type: 'mcp_service' as const })),
  ...openApiServices.value.map(service => ({ ...service, source_type: 'openapi_service' as const })),
])

function serviceCountKey(service: CapabilityServiceSource) {
  return `${service.source_type}:${service.service_key}`
}

function serviceUrl(service: CapabilityServiceSource) {
  return service.source_type === 'openapi_service' ? service.base_url : service.endpoint_url
}

function lastSyncAt(service: CapabilityServiceSource) {
  return service.source_type === 'openapi_service' ? service.last_imported_at : service.last_synced_at
}

async function loadServices() {
  try {
    const [mcp, openapi] = await Promise.all([api.listServices(), api.listOpenApiServices()])
    mcpServices.value = mcp
    openApiServices.value = openapi
    const counts: Record<string, number> = {}
    await Promise.all([
      ...mcp
        .filter(s => s.status === 'enabled')
        .map(async s => {
          try { counts[`mcp_service:${s.service_key}`] = (await api.listTools(s.service_key)).length } catch { counts[`mcp_service:${s.service_key}`] = 0 }
        }),
      ...openapi
        .filter(s => s.status === 'enabled')
        .map(async s => {
          try { counts[`openapi_service:${s.service_key}`] = (await api.listOpenApiTools(s.service_key)).length } catch { counts[`openapi_service:${s.service_key}`] = 0 }
        }),
    ])
    toolCounts.value = counts
  } catch { /* empty */ }
}

onMounted(async () => {
  await loadServices()
  loading.value = false
})

const filtered = computed(() => {
  let list = services.value
  if (sourceFilter.value !== 'all') list = list.filter(s => s.source_type === sourceFilter.value)
  if (statusFilter.value === 'enabled') list = list.filter(s => s.status === 'enabled')
  if (statusFilter.value === 'disabled') list = list.filter(s => s.status === 'disabled')
  if (statusFilter.value === 'error') list = list.filter(s => s.status === 'error')
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(s =>
      s.service_key.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      serviceUrl(s).toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q)
    )
  }
  return list
})

const filterTabs = computed(() => [
  { key: 'all', label: '全部', count: services.value.length },
  { key: 'enabled', label: '已启用', count: services.value.filter(s => s.status === 'enabled').length },
  { key: 'disabled', label: '已停用', count: services.value.filter(s => s.status === 'disabled').length },
  { key: 'error', label: '异常', count: services.value.filter(s => s.status === 'error').length },
])

const dialogTitle = computed(() => {
  const sourceLabel = sourceType.value === 'openapi_service' ? 'OpenAPI 服务' : 'MCP 服务'
  return formMode.value === 'edit' ? `编辑 ${sourceLabel}` : `新增 ${sourceLabel}`
})
const primaryActionLabel = computed(() => saving.value ? '保存中...' : '保存')

function openCreate(type: ServiceSourceType = 'mcp_service') {
  formMode.value = 'create'
  sourceType.value = type
  mcpForm.value = defaultServiceForm()
  openApiForm.value = defaultOpenApiServiceForm()
  formError.value = ''
  showForm.value = true
}

function openEdit(service: CapabilityServiceSource) {
  formMode.value = 'edit'
  sourceType.value = service.source_type
  formError.value = ''
  if (service.source_type === 'openapi_service') openApiForm.value = openApiServiceToForm(service)
  else mcpForm.value = serviceToForm(service)
  showForm.value = true
}

async function saveService() {
  formError.value = ''
  saving.value = true
  try {
    if (sourceType.value === 'openapi_service') {
      await api.registerOpenApiService(buildOpenApiServicePayload(openApiForm.value, formMode.value))
    } else {
      await api.registerService(buildServicePayload(mcpForm.value, formMode.value))
    }
    showForm.value = false
    await loadServices()
  } catch (e: any) {
    formError.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function toggleStatus(svc: CapabilityServiceSource) {
  const newStatus = svc.status === 'enabled' ? 'disabled' : 'enabled'
  if (svc.source_type === 'openapi_service') await api.updateOpenApiServiceStatus(svc.service_key, newStatus)
  else await api.updateServiceStatus(svc.service_key, newStatus)
  await loadServices()
}

async function syncMcpTools(key: string) {
  await api.syncServiceTools(key)
  await loadServices()
}

async function openImportDialog(service: OpenApiService) {
  importService.value = service
  importError.value = ''
  importing.value = true
  showImport.value = true
  try {
    const result = await api.importOpenApiOperations(service.service_key)
    importState.value = {
      operations: result.operations,
      selected: new Set(result.operations.map(item => item.tool_name)),
    }
  } catch (e: any) {
    importState.value = defaultOpenApiImportState()
    importError.value = e.message || '同步接口失败'
  } finally {
    importing.value = false
  }
}

function operationSelected(toolName: string) {
  return importState.value.selected.has(toolName)
}

function setOperationType(operation: OpenApiTool, value: string) {
  operation.tool_type = value
}

async function saveImportedOperations() {
  if (!importService.value) return
  importError.value = ''
  importSaving.value = true
  try {
    for (const operation of selectedOperations(importState.value)) {
      const payload = buildOpenApiToolPayload(operation)
      await api.upsertOpenApiTool(importService.value.service_key, payload.tool_name, payload)
    }
    showImport.value = false
    await loadServices()
  } catch (e: any) {
    importError.value = e.message || '保存接口失败'
  } finally {
    importSaving.value = false
  }
}

const toolTypeOptions = [
  { value: 'overview', label: '概览' },
  { value: 'search', label: '检索' },
  { value: 'detail', label: '明细' },
  { value: 'action', label: '操作' },
  { value: 'unconfigured', label: '未配置' },
]
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <Input v-model="search" placeholder="搜索服务名称、地址或描述..." class="pl-8" />
      </div>
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="tab in filterTabs" :key="tab.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            statusFilter === tab.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="statusFilter = tab.key"
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span></button>
      </div>
      <Select v-model="sourceFilter">
        <SelectTrigger class="w-[150px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类型</SelectItem>
          <SelectItem value="mcp_service">MCP</SelectItem>
          <SelectItem value="openapi_service">OpenAPI</SelectItem>
        </SelectContent>
      </Select>
      <Button @click="openCreate('openapi_service')" variant="outline">
        <Globe2 :size="14" />
        OpenAPI
      </Button>
      <Button @click="openCreate('mcp_service')">
        <Plus :size="14" />
        MCP
      </Button>
    </div>

    <Card>
      <CardContent class="p-0">
        <div v-if="filtered.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ search ? '无匹配结果' : '暂无已登记的服务' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">服务名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">类型</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">连接地址</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">工具数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">最近同步</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in filtered" :key="serviceCountKey(s)" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <span class="text-[13px] font-medium text-foreground">{{ s.service_key }}</span>
                <div class="mt-0.5 text-xs text-muted-foreground">{{ s.description || s.name }}</div>
              </td>
              <td class="px-4 py-3">
                <Badge v-if="s.source_type === 'openapi_service'" variant="secondary" class="bg-cyan-50 text-cyan-700">
                  <Globe2 :size="12" /> OpenAPI
                </Badge>
                <Badge v-else variant="secondary" class="bg-indigo-50 text-indigo-700">
                  <PlugZap :size="12" /> MCP
                </Badge>
              </td>
              <td class="max-w-[280px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 font-mono text-xs text-muted-foreground">{{ serviceUrl(s) }}</td>
              <td class="px-4 py-3">
                <Badge v-if="s.status === 'enabled'" variant="secondary" class="bg-green-50 text-green-700">已启用</Badge>
                <Badge v-else-if="s.status === 'error'" variant="destructive">连接失败</Badge>
                <Badge v-else variant="secondary" class="text-muted-foreground">已停用</Badge>
              </td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ toolCounts[serviceCountKey(s)] ?? '...' }}</td>
              <td class="px-4 py-3 text-xs text-muted-foreground">{{ timeAgo(lastSyncAt(s)) }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <Button v-if="s.source_type === 'openapi_service'" variant="ghost" size="sm" @click="openImportDialog(s)" class="h-8 gap-1.5 text-xs">
                    <RotateCw :size="14" />
                    同步接口
                  </Button>
                  <Button v-else variant="ghost" size="sm" @click="syncMcpTools(s.service_key)" class="h-8 gap-1.5 text-xs">
                    <RotateCw :size="14" />
                    同步
                  </Button>
                  <Button variant="ghost" size="sm" @click="openEdit(s)" class="h-8 text-xs">编辑</Button>
                  <Button variant="ghost" size="sm" @click="toggleStatus(s)" class="h-8 text-xs">
                    {{ s.status === 'enabled' ? '停用' : '启用' }}
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>共 {{ filtered.length }} 条记录</span>
    </div>

    <Dialog :open="showForm" @update:open="showForm = $event">
      <DialogContent class="sm:max-w-[680px]">
        <DialogHeader>
          <DialogTitle>{{ dialogTitle }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveService" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ formError }}</div>
          <div v-if="formMode === 'create'" class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
            <button type="button" :class="['flex-1 rounded-md px-3 py-1.5 text-sm font-medium', sourceType === 'mcp_service' ? 'bg-card shadow-sm' : 'text-muted-foreground']" @click="sourceType = 'mcp_service'">MCP</button>
            <button type="button" :class="['flex-1 rounded-md px-3 py-1.5 text-sm font-medium', sourceType === 'openapi_service' ? 'bg-card shadow-sm' : 'text-muted-foreground']" @click="sourceType = 'openapi_service'">OpenAPI</button>
          </div>

          <template v-if="sourceType === 'mcp_service'">
            <div class="space-y-2">
              <label class="text-sm font-medium">服务标识 <span class="text-destructive">*</span></label>
              <Input v-model="mcpForm.service_key" placeholder="mysql-query" required :disabled="formMode === 'edit'" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">服务名称 <span class="text-destructive">*</span></label>
              <Input v-model="mcpForm.name" placeholder="MySQL 查询服务" required />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">服务地址 <span class="text-destructive">*</span></label>
              <Input v-model="mcpForm.endpoint_url" placeholder="http://example-mcp.internal:8080" required />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">描述</label>
              <textarea v-model="mcpForm.description" rows="3" class="w-full rounded-lg border border-input px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">标签</label>
              <Input v-model="mcpForm.tags" placeholder="数据库, 查询" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">请求 Header</label>
              <textarea v-model="mcpForm.headers" placeholder='{"Authorization":"Bearer token"}' rows="5" class="w-full rounded-lg border border-input px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
            </div>
          </template>

          <template v-else>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-2">
                <label class="text-sm font-medium">服务标识 <span class="text-destructive">*</span></label>
                <Input v-model="openApiForm.service_key" placeholder="petstore" required :disabled="formMode === 'edit'" />
              </div>
              <div class="space-y-2">
                <label class="text-sm font-medium">服务名称 <span class="text-destructive">*</span></label>
                <Input v-model="openApiForm.name" placeholder="Petstore API" required />
              </div>
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">Base URL <span class="text-destructive">*</span></label>
              <Input v-model="openApiForm.base_url" placeholder="https://api.example.com/v1" required />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">Spec URL</label>
              <Input v-model="openApiForm.spec_url" placeholder="https://api.example.com/openapi.yaml" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">Spec 内容</label>
              <textarea v-model="openApiForm.spec_content" rows="6" class="w-full rounded-lg border border-input px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-2">
                <label class="text-sm font-medium">认证配置</label>
                <textarea v-model="openApiForm.auth_config" placeholder='{"type":"bearer","token":"..."}' rows="5" class="w-full rounded-lg border border-input px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
              </div>
              <div class="space-y-2">
                <label class="text-sm font-medium">请求 Header</label>
                <textarea v-model="openApiForm.headers" placeholder='{"Accept":"application/json"}' rows="5" class="w-full rounded-lg border border-input px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
              </div>
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">描述</label>
              <textarea v-model="openApiForm.description" rows="3" class="w-full rounded-lg border border-input px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium">标签</label>
              <Input v-model="openApiForm.tags" placeholder="业务, 查询" />
            </div>
          </template>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveService" :disabled="saving">{{ primaryActionLabel }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="showImport" @update:open="showImport = $event">
      <DialogContent class="sm:max-w-[920px]">
        <DialogHeader>
          <DialogTitle>同步接口 · {{ importService?.name || importService?.service_key }}</DialogTitle>
        </DialogHeader>
        <div v-if="importError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ importError }}</div>
        <div v-if="importing" class="py-10 text-center text-sm text-muted-foreground">解析 OpenAPI 文档中...</div>
        <div v-else class="max-h-[560px] overflow-auto rounded-lg border border-border">
          <table class="w-full">
            <thead>
              <tr class="border-b border-border bg-muted/40">
                <th class="w-10 px-3 py-2"></th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">接口</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">描述</th>
                <th class="w-32 px-3 py-2 text-left text-xs font-medium text-muted-foreground">类型</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="operation in importState.operations" :key="operation.tool_name" class="border-b border-border/60">
                <td class="px-3 py-2">
                  <input type="checkbox" :checked="operationSelected(operation.tool_name)" @change="toggleOperationSelection(importState, operation.tool_name)" />
                </td>
                <td class="px-3 py-2">
                  <div class="font-mono text-xs font-semibold">{{ operation.tool_name }}</div>
                  <div class="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <Badge variant="secondary">{{ operation.method }}</Badge>
                    <span class="font-mono">{{ operation.path }}</span>
                  </div>
                </td>
                <td class="px-3 py-2">
                  <Input v-model="operation.description" class="h-8 text-xs" />
                </td>
                <td class="px-3 py-2">
                  <Select :default-value="operation.tool_type" @update:model-value="(v) => setOperationType(operation, String(v))">
                    <SelectTrigger class="h-8 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="item in toolTypeOptions" :key="item.value" :value="item.value">{{ item.label }}</SelectItem>
                    </SelectContent>
                  </Select>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="importState.operations.length === 0" class="px-5 py-10 text-center text-sm text-muted-foreground">没有可导入接口</div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveImportedOperations" :disabled="importSaving || importing || importState.selected.size === 0">
            {{ importSaving ? '保存中...' : `保存选中接口（${importState.selected.size}）` }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
