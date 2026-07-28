<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Copy } from '@lucide/vue'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import { Button } from './ui/button'
import JsonViewer from './JsonViewer.vue'
import PayloadCodeViewer from './PayloadCodeViewer.vue'
import { renderMarkdown } from '../lib/markdown'
import {
  extractMcpStructuredPayload,
  payloadLanguageLabel,
  type PayloadLanguage,
} from '../lib/payloadPresentation'
import { formatJsonValue } from '../lib/jsonDisplay'

const props = defineProps<{
  open: boolean
  title: string
  label?: string
  content: string
  language: PayloadLanguage
}>()

defineEmits<{
  (event: 'update:open', open: boolean): void
}>()

const copied = ref(false)
const copyFailed = ref(false)
const jsonView = ref<'raw' | 'structured'>('raw')
let resetCopyStateTimer: ReturnType<typeof setTimeout> | undefined

const mcpStructured = computed(() => (
  props.language === 'json' ? extractMcpStructuredPayload(props.content) : null
))
const displayedJson = computed(() => (
  jsonView.value === 'structured' && mcpStructured.value
    ? mcpStructured.value.structured
    : props.content
))
const copyText = computed(() => (
  jsonView.value === 'structured' && mcpStructured.value
    ? formatJsonValue(mcpStructured.value.structured)
    : props.content
))

watch(
  () => [props.content, props.language],
  () => { jsonView.value = 'raw' },
)

async function copyContent(content: string) {
  copied.value = false
  copyFailed.value = false
  try {
    await navigator.clipboard.writeText(content)
    copied.value = true
  } catch {
    copyFailed.value = true
  }
  if (resetCopyStateTimer) clearTimeout(resetCopyStateTimer)
  resetCopyStateTimer = setTimeout(() => {
    copied.value = false
    copyFailed.value = false
  }, 1800)
}
</script>

<template>
  <Dialog :open="open" @update:open="$emit('update:open', $event)">
    <DialogContent class="w-[min(1280px,calc(100vw-2rem))] !max-w-[1280px] sm:!max-w-[1280px] max-h-[calc(100vh-2rem)] overflow-hidden">
      <DialogHeader>
        <div class="flex items-start justify-between gap-3 pr-6">
          <div class="min-w-0">
            <DialogTitle>{{ title }} · {{ payloadLanguageLabel(language) }}</DialogTitle>
            <div v-if="label" class="mt-1 text-xs text-muted-foreground">{{ label }}</div>
          </div>
          <Button variant="outline" size="sm" class="h-7 shrink-0 gap-1.5 text-xs" @click="copyContent(copyText)">
            <Check v-if="copied" :size="13" />
            <Copy v-else :size="13" />
            {{ copied ? '已复制' : copyFailed ? '复制失败' : '复制' }}
          </Button>
        </div>
      </DialogHeader>
      <div class="min-h-0 overflow-auto">
        <div v-if="mcpStructured" class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div class="inline-flex rounded-md bg-secondary p-0.5" role="group" aria-label="MCP JSON 视图">
            <Button
              size="xs"
              :variant="jsonView === 'raw' ? 'outline' : 'ghost'"
              :class="jsonView === 'raw' ? 'bg-card shadow-sm' : ''"
              @click="jsonView = 'raw'"
            >完整响应</Button>
            <Button
              size="xs"
              :variant="jsonView === 'structured' ? 'outline' : 'ghost'"
              :class="jsonView === 'structured' ? 'bg-card shadow-sm' : ''"
              @click="jsonView = 'structured'"
            >结构化响应</Button>
          </div>
          <div v-if="jsonView === 'structured'" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>service <span class="font-mono text-foreground">{{ mcpStructured.service }}</span></span>
            <span>tool <span class="font-mono text-foreground">{{ mcpStructured.toolName }}</span></span>
            <span v-if="mcpStructured.success !== null" :class="mcpStructured.success ? 'text-success-soft-fg' : 'text-destructive-soft-fg'">
              {{ mcpStructured.success ? '成功' : '失败' }}
            </span>
          </div>
        </div>
        <div
          v-if="language === 'markdown'"
          class="payload-markdown rounded-md border bg-background p-4"
          v-html="renderMarkdown(content)"
        />
        <JsonViewer
          v-else-if="language === 'json'"
          :value="displayedJson"
          max-height="min(68vh, 720px)"
        />
        <PayloadCodeViewer
          v-else
          :content="content"
          :language="language"
        />
      </div>
    </DialogContent>
  </Dialog>
</template>

<style>
.payload-markdown{max-height:min(68vh,720px);overflow:auto;line-height:1.65;color:var(--foreground)}
.payload-markdown h1,.payload-markdown h2,.payload-markdown h3,.payload-markdown h4{font-weight:600;line-height:1.3;margin:14px 0 6px}
.payload-markdown h1{font-size:1.35em}.payload-markdown h2{font-size:1.2em}.payload-markdown h3{font-size:1.08em}.payload-markdown h4{font-size:1em}
.payload-markdown>:first-child{margin-top:0}.payload-markdown>:last-child{margin-bottom:0}
.payload-markdown p{margin:0 0 8px}.payload-markdown ul,.payload-markdown ol{margin:4px 0 8px;padding-left:22px}
.payload-markdown blockquote{margin:8px 0;padding:4px 12px;border-left:3px solid var(--border);color:var(--muted-foreground)}
.payload-markdown pre{overflow:auto;margin:8px 0;padding:10px 12px;background:var(--muted);border-radius:var(--radius-control);font-family:var(--font-mono);font-size:12px}
.payload-markdown code{font-family:var(--font-mono);font-size:.9em;background:var(--muted);padding:1px 5px;border-radius:var(--radius-compact)}
.payload-markdown pre code{background:transparent;padding:0}
.payload-markdown table{width:100%;border-collapse:collapse;margin:8px 0}.payload-markdown th,.payload-markdown td{border:1px solid var(--border);padding:5px 8px;text-align:left}.payload-markdown th{background:var(--muted)}
</style>
