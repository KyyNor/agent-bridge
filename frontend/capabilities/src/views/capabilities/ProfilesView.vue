<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../../api/client'
import type {
  ProjectProfile,
  ProfileDocRender,
  ProfilePinPreview,
  ProfilePinRule,
  ProfileSourceRule,
  ProfileResourceRule,
  McpService,
  KnowledgeBaseSummary,
  CodeRepository,
} from '../../api/types'
import { Card, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'

const profiles = ref<ProjectProfile[]>([])
const loading = ref(true)
const search = ref('')
const statusFilter = ref('all')

const showAdd = ref(false)
const form = ref({ profile_key: '', name: '', description: '', status: 'active' })
const saving = ref(false)
const formError = ref('')

const showConfig = ref(false)
const configProfile = ref<ProjectProfile | null>(null)
const configLoading = ref(false)
const configRules = ref<ProfileSourceRule[]>([])
const configResources = ref<ProfileResourceRule[]>([])
const pendingRules = ref<ProfileSourceRule[]>([])
const pendingResources = ref<ProfileResourceRule[]>([])
const allServices = ref<McpService[]>([])
const allKbs = ref<KnowledgeBaseSummary[]>([])
const allRepos = ref<CodeRepository[]>([])
const configSaving = ref(false)
const pinPreview = ref<ProfilePinPreview | null>(null)
const pendingPins = ref<ProfilePinRule[]>([])
const pinMode = ref<'disabled' | 'ratio' | 'count'>('disabled')
const pinRatio = ref(10)
const pinCount = ref(3)
const pinSaving = ref(false)
const profileMarkdown = ref('')
const manualNotes = ref('')
const docSaving = ref(false)

const copied = ref('')
const pinToolTypes = ['overview', 'search', 'detail']

onMounted(async () => {
  try {
    profiles.value = await api.listProfiles()
    allServices.value = await api.listServices()
  } catch { /* empty */ }
  loading.value = false
})

const filtered = computed(() => {
  let list = profiles.value
  if (statusFilter.value === 'active') list = list.filter(p => p.status === 'active')
  if (statusFilter.value === 'disabled') list = list.filter(p => p.status !== 'active')
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(p => p.profile_key.toLowerCase().includes(q) || p.name.toLowerCase().includes(q))
  }
  return list
})

const filterTabs = computed(() => [
  { key: 'all', label: '全部', count: profiles.value.length },
  { key: 'active', label: '启用', count: profiles.value.filter(p => p.status === 'active').length },
  { key: 'disabled', label: '停用', count: profiles.value.filter(p => p.status !== 'active').length },
])

const allowedServices = computed(() =>
  allServices.value.filter(svc => isServiceAllowed(svc.service_key))
)

const autoPinGroups = computed(() =>
  (pinPreview.value?.groups || []).filter(g => g.source === 'auto')
)

const manualPinGroups = computed(() =>
  pendingPins.value.map(pin => ({
    ...pin,
    service_name: serviceName(pin.service_key),
  }))
)

async function createProfile() {
  formError.value = ''
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(form.value.profile_key)) {
    formError.value = '能力平面标识仅支持小写英文、数字、连字符和下划线'
    return
  }
  saving.value = true
  try {
    await api.upsertProfile({
      profile_key: form.value.profile_key,
      name: form.value.name,
      description: form.value.description,
      status: form.value.status,
    })
    showAdd.value = false
    profiles.value = await api.listProfiles()
  } catch (e: any) {
    formError.value = e.message || '创建失败'
  }
  saving.value = false
}

async function toggleStatus(p: ProjectProfile) {
  await api.upsertProfile({
    profile_key: p.profile_key,
    name: p.name,
    status: p.status === 'active' ? 'disabled' : 'active',
  })
  profiles.value = await api.listProfiles()
}

function getProfileCommand(p: ProjectProfile) {
  return `agent-bridge profile use ${p.profile_key}`
}

async function copyCommand(p: ProjectProfile) {
  const cmd = getProfileCommand(p)
  try {
    await navigator.clipboard.writeText(cmd)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = cmd
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = p.profile_key
  setTimeout(() => { copied.value = '' }, 2000)
}

async function openConfig(p: ProjectProfile) {
  configProfile.value = p
  showConfig.value = true
  configLoading.value = true
  try {
    const [services, kbs, repos, full, pins, doc] = await Promise.all([
      api.listServices(),
      api.listWikiKbs(),
      api.listCodeRepos(),
      api.getProfile(p.profile_key),
      api.getProfilePins(p.profile_key),
      api.renderProfileDoc(p.profile_key),
    ])
    allServices.value = services
    allKbs.value = kbs
    allRepos.value = repos
    configRules.value = full.rules || []
    configResources.value = full.resource_rules || []
    pendingRules.value = [...configRules.value]
    pendingResources.value = [...configResources.value]
    applyPinPreview(pins)
    applyProfileDoc(doc)
    manualNotes.value = ''
  } catch {
    configRules.value = []
    configResources.value = []
    pendingRules.value = []
    pendingResources.value = []
    pinPreview.value = null
    pendingPins.value = []
    pinMode.value = 'disabled'
    pinRatio.value = 10
    pinCount.value = 3
    profileMarkdown.value = ''
    manualNotes.value = ''
  }
  configLoading.value = false
}

function applyPinPreview(pins: ProfilePinPreview) {
  pinPreview.value = pins
  pendingPins.value = pins.groups
    .filter(g => g.source === 'manual')
    .map(({ service_key, tool_type }) => ({ service_key, tool_type }))
  pinMode.value = pins.settings.mode
  pinRatio.value = pins.settings.ratio_percent ?? 10
  pinCount.value = pins.settings.count ?? 3
}

function applyProfileDoc(doc: ProfileDocRender) {
  profileMarkdown.value = doc.markdown
}

function isServiceAllowed(key: string) {
  return pendingRules.value.some(r => r.source_key === key && r.effect === 'allow')
}

function toggleServiceAllow(key: string) {
  if (isServiceAllowed(key)) {
    pendingRules.value = pendingRules.value.filter(r => !(r.source_key === key && r.effect === 'allow'))
    pendingPins.value = pendingPins.value.filter(pin => pin.service_key !== key)
  } else {
    pendingRules.value = [...pendingRules.value, { source_type: 'mcp_service', source_key: key, effect: 'allow' as const }]
  }
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    overview: '概览',
    search: '搜索',
    detail: '详情',
    action: '操作',
  }
  return labels[type] || type
}

function serviceName(key: string) {
  return allServices.value.find(svc => svc.service_key === key)?.name || key
}

function manualPinExists(serviceKey: string, toolType: string) {
  return pendingPins.value.some(pin => pin.service_key === serviceKey && pin.tool_type === toolType)
}

function toggleManualPin(serviceKey: string, toolType: string) {
  if (!pinToolTypes.includes(toolType)) return
  if (manualPinExists(serviceKey, toolType)) {
    pendingPins.value = pendingPins.value.filter(pin => !(pin.service_key === serviceKey && pin.tool_type === toolType))
  } else {
    pendingPins.value = [...pendingPins.value, { service_key: serviceKey, tool_type: toolType }]
  }
}

function isResourceAllowed(type: string, key: string) {
  return pendingResources.value.some(r => r.resource_type === type && r.resource_key === key)
}

function toggleResource(type: string, key: string) {
  if (isResourceAllowed(type, key)) {
    pendingResources.value = pendingResources.value.filter(r => !(r.resource_type === type && r.resource_key === key))
  } else {
    pendingResources.value = [...pendingResources.value, { resource_type: type, resource_key: key }]
  }
}

async function saveConfig() {
  if (!configProfile.value) return
  configSaving.value = true
  try {
    await api.replaceProfileRules(configProfile.value.profile_key, pendingRules.value)
    await api.replaceProfileResources(configProfile.value.profile_key, pendingResources.value)
    await savePins()
    await refreshProfileDoc()
    configRules.value = [...pendingRules.value]
    configResources.value = [...pendingResources.value]
    profiles.value = await api.listProfiles()
    showConfig.value = false
  } catch { /* ignore */ }
  configSaving.value = false
}

async function savePins() {
  if (!configProfile.value) return
  pinSaving.value = true
  try {
    await api.replaceProfilePins(configProfile.value.profile_key, pendingPins.value)
    const pins = await api.updateProfilePinSettings(configProfile.value.profile_key, {
      mode: pinMode.value,
      ratio_percent: pinMode.value === 'ratio' ? pinRatio.value : null,
      count: pinMode.value === 'count' ? pinCount.value : null,
    })
    applyPinPreview(pins)
  } finally {
    pinSaving.value = false
  }
}

async function refreshPins() {
  if (!configProfile.value) return
  pinSaving.value = true
  try {
    const pins = await api.refreshProfilePins(configProfile.value.profile_key)
    applyPinPreview(pins)
  } catch { /* ignore */ }
  pinSaving.value = false
}

async function saveManualNotes() {
  if (!configProfile.value) return
  docSaving.value = true
  try {
    const doc = await api.updateProfileManualNotes(configProfile.value.profile_key, manualNotes.value)
    applyProfileDoc(doc)
    manualNotes.value = ''
  } catch { /* ignore */ }
  docSaving.value = false
}

async function refreshProfileDoc() {
  if (!configProfile.value) return
  docSaving.value = true
  try {
    const doc = await api.renderProfileDoc(configProfile.value.profile_key)
    applyProfileDoc(doc)
  } finally {
    docSaving.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <Input v-model="search" placeholder="搜索能力平面标识或名称..." class="pl-8" />
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
        >{{ tab.label }} <span class="font-normal text-muted-foreground">{{ tab.count }}</span></button>
      </div>
      <Button @click="showAdd = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加能力平面
</Button>
    </div>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="filtered.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ search ? '无匹配结果' : '暂无能力平面' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">能力平面</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Allow</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in filtered" :key="p.profile_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="px-4 py-3">
                <span class="text-[13px] font-medium text-foreground">{{ p.profile_key }}</span>
                <div class="mt-0.5 text-xs text-muted-foreground">{{ p.name }}</div>
              </td>
              <td class="px-4 py-3">
                <Badge v-if="p.status === 'active'" variant="secondary" class="bg-green-50 text-green-700">启用</Badge>
                <Badge v-else variant="secondary" class="text-muted-foreground">停用</Badge>
              </td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ p.allow_count || 0 }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <Button variant="ghost" size="sm" @click="openConfig(p)" class="h-8 gap-1.5 text-xs">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                    配置
                  </Button>
                  <Button variant="ghost" size="sm" @click="copyCommand(p)" class="h-8 text-xs">
                    {{ copied === p.profile_key ? '已复制' : '复制命令' }}
                  </Button>
                  <Button variant="ghost" size="sm" @click="toggleStatus(p)" class="h-8 text-xs">
                    {{ p.status === 'active' ? '停用' : '启用' }}
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

    <!-- Add Dialog -->
    <Dialog :open="showAdd" @update:open="showAdd = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>添加能力平面</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createProfile" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ formError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">能力平面标识 <span class="text-destructive">*</span></label>
            <Input v-model="form.profile_key" placeholder="safe-readonly" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">名称 <span class="text-destructive">*</span></label>
            <Input v-model="form.name" placeholder="安全只读" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">描述</label>
            <Input v-model="form.description" placeholder="适用于当前项目的能力策略" />
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline" type="button">取消</Button></DialogClose>
          <Button @click="createProfile" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Config Dialog -->
    <Dialog :open="showConfig" @update:open="showConfig = $event">
      <DialogContent class="max-h-[85vh] overflow-y-auto sm:max-w-[800px]">
        <DialogHeader>
          <DialogTitle>{{ configProfile?.name || configProfile?.profile_key }} — 配置</DialogTitle>
        </DialogHeader>
        <div v-if="configLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else class="space-y-5">
          <!-- Copy Command -->
          <div>
            <div class="mb-2 text-sm font-medium">接入命令</div>
            <div class="flex items-center gap-2 rounded-lg bg-secondary px-4 py-2.5">
              <code class="flex-1 font-mono text-sm text-foreground">{{ configProfile ? getProfileCommand(configProfile) : '' }}</code>
              <Button variant="ghost" size="sm" @click="configProfile && copyCommand(configProfile)">
                {{ copied === configProfile?.profile_key ? '已复制' : '复制' }}
              </Button>
            </div>
          </div>

          <!-- Allow List -->
          <div>
            <div class="mb-2 text-sm font-medium">允许访问的服务</div>
            <div v-if="allServices.length === 0" class="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              暂无已注册的服务，请先在能力接入中添加服务
            </div>
            <div v-else class="max-h-[240px] space-y-1 overflow-y-auto">
              <label
                v-for="svc in allServices" :key="svc.service_key"
                class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-secondary"
              >
                <input
                  type="checkbox"
                  :checked="isServiceAllowed(svc.service_key)"
                  @change="toggleServiceAllow(svc.service_key)"
                  class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ svc.name || svc.service_key }}</div>
                  <div class="text-xs text-muted-foreground">{{ svc.service_key }}</div>
                </div>
                <Badge v-if="svc.status === 'enabled'" variant="secondary" class="bg-green-50 text-green-700 text-[11px]">已启用</Badge>
                <Badge v-else-if="svc.status === 'error'" variant="destructive" class="text-[11px]">异常</Badge>
                <Badge v-else variant="secondary" class="text-[11px] text-muted-foreground">已停用</Badge>
              </label>
            </div>
          </div>

          <!-- Pinned Tools -->
          <div class="space-y-3 rounded-lg border border-border p-4">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div class="text-sm font-medium">Pinned Tools</div>
                <div class="mt-1 text-xs text-muted-foreground">
                  当前会暴露 {{ pinPreview?.tools.length || 0 }} 个 pin_* 工具
                </div>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" :disabled="pinSaving" @click="refreshPins">
                  {{ pinSaving ? '处理中...' : '重新计算自动 Pin' }}
                </Button>
                <Button size="sm" :disabled="pinSaving" @click="savePins">
                  {{ pinSaving ? '保存中...' : '保存 Pin' }}
                </Button>
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-[160px_1fr_1fr]">
              <label class="space-y-1">
                <span class="text-xs font-medium text-muted-foreground">自动 Pin 模式</span>
                <select
                  v-model="pinMode"
                  class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="disabled">关闭</option>
                  <option value="ratio">按比例</option>
                  <option value="count">按数量</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium text-muted-foreground">比例百分比</span>
                <Input v-model.number="pinRatio" type="number" min="1" max="100" :disabled="pinMode !== 'ratio'" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium text-muted-foreground">数量</span>
                <Input v-model.number="pinCount" type="number" min="1" :disabled="pinMode !== 'count'" />
              </label>
            </div>

            <div>
              <div class="mb-2 text-xs font-medium text-muted-foreground">手动 Pin</div>
              <div v-if="manualPinGroups.length === 0" class="rounded-md border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">
                暂无手动 pin；可在下方按服务选择概览、搜索或详情。
              </div>
              <div v-else class="flex flex-wrap gap-2">
                <span
                  v-for="pin in manualPinGroups"
                  :key="`${pin.service_key}:${pin.tool_type}`"
                  class="inline-flex items-center gap-2 rounded-md bg-secondary px-2.5 py-1 text-xs"
                >
                  <span class="font-medium text-foreground">{{ pin.service_name }}</span>
                  <span class="text-muted-foreground">{{ pin.service_key }} / {{ typeLabel(pin.tool_type) }}</span>
                  <button class="text-muted-foreground hover:text-destructive" type="button" @click="toggleManualPin(pin.service_key, pin.tool_type)">移除</button>
                </span>
              </div>
            </div>

            <div>
              <div class="mb-2 text-xs font-medium text-muted-foreground">按已允许服务选择 Pin Level</div>
              <div v-if="allowedServices.length === 0" class="rounded-md border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">
                请先在上方允许至少一个服务。
              </div>
              <div v-else class="max-h-[220px] space-y-2 overflow-y-auto">
                <div
                  v-for="svc in allowedServices"
                  :key="`pin-${svc.service_key}`"
                  class="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 px-3 py-2"
                >
                  <div class="min-w-[180px] flex-1">
                    <div class="text-sm font-medium">{{ svc.name || svc.service_key }}</div>
                    <div class="text-xs text-muted-foreground">{{ svc.service_key }}</div>
                  </div>
                  <label
                    v-for="toolType in pinToolTypes"
                    :key="`${svc.service_key}-${toolType}`"
                    class="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors hover:bg-background"
                  >
                    <input
                      type="checkbox"
                      :checked="manualPinExists(svc.service_key, toolType)"
                      @change="toggleManualPin(svc.service_key, toolType)"
                      class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                    />
                    {{ typeLabel(toolType) }}
                  </label>
                </div>
              </div>
            </div>

            <div v-if="autoPinGroups.length > 0" class="rounded-md bg-secondary px-3 py-2">
              <div class="mb-1 text-xs font-medium text-muted-foreground">自动 Pin 预览</div>
              <div class="flex flex-wrap gap-2">
                <span
                  v-for="pin in autoPinGroups.slice(0, 12)"
                  :key="`auto-${pin.service_key}-${pin.tool_type}`"
                  class="rounded bg-background px-2 py-1 text-xs text-muted-foreground"
                >
                  {{ serviceName(pin.service_key) }} / {{ typeLabel(pin.tool_type) }}{{ pin.calls ? ` · ${pin.calls} 次` : '' }}
                </span>
                <span v-if="autoPinGroups.length > 12" class="px-2 py-1 text-xs text-muted-foreground">
                  另 {{ autoPinGroups.length - 12 }} 项
                </span>
              </div>
            </div>
          </div>

          <!-- KB Resources -->
          <div>
            <div class="mb-2 text-sm font-medium">允许访问的文档知识</div>
            <div v-if="allKbs.length === 0" class="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
              暂无文档知识，请先在文档知识中添加
            </div>
            <div v-else class="max-h-[200px] space-y-1 overflow-y-auto">
              <label
                v-for="kb in allKbs" :key="kb.slug"
                class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-secondary"
              >
                <input
                  type="checkbox"
                  :checked="isResourceAllowed('wiki_kb', kb.slug)"
                  @change="toggleResource('wiki_kb', kb.slug)"
                  class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ kb.name }}</div>
                  <div class="text-xs text-muted-foreground">{{ kb.slug }} · {{ kb.document_count }} 文档</div>
                </div>
              </label>
            </div>
          </div>

          <!-- Code Repo Resources -->
          <div>
            <div class="mb-2 text-sm font-medium">允许访问的代码仓库</div>
            <div v-if="allRepos.length === 0" class="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
              暂无代码仓库，请先在代码知识中添加
            </div>
            <div v-else class="max-h-[200px] space-y-1 overflow-y-auto">
              <label
                v-for="repo in allRepos" :key="repo.repo_key"
                class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-secondary"
              >
                <input
                  type="checkbox"
                  :checked="isResourceAllowed('code_repo', repo.repo_key)"
                  @change="toggleResource('code_repo', repo.repo_key)"
                  class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ repo.name }}</div>
                  <div class="text-xs text-muted-foreground">{{ repo.repo_key }}</div>
                </div>
              </label>
            </div>
          </div>

          <!-- Profile Doc -->
          <div class="space-y-3 rounded-lg border border-border p-4">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div class="text-sm font-medium">Profile 文档</div>
                <div class="mt-1 text-xs text-muted-foreground">手动补充保存后会写入 profile 文档。</div>
              </div>
              <Button variant="outline" size="sm" :disabled="docSaving" @click="refreshProfileDoc">
                {{ docSaving ? '生成中...' : '重新生成/预览' }}
              </Button>
            </div>
            <div class="space-y-2">
              <label class="text-xs font-medium text-muted-foreground">手动补充 Notes</label>
              <textarea
                v-model="manualNotes"
                class="min-h-[96px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="补充该 profile 的使用边界、注意事项或协作约定..."
              />
              <Button size="sm" :disabled="docSaving || manualNotes.trim().length === 0" @click="saveManualNotes">
                {{ docSaving ? '保存中...' : '保存手动补充' }}
              </Button>
            </div>
            <div>
              <div class="mb-2 text-xs font-medium text-muted-foreground">Markdown 预览</div>
              <pre class="max-h-[260px] overflow-y-auto whitespace-pre-wrap rounded-md bg-secondary p-3 text-xs leading-relaxed text-foreground">{{ profileMarkdown || '暂无 profile 文档预览' }}</pre>
            </div>
          </div>
        </div>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button @click="saveConfig" :disabled="configSaving || pinSaving || docSaving">{{ configSaving ? '保存中...' : '确认' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
