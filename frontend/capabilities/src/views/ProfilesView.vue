<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api } from '../api/client'
import type { ProjectProfile, ProfileSourceRule } from '../api/types'
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

const showDetail = ref(false)
const detailProfile = ref<ProjectProfile | null>(null)
const detailLoading = ref(false)
const detailRules = ref<ProfileSourceRule[]>([])
const newRule = ref({ source_type: 'mcp_service', source_key: '', effect: 'allow' as 'allow' | 'deny' })

const copied = ref('')

onMounted(async () => {
  try { profiles.value = await api.listProfiles() } catch { /* empty */ }
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

async function openDetail(p: ProjectProfile) {
  detailProfile.value = p
  showDetail.value = true
  detailLoading.value = true
  try {
    const full = await api.getProfile(p.profile_key)
    detailRules.value = full.rules || []
  } catch {
    detailRules.value = []
  }
  detailLoading.value = false
}

async function addRule() {
  if (!detailProfile.value || !newRule.value.source_key) return
  const rules = [...detailRules.value, { ...newRule.value }]
  await api.replaceProfileRules(detailProfile.value.profile_key, rules)
  detailRules.value = rules
  newRule.value.source_key = ''
  profiles.value = await api.listProfiles()
}

async function removeRule(idx: number) {
  if (!detailProfile.value) return
  const rules = detailRules.value.filter((_, i) => i !== idx)
  await api.replaceProfileRules(detailProfile.value.profile_key, rules)
  detailRules.value = rules
  profiles.value = await api.listProfiles()
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
    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="filtered.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          {{ search ? '无匹配结果' : '暂无 Profile' }}
        </div>
        <table v-else class="w-full">
          <thead>
            <tr class="border-b border-border bg-secondary/50">
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Profile</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">状态</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Allow</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Deny</th>
              <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in filtered" :key="p.profile_key" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3">
                <span class="cursor-pointer text-[13px] font-medium text-foreground hover:text-primary" @click="openDetail(p)">{{ p.profile_key }}</span>
                <div class="mt-0.5 text-xs text-muted-foreground">{{ p.name }}</div>
              </td>
              <td class="px-4 py-3">
                <Badge v-if="p.status === 'active'" variant="secondary" class="bg-green-50 text-green-700">启用</Badge>
                <Badge v-else variant="secondary" class="text-muted-foreground">停用</Badge>
              </td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ p.allow_count || 0 }}</td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ p.deny_count || 0 }}</td>
              <td class="px-4 py-3">
                <div class="flex gap-1">
                  <button @click="copyCommand(p)" class="rounded-md p-1.5 hover:bg-secondary transition-colors" title="复制接入命令">
                    <svg v-if="copied === p.profile_key" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
                    <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  </button>
                  <button @click="toggleStatus(p)" class="rounded-md p-1.5 hover:bg-secondary transition-colors" :title="p.status === 'active' ? '停用' : '启用'">
                    <svg v-if="p.status === 'active'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>
                    <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 12l2.5 2.5L16 9"/></svg>
                  </button>
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

    <!-- Detail Dialog -->
    <Dialog :open="showDetail" @update:open="showDetail = $event">
      <DialogContent class="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{{ detailProfile?.name || detailProfile?.profile_key }}</DialogTitle>
        </DialogHeader>
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        <div v-else class="space-y-5">
          <!-- Copy Command -->
          <div>
            <div class="mb-2 text-sm font-medium">接入命令</div>
            <div class="flex items-center gap-2 rounded-lg bg-secondary px-4 py-2.5">
              <code class="flex-1 font-mono text-sm text-foreground">{{ detailProfile ? getProfileCommand(detailProfile) : '' }}</code>
              <Button variant="ghost" size="sm" @click="detailProfile && copyCommand(detailProfile)">
                <svg v-if="copied === detailProfile?.profile_key" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
                <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </Button>
            </div>
          </div>

          <!-- Allow/Deny Rules -->
          <div>
            <div class="mb-2 text-sm font-medium">访问规则 (Allow / Deny)</div>
            <table v-if="detailRules.length > 0" class="mb-3 w-full">
              <thead>
                <tr class="border-b border-border bg-secondary/50">
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">来源类型</th>
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">来源标识</th>
                  <th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">策略</th>
                  <th class="px-3 py-2 text-right text-xs font-semibold text-muted-foreground"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, idx) in detailRules" :key="idx" class="border-b border-border/60">
                  <td class="px-3 py-2 text-sm">{{ r.source_type }}</td>
                  <td class="px-3 py-2 font-mono text-sm">{{ r.source_key }}</td>
                  <td class="px-3 py-2">
                    <Badge v-if="r.effect === 'allow'" variant="secondary" class="bg-green-50 text-green-700">Allow</Badge>
                    <Badge v-else variant="secondary" class="bg-red-50 text-red-700">Deny</Badge>
                  </td>
                  <td class="px-3 py-2 text-right">
                    <button @click="removeRule(idx)" class="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="mb-3 rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              暂无规则，添加来源以控制访问
            </div>

            <!-- Add Rule Form -->
            <div class="flex items-end gap-3">
              <div class="flex-1 space-y-1">
                <label class="text-xs font-medium text-muted-foreground">来源标识</label>
                <Input v-model="newRule.source_key" placeholder="如: my-mcp-service" />
              </div>
              <div class="w-[130px] space-y-1">
                <label class="text-xs font-medium text-muted-foreground">策略</label>
                <select v-model="newRule.effect" class="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm">
                  <option value="allow">Allow</option>
                  <option value="deny">Deny</option>
                </select>
              </div>
              <Button @click="addRule" :disabled="!newRule.source_key" size="sm">添加</Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
