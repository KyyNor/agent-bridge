<script setup lang="ts">
import { Bot, ClipboardList, FileOutput, FileTerminal } from 'lucide-vue-next'
import type { WorkflowNodeType } from '../../api/types'

withDefaults(defineProps<{
  /** 摆放方向：vertical=左栏竖排（默认，向后兼容），horizontal=顶部横排。 */
  orientation?: 'vertical' | 'horizontal'
}>(), { orientation: 'vertical' })

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
  <aside
    class="bg-muted/20 p-3"
    :class="orientation === 'horizontal'
      ? 'flex flex-wrap items-center gap-2 border-b'
      : 'grid gap-2 border-r'"
  >
    <div
      class="text-xs font-semibold text-muted-foreground"
      :class="orientation === 'horizontal' ? 'mr-1 self-center' : 'mb-3'"
    >节点</div>
    <button
      v-for="entry in entries"
      :key="entry.type"
      type="button"
      draggable="true"
      class="flex items-center gap-2 rounded-sm border bg-background px-2 text-left text-sm transition-colors hover:bg-muted"
      :class="orientation === 'horizontal' ? 'h-8' : 'h-9'"
      @click="emit('addNode', entry.type)"
      @dragstart="dragStart($event, entry.type)"
    >
      <component :is="entry.icon" class="h-4 w-4 text-muted-foreground" />
      <span>{{ entry.label }}</span>
    </button>
  </aside>
</template>
