<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { TerminalDirectoryListing, WorkspaceAgent } from '../../api/types'
import { childPath, parentPath } from '../../lib/workspaceFiles'
import { Button } from '../ui/button'
import { Input } from '../ui/input'

const props = defineProps<{ agent: WorkspaceAgent; initialDirectory?: string }>()
const emit = defineEmits<{ start: [directory: string]; cancel: [] }>()
const listing = ref<TerminalDirectoryListing | null>(null)
const path = ref(props.initialDirectory || '')
const busy = ref(false)
const error = ref('')
let request = 0
async function browse(directory?: string) {
  const token = ++request
  busy.value = true
  error.value = ''
  try {
    const result = await api.browseTerminalDirectories(props.agent, directory)
    if (token !== request) return
    listing.value = result
    path.value = result.root
  } catch (e: any) {
    if (token === request) error.value = e.message || '无法读取目录'
  } finally {
    if (token === request) busy.value = false
  }
}
onMounted(() => browse(props.initialDirectory || undefined))
</script>

<template>
  <div class="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6">
    <section class="flex max-h-full w-full max-w-2xl flex-col rounded-lg border border-border bg-card p-5">
      <h2 class="text-base font-medium">选择终端启动目录</h2>
      <p class="mt-1 text-sm text-muted-foreground">默认使用所属小组 Linux 用户的 home。选择目录后再启动，文件侧栏也以此目录为根。</p>
      <form class="mt-4 flex gap-2" @submit.prevent="browse(path)">
        <Input v-model="path" aria-label="启动目录路径" placeholder="/home/zhangsan" :disabled="busy" />
        <Button variant="outline" type="submit" :disabled="busy || !path">打开目录</Button>
      </form>
      <div class="mt-3 flex items-center justify-between gap-2">
        <span class="truncate text-xs text-muted-foreground" :title="listing?.root">{{ listing?.root || '正在读取默认目录…' }}</span>
        <Button variant="ghost" size="sm" :disabled="busy || !listing || listing.root === '/'" @click="browse(parentPath(listing!.root))">上一级</Button>
      </div>
      <p v-if="error" role="alert" class="mt-3 text-sm text-destructive">{{ error }}</p>
      <div class="my-3 min-h-32 overflow-y-auto rounded-md border border-border">
        <div v-if="busy" class="p-4 text-sm text-muted-foreground">正在读取目录…</div>
        <template v-else>
          <button v-for="entry in listing?.entries" :key="entry.name" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" @click="browse(childPath(listing!.root, entry.name))">
            <span aria-hidden="true">▸</span><span class="truncate">{{ entry.name }}</span>
          </button>
          <p v-if="listing && !listing.entries.length" class="p-4 text-sm text-muted-foreground">此目录下没有子目录，可以直接在这里启动。</p>
        </template>
      </div>
      <p v-if="listing?.truncated" class="mb-2 text-xs text-muted-foreground">子目录较多，仅展示前 2000 项；可以直接输入完整路径。</p>
      <p v-if="initialDirectory" class="mb-3 text-xs text-muted-foreground">切换到其他目录会重新启动当前个人终端；选择原目录可继续原会话。</p>
      <div class="flex justify-end gap-2">
        <Button v-if="initialDirectory" variant="outline" @click="emit('cancel')">返回原终端</Button>
        <Button :disabled="busy || !listing || !!error || path !== listing.root" @click="emit('start', listing!.root)">在此目录启动</Button>
      </div>
    </section>
  </div>
</template>
