<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Download, Folder, File, PanelLeftClose, PanelLeftOpen, RefreshCw, Upload } from '@lucide/vue'
import { api } from '../../api/client'
import type { TerminalDirectoryListing, WorkspaceAgent } from '../../api/types'
import { childPath, parentPath, fileSizeLabel, MAX_TERMINAL_FILE_BYTES } from '../../lib/workspaceFiles'
import { Button } from '../ui/button'

const props = defineProps<{ agent: WorkspaceAgent }>()
const collapsed = ref(false)
const listing = ref<TerminalDirectoryListing | null>(null)
const path = ref('')
const error = ref('')
const busy = ref(false)
const uploadInput = ref<HTMLInputElement | null>(null)
async function load(directory = path.value) {
  busy.value = true
  error.value = ''
  try {
    listing.value = await api.listTerminalFiles(props.agent, directory)
    path.value = listing.value.path
  } catch (e: any) {
    error.value = e.message || '无法读取文件'
  } finally { busy.value = false }
}
async function download(name: string) {
  busy.value = true
  error.value = ''
  try {
    const blob = await api.downloadTerminalFile(props.agent, childPath(path.value, name))
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = name
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e: any) { error.value = e.message || '下载失败' }
  finally { busy.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  busy.value = true
  error.value = ''
  let failure = ''
  try {
    for (const file of files) {
      if (file.size > MAX_TERMINAL_FILE_BYTES) throw new Error(`${file.name} 超过 32 MiB`)
      await api.uploadTerminalFile(props.agent, path.value, file)
    }
  } catch (e: any) { failure = e.message || '上传失败' }
  finally {
    input.value = ''
    await load()
    if (failure) error.value = failure
    busy.value = false
  }
}
onMounted(() => load(''))
</script>

<template>
  <aside class="flex min-h-0 shrink-0 flex-col border-r border-border bg-card" :class="collapsed ? 'w-10' : 'w-72 max-w-[45vw]'" aria-label="工作目录文件">
    <div class="flex min-h-10 shrink-0 items-center justify-between border-b border-border px-1">
      <span v-if="!collapsed" class="pl-2 text-xs font-medium">工作目录文件</span>
      <Button variant="ghost" size="sm" class="px-2" :aria-expanded="!collapsed" :aria-label="collapsed ? '展开文件侧栏' : '折叠文件侧栏'" @click="collapsed = !collapsed">
        <PanelLeftOpen v-if="collapsed" class="size-4" /><PanelLeftClose v-else class="size-4" />
      </Button>
    </div>
    <template v-if="!collapsed">
      <div class="shrink-0 space-y-2 border-b border-border p-3">
        <div class="break-all text-xs text-muted-foreground" :title="listing?.root">{{ listing?.root }}</div>
        <div class="truncate text-xs" :title="path || '/'">{{ path || '/' }}</div>
        <div class="flex flex-wrap items-center gap-1">
          <Button variant="outline" size="sm" :disabled="busy || !path" @click="load(parentPath(path))">上一级</Button>
          <Button variant="outline" size="sm" :disabled="busy" aria-label="刷新文件" @click="load()"><RefreshCw class="size-3.5" /></Button>
          <Button variant="outline" size="sm" :disabled="busy" @click="uploadInput?.click()"><Upload class="mr-1 size-3.5" />上传</Button>
          <input ref="uploadInput" type="file" multiple class="hidden" aria-label="上传到工作目录" :disabled="busy" @change="upload" />
        </div>
        <p class="text-xs text-muted-foreground">单文件最多 32 MiB，同名文件不覆盖。</p>
        <p v-if="error" role="alert" class="break-words text-xs text-destructive">{{ error }}</p>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto p-1">
        <p v-if="busy" class="p-3 text-xs text-muted-foreground">正在处理…</p>
        <div v-for="entry in listing?.entries" :key="entry.name" class="flex items-center gap-1 rounded-md px-2 py-1 hover:bg-muted">
          <Folder v-if="entry.kind === 'directory'" class="size-4 shrink-0 text-primary" />
          <File v-else class="size-4 shrink-0 text-muted-foreground" />
          <button v-if="entry.kind === 'directory'" class="min-w-0 flex-1 truncate text-left text-xs" :title="entry.name" :disabled="busy" @click="load(childPath(path, entry.name))">{{ entry.name }}</button>
          <span v-else class="min-w-0 flex-1 truncate text-xs" :title="entry.name">{{ entry.name }}</span>
          <Button v-if="entry.kind === 'file'" variant="ghost" size="sm" class="px-1" :aria-label="`下载 ${entry.name}`" :title="fileSizeLabel(entry.size)" :disabled="busy || entry.size > MAX_TERMINAL_FILE_BYTES" @click="download(entry.name)"><Download class="size-3.5" /></Button>
          <span v-if="entry.kind === 'symlink'" class="text-xs text-muted-foreground">软链</span>
        </div>
        <p v-if="listing && !listing.entries.length && !busy" class="p-3 text-xs text-muted-foreground">此目录暂无文件。</p>
        <p v-if="listing?.truncated" class="p-3 text-xs text-muted-foreground">仅展示前 2000 项。</p>
      </div>
    </template>
  </aside>
</template>
