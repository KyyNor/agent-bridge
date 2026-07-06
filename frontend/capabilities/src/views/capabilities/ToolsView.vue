<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import type { CapabilityServiceSource, CapabilityTool } from '../../api/types'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { Input } from '../../components/ui/input'
import PaginationBar from '../../components/PaginationBar.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

type ToolWithService = CapabilityTool & {
  source_type: 'mcp_service' | 'openapi_service'
  service_name: string
}

const allTools = ref<ToolWithService[]>([])
const services = ref<CapabilityServiceSource[]>([])
const selectedService = ref('__all__')
const loading = ref(false)
const search = ref('')
const typeFilter = ref('')
const page = ref(1)
const pageSize = ref(10)

onMounted(async () => {
  loading.value = true
  try {
    const [mcpServices, openApiServices] = await Promise.all([api.listServices(), api.listOpenApiServices()])
    services.value = [
      ...mcpServices.map(service => ({ ...service, source_type: 'mcp_service' as const })),
      ...openApiServices.map(service => ({ ...service, source_type: 'openapi_service' as const })),
    ]
    const active = services.value.filter(s => s.status === 'enabled')
    const results: ToolWithService[] = []
    for (const s of active) {
      try {
        const tools = s.source_type === 'openapi_service'
          ? await api.listOpenApiTools(s.service_key)
          : await api.listTools(s.service_key)
        for (const t of tools) {
          results.push({ ...t, source_type: s.source_type, service_name: s.name || s.service_key })
        }
      } catch { /* ignore errors for individual services */ }
    }
    allTools.value = results
  } catch { /* empty */ }
  loading.value = false
})

async function updateType(svc: string, toolName: string, newType: string) {
  const found = allTools.value.find(x => x.service_key === svc && x.tool_name === toolName)
  if (found?.source_type === 'openapi_service') await api.updateOpenApiToolType(svc, toolName, newType)
  else await api.updateToolType(svc, toolName, newType)
  if (found) found.tool_type = newType
}

const displayTools = computed(() => {
  let list = allTools.value
  if (selectedService.value && selectedService.value !== '__all__') {
    const [sourceType, serviceKey] = selectedService.value.split(':')
    list = list.filter(t => t.source_type === sourceType && t.service_key === serviceKey)
  }
  if (typeFilter.value) list = list.filter(t => t.tool_type === typeFilter.value)
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(t => t.tool_name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q))
  }
  return list
})
const pagedTools = computed(() => paginate(displayTools.value, page.value, pageSize.value))

const toolTypes = [
  { value: 'overview', label: '概览', color: 'bg-blue-50 text-blue-700' },
  { value: 'search', label: '检索', color: 'bg-purple-50 text-purple-700' },
  { value: 'detail', label: '明细', color: 'bg-teal-50 text-teal-700' },
  { value: 'action', label: '操作', color: 'bg-amber-50 text-amber-700' },
  { value: 'unconfigured', label: '未配置', color: 'bg-gray-100 text-gray-600' },
]

const filterTabs = computed(() => [
  { key: '', label: '全部', count: allTools.value.length },
  ...toolTypes.map(tt => ({
    key: tt.value,
    label: tt.label,
    count: allTools.value.filter(t => t.tool_type === tt.value).length,
  })),
])

function typeLabel(v: string) { return toolTypes.find(t => t.value === v)?.label || v }
function typeColor(v: string) { return toolTypes.find(t => t.value === v)?.color || '' }
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <Input v-model="search" placeholder="搜索工具名称或描述..." class="pl-8" />
      </div>
      <div class="flex gap-0.5 rounded-lg bg-secondary p-0.5">
        <button
          v-for="tab in filterTabs" :key="tab.key"
          :class="[
            'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors',
            typeFilter === tab.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="() => { typeFilter = tab.key; page = 1 }"
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span></button>
      </div>
      <Select v-model="selectedService" @update:model-value="page = 1">
        <SelectTrigger class="w-[200px]">
          <SelectValue placeholder="全部服务" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部服务</SelectItem>
          <SelectItem v-for="s in services" :key="`${s.source_type}:${s.service_key}`" :value="`${s.source_type}:${s.service_key}`">
            {{ s.name }} · {{ s.source_type === 'openapi_service' ? 'OpenAPI' : 'MCP' }}
          </SelectItem>
        </SelectContent>
      </Select>
    </div>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="displayTools.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ allTools.length === 0 ? '暂无已同步的工具，请先在能力接入中同步工具。' : '无匹配结果' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">工具名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">服务</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">层级</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">标签</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">描述</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">配置</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in pagedTools" :key="`${t.service_key}:${t.tool_name}`" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <span class="font-mono text-[13px] font-semibold text-foreground">{{ t.tool_name }}</span>
              </td>
              <td class="px-4 py-3 text-sm text-muted-foreground">
                {{ t.service_name }}
                <div v-if="t.source_type === 'openapi_service'" class="mt-0.5 font-mono text-[11px] text-muted-foreground">
                  {{ 'method' in t ? t.method : '' }} {{ 'path' in t ? t.path : '' }}
                </div>
              </td>
              <td class="px-4 py-3">
                <Badge variant="secondary" :class="typeColor(t.tool_type)">{{ typeLabel(t.tool_type) }}</Badge>
              </td>
              <td class="px-4 py-3">
                <span v-if="t.tags?.length" class="flex flex-wrap gap-1">
                  <span v-for="tag in t.tags" :key="tag" class="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">{{ tag }}</span>
                </span>
                <span v-else class="text-xs text-muted-foreground">&mdash;</span>
              </td>
              <td class="max-w-[300px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ t.description }}</td>
              <td class="px-4 py-3">
                <Select :default-value="t.tool_type" @update:model-value="(v) => updateType(t.service_key, t.tool_name, String(v))">
                  <SelectTrigger class="h-8 w-[100px] text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="tt in toolTypes" :key="tt.value" :value="tt.value">{{ tt.label }}</SelectItem>
                  </SelectContent>
                </Select>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <PaginationBar
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="displayTools.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />
  </div>
</template>
