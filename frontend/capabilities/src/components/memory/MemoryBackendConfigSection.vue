<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { MemoryBackendConfig } from '../../api/types'
import { Card, CardContent } from '../ui/card'
import { Button } from '../ui/button'
import { Input } from '../ui/input'

const config = ref<MemoryBackendConfig | null>(null)
const backend = ref('claude-mem')
const url = ref('')
const apiToken = ref('')
const clearToken = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')

async function load() {
  try {
    config.value = await api.getMemoryBackendConfig()
    backend.value = config.value.backend
    url.value = config.value.url
    error.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : '读取记忆后端配置失败' }
}

async function save() {
  busy.value = true
  error.value = message.value = ''
  try {
    config.value = await api.saveMemoryBackendConfig({ backend: backend.value, url: url.value,
      api_token: apiToken.value || null, clear_api_token: clearToken.value, expected_edit_token: config.value?.edit_token })
    apiToken.value = ''
    clearToken.value = false
    message.value = '已保存。工作台与终端下次启动生效。'
  } catch (e) { error.value = e instanceof Error ? e.message : '保存失败' }
  finally { busy.value = false }
}

async function test() {
  busy.value = true
  error.value = message.value = ''
  try { await api.testMemoryBackend(); message.value = '已保存的 Hindsight 配置连接成功' }
  catch (e) { error.value = e instanceof Error ? e.message : '连接失败' }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <Card><CardContent class="space-y-4 p-5">
    <div class="flex items-center justify-between gap-4">
      <div><div class="text-sm font-medium">记忆后端</div>
        <p class="mt-1 text-xs text-muted-foreground">沿用记忆区块和 Profile 绑定。Hindsight 需独立部署，每个区块使用固定 bank。</p></div>
      <Button variant="outline" size="sm" :disabled="busy" @click="load">刷新</Button>
    </div>
    <label class="flex items-center gap-4 text-sm">后端
      <select v-model="backend" class="rounded-md border border-input bg-background px-3 py-2">
        <option value="claude-mem">claude-mem</option><option value="hindsight">Hindsight</option>
      </select>
    </label>
    <template v-if="backend === 'hindsight'">
      <label class="block space-y-2 text-sm"><span>Hindsight URL</span>
        <Input v-model="url" placeholder="http://hindsight:8888" /></label>
      <label class="block space-y-2 text-sm"><span>上游 API Token（{{ config?.api_token_set ? '已配置' : '未配置' }}）</span>
        <Input v-model="apiToken" type="password" autocomplete="new-password" placeholder="留空保持不变" :disabled="clearToken" /></label>
      <label class="flex items-center gap-2 text-xs text-muted-foreground"><input v-model="clearToken" type="checkbox">清空上游 API Token</label>
      <p class="text-xs text-muted-foreground">Pi 与 DSH 的 Web / 终端使用官方原生插件 0.8.7。自动保存会话，默认轻量召回；不自动扫描代码或 Git 历史。</p>
    </template>
    <p class="text-xs text-muted-foreground">运行中的实例保持启动时配置，重新启动后生效。旧 claude-mem 数据保留，切换后端不会自动迁移。</p>
    <div class="flex flex-wrap items-center gap-3">
      <Button size="sm" :disabled="busy || !config" @click="save">保存配置</Button>
      <Button v-if="backend === 'hindsight'" size="sm" variant="outline" :disabled="busy || !config" @click="test">测试已保存连接</Button>
      <span v-if="error" role="alert" class="text-xs text-destructive">{{ error }}</span>
      <span v-else-if="message" role="status" class="text-xs text-success">{{ message }}</span>
    </div>
  </CardContent></Card>
</template>
