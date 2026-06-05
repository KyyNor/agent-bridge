<script setup lang="ts">
import { ref, computed } from 'vue'
import AppShell from './components/AppShell.vue'
import type { NavGroup } from './components/AppShell.vue'
import DashboardView from './views/DashboardView.vue'
import ServicesView from './views/ServicesView.vue'
import ToolsView from './views/ToolsView.vue'
import ProfilesView from './views/ProfilesView.vue'
import LogsView from './views/LogsView.vue'
import StatsView from './views/StatsView.vue'

const hash = ref(window.location.hash.slice(1) || 'dashboard')
window.addEventListener('hashchange', () => {
  hash.value = window.location.hash.slice(1) || 'dashboard'
})

const navGroups: NavGroup[] = [
  {
    label: '总览',
    items: [{ key: 'dashboard', label: '平台概览' }],
  },
  {
    label: '能力治理',
    items: [
      { key: 'services', label: '能力接入' },
      { key: 'tools', label: '工具目录' },
      { key: 'profiles', label: '能力平面' },
    ],
  },
  {
    label: '调用观测',
    items: [
      { key: 'logs', label: '调用日志' },
      { key: 'stats', label: '调用统计' },
    ],
  },
]

const view = computed(() => hash.value)
</script>

<template>
  <AppShell :nav-groups="navGroups" :active="hash">
    <div class="border-b border-border bg-card px-8 py-5">
      <h1 class="text-xl font-semibold text-foreground">
        {{ navGroups.flatMap(g => g.items).find(i => i.key === hash)?.label || 'Agent Bridge' }}
      </h1>
    </div>
    <div class="flex-1 p-6">
      <DashboardView v-if="view === 'dashboard'" />
      <ServicesView v-else-if="view === 'services'" />
      <ToolsView v-else-if="view === 'tools'" />
      <ProfilesView v-else-if="view === 'profiles'" />
      <LogsView v-else-if="view === 'logs'" />
      <StatsView v-else-if="view === 'stats'" />
    </div>
  </AppShell>
</template>
