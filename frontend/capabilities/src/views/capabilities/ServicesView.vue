<script setup lang="ts">
import { Search, Plus, RotateCw } from 'lucide-vue-next'
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import type { McpService } from '../../api/types'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { timeAgo } from '../../lib/time'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import { buildServicePayload, defaultServiceForm, serviceToForm, type ServiceFormMode } from './serviceForm'

const services = ref<McpService[]>([])
const toolCounts = ref<Record<string, number>>({})
const loading = ref(true)
const search = ref('')
const statusFilter = ref('all')

const showForm = ref(false)
const formMode = ref<ServiceFormMode>('create')
const form = ref(defaultServiceForm())
const saving = ref(false)
const formError = ref('')

async function loadServices() {
  try {
    services.value = await api.listServices()
    const counts: Record<string, number> = {}
    await Promise.all(
      services.value
        .filter(s => s.status === 'enabled')
        .map(async s => {
          try { counts[s.service_key] = (await api.listTools(s.service_key)).length } catch { counts[s.service_key] = 0 }
        })
    )
    toolCounts.value = counts
  } catch { /* empty */ }
}

onMounted(async () => {
  await loadServices()
  loading.value = false
})

const filtered = computed(() => {
  let list = services.value
  if (statusFilter.value === 'enabled') list = list.filter(s => s.status === 'enabled')
  if (statusFilter.value === 'disabled') list = list.filter(s => s.status === 'disabled')
  if (statusFilter.value === 'error') list = list.filter(s => s.status === 'error')
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(s =>
      s.service_key.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      s.endpoint_url.toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q)
    )
  }
  return list
})

const filterTabs = [
  { key: 'all', label: '全部', count: computed(() => services.value.length) },
  { key: 'enabled', label: '已启用', count: computed(() => services.value.filter(s => s.status === 'enabled').length) },
  { key: 'disabled', label: '已停用', count: computed(() => services.value.filter(s => s.status === 'disabled').length) },
  { key: 'error', label: '异常', count: computed(() => services.value.filter(s => s.status === 'error').length) },
]

const dialogTitle = computed(() => formMode.value === 'edit' ? '编辑 MCP 服务' : '新增 MCP 服务')
const primaryActionLabel = computed(() => saving.value ? '保存中...' : '保存')

function openCreate() {
  formMode.value = 'create'
  form.value = defaultServiceForm()
  formError.value = ''
  showForm.value = true
}

function openEdit(service: McpService) {
  formMode.value = 'edit'
  form.value = serviceToForm(service)
  formError.value = ''
  showForm.value = true
}

async function saveService() {
  formError.value = ''
  saving.value = true
  try {
    await api.registerService(buildServicePayload(form.value, formMode.value))
    showForm.value = false
    await loadServices()
  } catch (e: any) {
    formError.value = e.message || '注册失败'
  } finally {
    saving.value = false
  }
}

async function toggleStatus(svc: McpService) {
  const newStatus = svc.status === 'enabled' ? 'disabled' : 'enabled'
  await api.updateServiceStatus(svc.service_key, newStatus)
  await loadServices()
}

async function syncTools(key: string) {
  await api.syncServiceTools(key)
  await loadServices()
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
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
            statusFilter === tab.key
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          ]"
          @click="statusFilter = tab.key"
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count.value }}</span></button>
      </div>
      <Button @click="openCreate">
        <Plus :size="14" />
        新增服务
      </Button>
    </div>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="filtered.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ search ? '无匹配结果' : '暂无已登记的服务' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">服务名称</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">连接地址</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">工具数</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">最近同步</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in filtered" :key="s.service_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <span class="text-[13px] font-medium text-foreground">{{ s.service_key }}</span>
                <div class="mt-0.5 text-xs text-muted-foreground">{{ s.description || s.name }}</div>
              </td>
              <td class="max-w-[280px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 font-mono text-xs text-muted-foreground">{{ s.endpoint_url }}</td>
              <td class="px-4 py-3">
                <Badge v-if="s.status === 'enabled'" variant="secondary" class="bg-green-50 text-green-700">已启用</Badge>
                <Badge v-else-if="s.status === 'error'" variant="destructive">连接失败</Badge>
                <Badge v-else variant="secondary" class="text-muted-foreground">已停用</Badge>
              </td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ toolCounts[s.service_key] ?? '...' }}</td>
              <td class="px-4 py-3 text-xs text-muted-foreground">{{ timeAgo(s.last_synced_at) }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <Button variant="ghost" size="sm" @click="syncTools(s.service_key)" class="h-8 gap-1.5 text-xs">
                    <RotateCw :size="14" />
                    同步
                  </Button>
                  <Button variant="ghost" size="sm" @click="openEdit(s)" class="h-8 text-xs">
                    编辑
                  </Button>
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

    <!-- Pagination -->
    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>共 {{ filtered.length }} 条记录</span>
    </div>

    <!-- Service Dialog -->
    <Dialog :open="showForm" @update:open="showForm = $event">
      <DialogContent class="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{{ dialogTitle }}</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="saveService" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ formError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">服务标识 <span class="text-destructive">*</span></label>
            <Input v-model="form.service_key" placeholder="mysql-query" required :disabled="formMode === 'edit'" />
            <div class="text-xs text-muted-foreground">唯一标识符，仅支持小写字母、数字和连字符</div>
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">服务名称 <span class="text-destructive">*</span></label>
            <Input v-model="form.name" placeholder="MySQL 查询服务" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">服务地址 <span class="text-destructive">*</span></label>
            <Input v-model="form.endpoint_url" placeholder="http://example-mcp.internal:8080" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <textarea v-model="form.description" placeholder="简要描述该 MCP 服务提供的功能" rows="3" class="w-full rounded-lg border border-input px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">标签</label>
            <Input v-model="form.tags" placeholder="数据库, 查询 (逗号分隔)" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">请求 Header</label>
            <textarea
              v-model="form.headers"
              placeholder='{"Authorization":"Bearer token","X-Tenant":"docs"}'
              rows="5"
              class="w-full rounded-lg border border-input px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/10"
            />
            <div class="text-xs text-muted-foreground">
              使用 JSON 对象。编辑时留空表示保留已有 Header；填写 <code>{}</code> 可清空 Header。
            </div>
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="saveService" :disabled="saving">{{ primaryActionLabel }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
