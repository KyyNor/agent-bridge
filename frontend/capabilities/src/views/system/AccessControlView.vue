<script setup lang="ts">
import { Plus, RefreshCw, Trash2, Users } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { AccessActorContext, AccessGroup, UserGroupMembership } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { alert, confirm } from '../../composables/useConfirm'

const context = ref<AccessActorContext | null>(null)
const groups = ref<AccessGroup[]>([])
const memberships = ref<UserGroupMembership[]>([])
const loading = ref(true)
const error = ref('')

const showGroupDialog = ref(false)
const groupSaving = ref(false)
const groupForm = ref({ group_key: '', name: '', description: '' })
const editingGroup = ref(false)

const memberSaving = ref(false)
const memberForm = ref({ user_id: '', group_key: '' })
const memberSearch = ref('')

const filteredMemberships = computed(() => {
  const query = memberSearch.value.trim().toLowerCase()
  if (!query) return memberships.value
  return memberships.value.filter(item =>
    item.user_id.toLowerCase().includes(query)
    || item.group_key.toLowerCase().includes(query)
    || item.group_name.toLowerCase().includes(query),
  )
})

const memberCountByGroup = computed(() => {
  const counts: Record<string, number> = {}
  for (const membership of memberships.value) {
    counts[membership.group_key] = (counts[membership.group_key] || 0) + 1
  }
  return counts
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    context.value = await api.getAccessContext()
    if (!context.value.is_maintenance_admin) {
      groups.value = []
      memberships.value = []
      return
    }
    ;[groups.value, memberships.value] = await Promise.all([
      api.listAccessGroups(),
      api.listGroupMemberships(),
    ])
    if (!memberForm.value.group_key && groups.value.length) {
      memberForm.value.group_key = groups.value[0].group_key
    }
  } catch (e: any) {
    error.value = e.message || '加载小组权限数据失败'
  } finally {
    loading.value = false
  }
}

function openCreateGroup() {
  editingGroup.value = false
  groupForm.value = { group_key: '', name: '', description: '' }
  showGroupDialog.value = true
}

function openEditGroup(group: AccessGroup) {
  editingGroup.value = true
  groupForm.value = {
    group_key: group.group_key,
    name: group.name,
    description: group.description || '',
  }
  showGroupDialog.value = true
}

async function saveGroup() {
  if (!groupForm.value.group_key.trim() || !groupForm.value.name.trim()) return
  groupSaving.value = true
  try {
    await api.upsertAccessGroup({
      group_key: groupForm.value.group_key.trim(),
      name: groupForm.value.name.trim(),
      description: groupForm.value.description.trim(),
    })
    showGroupDialog.value = false
    await load()
  } catch (e: any) {
    await alert({ title: '保存小组失败', description: e.message || '保存失败', destructive: true })
  } finally {
    groupSaving.value = false
  }
}

async function saveMembership() {
  if (!memberForm.value.user_id.trim() || !memberForm.value.group_key) return
  memberSaving.value = true
  try {
    await api.setUserGroup({
      user_id: memberForm.value.user_id.trim(),
      group_key: memberForm.value.group_key,
    })
    memberForm.value.user_id = ''
    await load()
  } catch (e: any) {
    await alert({ title: '分配小组失败', description: e.message || '保存失败', destructive: true })
  } finally {
    memberSaving.value = false
  }
}

async function removeMembership(membership: UserGroupMembership) {
  if (!await confirm({
    title: '移除用户小组',
    description: `确定移除用户「${membership.user_id}」与小组「${membership.group_name}」的关系？移除后该用户不能新建组内资源。`,
    destructive: true,
    confirmText: '移除',
  })) return
  try {
    await api.deleteUserGroup(membership.user_id)
    await load()
  } catch (e: any) {
    await alert({ title: '移除失败', description: e.message || '移除失败', destructive: true })
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <Teleport to="#ph-actions" defer>
      <Button variant="outline" size="lg" :disabled="loading" @click="load">
        <RefreshCw :size="14" />刷新
      </Button>
      <Button v-if="context?.is_maintenance_admin" size="lg" class="shadow-btn" @click="openCreateGroup">
        <Plus :size="14" />新建小组
      </Button>
    </Teleport>

    <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
    <div v-else-if="error" class="rounded-lg border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-soft-fg">{{ error }}</div>
    <template v-else>
      <Card>
        <CardContent class="flex flex-wrap items-center justify-between gap-4 p-5">
          <div class="flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary"><Users :size="20" /></div>
            <div>
              <div class="text-sm font-semibold">{{ context?.user_id }}</div>
              <div class="mt-0.5 text-xs text-muted-foreground">
                当前小组：{{ context?.group_name || '尚未分配' }}
                <span v-if="context?.group_key" class="font-mono">（{{ context.group_key }}）</span>
              </div>
            </div>
          </div>
          <Badge v-if="context?.is_maintenance_admin" variant="secondary">映射维护员</Badge>
          <Badge v-else variant="outline">普通成员</Badge>
        </CardContent>
      </Card>

      <div v-if="!context?.is_maintenance_admin" class="rounded-lg border border-border bg-card p-6 text-sm text-muted-foreground">
        你可以查看自己的小组归属。用户与小组映射由系统维护员统一管理。
      </div>

      <template v-else>
        <div class="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
          <Card>
            <CardContent class="p-0">
              <div class="flex items-center justify-between border-b border-border px-5 py-4">
                <div>
                  <h2 class="text-sm font-semibold">数据小组</h2>
                  <p class="mt-0.5 text-xs text-muted-foreground">资源归属使用稳定的小组标识。</p>
                </div>
                <Badge variant="outline">{{ groups.length }} 个</Badge>
              </div>
              <div v-if="groups.length === 0" class="p-8 text-center text-sm text-muted-foreground">暂无小组</div>
              <button
                v-for="group in groups"
                :key="group.group_key"
                type="button"
                class="flex w-full items-start justify-between gap-3 border-b border-border/60 px-5 py-4 text-left transition-colors hover:bg-muted/30"
                @click="openEditGroup(group)"
              >
                <div class="min-w-0">
                  <div class="text-sm font-medium">{{ group.name }}</div>
                  <div class="mt-1 font-mono text-xs text-muted-foreground">{{ group.group_key }}</div>
                  <div v-if="group.description" class="mt-2 text-xs text-muted-foreground">{{ group.description }}</div>
                </div>
                <Badge variant="secondary" class="shrink-0">{{ memberCountByGroup[group.group_key] || 0 }} 人</Badge>
              </button>
            </CardContent>
          </Card>

          <Card>
            <CardContent class="p-0">
              <div class="border-b border-border p-5">
                <h2 class="text-sm font-semibold">用户归属</h2>
                <p class="mt-0.5 text-xs text-muted-foreground">每个用户只归属一个小组；重复保存会直接更新映射。</p>
                <form class="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_220px_auto]" @submit.prevent="saveMembership">
                  <Input v-model="memberForm.user_id" placeholder="SSO 用户 ID / Linux 用户名" required />
                  <Select v-model="memberForm.group_key">
                    <SelectTrigger><SelectValue placeholder="选择小组" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="group in groups" :key="group.group_key" :value="group.group_key">{{ group.name }}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button type="submit" :disabled="memberSaving || !groups.length">{{ memberSaving ? '保存中...' : '分配小组' }}</Button>
                </form>
                <Input v-model="memberSearch" class="mt-3" placeholder="搜索用户或小组..." />
              </div>
              <div v-if="filteredMemberships.length === 0" class="p-8 text-center text-sm text-muted-foreground">暂无匹配的用户映射</div>
              <div v-else class="max-h-[560px] overflow-auto">
                <table class="w-full">
                  <thead><tr class="border-b border-border">
                    <th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">用户 ID</th>
                    <th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">小组</th>
                    <th class="px-5 py-3"></th>
                  </tr></thead>
                  <tbody><tr v-for="membership in filteredMemberships" :key="membership.user_id" class="border-b border-border/60">
                    <td class="px-5 py-3 font-mono text-xs">{{ membership.user_id }}</td>
                    <td class="px-5 py-3">
                      <div class="text-sm">{{ membership.group_name }}</div>
                      <div class="font-mono text-[10px] text-muted-foreground">{{ membership.group_key }}</div>
                    </td>
                    <td class="px-5 py-3 text-right">
                      <Button variant="ghost" size="sm" class="text-destructive" @click="removeMembership(membership)"><Trash2 :size="14" /></Button>
                    </td>
                  </tr></tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      </template>
    </template>

    <Dialog :open="showGroupDialog" @update:open="showGroupDialog = $event">
      <DialogContent class="sm:max-w-[480px]">
        <DialogHeader><DialogTitle>{{ editingGroup ? '编辑小组' : '新建小组' }}</DialogTitle></DialogHeader>
        <form class="space-y-4" @submit.prevent="saveGroup">
          <div class="space-y-2">
            <label class="text-sm font-medium">小组标识</label>
            <Input v-model="groupForm.group_key" placeholder="chengdu-rd" :disabled="editingGroup" required />
            <p class="text-xs text-muted-foreground">仅支持小写字母、数字、点、下划线和短横线；创建后不修改。</p>
          </div>
          <div class="space-y-2"><label class="text-sm font-medium">小组名称</label><Input v-model="groupForm.name" required /></div>
          <div class="space-y-2"><label class="text-sm font-medium">说明</label><Input v-model="groupForm.description" /></div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button :disabled="groupSaving" @click="saveGroup">{{ groupSaving ? '保存中...' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
