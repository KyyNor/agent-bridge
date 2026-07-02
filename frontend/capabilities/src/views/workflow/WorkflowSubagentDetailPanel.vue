<script setup lang="ts">
import { Badge } from '../../components/ui/badge'
import type { WorkflowSubagentDetail, WorkflowSubagentTranscriptAgent, WorkflowSubagentTranscriptEvent } from '../../api/types'

const props = defineProps<{
  detail: WorkflowSubagentDetail | null
  loading: boolean
  error: string
}>()

function shortAgentId(agentId: string) {
  return agentId.length > 12 ? `${agentId.slice(0, 8)}...${agentId.slice(-4)}` : agentId
}

function eventLabel(event: WorkflowSubagentTranscriptEvent) {
  if (event.kind === 'prompt') return '提示词'
  if (event.kind === 'thinking') return '思考'
  if (event.kind === 'tool_call') return '工具调用'
  if (event.kind === 'tool_result') return '工具结果'
  if (event.kind === 'text') return '文本输出'
  return event.kind
}

function eventClass(event: WorkflowSubagentTranscriptEvent) {
  if (event.kind === 'thinking') return 'border-amber-300'
  if (event.kind === 'tool_call') return 'border-blue-400'
  if (event.kind === 'tool_result') return event.is_error ? 'border-red-400' : 'border-green-400'
  if (event.kind === 'text') return 'border-foreground/40'
  return 'border-border'
}

function formatValue(value: unknown) {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function promptEvents(agent: WorkflowSubagentTranscriptAgent) {
  return agent.events.filter(event => event.kind === 'prompt')
}

function processEvents(agent: WorkflowSubagentTranscriptAgent) {
  return agent.events.filter(event => event.kind !== 'prompt')
}

function eventBody(event: WorkflowSubagentTranscriptEvent) {
  if (event.kind === 'tool_call') return formatValue(event.input)
  return formatValue(event.content)
}
</script>

<template>
  <div class="space-y-3">
    <div v-if="props.loading" class="rounded-sm border bg-background px-3 py-2 text-xs text-muted-foreground">
      正在读取 Claude transcript
    </div>
    <div v-if="props.error" class="rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
      {{ props.error }}
    </div>

    <template v-if="props.detail">
      <div v-if="!props.detail.transcript_dir" class="rounded-sm border bg-background px-3 py-2 text-xs text-muted-foreground">
        没有在 stdout.log 里找到该子 Agent 的 transcript 目录，下面显示原始进度事件。
      </div>
      <div v-else class="rounded-sm border bg-background px-3 py-2 text-xs">
        <div class="font-medium text-foreground">Claude transcript</div>
        <div class="mt-1 break-all font-mono text-[11px] text-muted-foreground">{{ props.detail.transcript_dir }}</div>
      </div>

      <section v-if="props.detail.task_output" class="space-y-1 rounded-sm border bg-background p-3">
        <div class="flex items-center gap-2">
          <div class="text-xs font-semibold text-foreground">返回主 Agent</div>
          <Badge v-if="props.detail.task_output_status" variant="outline" class="text-[10px]">
            {{ props.detail.task_output_status }}
          </Badge>
        </div>
        <pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded-sm bg-muted/50 p-2 text-[11px] leading-relaxed text-foreground">{{ props.detail.task_output }}</pre>
      </section>

      <section v-for="agent in props.detail.agents" :key="agent.agent_id" class="space-y-3 rounded-sm border bg-background p-3">
        <div class="flex flex-wrap items-center gap-2">
          <div class="font-mono text-xs font-semibold text-foreground">{{ shortAgentId(agent.agent_id) }}</div>
          <Badge variant="outline" class="text-[10px]">{{ agent.events.length }} events</Badge>
        </div>

        <div v-if="promptEvents(agent).length" class="space-y-1">
          <div class="text-xs font-semibold text-foreground">提示词</div>
          <pre
            v-for="(event, idx) in promptEvents(agent)"
            :key="agent.agent_id + ':prompt:' + idx"
            class="max-h-48 overflow-auto whitespace-pre-wrap rounded-sm bg-muted/50 p-2 text-[11px] leading-relaxed text-foreground"
          >{{ formatValue(event.content) }}</pre>
        </div>

        <div v-if="processEvents(agent).length" class="space-y-2">
          <div class="text-xs font-semibold text-foreground">过程</div>
          <div
            v-for="(event, idx) in processEvents(agent)"
            :key="agent.agent_id + ':event:' + idx"
            class="border-l-2 pl-2"
            :class="eventClass(event)"
          >
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-medium text-foreground">{{ eventLabel(event) }}</span>
              <span v-if="event.tool_name" class="font-mono text-muted-foreground">{{ event.tool_name }}</span>
              <span v-if="event.created_at" class="text-muted-foreground">{{ event.created_at }}</span>
            </div>
            <pre v-if="eventBody(event)" class="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded-sm bg-muted/40 p-2 text-[11px] leading-relaxed text-foreground">{{ eventBody(event) }}</pre>
          </div>
        </div>

        <div v-if="agent.result != null" class="space-y-1">
          <div class="text-xs font-semibold text-foreground">结构化结果</div>
          <pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded-sm bg-muted/50 p-2 text-[11px] leading-relaxed text-foreground">{{ formatValue(agent.result) }}</pre>
        </div>
      </section>
    </template>
  </div>
</template>
