<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import type { McpTool, McpService } from '../../api/types'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { Input } from '../../components/ui/input'

interface ToolWithService extends McpTool {
  service_name: string
}

const allTools = ref<ToolWithService[]>([])
const services = ref<McpService[]>([])
const selectedService = ref('__all__')
const loading = ref(false)
const search = ref('')
const typeFilter = ref('')

onMounted(async () => {
  loading.value = true
  try {
    services.value = await api.listServices()
    const active = services.value.filter(s => s.status === 'enabled')
    const results: ToolWithService[] = []
    for (const s of active) {
      try {
        const tools = await api.listTools(s.service_key)
        for (const t of tools) {
          results.push({ ...t, service_name: s.name || s.service_key })
        }
      } catch { /* ignore errors for individual services */ }
    }
    allTools.value = results
  } catch { /* empty */ }
  loading.value = false
})

async function updateType(svc: string, toolName: string, newType: string) {
  await api.updateToolType(svc, toolName, newType)
  const found = allTools.value.find(x => x.service_key === svc && x.tool_name === toolName)
  if (found) found.tool_type = newType
}

const displayTools = computed(() => {
  let list = allTools.value
  if (selectedService.value && selectedService.value !== '__all__') list = list.filter(t => t.service_key === selectedService.value)
  if (typeFilter.value) list = list.filter(t => t.tool_type === typeFilter.value)
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(t => t.tool_name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q))
  }
  return list
})

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
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
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
          @click="typeFilter = tab.key"
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span></button>
      </div>
      <Select v-model="selectedService">
        <SelectTrigger class="w-[200px]">
          <SelectValue placeholder="全部服务" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部服务</SelectItem>
          <SelectItem v-for="s in services" :key="s.service_key" :value="s.service_key">{{ s.name }}</SelectItem>
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
            <tr v-for="t in displayTools" :key="`${t.service_key}:${t.tool_name}`" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <span class="font-mono text-[13px] font-semibold text-foreground">{{ t.tool_name }}</span>
              </td>
              <td class="px-4 py-3 text-sm text-muted-foreground">{{ t.service_name }}</td>
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

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>共 {{ displayTools.length }} 条记录</span>
    </div>
  </div>
</template>
