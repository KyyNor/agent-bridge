<script setup lang="ts">
import { computed } from 'vue'
import {
  LayoutDashboard,
  Server,
  Wrench,
  Shield,
  BookOpen,
  GitBranch,
  Cog,
  FileCheck,
  Terminal,
  Workflow,
  FileText,
  BarChart3,
  Activity,
  Bug,
  Brain,
  TableProperties,
} from '@lucide/vue'

const props = defineProps<{ name: string }>()

const icon = computed(() => {
  const map: Record<string, any> = {
    dashboard: LayoutDashboard,
    services: Server,
    tools: Wrench,
    profiles: Shield,
    'tool-debug': Bug,
    knowledge: BookOpen,
    'code-repos': GitBranch,
    'system-config': Cog,
    skills: FileCheck,
    scripts: Terminal,
    workflow: Workflow,
    logs: FileText,
    stats: BarChart3,
    'agent-runs': Activity,
    memory: Brain,
    'business-ledgers': TableProperties,
  }
  return map[props.name] ?? null
})
</script>

<template>
  <component
    :is="icon"
    v-if="icon"
    :size="18"
    :stroke-width="1.5"
    class="shrink-0"
  />
  <span v-else class="shrink-0 w-[18px] h-[18px]" />
</template>
