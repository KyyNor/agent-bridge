<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentRun } from '../api/types'
import { Button } from './ui/button'
import JsonViewer from './JsonViewer.vue'
import PayloadDetailDialog from './PayloadDetailDialog.vue'
import { preparePayloadPresentation, type PayloadPresentation } from '../lib/payloadPresentation'

type ExecutionDetail = PayloadPresentation & { title: string }

const props = withDefaults(defineProps<{
  run: Pick<AgentRun, 'prompt' | 'result' | 'run_key'> | null
  loading?: boolean
  error?: string
}>(), {
  loading: false,
  error: '',
})

const detail = ref<ExecutionDetail | null>(null)
const hasPrompt = computed(() => Boolean(props.run?.prompt))
const hasResult = computed(() => props.run?.result != null)

function openDetail(title: string, value: unknown) {
  detail.value = { title, ...preparePayloadPresentation(value) }
}
</script>

<template>
  <div class="space-y-3">
    <div v-if="loading && !run" class="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      正在加载 Agent 输入与结果
    </div>
    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-xs text-destructive">
      Agent 输入与结果加载失败：{{ error }}
    </div>

    <section v-if="hasPrompt" class="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <header class="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-2.5">
        <h3 class="text-sm font-semibold text-foreground">输入提示词</h3>
        <Button variant="outline" size="sm" class="h-7 px-2 text-xs" @click="openDetail('输入提示词', run?.prompt)">详情</Button>
      </header>
      <pre class="max-h-[180px] overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-5 text-foreground">{{ run?.prompt }}</pre>
    </section>

    <section v-if="hasResult" class="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <header class="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-2.5">
        <h3 class="text-sm font-semibold text-foreground">执行结果</h3>
        <Button variant="outline" size="sm" class="h-7 px-2 text-xs" @click="openDetail('执行结果', run?.result)">详情</Button>
      </header>
      <div class="px-4 py-3">
        <JsonViewer :value="run?.result" max-height="240px" />
      </div>
    </section>
  </div>

  <PayloadDetailDialog
    v-if="detail"
    :open="detail !== null"
    :title="detail.title"
    :content="detail.content"
    :language="detail.language"
    @update:open="(open: boolean) => { if (!open) detail = null }"
  />
</template>
