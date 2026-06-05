<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import type { McpTool } from '../api/types'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { Input } from '../components/ui/input'

const tools = ref<McpTool[]>([])
const services = ref<{ service_key: string; name: string }[]>([])
const selectedService = ref('')
const loading = ref(false)
const search = ref('')
const typeFilter = ref('')

onMounted(async () => {
  services.value = await api.listServices()
})

async function loadTools() {
  if (!selectedService.value) {
    tools.value = []
    return
  }
  loading.value = true
  try {
    tools.value = await api.listTools(selectedService.value)
  } catch { tools.value = [] }
  loading.value = false
}

async function updateType(svc: string, tool: string, t: string) {
  await api.updateToolType(svc, tool, t)
  await loadTools()
}

const filtered = ref(false)
const displayTools = ref<McpTool[]>([])

function filter() {
  let list = tools.value
  if (typeFilter.value) list = list.filter(t => t.tool_type === typeFilter.value)
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(t => t.tool_name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q))
  }
  displayTools.value = list
}

watch([tools, typeFilter, search], () => filter())

const toolTypes = [
  { value: 'overview', label: '概览', color: 'bg-blue-50 text-blue-700' },
  { value: 'search', label: '检索', color: 'bg-purple-50 text-purple-700' },
  { value: 'detail', label: '明细', color: 'bg-teal-50 text-teal-700' },
  { value: 'action', label: '操作', color: 'bg-amber-50 text-amber-700' },
  { value: 'unconfigured', label: '未配置', color: 'bg-gray-100 text-gray-600' },
]

function typeLabel(v: string) { return toolTypes.find(t => t.value === v)?.label || v }
function typeColor(v: string) { return toolTypes.find(t => t.value === v)?.color || '' }
</script>

<template>
  <div class="space-y-5">
    <Card class="border-border">
      <CardHeader>
        <div class="flex flex-wrap items-center justify-between gap-4">
          <CardTitle>工具目录</CardTitle>
          <div class="flex items-center gap-3">
            <Select v-model="selectedService" @update:model-value="loadTools()">
              <SelectTrigger class="w-[220px]">
                <SelectValue placeholder="选择服务..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem v-for="s in services" :key="s.service_key" :value="s.service_key">{{ s.name }}</SelectItem>
              </SelectContent>
            </Select>
            <Select v-model="typeFilter">
              <SelectTrigger class="w-[120px]">
                <SelectValue placeholder="全部层级" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部层级</SelectItem>
                <SelectItem v-for="t in toolTypes" :key="t.value" :value="t.value">{{ t.label }}</SelectItem>
              </SelectContent>
            </Select>
            <div class="relative">
              <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <Input v-model="search" placeholder="搜索工具..." class="w-[200px] pl-8" />
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent class="p-0">
        <div v-if="loading" class="px-5 py-12 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else-if="displayTools.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ selectedService ? (search || typeFilter ? '无匹配结果' : '该服务暂无已同步的工具') : '请选择一个服务' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">工具名称</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">层级</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">标签</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">描述</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">配置</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in displayTools" :key="t.tool_name" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3">
                <span class="font-mono text-[13px] font-semibold text-foreground">{{ t.tool_name }}</span>
              </td>
              <td class="px-4 py-3">
                <Badge variant="secondary" :class="typeColor(t.tool_type)">{{ typeLabel(t.tool_type) }}</Badge>
              </td>
              <td class="px-4 py-3">
                <span v-if="t.tags?.length" class="flex flex-wrap gap-1">
                  <span v-for="tag in t.tags" :key="tag" class="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">{{ tag }}</span>
                </span>
                <span v-else class="text-xs text-muted-foreground">—</span>
              </td>
              <td class="max-w-[300px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{{ t.description }}</td>
              <td class="px-4 py-3">
                <Select :default-value="t.tool_type" @update:model-value="(v) => updateType(selectedService, t.tool_name, String(v))">
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
  </div>
</template>
