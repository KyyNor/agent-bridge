<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import type { ProjectProfile, ProfileSourceRule, ProfileResourceRule, McpService, KnowledgeBaseSummary, CodeRepository } from '../api/types'
import { Card, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'

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
const allServices = ref<McpService[]>([])
const allKbs = ref<KnowledgeBaseSummary[]>([])
const allRepos = ref<CodeRepository[]>([])


const copied = ref('')

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

async function createProfile() {
  formError.value = ''
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(form.value.profile_key)) {
    formError.value = 'Profile 标识仅支持小写英文、数字、连字符和下划线'
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
    const [services, kbs, repos, full] = await Promise.all([
      api.listServices(),
      api.listWikiKbs(),
      api.listCodeRepos(),
      api.getProfile(p.profile_key),
    ])
    allServices.value = services
    allKbs.value = kbs
    allRepos.value = repos
    configRules.value = full.rules || []
    configResources.value = full.resource_rules || []
  } catch {
    configRules.value = []
    configResources.value = []
  }
  configLoading.value = false
}

function isServiceAllowed(key: string) {
  return configRules.value.some(r => r.source_key === key && r.effect === 'allow')
}

async function toggleServiceAllow(key: string) {
  if (!configProfile.value) return
  let rules: ProfileSourceRule[]
  if (isServiceAllowed(key)) {
    rules = configRules.value.filter(r => !(r.source_key === key && r.effect === 'allow'))
  } else {
    rules = [...configRules.value, { source_type: 'mcp_service', source_key: key, effect: 'allow' as const }]
  }
  await api.replaceProfileRules(configProfile.value.profile_key, rules)
  configRules.value = rules
  profiles.value = await api.listProfiles()
}

function isResourceAllowed(type: string, key: string) {
  return configResources.value.some(r => r.resource_type === type && r.resource_key === key)
}

async function toggleResource(type: string, key: string) {
  if (!configProfile.value) return
  let resources: ProfileResourceRule[]
  if (isResourceAllowed(type, key)) {
    resources = configResources.value.filter(r => !(r.resource_type === type && r.resource_key === key))
  } else {
    resources = [...configResources.value, { resource_type: type, resource_key: key }]
  }
  await api.replaceProfileResources(configProfile.value.profile_key, resources)
  configResources.value = resources
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-4">
      <div class="relative flex-1 max-w-[360px]">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <Input v-model="search" placeholder="搜索 Profile 标识或名称..." class="pl-8" />
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
        添加 Profile
      </Button>
    </div>

    <!-- Table -->
    <Card>
      <CardContent class="p-0">
        <div v-if="filtered.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ search ? '无匹配结果' : '暂无 Profile' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border">
              <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Profile</th>
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
          <DialogTitle>添加 Profile</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createProfile" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ formError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Profile 标识 <span class="text-destructive">*</span></label>
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
      <DialogContent class="sm:max-w-[600px]">
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
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
