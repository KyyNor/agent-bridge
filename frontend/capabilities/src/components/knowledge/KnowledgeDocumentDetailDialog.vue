<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import type { DocumentDetail } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Button } from '../ui/button'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'

const props = defineProps<{ open: boolean; slug: string }>()
const emit = defineEmits<{ 'update:open': [open: boolean] }>()

const loading = ref(false)
const detail = ref<DocumentDetail | null>(null)
const error = ref('')

async function load() {
  if (!props.open || !props.slug) return
  detail.value = null
  error.value = ''
  loading.value = true
  try {
    detail.value = await api.getDoc(props.slug)
  } catch (cause: any) {
    error.value = cause.message || '加载文档详情失败'
  } finally {
    loading.value = false
  }
}

watch(() => [props.open, props.slug] as const, () => { void load() })
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="sm:max-w-[640px]">
      <DialogHeader><DialogTitle>文档详情</DialogTitle></DialogHeader>
      <div v-if="loading" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      <div v-else-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ error }}</div>
      <div v-else-if="detail" class="space-y-4 text-sm">
        <div class="grid gap-2 sm:grid-cols-2">
          <div><span class="text-xs text-muted-foreground">标题</span><div class="font-medium">{{ detail.title }}</div></div>
          <div><span class="text-xs text-muted-foreground">标识</span><div class="font-mono text-xs">{{ detail.slug }}</div></div>
          <div><span class="text-xs text-muted-foreground">状态</span><div>{{ detail.status }}</div></div>
          <div><span class="text-xs text-muted-foreground">知识库</span><div>{{ detail.kb_slugs.join('、') || '—' }}</div></div>
        </div>
        <div>
          <div class="mb-2 text-xs font-medium text-muted-foreground">版本</div>
          <div class="max-h-48 overflow-y-auto rounded-md border border-border">
            <div v-for="version in detail.versions" :key="version.id" class="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 last:border-b-0">
              <span>v{{ version.version_no }} · {{ version.original_filename }}</span>
              <span class="shrink-0 text-xs text-muted-foreground">{{ formatLocalDatetime(version.created_at) }}</span>
            </div>
            <div v-if="detail.versions.length === 0" class="px-3 py-4 text-center text-xs text-muted-foreground">暂无版本</div>
          </div>
        </div>
      </div>
      <DialogFooter><DialogClose as-child><Button variant="outline">关闭</Button></DialogClose></DialogFooter>
    </DialogContent>
  </Dialog>
</template>
