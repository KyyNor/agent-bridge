<script setup lang="ts">
import { Shield, ShieldCheck } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { AdminAccessStatus } from '../api/types'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Input } from './ui/input'

const status = ref<AdminAccessStatus | null>(null)
const showDialog = ref(false)
const password = ref('')
const passwordConfirm = ref('')
const submitting = ref(false)
const error = ref('')

const firstSetup = computed(() => status.value?.configured === false)

async function loadStatus() {
  try {
    status.value = await api.getAdminAccessStatus()
  } catch {
    status.value = null
  }
}

function openSwitchDialog() {
  password.value = ''
  passwordConfirm.value = ''
  error.value = ''
  showDialog.value = true
}

async function submit() {
  error.value = ''
  if (password.value.length < 8) {
    error.value = '管理员密码至少需要 8 个字符'
    return
  }
  if (firstSetup.value && password.value !== passwordConfirm.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    await api.createAdminSession(password.value)
    window.location.reload()
  } catch (e: any) {
    error.value = e.message || '切换管理员失败'
  } finally {
    submitting.value = false
  }
}

async function leaveAdmin() {
  try {
    await api.deleteAdminSession()
  } finally {
    window.location.reload()
  }
}

onMounted(loadStatus)
</script>

<template>
  <div class="space-y-2">
    <div v-if="status?.active" class="flex items-center gap-2 rounded-md bg-primary/8 px-2.5 py-2 text-[11px] text-primary">
      <ShieldCheck :size="14" />
      <span class="min-w-0 flex-1 truncate">管理员模式</span>
      <button type="button" class="text-muted-foreground hover:text-foreground" @click="leaveAdmin">退出</button>
    </div>
    <Button v-else variant="outline" size="sm" class="h-8 w-full justify-start gap-2 text-xs" @click="openSwitchDialog">
      <Shield :size="14" />切换管理员
    </Button>

    <Dialog :open="showDialog" @update:open="showDialog = $event">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ firstSetup ? '首次设置管理员密码' : '切换为管理员' }}</DialogTitle>
        </DialogHeader>
        <form class="space-y-4" @submit.prevent="submit">
          <p class="text-sm text-muted-foreground">
            {{ firstSetup
              ? '系统尚未设置管理员密码。本次设置成功后，当前浏览器会直接进入管理员模式。'
              : '输入管理员密码后，当前浏览器将临时获得全部数据的查看和维护权限。' }}
          </p>
          <label class="block space-y-1.5 text-sm">
            <span>管理员密码</span>
            <Input v-model="password" type="password" autocomplete="current-password" autofocus placeholder="至少 8 个字符" />
          </label>
          <label v-if="firstSetup" class="block space-y-1.5 text-sm">
            <span>确认密码</span>
            <Input v-model="passwordConfirm" type="password" autocomplete="new-password" placeholder="再次输入管理员密码" />
          </label>
          <div v-if="error" class="rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ error }}</div>
          <DialogFooter>
            <Button type="button" variant="outline" @click="showDialog = false">取消</Button>
            <Button type="submit" :disabled="submitting">{{ submitting ? '切换中...' : firstSetup ? '设置并进入' : '进入管理员模式' }}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  </div>
</template>
