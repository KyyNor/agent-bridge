<script setup lang="ts">
import { Plus, RefreshCw, Users } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import type { AccessActorContext, AccessGroup, AccessUser } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select'
import { alert } from '../../composables/useConfirm'
import AccessGroupManagementView from './AccessGroupManagementView.vue'

const props = defineProps<{ routeKey: string }>()
const router = useRouter()
const context = ref<AccessActorContext | null>(null)
const users = ref<AccessUser[]>([])
const groups = ref<AccessGroup[]>([])
const loading = ref(true)
const error = ref('')
const userSearch = ref('')
const showUserDialog = ref(false)
const userSaving = ref(false)
const userId = ref('')
const editingUserId = ref<string | null>(null)
const editingGroupKey = ref('')
const membershipSaving = ref(false)

const UNASSIGNED_GROUP = '__unassigned__'
const showingGroups = computed(() => props.routeKey === 'groups')
const filteredUsers = computed(() => {
  const query = userSearch.value.trim().toLowerCase()
  if (!query) return users.value
  return users.value.filter(user =>
    user.user_id.toLowerCase().includes(query)
    || user.group_name?.toLowerCase().includes(query)
    || user.group_key?.toLowerCase().includes(query),
  )
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    context.value = await api.getAccessContext()
    if (!context.value.is_maintenance_admin) {
      users.value = []
      groups.value = []
      return
    }
    ;[users.value, groups.value] = await Promise.all([
      api.listAccessUsers(),
      api.listAccessGroups(),
    ])
  } catch (e: any) {
    error.value = e.message || '加载用户与小组数据失败'
  } finally {
    loading.value = false
  }
}

function openCreateUser() {
  userId.value = ''
  showUserDialog.value = true
}

async function saveUser() {
  if (!userId.value.trim()) return
  userSaving.value = true
  try {
    await api.createAccessUser(userId.value.trim())
    showUserDialog.value = false
    await load()
  } catch (e: any) {
    await alert({ title: '添加用户失败', description: e.message || '保存失败', destructive: true })
  } finally {
    userSaving.value = false
  }
}

function beginMembershipEdit(user: AccessUser) {
  editingUserId.value = user.user_id
  editingGroupKey.value = user.group_key || UNASSIGNED_GROUP
}

function cancelMembershipEdit() {
  editingUserId.value = null
  editingGroupKey.value = ''
}

async function saveMembership(user: AccessUser) {
  if (!editingGroupKey.value) return
  membershipSaving.value = true
  try {
    await api.setUserGroup({
      user_id: user.user_id,
      group_key: editingGroupKey.value === UNASSIGNED_GROUP ? null : editingGroupKey.value,
    })
    cancelMembershipEdit()
    await load()
  } catch (e: any) {
    await alert({ title: '保存用户归属失败', description: e.message || '保存失败', destructive: true })
  } finally {
    membershipSaving.value = false
  }
}

function openGroupManagement() {
  void router.push('/access-control/groups')
}

function returnToUsers() {
  void router.push('/access-control')
}

onMounted(load)
watch(() => props.routeKey, routeKey => {
  if (!routeKey) void load()
})
</script>

<template>
  <AccessGroupManagementView v-if="showingGroups" @back="returnToUsers" />

  <div v-else class="space-y-5">
    <Teleport to="#ph-actions" defer>
      <Button variant="outline" size="lg" :disabled="loading" @click="load"><RefreshCw :size="14" />刷新</Button>
      <Button v-if="context?.is_maintenance_admin" variant="outline" size="lg" @click="openGroupManagement">维护小组</Button>
      <Button v-if="context?.is_maintenance_admin" size="lg" class="shadow-btn" @click="openCreateUser"><Plus :size="14" />添加用户</Button>
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
        你可以查看自己的小组归属。用户、小组和归属关系由系统维护员统一管理。
      </div>

      <Card v-else>
        <CardContent class="p-0">
          <div class="flex flex-wrap items-end justify-between gap-4 border-b border-border p-5">
            <div>
              <h2 class="text-sm font-semibold">用户与小组归属</h2>
              <p class="mt-0.5 text-xs text-muted-foreground">用户独立保留在目录中；换组或取消归属只更新当前小组，不会删除用户。</p>
            </div>
            <Input v-model="userSearch" class="w-full sm:w-[280px]" placeholder="搜索用户或小组..." />
          </div>
          <div v-if="filteredUsers.length === 0" class="p-8 text-center text-sm text-muted-foreground">暂无用户</div>
          <div v-else class="overflow-auto">
            <table class="w-full min-w-[720px]">
              <thead><tr class="border-b border-border">
                <th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">用户 ID</th>
                <th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">当前小组</th>
                <th class="px-5 py-3 text-right text-xs font-medium text-muted-foreground">操作</th>
              </tr></thead>
              <tbody>
                <tr v-for="user in filteredUsers" :key="user.user_id" class="border-b border-border/60 last:border-0">
                  <td class="px-5 py-3 font-mono text-xs">{{ user.user_id }}</td>
                  <td class="px-5 py-3">
                    <template v-if="editingUserId === user.user_id">
                      <Select v-model="editingGroupKey">
                        <SelectTrigger class="w-[240px]"><SelectValue placeholder="选择小组" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem :value="UNASSIGNED_GROUP">暂不分配</SelectItem>
                          <SelectItem v-for="group in groups" :key="group.group_key" :value="group.group_key">{{ group.name }}</SelectItem>
                        </SelectContent>
                      </Select>
                    </template>
                    <template v-else>
                      <div v-if="user.group_name" class="text-sm">{{ user.group_name }}</div>
                      <div v-if="user.group_key" class="font-mono text-[10px] text-muted-foreground">{{ user.group_key }}</div>
                      <span v-else class="text-sm text-muted-foreground">暂未分配</span>
                    </template>
                  </td>
                  <td class="px-5 py-3 text-right">
                    <div v-if="editingUserId === user.user_id" class="flex justify-end gap-2">
                      <Button variant="outline" size="sm" :disabled="membershipSaving" @click="cancelMembershipEdit">取消</Button>
                      <Button size="sm" :disabled="membershipSaving || !editingGroupKey" @click="saveMembership(user)">{{ membershipSaving ? '保存中...' : '确认保存' }}</Button>
                    </div>
                    <Button v-else variant="outline" size="sm" :disabled="!groups.length && !user.group_key" @click="beginMembershipEdit(user)">
                      {{ user.group_key ? '修改小组' : '分配小组' }}
                    </Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </template>

    <Dialog :open="showUserDialog" @update:open="showUserDialog = $event">
      <DialogContent class="sm:max-w-[440px]">
        <DialogHeader><DialogTitle>添加用户</DialogTitle></DialogHeader>
        <form class="space-y-4" @submit.prevent="saveUser">
          <div class="space-y-2">
            <label class="text-sm font-medium">用户 ID</label>
            <Input v-model="userId" autofocus placeholder="SSO 用户 ID / Linux 用户名" required />
            <p class="text-xs text-muted-foreground">添加后用户先保留在目录中，可在列表里分配或修改小组。</p>
          </div>
        </form>
        <DialogFooter>
          <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
          <Button :disabled="userSaving || !userId.trim()" @click="saveUser">{{ userSaving ? '添加中...' : '添加用户' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
