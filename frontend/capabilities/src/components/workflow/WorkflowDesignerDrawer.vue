<script setup lang="ts">
import { Check, WandSparkles } from '@lucide/vue'
import type { DesignAgentResponse, WorkflowDesignResult } from '../../api/types'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import SegmentedTabs from '../SegmentedTabs.vue'

type DesignDraft = NonNullable<WorkflowDesignResult['workflow']>

defineProps<{
  open: boolean
  mode: 'create' | 'modify'
  prompt: string
  busy: boolean
  stopRequested: boolean
  error: string
  response: DesignAgentResponse<WorkflowDesignResult> | null
  draft: DesignDraft | null
  saving: boolean
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  'update:mode': [mode: 'create' | 'modify']
  'update:prompt': [prompt: string]
  run: []
  stop: []
  accept: []
}>()
</script>

<template>
  <aside v-if="open" class="fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l bg-background shadow-xl">
    <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
      <div><div class="text-sm font-semibold text-foreground">工作流设计 Agent</div><div class="font-mono text-xs text-muted-foreground">design_workflow</div></div>
      <Button variant="ghost" size="sm" class="h-8 px-2" :disabled="busy" @click="emit('update:open', false)">关闭</Button>
    </div>
    <div class="flex-1 space-y-4 overflow-auto p-4">
      <SegmentedTabs :model-value="mode" :tabs="[{ key: 'modify', label: '修改' }, { key: 'create', label: '新建' }]" @update:model-value="emit('update:mode', $event as 'create' | 'modify')" />
      <div>
        <label class="mb-1 block text-xs text-muted-foreground">提示词</label>
        <textarea :value="prompt" class="min-h-32 w-full rounded-md border bg-background p-3 text-sm" placeholder="描述希望 agent 设计或修改的工作流目标" @input="emit('update:prompt', ($event.target as HTMLTextAreaElement).value)" />
      </div>
      <Button class="w-full" :disabled="stopRequested" @click="busy ? emit('stop') : emit('run')"><WandSparkles class="mr-1.5 h-4 w-4" />{{ busy ? (stopRequested ? '停止中' : '立即停止') : '生成方案' }}</Button>
      <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ error }}</div>
      <section v-if="response?.result" class="space-y-3 rounded-md border bg-muted/10 p-3">
        <div class="flex items-center justify-between gap-2"><div class="text-sm font-semibold">生成结果</div><Badge v-if="response.run_key" variant="outline">{{ response.run_key }}</Badge></div>
        <p class="text-sm text-muted-foreground">{{ response.result.summary }}</p>
        <div v-if="response.result.notes?.length" class="space-y-1 text-xs text-muted-foreground"><div v-for="note in response.result.notes" :key="note">· {{ note }}</div></div>
        <div v-if="draft" class="grid gap-2 text-xs"><div class="rounded-md border bg-muted/10 p-2"><div class="font-mono font-medium text-foreground">{{ draft.workflow_key }}</div><div class="mt-1 text-muted-foreground">{{ draft.name }}</div></div><pre class="max-h-96 overflow-auto rounded-md border bg-muted/10 p-3 text-xs">{{ JSON.stringify(draft.definition, null, 2) }}</pre></div>
      </section>
    </div>
    <div class="flex items-center justify-end gap-2 border-t p-4">
      <Button variant="outline" :disabled="busy" @click="emit('update:open', false)">取消</Button>
      <Button :disabled="busy || !draft || saving" @click="emit('accept')"><Check class="mr-1.5 h-4 w-4" />{{ saving ? '保存中' : '采纳并保存' }}</Button>
    </div>
  </aside>
</template>
