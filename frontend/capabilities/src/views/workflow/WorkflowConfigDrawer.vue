<script lang="ts">
export type WorkflowConfigDrawerMode = 'overlay' | 'fullscreen'

export interface WorkflowConfigDrawerState {
  open: boolean
  mode: WorkflowConfigDrawerMode
}

export function createWorkflowDrawerState(mode: WorkflowConfigDrawerMode = 'overlay'): WorkflowConfigDrawerState {
  return { open: true, mode }
}

export function toggleDrawerFullscreen(state: WorkflowConfigDrawerState): WorkflowConfigDrawerState {
  state.mode = state.mode === 'fullscreen' ? 'overlay' : 'fullscreen'
  state.open = true
  return state
}

export function closeDrawer(state: WorkflowConfigDrawerState): WorkflowConfigDrawerState {
  state.open = false
  return state
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import { Maximize2, Minimize2, X } from 'lucide-vue-next'
import { Button } from '../../components/ui/button'

type DrawerMode = 'overlay' | 'fullscreen'

const props = defineProps<{
  open: boolean
  mode: DrawerMode
  title: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  'update:mode': [value: DrawerMode]
}>()

const fullscreen = computed(() => props.mode === 'fullscreen')

function close() {
  emit('update:open', false)
}

function toggleFullscreen() {
  emit('update:mode', fullscreen.value ? 'overlay' : 'fullscreen')
}
</script>

<template>
  <aside
    v-if="open"
    class="workflow-config-drawer border-l bg-background shadow-xl"
    :class="fullscreen ? 'workflow-config-drawer--fullscreen' : 'workflow-config-drawer--overlay'"
    aria-label="工作流配置"
  >
    <header class="flex h-12 shrink-0 items-center justify-between gap-2 border-b px-3">
      <div class="min-w-0 truncate text-sm font-semibold">{{ title }}</div>
      <div class="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          type="button"
          :title="fullscreen ? '退出全屏' : '全屏配置'"
          @click="toggleFullscreen"
        >
          <Minimize2 v-if="fullscreen" class="h-4 w-4" />
          <Maximize2 v-else class="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon-sm" type="button" title="关闭配置" @click="close">
          <X class="h-4 w-4" />
        </Button>
      </div>
    </header>
    <div class="min-h-0 flex-1 overflow-auto">
      <slot />
    </div>
  </aside>
</template>

<style scoped>
.workflow-config-drawer {
  position: absolute;
  z-index: 20;
  display: flex;
  flex-direction: column;
}

.workflow-config-drawer--overlay {
  inset-block: 0;
  inset-inline-end: 0;
  width: min(560px, 52vw);
  max-width: 100%;
}

.workflow-config-drawer--fullscreen {
  inset: 0;
  width: auto;
  border-left-width: 0;
}

@media (max-width: 1024px) {
  .workflow-config-drawer {
    inset: 0;
    width: auto;
    border-left-width: 0;
  }
}
</style>
