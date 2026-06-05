<script setup lang="ts">
import { ref, computed } from 'vue'
import AppShell from './components/AppShell.vue'
import ServicesView from './views/ServicesView.vue'
import ToolsView from './views/ToolsView.vue'
import ProfilesView from './views/ProfilesView.vue'
import LogsView from './views/LogsView.vue'
import StatsView from './views/StatsView.vue'
import BuiltinsView from './views/BuiltinsView.vue'
import ClaudeConfigView from './views/ClaudeConfigView.vue'

const hash = ref(window.location.hash.slice(1) || 'catalog')
window.addEventListener('hashchange', () => {
  hash.value = window.location.hash.slice(1) || 'catalog'
})

const view = computed(() => {
  const m: Record<string, string> = {
    catalog: 'services',
    services: 'services',
    tools: 'tools',
    profiles: 'profiles',
    logs: 'logs',
    stats: 'stats',
    builtins: 'builtins',
    claude: 'claude',
  }
  return m[hash.value] || 'services'
})

const navItems = [
  { key: 'catalog', label: 'Catalog' },
  { key: 'services', label: 'Services' },
  { key: 'tools', label: 'Tools' },
  { key: 'profiles', label: 'Profiles' },
  { key: 'logs', label: 'Logs' },
  { key: 'stats', label: 'Stats' },
  { key: 'builtins', label: 'Built-ins' },
  { key: 'claude', label: 'Claude Config' },
]
</script>

<template>
  <AppShell :nav-items="navItems" :active="hash">
    <ServicesView v-if="view === 'services'" />
    <ToolsView v-else-if="view === 'tools'" />
    <ProfilesView v-else-if="view === 'profiles'" />
    <LogsView v-else-if="view === 'logs'" />
    <StatsView v-else-if="view === 'stats'" />
    <BuiltinsView v-else-if="view === 'builtins'" />
    <ClaudeConfigView v-else-if="view === 'claude'" />
  </AppShell>
</template>
