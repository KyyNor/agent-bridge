<script setup lang="ts">
import { Bot, ClipboardList, FileOutput, FileTerminal } from 'lucide-vue-next'
import type { WorkflowNodeType } from '../../api/types'

const emit = defineEmits<{ addNode: [type: WorkflowNodeType] }>()
const entries: Array<{ type: WorkflowNodeType; label: string; icon: typeof Bot }> = [
  { type: 'get_task', label: '获取任务', icon: ClipboardList },
  { type: 'agent', label: 'Agent', icon: Bot },
  { type: 'script', label: '托管脚本', icon: FileTerminal },
  { type: 'output', label: '输出结果', icon: FileOutput },
]

function dragStart(event: DragEvent, type: WorkflowNodeType) {
  event.dataTransfer?.setData('application/agent-bridge-workflow-node', type)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
</script>

<template>
  <aside class="border-r bg-muted/20 p-3">
    <div class="mb-3 text-xs font-semibold text-muted-foreground">节点</div>
    <div class="grid gap-2">
      <button v-for="entry in entries" :key="entry.type" type="button" draggable="true" class="flex h-9 items-center gap-2 rounded-sm border bg-background px-2 text-left text-sm hover:bg-muted" @click="emit('addNode', entry.type)" @dragstart="dragStart($event, entry.type)">
        <component :is="entry.icon" class="h-4 w-4 text-muted-foreground" />
        <span>{{ entry.label }}</span>
      </button>
    </div>
  </aside>
</template>
