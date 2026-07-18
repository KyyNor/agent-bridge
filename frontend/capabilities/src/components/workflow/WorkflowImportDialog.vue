<script setup lang="ts">
import { computed, ref } from 'vue'
import type { WorkflowImportPreview, WorkflowImportTargetMode } from '../../api/types'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import UnifiedDiff from '../../components/diff/UnifiedDiff.vue'
import WorkflowStructuredDiff from '../../components/diff/WorkflowStructuredDiff.vue'

interface Props {
  open: boolean
  preview: WorkflowImportPreview | null
  loading: boolean
  confirming: boolean
  error: string | null
  targetWorkflowKey: string
  targetMode: WorkflowImportTargetMode
  hasFile: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'select-file', file: File): void
  (event: 'update-target-key', value: string): void
  (event: 'update-target-mode', value: WorkflowImportTargetMode): void
  (event: 'preview'): void
  (event: 'confirm'): void
}>()

const diffView = ref<'structured' | 'text'>('structured')
const isBusy = computed(() => props.loading || props.confirming)
const canConfirm = computed(() => Boolean(props.preview?.can_confirm) && !isBusy.value)

function onOpenChange(value: boolean) {
  if (!value && isBusy.value) return
  emit('update:open', value)
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) emit('select-file', file)
}

function onTargetKeyInput(value: string | number) {
  emit('update-target-key', String(value))
}

function onTargetModeChange(event: Event) {
  emit('update-target-mode', (event.target as HTMLSelectElement).value as WorkflowImportTargetMode)
}

function formatExpiresAt(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
</script>

<template>
  <Dialog :open="open" @update:open="onOpenChange">
    <DialogContent class="w-[min(1180px,calc(100vw-2rem))] sm:max-w-[1180px] max-h-[calc(100vh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
      <DialogHeader>
        <DialogTitle>导入工作流</DialogTitle>
        <p class="text-xs text-muted-foreground">
          先预览校验和变化，再确认写入；导入不会携带运行记录或执行产物。
        </p>
      </DialogHeader>

      <div class="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        <div class="grid gap-3 rounded-md border border-border bg-muted/20 p-3 md:grid-cols-[minmax(0,1fr)_220px]">
          <div class="space-y-1.5">
            <label for="workflow-import-file" class="text-sm font-medium">选择工作流文件</label>
            <input
              id="workflow-import-file"
              type="file"
              accept=".json,application/json"
              :disabled="isBusy"
              class="h-9 w-full cursor-pointer rounded-sm border border-input bg-transparent px-2.5 py-1 text-sm file:mr-2 file:border-0 file:bg-transparent file:font-medium"
              @change="onFileChange"
            />
            <p class="text-xs text-muted-foreground">仅支持从工作流导出功能生成的 JSON 文件。</p>
          </div>
          <div class="space-y-1.5">
            <label for="workflow-import-mode" class="text-sm font-medium">导入方式</label>
            <select
              id="workflow-import-mode"
              :value="targetMode"
              :disabled="isBusy"
              class="h-9 w-full rounded-sm border border-input bg-background px-2 text-sm"
              @change="onTargetModeChange"
            >
              <option value="auto">自动判断（同 key 时覆盖）</option>
              <option value="new">导入为新工作流</option>
              <option value="overwrite">覆盖现有工作流</option>
            </select>
          </div>
          <div class="space-y-1.5 md:col-span-2">
            <label for="workflow-import-target-key" class="text-sm font-medium">目标 workflow key</label>
            <Input
              id="workflow-import-target-key"
              :model-value="targetWorkflowKey"
              :disabled="isBusy"
              placeholder="默认使用导出文件中的 workflow key"
              @update:model-value="onTargetKeyInput"
            />
            <p class="text-xs text-muted-foreground">
              默认使用文件中的 key；同 key 时会生成覆盖预览，确认前仍可改成新 key。
            </p>
          </div>
        </div>

        <div v-if="preview" class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>文件：<strong class="font-medium text-foreground">{{ preview.filename }}</strong></span>
          <span>目标：<strong class="font-medium text-foreground">{{ preview.target_workflow_key }}</strong></span>
          <span>动作：<strong
            :class="preview.operation === 'overwrite'
              ? 'inline-flex items-center gap-1 rounded-sm border border-destructive/30 bg-destructive-soft px-1.5 py-0.5 font-semibold text-destructive-soft-fg'
              : 'font-medium text-foreground'"
          ><span v-if="preview.operation === 'overwrite'" aria-hidden="true">⚠</span>{{ preview.operation === 'overwrite' ? '覆盖现有工作流' : '创建新工作流' }}</strong></span>
          <span>预览有效期至：<strong class="font-medium text-foreground">{{ formatExpiresAt(preview.expires_at) }}</strong></span>
        </div>

        <div v-if="loading" class="rounded-md border border-border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
          正在解析、校验并计算变化...
        </div>
        <div v-else-if="error" class="whitespace-pre-line rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
          {{ error }}
        </div>

        <template v-else-if="preview">
          <div v-if="preview.operation === 'create'" class="rounded-md border border-success/30 bg-success-soft px-3 py-2 text-sm text-success-soft-fg">
            将创建新工作流「{{ preview.target_workflow_key }}」，确认后产生导入来源的 v1。
          </div>
          <div v-else-if="preview.diff" class="space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="text-sm font-medium">当前 v{{ preview.target_revision_no }} → 导入内容</div>
              <div class="inline-flex h-8 items-center gap-0.5 rounded-md bg-secondary p-1">
                <button
                  type="button"
                  class="h-6 rounded-sm px-2 text-xs font-medium"
                  :class="diffView === 'structured' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
                  @click="diffView = 'structured'"
                >结构化</button>
                <button
                  type="button"
                  class="h-6 rounded-sm px-2 text-xs font-medium"
                  :class="diffView === 'text' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
                  @click="diffView = 'text'"
                >文本 diff</button>
              </div>
            </div>
            <WorkflowStructuredDiff
              v-if="diffView === 'structured' && preview.diff.structured"
              :diff="preview.diff.structured"
            />
            <UnifiedDiff
              v-else
              :content="preview.diff.text.content"
              :caption="`${preview.diff.text.from_label} → ${preview.diff.text.to_label}`"
            />
          </div>
        </template>

        <div v-if="hasFile && !loading" class="flex justify-end">
          <Button variant="outline" type="button" :disabled="isBusy" @click="emit('preview')">重新预览</Button>
        </div>

        <div v-else-if="!hasFile" class="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          选择工作流 JSON 文件开始预览。
        </div>
      </div>

      <DialogFooter class="shrink-0">
        <Button variant="outline" type="button" :disabled="isBusy" @click="onOpenChange(false)">取消</Button>
        <Button type="button" :disabled="!canConfirm" @click="emit('confirm')">
          {{ confirming ? '确认中...' : '确认导入' }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
