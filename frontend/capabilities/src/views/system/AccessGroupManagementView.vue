<script setup lang="ts">
import { ArrowLeft, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { AccessGroup } from '../../api/types'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { alert, confirm } from '../../composables/useConfirm'

defineEmits<{ back: [] }>()

const groups = ref<AccessGroup[]>([])
const loading = ref(true)
const error = ref('')
const showCreateDialog = ref(false)
const saving = ref(false)
const deleting = ref('')
const form = ref({ group_key: '', name: '', description: '' })

async function load() {
  loading.value = true
  error.value = ''
  try {
    groups.value = await api.listAccessGroups()
  } catch (e: any) {
    error.value = e.message || '加载小组失败'
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  form.value = { group_key: '', name: '', description: '' }
  showCreateDialog.value = true
}

async function saveGroup() {
  if (!form.value.group_key.trim() || !form.value.name.trim()) return
  saving.value = true
  try {
    await api.upsertAccessGroup({
      group_key: form.value.group_key.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim(),
    })
    showCreateDialog.value = false
    await load()
  } catch (e: any) {
    await alert({ title: '添加小组失败', description: e.message || '保存失败', destructive: true })
  } finally {
    saving.value = false
  }
}

async function deleteGroup(group: AccessGroup) {
  if (group.member_count > 0) {
    await alert({
      title: '暂不能删除小组',
      description: `小组「${group.name}」仍有 ${group.member_count} 名成员。请先在用户列表中为这些成员换组或选择“暂不分配”。`,
      destructive: true,
    })
    return
  }
  if (!await confirm({
    title: '删除小组',
    description: `确定删除小组「${group.name}」？此操作不会删除任何用户。`,
    destructive: true,
    confirmText: '删除小组',
  })) return
  deleting.value = group.group_key
  try {
    await api.deleteAccessGroup(group.group_key)
    await load()
  } catch (e: any) {
    await alert({ title: '删除小组失败', description: e.message || '删除失败', destructive: true })
  } finally {
    deleting.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="flex items-start gap-2">
        <Button variant="ghost" size="sm" class="mt-0.5 px-2" @click="$emit('back')"><ArrowLeft :size="14" class="mr-1.5" />返回</Button>
        <div><h2 class="text-lg font-semibold">维护小组</h2><p class="mt-1 text-sm text-muted-foreground">管理可分配给用户的协作小组。</p></div>
      </div>
      <div class="flex gap-2"><Button variant="outline" :disabled="loading" @click="load"><RefreshCw :size="14" />刷新</Button><Button class="shadow-btn" @click="openCreateDialog"><Plus :size="14" />添加小组</Button></div>
    </div>

    <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
    <div v-else-if="error" class="rounded-lg border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-soft-fg">{{ error }}</div>
    <Card v-else>
      <CardContent class="p-0">
        <div v-if="groups.length === 0" class="p-10 text-center text-sm text-muted-foreground">暂无小组，先添加一个小组后即可为用户分配。</div>
        <div v-else class="overflow-auto">
          <table class="w-full min-w-[720px]">
            <thead><tr class="border-b border-border"><th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">展示名称</th><th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">小组 ID</th><th class="px-5 py-3 text-left text-xs font-medium text-muted-foreground">成员</th><th class="px-5 py-3 text-right text-xs font-medium text-muted-foreground">操作</th></tr></thead>
            <tbody><tr v-for="group in groups" :key="group.group_key" class="border-b border-border/60 last:border-0"><td class="px-5 py-4"><div class="text-sm font-medium">{{ group.name }}</div><div v-if="group.description" class="mt-1 text-xs text-muted-foreground">{{ group.description }}</div></td><td class="px-5 py-4 font-mono text-xs">{{ group.group_key }}</td><td class="px-5 py-4 text-sm">{{ group.member_count }} 人</td><td class="px-5 py-4 text-right"><Button variant="ghost" size="sm" class="text-destructive" :disabled="deleting === group.group_key" @click="deleteGroup(group)"><Trash2 :size="14" /><span class="ml-1">删除</span></Button></td></tr></tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <Dialog :open="showCreateDialog" @update:open="showCreateDialog = $event">
      <DialogContent class="sm:max-w-[480px]">
        <DialogHeader><DialogTitle>添加小组</DialogTitle></DialogHeader>
        <form class="space-y-4" @submit.prevent="saveGroup">
          <div class="space-y-2"><label class="text-sm font-medium">展示名称</label><Input v-model="form.name" placeholder="例如：E2E 研发 A 组" required /></div>
          <div class="space-y-2"><label class="text-sm font-medium">小组 ID</label><Input v-model="form.group_key" placeholder="例如：e2e-rd-a" required /><p class="text-xs text-muted-foreground">仅支持小写英文、数字、点、下划线和短横线；创建后不修改。</p></div>
          <div class="space-y-2"><label class="text-sm font-medium">说明（可选）</label><Input v-model="form.description" placeholder="说明这个小组的协作范围" /></div>
        </form>
        <DialogFooter><DialogClose as-child><Button variant="outline">取消</Button></DialogClose><Button :disabled="saving || !form.group_key.trim() || !form.name.trim()" @click="saveGroup">{{ saving ? '添加中...' : '添加小组' }}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
