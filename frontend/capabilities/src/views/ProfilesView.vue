<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { ProjectProfile } from '../api/types'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../components/ui/dialog'

const profiles = ref<ProjectProfile[]>([])
const loading = ref(true)

const showAdd = ref(false)
const form = ref({ profile_key: '', name: '', description: '', status: 'active' })
const saving = ref(false)
const formError = ref('')

onMounted(async () => {
  try { profiles.value = await api.listProfiles() } catch { /* empty */ }
  loading.value = false
})

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
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-5">
    <div class="flex items-center justify-between">
      <div class="text-sm text-muted-foreground">维护服务级白名单/黑名单，供 MetaMCP 和项目接入使用。</div>
      <Button @click="showAdd = true">添加 Profile</Button>
    </div>

    <Card class="border-border">
      <CardContent class="p-0">
        <div v-if="profiles.length === 0" class="px-5 py-12 text-center text-sm text-muted-foreground">
          暂无 Project Profile
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
            <tr v-for="p in profiles" :key="p.profile_key" class="border-b border-border/60 transition-colors hover:bg-secondary/30">
              <td class="px-4 py-3">
                <span class="text-[13px] font-medium text-foreground">{{ p.profile_key }}</span>
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

    <Dialog :open="showAdd" @update:open="showAdd = $event">
      <DialogContent class="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>添加 Project Profile</DialogTitle>
        </DialogHeader>
        <form @submit.prevent="createProfile" class="space-y-4">
          <div v-if="formError" class="rounded-lg bg-red-50 p-3 text-sm text-destructive">{{ formError }}</div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Profile 标识 <span class="text-destructive">*</span></label>
            <Input v-model="form.profile_key" placeholder="safe-readonly" required />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Profile 名称 <span class="text-destructive">*</span></label>
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
