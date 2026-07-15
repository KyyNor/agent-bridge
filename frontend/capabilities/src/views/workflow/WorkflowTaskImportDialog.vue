<script setup lang="ts">
import { computed } from 'vue'
import type { WorkflowTaskImportPreview, WorkflowTaskImportRow } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'

interface Props {
  open: boolean
  preview: WorkflowTaskImportPreview | null
  loading: boolean
  confirming: boolean
  error: string | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'select-file', file: File): void
  (event: 'download-template'): void
  (event: 'confirm'): void
}>()

const isBusy = computed(() => props.loading || props.confirming)
const canConfirm = computed(() => Boolean(props.preview?.can_confirm) && !isBusy.value)

function onOpenChange(value: boolean) {
  if (!value && isBusy.value) return
  emit('update:open', value)
}

const summaryCards = computed(() => {
  const summary = props.preview?.summary
  if (!summary) return []

  return [
    { label: '总行数', value: summary.total_rows, class: 'text-foreground' },
    { label: '有效', value: summary.valid_rows, class: 'text-green-700' },
    { label: '无效', value: summary.invalid_rows, class: 'text-destructive' },
    { label: '新增', value: summary.created, class: 'text-blue-700' },
    { label: '更新', value: summary.updated, class: 'text-violet-700' },
    { label: '跳过（运行中）', value: summary.skipped_running, class: 'text-amber-700' },
    { label: '跳过（已完成）', value: summary.skipped_completed, class: 'text-amber-700' },
    { label: '重开（已过期）', value: summary.reopened_expired, class: 'text-amber-700' },
  ]
})

const actionLabels: Record<WorkflowTaskImportRow['action'], string> = {
  created: '新增',
  updated: '更新',
  skipped_running: '跳过（运行中）',
  skipped_completed: '跳过（已完成）',
  reopened_expired: '重开（已过期）',
  error: '错误',
}

function actionLabel(action: WorkflowTaskImportRow['action']): string {
  return actionLabels[action]
}

function formatPayload(payload: Record<string, unknown>): string {
  return JSON.stringify(payload, null, 2)
}

function formatExpiresAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) emit('select-file', file)
}
</script>

<template>
  <Dialog :open="open" @update:open="onOpenChange">
    <DialogContent :show-close-button="!isBusy" class="w-[min(1180px,calc(100vw-2rem))] sm:max-w-[1180px] max-h-[calc(100vh-2rem)] overflow-hidden">
      <DialogHeader>
        <DialogTitle>导入工作流任务</DialogTitle>
        <p class="text-xs text-muted-foreground">
          上传 xlsx 文件后先预览校验，确认后才会写入任务队列。
        </p>
      </DialogHeader>

      <div class="min-h-0 space-y-4 overflow-y-auto pr-1">
        <div class="flex flex-col gap-3 rounded-md border border-border bg-muted/20 p-3 sm:flex-row sm:items-end sm:justify-between">
          <div class="min-w-0 flex-1 space-y-1.5">
            <label for="workflow-task-import-file" class="text-sm font-medium">选择任务文件</label>
            <input
              id="workflow-task-import-file"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              data-slot="input"
              class="dark:bg-input/30 border-input focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 dark:aria-invalid:border-destructive aria-invalid:border-destructive disabled:bg-input/50 dark:disabled:bg-input/80 h-9 rounded-sm border bg-transparent px-2.5 py-1 text-base transition-colors file:h-6 file:text-sm file:font-medium focus-visible:ring-3 aria-invalid:ring-3 md:text-sm w-full min-w-0 outline-none file:inline-flex file:border-0 file:bg-transparent file:text-foreground placeholder:text-placeholder disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer file:cursor-pointer"
              :disabled="isBusy"
              @change="onFileChange"
            />
            <p class="text-xs text-muted-foreground">仅支持 .xlsx 文件，建议先下载模板填写。</p>
          </div>
          <Button variant="outline" type="button" :disabled="isBusy" @click="emit('download-template')">
            下载模板
          </Button>
        </div>

        <div v-if="preview" class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>文件：<strong class="font-medium text-foreground">{{ preview.filename }}</strong></span>
          <span>工作表：<strong class="font-medium text-foreground">{{ preview.sheet_name }}</strong></span>
          <span>预览有效期至：<strong class="font-medium text-foreground">{{ formatExpiresAt(preview.expires_at) }}</strong></span>
        </div>

        <div v-if="loading" class="rounded-md border border-border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
          正在解析并校验 Excel 文件...
        </div>
        <div v-else-if="error" class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {{ error }}
        </div>

        <template v-else-if="preview">
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
            <div v-for="card in summaryCards" :key="card.label" class="rounded-md border border-border bg-background px-3 py-2.5">
              <div class="text-xs text-muted-foreground">{{ card.label }}</div>
              <div class="mt-1 text-xl font-semibold tabular-nums" :class="card.class">{{ card.value }}</div>
            </div>
          </div>

          <div v-if="!preview.can_confirm" class="rounded-md border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            预览中存在错误行，修正 Excel 后重新上传才能确认导入。
          </div>

          <div class="overflow-hidden rounded-md border border-border">
            <div class="max-h-[420px] overflow-auto">
              <table class="min-w-full text-left text-xs">
                <thead class="sticky top-0 z-10 bg-muted/95 text-muted-foreground">
                  <tr>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">行号</th>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">任务 Key</th>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">版本</th>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">类型</th>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">动作</th>
                    <th class="min-w-[220px] px-3 py-2 font-medium">错误</th>
                    <th class="whitespace-nowrap px-3 py-2 font-medium">Payload</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-border">
                  <tr v-for="row in preview.rows" :key="row.row_number" class="align-top hover:bg-muted/30">
                    <td class="whitespace-nowrap px-3 py-2 font-mono tabular-nums">{{ row.row_number }}</td>
                    <td class="max-w-[220px] break-all px-3 py-2 font-mono font-medium">{{ row.task_key || '—' }}</td>
                    <td class="whitespace-nowrap px-3 py-2 font-mono">{{ row.task_version || '—' }}</td>
                    <td class="whitespace-nowrap px-3 py-2">{{ row.type || '—' }}</td>
                    <td class="whitespace-nowrap px-3 py-2">
                      <Badge :variant="row.action === 'error' ? 'destructive' : 'secondary'" :title="`原始状态：${row.action}`">
                        {{ actionLabel(row.action) }}
                      </Badge>
                      <div class="mt-1 font-mono text-[10px] text-muted-foreground">{{ row.action }}</div>
                    </td>
                    <td class="px-3 py-2">
                      <div v-if="row.errors.length" class="space-y-1 rounded-sm border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-destructive">
                        <div v-for="message in row.errors" :key="message">{{ message }}</div>
                      </div>
                      <span v-else class="text-muted-foreground">—</span>
                    </td>
                    <td class="px-3 py-2">
                      <details class="min-w-[160px]">
                        <summary class="cursor-pointer select-none text-primary hover:underline">查看详情</summary>
                        <pre class="mt-2 max-w-[360px] overflow-auto rounded-sm bg-muted p-2 font-mono text-[11px] leading-relaxed">{{ formatPayload(row.payload) }}</pre>
                      </details>
                    </td>
                  </tr>
                  <tr v-if="preview.rows.length === 0">
                    <td colspan="7" class="px-3 py-8 text-center text-muted-foreground">没有可展示的行。</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>

        <div v-else class="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          选择 xlsx 文件开始预览。
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" type="button" :disabled="isBusy" @click="onOpenChange(false)">取消</Button>
        <Button type="button" :disabled="!canConfirm" @click="emit('confirm')">
          {{ confirming ? '确认中...' : '确认导入' }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
