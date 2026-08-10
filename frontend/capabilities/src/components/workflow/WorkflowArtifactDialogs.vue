<script setup lang="ts">
import { Maximize2, Minimize2 } from '@lucide/vue'
import type { WorkflowArtifactDetail, WorkflowArtifactHistoryVersion } from '../../api/types'
import type { FullscreenArtifact } from '../../composables/useWorkflowArtifacts'
import { formatLocalDatetime } from '../../lib/time'
import { renderMarkdown } from '../../lib/markdown'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'

defineProps<{
  open: boolean
  detail: WorkflowArtifactDetail | null
  detailLoading: boolean
  visibilitySaving: boolean
  detailHtml: string
  fullscreen: FullscreenArtifact | null
  fullscreenHtml: string
  historyOpen: boolean
  historyTitle: string
  historyLoading: boolean
  history: WorkflowArtifactHistoryVersion[]
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  'update:historyOpen': [open: boolean]
  openFullscreen: [artifact: WorkflowArtifactDetail]
  closeFullscreen: []
  setVisibility: [visibility: 'group' | 'shared']
}>()

function updateOpen(open: boolean) {
  emit('update:open', open)
  if (!open) emit('closeFullscreen')
}
</script>

<template>
  <Dialog :open="open" @update:open="updateOpen">
    <DialogContent class="max-w-[900px] sm:max-w-[900px]">
      <DialogHeader><DialogTitle>{{ detail?.title || '产物详情' }}</DialogTitle></DialogHeader>
      <div class="max-h-[74vh] space-y-3 overflow-x-hidden overflow-y-auto pr-1">
        <div v-if="detailLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
        <template v-else-if="detail">
          <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">{{ detail.path }}</Badge>
            <Badge :variant="detail.visibility === 'shared' ? 'secondary' : 'outline'">{{ detail.visibility === 'shared' ? '共享' : '仅本小组' }}</Badge>
            <Badge v-for="tag in detail.tags" :key="tag" variant="outline">{{ tag }}</Badge>
          </div>
          <p v-if="detail.summary" class="text-sm text-muted-foreground">{{ detail.summary }}</p>
          <iframe v-if="detail.format === 'html'" :srcdoc="detail.content" sandbox="allow-same-origin" class="min-h-[60vh] w-full rounded-lg border bg-card" :title="detail.title || 'HTML 报告'" />
          <div v-else class="prose prose-sm max-w-none overflow-x-auto rounded-md border bg-background p-4" v-html="detailHtml"></div>
        </template>
        <div v-else class="py-8 text-center text-sm text-muted-foreground">无内容</div>
      </div>
      <DialogFooter>
        <Button v-if="detail" variant="outline" :disabled="visibilitySaving" @click="emit('setVisibility', detail.visibility === 'shared' ? 'group' : 'shared')">{{ visibilitySaving ? '保存中...' : detail.visibility === 'shared' ? '改为仅本小组' : '共享给所有小组' }}</Button>
        <Button v-if="detail" variant="outline" class="mr-auto" title="全屏查看" @click="emit('openFullscreen', detail)"><Maximize2 :size="14" /> 全屏</Button>
        <Button variant="outline" @click="emit('update:open', false)">关闭</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>

  <Teleport to="body">
    <div v-if="fullscreen" class="pointer-events-auto fixed inset-0 z-[10000] flex flex-col bg-background">
      <div class="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
        <div class="flex min-w-0 items-center gap-2">
          <span class="truncate text-sm font-medium">{{ fullscreen.title || '产物详情' }}</span>
          <Badge variant="outline" class="shrink-0 font-mono text-xs">{{ fullscreen.path }}</Badge>
          <Badge v-for="tag in fullscreen.tags" :key="tag" variant="outline" class="shrink-0 text-xs">{{ tag }}</Badge>
        </div>
        <Button variant="ghost" size="sm" class="h-8 w-8 shrink-0 p-0" title="退出全屏" @click="emit('closeFullscreen')"><Minimize2 :size="16" /></Button>
      </div>
      <div class="flex-1 overflow-x-hidden overflow-y-auto px-6 py-4" @click="emit('closeFullscreen')">
        <div v-if="fullscreen.format === 'html'" class="mx-auto h-full w-full max-w-[1360px]" @click.stop>
          <iframe :srcdoc="fullscreen.content" sandbox="allow-same-origin" class="h-full min-h-[70vh] w-full rounded-lg border bg-card" :title="fullscreen.title || 'HTML 报告'" />
        </div>
        <div v-else class="mx-auto w-full max-w-[1360px] space-y-4" @click.stop>
          <div v-if="fullscreen.summary" class="text-sm text-muted-foreground">{{ fullscreen.summary }}</div>
          <div class="prose prose-sm max-w-none overflow-x-auto" v-html="fullscreenHtml"></div>
        </div>
      </div>
    </div>
  </Teleport>

  <Dialog :open="historyOpen" @update:open="emit('update:historyOpen', $event)">
    <DialogContent class="max-w-[980px] sm:max-w-[980px]">
      <DialogHeader><DialogTitle>{{ historyTitle || '历史版本' }}</DialogTitle></DialogHeader>
      <div class="max-h-[74vh] space-y-3 overflow-auto pr-1">
        <div v-if="historyLoading" class="py-8 text-center text-sm text-muted-foreground">加载中</div>
        <div v-else-if="!history.length" class="py-8 text-center text-sm text-muted-foreground">暂无历史版本</div>
        <template v-else>
          <details v-for="version in history" :key="version.task_version" class="rounded-md border p-3" :open="version.is_current">
            <summary class="cursor-pointer text-sm font-medium"><span class="font-mono">{{ version.task_version || 'default' }}</span><Badge v-if="version.is_current" variant="outline" class="ml-2">current</Badge><span class="ml-2 text-xs font-normal text-muted-foreground">{{ formatLocalDatetime(version.updated_at) }}</span><span v-if="version.runs.length > 1" class="ml-2 text-xs font-normal text-muted-foreground">· {{ version.runs.length }} 次执行</span></summary>
            <div class="mt-3 space-y-3">
              <details v-for="run in version.runs" :key="run.run_id" class="rounded-md border bg-muted/30 p-3" :open="run.is_current">
                <summary class="cursor-pointer text-xs font-medium"><span class="font-mono">{{ run.run_id }}</span><Badge v-if="run.is_current" variant="outline" class="ml-2">current</Badge><span class="ml-2 font-normal text-muted-foreground">{{ formatLocalDatetime(run.updated_at) }}</span></summary>
                <div class="mt-3 space-y-3">
                  <div v-for="item in run.artifacts" :key="item.artifact_id" class="space-y-2">
                    <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><Badge variant="outline">{{ item.path }}</Badge><Badge variant="outline">{{ item.format }}</Badge><span>{{ formatLocalDatetime(item.updated_at) }}</span><Badge v-for="tag in item.tags" :key="tag" variant="outline">{{ tag }}</Badge></div>
                    <div class="text-sm font-medium">{{ item.title }}</div>
                    <p v-if="item.summary" class="text-sm text-muted-foreground">{{ item.summary }}</p>
                    <iframe v-if="item.format === 'html'" :srcdoc="item.content" sandbox="allow-same-origin" class="min-h-[50vh] w-full rounded-lg border bg-card" :title="item.title || 'HTML 报告'" />
                    <div v-else class="prose prose-sm max-w-none rounded-md border bg-background p-4" v-html="renderMarkdown(item.content)"></div>
                  </div>
                </div>
              </details>
            </div>
          </details>
        </template>
      </div>
      <DialogFooter><Button variant="outline" @click="emit('update:historyOpen', false)">关闭</Button></DialogFooter>
    </DialogContent>
  </Dialog>
</template>
