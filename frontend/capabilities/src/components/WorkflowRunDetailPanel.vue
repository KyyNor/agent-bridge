<script setup lang="ts">
import type { AgentRun, WorkflowRunEvent, WorkflowSubagentDetail } from '../api/types'
import AgentRunTabs from './AgentRunTabs.vue'
import RunEventTimeline from './RunEventTimeline.vue'
import SubagentDetailPanel from './SubagentDetailPanel.vue'

type DetailFn<T> = (taskId: string) => T

const props = withDefaults(defineProps<{
  events: WorkflowRunEvent[]
  agentRuns: AgentRun[]
  selectedAgentRunKey: string
  eventsLoading: boolean
  agentRunsLoading: boolean
  contextKey?: string
  detailError?: string
  subagentDetail: DetailFn<WorkflowSubagentDetail | null>
  subagentDetailLoading: DetailFn<boolean>
  subagentDetailError: DetailFn<string>
  showHeader?: boolean
}>(), {
  contextKey: 'run:detail',
  detailError: '',
  showHeader: true,
})

const emit = defineEmits<{
  (event: 'select-agent-run', runKey: string): void
  (event: 'refresh'): void
  (event: 'expand-subagent', taskId: string): void
}>()

function selectAgentRun(runKey: string) {
  emit('select-agent-run', runKey)
}

function expandSubagent(taskId: string) {
  emit('expand-subagent', taskId)
}
</script>

<template>
  <div class="space-y-2">
    <AgentRunTabs
      v-if="props.showHeader"
      :agent-runs="props.agentRuns"
      :selected-agent-run-key="props.selectedAgentRunKey"
      :event-count="props.events.length"
      :events-loading="props.eventsLoading"
      :agent-runs-loading="props.agentRunsLoading"
      :detail-error="props.detailError"
      @select-agent-run="selectAgentRun"
      @refresh="emit('refresh')"
    />

    <div v-if="props.eventsLoading" class="rounded-md border bg-background px-3 py-8 text-center text-sm text-muted-foreground">
      加载中
    </div>
    <div v-else-if="!props.events.length" class="rounded-md border bg-background px-3 py-8 text-center text-sm text-muted-foreground">
      还没有 Agent 输出，任务被领取执行后这里会按时间顺序显示对话流。
    </div>
    <div v-else class="min-h-[12rem] overflow-x-hidden overflow-y-visible pr-1">
      <RunEventTimeline
        :events="props.events"
        :context-key="props.contextKey"
        show-agent-name
        @expand="expandSubagent"
      >
        <template #subagent-body="{ taskId }">
          <SubagentDetailPanel
            :detail="props.subagentDetail(taskId)"
            :loading="props.subagentDetailLoading(taskId)"
            :error="props.subagentDetailError(taskId)"
          />
        </template>
      </RunEventTimeline>
    </div>
  </div>
</template>
