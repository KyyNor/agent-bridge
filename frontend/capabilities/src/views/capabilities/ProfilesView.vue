<script setup lang="ts">
import { Search, Plus, Settings } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { ProjectProfile } from '../../api/types'
import { Card, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../../components/ui/dialog'
import StatusBadge from '../../components/StatusBadge.vue'
import SegmentedTabs from '../../components/SegmentedTabs.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import ProfileDetailView from './ProfileDetailView.vue'
import { confirm } from '../../composables/useConfirm'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

const props = defineProps<{ routeKey: string }>()

const profiles = ref<ProjectProfile[]>([])
const loading = ref(true)
const search = ref('')
const statusFilter = ref('all')
const page = ref(1)
const pageSize = ref(10)

const showAdd = ref(false)
const form = ref({ profile_key: '', name: '', description: '', status: 'active' })
const saving = ref(false)
const formError = ref('')

const copied = ref('')
const detailRef = ref<{ hasUnsavedChanges: boolean } | null>(null)
const allowRouteLeave = ref(false)

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
const pagedProfiles = computed(() => paginate(filtered.value, page.value, pageSize.value))
const detailProfile = computed(() => profiles.value.find(p => p.profile_key === props.routeKey) || null)

const filterTabs = computed(() => [
  { key: 'all', label: '全部', count: profiles.value.length },
  { key: 'active', label: '启用', count: profiles.value.filter(p => p.status === 'active').length },
  { key: 'disabled', label: '停用', count: profiles.value.filter(p => p.status !== 'active').length },
])

onMounted(async () => {
  try {
    profiles.value = await api.listProfiles()
  } catch {
    profiles.value = []
  } finally {
    loading.value = false
  }
})

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

async function toggleStatus(profile: ProjectProfile) {
  await api.upsertProfile({
    profile_key: profile.profile_key,
    name: profile.name,
    status: profile.status === 'active' ? 'disabled' : 'active',
  })
  profiles.value = await api.listProfiles()
}

function getProfileCommand(profile: ProjectProfile) {
  return `agent-bridge profile use ${profile.profile_key}`
}

async function copyCommand(profile: ProjectProfile) {
  const cmd = getProfileCommand(profile)
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
  copied.value = profile.profile_key
  setTimeout(() => { copied.value = '' }, 2000)
}

function openDetail(profile: ProjectProfile) {
  window.location.hash = `profiles/${profile.profile_key}`
}

async function confirmDiscardChanges() {
  return confirm({
    title: '放弃未保存修改',
    description: '当前能力平面配置有未保存修改，确定离开吗？',
    confirmText: '放弃并返回',
  })
}

async function requestListNavigation() {
  if (detailRef.value?.hasUnsavedChanges && !await confirmDiscardChanges()) return
  allowRouteLeave.value = true
  window.location.hash = 'profiles'
}

async function handleBrowserBack(previousKey: string) {
  window.location.hash = `profiles/${previousKey}`
  if (!detailRef.value?.hasUnsavedChanges) {
    allowRouteLeave.value = true
    window.location.hash = 'profiles'
    return
  }
  if (!await confirmDiscardChanges()) return
  allowRouteLeave.value = true
  window.location.hash = 'profiles'
}

watch(() => props.routeKey, (nextKey, previousKey) => {
  if (previousKey && !nextKey && !allowRouteLeave.value) {
    void handleBrowserBack(previousKey)
    return
  }
  allowRouteLeave.value = false
}, { flush: 'sync' })

async function handleDetailSaved() {
  profiles.value = await api.listProfiles()
  allowRouteLeave.value = true
  window.location.hash = 'profiles'
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <ProfileDetailView
    v-else-if="props.routeKey"
    ref="detailRef"
    :profile-key="props.routeKey"
    :profile="detailProfile"
    @back="requestListNavigation"
    @cancel="requestListNavigation"
    @saved="handleDetailSaved"
  />
  <div v-else class="space-y-5">
    <!-- 页头操作：添加能力平面进 #ph-actions（仅列表态） -->
    <Teleport v-if="!props.routeKey" to="#ph-actions" defer>
      <Button size="lg" class="shadow-btn" @click="showAdd = true">
        <Plus :size="14" />
        添加能力平面
      </Button>
    </Teleport>

    <!-- 页头筛选：搜索 + 状态分段进 #ph-filters（仅列表态） -->
    <Teleport v-if="!props.routeKey" to="#ph-filters" defer>
      <div class="relative w-full max-w-[360px]">
        <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-placeholder" />
        <Input v-model="search" placeholder="搜索能力平面标识或名称..." class="h-9 pl-8" />
      </div>
      <SegmentedTabs v-model="statusFilter" :tabs="filterTabs" @update:model-value="page = 1" />
    </Teleport>

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
            <tr v-for="profile in pagedProfiles" :key="profile.profile_key" class="border-b border-border/60 transition-colors hover:bg-muted/50">
              <td class="min-w-0 px-4 py-3">
                <span class="block break-all text-[13px] font-medium text-foreground">{{ profile.profile_key }}</span>
                <div class="mt-0.5 break-all text-xs text-muted-foreground">{{ profile.name }}</div>
              </td>
              <td class="px-4 py-3">
                <StatusBadge v-if="profile.status === 'active'" status="enabled" label="启用" />
                <StatusBadge v-else status="disabled" label="停用" />
              </td>
              <td class="px-4 py-3 tabular-nums font-semibold">{{ profile.allow_count || 0 }}</td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <Button variant="ghost" size="sm" @click="openDetail(profile)" class="h-8 gap-1.5 text-xs">
                    <Settings :size="14" />
                    配置
                  </Button>
                  <Button variant="ghost" size="sm" @click="copyCommand(profile)" class="h-8 text-xs">
                    {{ copied === profile.profile_key ? '已复制' : '复制命令' }}
                  </Button>
                  <Button variant="ghost" size="sm" @click="toggleStatus(profile)" class="h-8 text-xs">
                    {{ profile.status === 'active' ? '停用' : '启用' }}
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>

    <PaginationBar
      v-model:page="page"
      v-model:page-size="pageSize"
      :total="filtered.length"
      :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
    />

    <!-- Add Dialog -->
    <Dialog :open="showAdd" @update:open="showAdd = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>添加能力平面</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createProfile" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ formError }}</div>
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
  </div>
</template>
