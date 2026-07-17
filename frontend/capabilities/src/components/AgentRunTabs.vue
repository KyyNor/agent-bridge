<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api/client'
import type { AgentRun } from '../api/types'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

const props = withDefaults(defineProps<{
  agentRuns: AgentRun[]
  selectedAgentRunKey: string
  eventCount: number
  eventsLoading: boolean
  agentRunsLoading: boolean
  detailError?: string
  sticky?: boolean
  workflowRunId?: string
  workflowRunStatus?: string
}>(), {
  detailError: '',
  sticky: true,
  workflowRunId: '',
  workflowRunStatus: '',
})

const emit = defineEmits<{
  (event: 'select-agent-run', runKey: string): void
  (event: 'refresh'): void
}>()

const stopRequested = ref(false)
const stopError = ref('')
const terminalWorkflowStatuses = ['completed', 'no_task', 'failed', 'stopped']
const workflowRunCanStop = computed(() => Boolean(
  props.workflowRunId
  && !stopRequested.value
  && props.workflowRunStatus !== 'stopping'
  && !terminalWorkflowStatuses.includes(props.workflowRunStatus),
))
const workflowRunStopping = computed(() => stopRequested.value || props.workflowRunStatus === 'stopping')

watch(() => props.workflowRunId, () => {
  stopRequested.value = false
  stopError.value = ''
})

watch(() => props.workflowRunStatus, status => {
  if (terminalWorkflowStatuses.includes(status)) {
    stopRequested.value = false
    stopError.value = ''
  }
})

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

async function stopWorkflowRun() {
  const runId = props.workflowRunId
  if (!runId || !workflowRunCanStop.value) return
  stopRequested.value = true
  stopError.value = ''
  try {
    await api.stopWorkflowRun(runId)
    emit('refresh')
  } catch (error: unknown) {
    stopRequested.value = false
    stopError.value = errorMessage(error)
  }
}

function agentRunLabel(run: AgentRun) {
  if (run.agent_name === 'workflow') return 'Workflow Agent'
  if (run.agent_name === 'workflow_html_reporter') return 'HTML Reporter'
  return run.agent_name || 'Agent'
}
</script>

<template>
  <div
    class="workflow-agent-tabs space-y-2"
    :class="props.sticky ? 'sticky top-0 z-30 border-b border-border/70 bg-card/95 py-2 backdrop-blur' : ''"
  >
    <div class="flex items-center justify-between gap-2">
      <div class="flex min-w-0 flex-wrap items-center gap-1">
        <div class="text-xs font-semibold text-foreground">Agent 输出</div>
        <Badge variant="outline">{{ props.eventCount }}</Badge>
        <template v-if="props.agentRuns.length > 1">
          <button
            v-for="agentRun in props.agentRuns"
            :key="agentRun.run_key"
            type="button"
            class="cursor-pointer"
            @click="emit('select-agent-run', agentRun.run_key)"
          >
            <Badge :variant="props.selectedAgentRunKey === agentRun.run_key ? 'default' : 'outline'">
              {{ agentRunLabel(agentRun) }}
            </Badge>
          </button>
        </template>
      </div>
      <Button
        variant="outline"
        size="sm"
        :disabled="props.eventsLoading || props.agentRunsLoading"
        @click="emit('refresh')"
      >
        {{ props.eventsLoading || props.agentRunsLoading ? '刷新中' : '刷新' }}
      </Button>
      <Button
        v-if="props.workflowRunId && !terminalWorkflowStatuses.includes(props.workflowRunStatus)"
        variant="outline"
        size="sm"
        class="text-destructive"
        :disabled="!workflowRunCanStop"
        @click="stopWorkflowRun"
      >
        {{ workflowRunStopping ? '停止中' : '立即停止' }}
      </Button>
    </div>

    <div v-if="stopError" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      停止失败：{{ stopError }}
    </div>
    <div v-if="props.detailError" class="rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning-soft-fg">
      事件刷新暂时不可用，批量运行仍会继续：{{ props.detailError }}
    </div>
  </div>
</template>
