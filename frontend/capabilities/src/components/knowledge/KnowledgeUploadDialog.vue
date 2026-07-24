<script setup lang="ts">
import { File, Folder, Upload } from '@lucide/vue'
import type { KnowledgeBaseSummary } from '../../api/types'
import type { KnowledgeUploadItem } from '../../composables/useKnowledgeUploadQueue'
import { Button } from '../ui/button'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'

defineProps<{
  open: boolean
  kb: KnowledgeBaseSummary | null
  folderLabel: string
  files: KnowledgeUploadItem[]
  uploading: boolean
  dragOver: boolean
  error: string
  failedCount: number
  retryableCount: number
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  filesSelected: [event: Event]
  dragOver: [event: DragEvent]
  dragLeave: [event: DragEvent]
  drop: [event: DragEvent]
  clear: []
  upload: []
}>()

function getFileSizeLabel(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent :show-close-button="!uploading" class="sm:max-w-[640px]">
      <DialogHeader>
        <DialogTitle>上传文档 — {{ kb?.name || '' }}</DialogTitle>
      </DialogHeader>
      <div class="space-y-4">
        <div class="text-xs text-muted-foreground">
          目标知识库：<span class="font-medium text-foreground">{{ kb?.name }}</span>
          <span class="font-mono ml-1">({{ kb?.slug }})</span>
          <span v-if="folderLabel" class="ml-2">· 目标目录：{{ folderLabel }}</span>
        </div>

        <div v-if="files.length === 0"
          :class="['rounded-lg border-2 border-dashed p-10 text-center transition-colors cursor-pointer', dragOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/20']"
          @dragover="emit('dragOver', $event)"
          @dragleave="emit('dragLeave', $event)"
          @drop="emit('drop', $event)"
        >
          <Upload :size="40" stroke-width="1.5" class="mx-auto mb-3 text-placeholder" />
          <div class="text-sm font-medium mb-1">拖拽文件或文件夹到此处</div>
          <div class="text-xs text-muted-foreground mb-4">支持 PDF、Word、Excel、PPT、TXT、Markdown、ZIP — 压缩包将自动识别其中的文档</div>
          <div class="flex items-center justify-center gap-3">
            <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm bg-primary text-primary-foreground text-sm font-medium cursor-pointer hover:bg-primary/80">
              <File :size="14" />
              选择文件
              <input type="file" multiple class="hidden" :disabled="uploading" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.zip" @change="emit('filesSelected', $event)" />
            </label>
            <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm border border-border bg-background text-sm font-medium cursor-pointer hover:bg-muted">
              <Folder :size="14" />
              选择文件夹
              <input type="file" multiple webkitdirectory class="hidden" :disabled="uploading" @change="emit('filesSelected', $event)" />
            </label>
          </div>
        </div>

        <div v-else class="rounded-lg border-2 border-success/30 bg-muted/20 p-4">
          <div class="flex items-center justify-between mb-3">
            <div class="text-sm font-medium">
              已选择 <span class="text-success">{{ files.length }}</span> 个文件
              <span v-if="failedCount > 0" class="ml-2 text-destructive">失败 {{ failedCount }} 个</span>
            </div>
            <Button variant="ghost" size="xs" class="h-7 text-xs text-muted-foreground" :disabled="uploading" @click="emit('clear')">清除</Button>
          </div>
          <div class="min-w-0 space-y-1.5 max-h-[240px] overflow-y-auto">
            <div v-for="(file, index) in files" :key="index"
              class="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 overflow-hidden rounded border border-border bg-background px-3 py-2 text-sm"
            >
              <File :size="14" class="shrink-0 text-placeholder" />
              <span class="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" :title="file.relativePath">{{ file.relativePath }}</span>
              <div class="flex shrink-0 flex-col items-end gap-0.5 text-xs">
                <span class="text-muted-foreground">{{ getFileSizeLabel(file.file.size) }}</span>
                <span :class="file.status === 'error' ? 'text-destructive' : file.status === 'success' ? 'text-success' : 'text-muted-foreground'">
                  {{ file.status === 'success' ? '成功' : file.status === 'error' ? '失败' : file.status === 'processing' ? '处理中' : file.status === 'uploading' ? '上传中' : '等待中' }}
                </span>
              </div>
              <div class="col-span-full min-w-0 space-y-1">
                <div class="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span class="min-w-0 truncate">{{ file.stage }}</span>
                  <span class="shrink-0">{{ file.progress }}%</span>
                </div>
                <div class="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div class="h-full rounded-full bg-primary transition-[width]" :style="{ width: `${file.progress}%` }"></div>
                </div>
                <div v-if="file.error" class="max-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded border border-destructive/20 bg-destructive-soft px-2 py-1 text-xs text-destructive-soft-fg">
                  {{ file.error }}
                </div>
              </div>
            </div>
          </div>
          <label class="block mt-3 py-2 border border-dashed border-border rounded text-center text-xs text-muted-foreground cursor-pointer hover:bg-muted/50 transition-colors">
            + 继续添加文件
            <input type="file" multiple class="hidden" :disabled="uploading" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.zip" @change="emit('filesSelected', $event)" />
          </label>
        </div>
        <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ error }}</div>
      </div>
      <DialogFooter>
        <DialogClose as-child><Button variant="outline" :disabled="uploading">取消</Button></DialogClose>
        <Button @click="emit('upload')" :disabled="retryableCount === 0 || uploading">
          {{ uploading ? '上传中...' : failedCount > 0 ? `重试失败 (${failedCount})` : `上传 (${files.length})` }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
