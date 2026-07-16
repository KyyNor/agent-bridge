<script setup lang="ts">
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
}>(), {
  detailError: '',
  sticky: true,
})

const emit = defineEmits<{
  (event: 'select-agent-run', runKey: string): void
  (event: 'refresh'): void
}>()

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
    </div>

    <div v-if="props.detailError" class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
      事件刷新暂时不可用，批量运行仍会继续：{{ props.detailError }}
    </div>
  </div>
</template>
