<script setup lang="ts">
import { ref } from 'vue'
import { ChevronDown, ChevronRight } from '@lucide/vue'
import type { WorkflowSubagentDetail, WorkflowSubagentTranscriptAgent, WorkflowSubagentTranscriptEvent } from '../api/types'
import { formatLocalDatetime } from '../lib/time'
import { renderMarkdown } from '../lib/markdown'
import JsonViewer from './JsonViewer.vue'

const props = defineProps<{
  detail: WorkflowSubagentDetail | null
  loading: boolean
  error: string
}>()

const expandedAgents = ref<Set<string>>(new Set())

function shortAgentId(agentId: string) {
  return agentId.length > 12 ? `${agentId.slice(0, 8)}…${agentId.slice(-4)}` : agentId
}

function toggleAgent(agentId: string) {
  const next = new Set(expandedAgents.value)
  if (next.has(agentId)) next.delete(agentId)
  else next.add(agentId)
  expandedAgents.value = next
}

function isAgentExpanded(agentId: string) {
  return expandedAgents.value.has(agentId)
}

/** Map a transcript event.kind into a timeline visual family. */
function miniKind(event: WorkflowSubagentTranscriptEvent): 'think' | 'tool' | 'result' | 'error' | 'message' {
  if (event.kind === 'thinking') return 'think'
  if (event.kind === 'tool_call') return 'tool'
  if (event.kind === 'tool_result') return event.is_error ? 'error' : 'result'
  if (event.kind === 'text') return 'message'
  return 'message'
}

function miniLabel(event: WorkflowSubagentTranscriptEvent) {
  if (event.kind === 'prompt') return '提示词'
  if (event.kind === 'thinking') return '思考'
  if (event.kind === 'tool_call') return '工具调用'
  if (event.kind === 'tool_result') return event.is_error ? '工具失败' : '工具完成'
  if (event.kind === 'text') return '文本输出'
  return event.kind
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

/** Whether an event's body should render as monospace dump (code/json) vs prose. */
function isDump(event: WorkflowSubagentTranscriptEvent) {
  return event.kind === 'tool_call' || event.kind === 'tool_result'
}

/** Whether an event's body is free-form agent text worth rendering as markdown. */
function isMarkdown(event: WorkflowSubagentTranscriptEvent) {
  return event.kind === 'text'
}

function eventBody(event: WorkflowSubagentTranscriptEvent) {
  if (event.kind === 'tool_call') return formatValue(event.input)
  return formatValue(event.content)
}

function promptEvents(agent: WorkflowSubagentTranscriptAgent) {
  return agent.events.filter(event => event.kind === 'prompt')
}

function processEvents(agent: WorkflowSubagentTranscriptAgent) {
  return agent.events.filter(event => event.kind !== 'prompt')
}

function agentLabel(agent: WorkflowSubagentTranscriptAgent) {
  return agent.label || `子 Agent #${agent.index || '?'}`
}

function agentStatus(agent: WorkflowSubagentTranscriptAgent) {
  return agent.result == null ? 'running' : 'completed'
}

function agentStatusLabel(agent: WorkflowSubagentTranscriptAgent) {
  return agentStatus(agent) === 'completed' ? '完成' : '运行中'
}
</script>

<template>
  <div class="space-y-3">
    <div v-if="props.loading" class="tl-mini-event">
      <div class="tl-mavatar" />
      <div class="tl-mini-head"><span class="tl-mini-kind" style="background:var(--muted);color:var(--muted-foreground)">加载中</span></div>
      <div class="tl-mini-content">正在读取 Claude transcript…</div>
    </div>
    <div v-if="props.error" class="tl-mini-event k-error">
      <div class="tl-mavatar" />
      <div class="tl-mini-head"><span class="tl-mini-kind">错误</span></div>
      <div class="tl-mini-content tl-dump" style="background:color-mix(in oklch,var(--destructive) 8%,var(--muted))">{{ props.error }}</div>
    </div>

    <template v-if="props.detail">
      <div v-if="!props.detail.transcript_dir" class="tl-mini-event">
        <div class="tl-mavatar" />
        <div class="tl-mini-head"><span class="tl-mini-kind" style="background:var(--muted);color:var(--muted-foreground)">提示</span></div>
        <div class="tl-mini-content">没有在 stdout.log 里找到该子 Agent 的 transcript 目录，下面显示原始进度事件。</div>
      </div>

      <div v-if="props.detail.agents.length" class="tl-mini-event">
        <div class="tl-mavatar" />
        <div class="tl-mini-head">
          <span class="tl-mini-kind" style="background:var(--muted);color:var(--muted-foreground)">内部子 Agent</span>
          <span class="tl-mini-target"><b>{{ props.detail.agent_count || props.detail.agents.length }}</b> 个</span>
        </div>
      </div>

      <!-- 返回主 Agent 的结果（thread card 顶部高亮块） -->
      <div v-if="props.detail.task_output" class="tl-result">
        <div class="tl-result-label">返回主 Agent</div>
        <div class="tl-result-text">
          <pre>{{ props.detail.task_output }}</pre>
        </div>
      </div>

      <!-- 每个 workflow 内部 agent 独立折叠，避免看起来像只有一个子 Agent。 -->
      <div v-for="agent in props.detail.agents" :key="agent.agent_id" class="rounded-sm border bg-background">
        <button type="button" class="flex w-full min-w-0 items-center gap-2 px-2 py-2 text-left text-xs" @click="toggleAgent(agent.agent_id)">
          <component :is="isAgentExpanded(agent.agent_id) ? ChevronDown : ChevronRight" :size="13" class="shrink-0 text-muted-foreground" />
          <span class="shrink-0 font-medium text-foreground">{{ agentLabel(agent) }}</span>
          <span class="shrink-0 font-mono text-[11px] text-muted-foreground">{{ shortAgentId(agent.agent_id) }}</span>
          <span class="shrink-0 rounded-sm border px-1.5 py-0.5 text-[10px]" :class="agentStatus(agent) === 'completed' ? 'bg-success-soft text-success-soft-fg' : 'bg-info-soft text-info-soft-fg'">
            {{ agentStatusLabel(agent) }}
          </span>
          <span class="shrink-0 text-[10px] text-muted-foreground">{{ agent.events.length }} events</span>
          <span v-if="agent.prompt_preview" class="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">{{ agent.prompt_preview }}</span>
        </button>

        <div v-if="isAgentExpanded(agent.agent_id)" class="space-y-3 border-t px-2 py-2">
          <div v-if="promptEvents(agent).length" class="tl-mini">
            <div
              v-for="(event, idx) in promptEvents(agent)"
              :key="agent.agent_id + ':prompt:' + idx"
              class="tl-mini-event"
            >
              <div class="tl-mavatar" />
              <div class="tl-mini-head">
                <span class="tl-mini-kind" style="background:var(--muted);color:var(--muted-foreground)">提示词</span>
                <span v-if="event.created_at" class="tl-mini-time">{{ formatLocalDatetime(event.created_at) }}</span>
              </div>
              <div class="tl-mini-content">{{ formatValue(event.content) }}</div>
            </div>
          </div>

          <div v-if="processEvents(agent).length" class="tl-mini">
            <div
              v-for="(event, idx) in processEvents(agent)"
              :key="agent.agent_id + ':event:' + idx"
              class="tl-mini-event"
              :class="'k-' + miniKind(event)"
            >
              <div class="tl-mavatar" />
              <div class="tl-mini-head">
                <span class="tl-mini-kind">{{ miniLabel(event) }}</span>
                <span v-if="event.tool_name" class="tl-mini-target"><b>{{ event.tool_name }}</b></span>
                <span v-if="event.created_at" class="tl-mini-time">{{ formatLocalDatetime(event.created_at) }}</span>
              </div>
              <div v-if="eventBody(event)" class="tl-mini-content" :class="isDump(event) ? 'tl-dump' : ''">
                <div
                  v-if="isMarkdown(event)"
                  class="tl-md"
                  v-html="renderMarkdown(eventBody(event))"
                />
                <template v-else>{{ eventBody(event) }}</template>
              </div>
            </div>
          </div>

          <!-- 结构化结果（如果与 task_output 不同） -->
          <div v-if="agent.result != null && formatValue(agent.result) !== formatValue(props.detail.task_output)" class="tl-result" style="margin-top:10px">
            <div class="tl-result-label">结构化结果 · {{ shortAgentId(agent.agent_id) }}</div>
            <div class="tl-result-text">
              <JsonViewer :value="agent.result" max-height="200px" />
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
