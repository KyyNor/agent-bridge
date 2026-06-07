<script setup lang="ts">
import { ref, computed } from 'vue'
import AppShell from './components/AppShell.vue'
import type { NavGroup } from './components/AppShell.vue'
import DashboardView from './views/DashboardView.vue'
import ServicesView from './views/ServicesView.vue'
import ToolsView from './views/ToolsView.vue'
import ProfilesView from './views/ProfilesView.vue'
import CodeRepoView from './views/CodeRepoView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import KnowledgeProcessingConfigView from './views/KnowledgeProcessingConfigView.vue'
import LogsView from './views/LogsView.vue'
import StatsView from './views/StatsView.vue'

const hash = ref(window.location.hash.slice(1) || 'dashboard')
window.addEventListener('hashchange', () => {
  hash.value = window.location.hash.slice(1) || 'dashboard'
})

const navGroups: NavGroup[] = [
  {
    label: '总览',
    items: [
      { key: 'dashboard', label: '平台概览', description: '查看平台运行状态和关键指标' },
    ],
  },
  {
    label: '能力治理',
    items: [
      { key: 'services', label: '能力接入', description: '管理和配置 MCP 服务连接' },
      { key: 'tools', label: '工具目录', description: '浏览和配置已同步的工具' },
      { key: 'profiles', label: '能力平面', description: '管理能力访问策略和权限' },
    ],
  },
  {
    label: '知识管理',
    items: [
      { key: 'knowledge', label: '文档知识', description: '管理文档知识库和文档' },
      { key: 'code-repos', label: '代码知识', description: '管理代码仓库和知识图谱' },
      { key: 'knowledge-config', label: '知识处理配置', description: '配置同步计划和仓库分类' },
    ],
  },
  {
    label: '调用观测',
    items: [
      { key: 'logs', label: '调用日志', description: '查看和分析工具调用记录' },
      { key: 'stats', label: '调用统计', description: '查看工具调用统计和趋势' },
    ],
  },
]

const currentNav = computed(() => navGroups.flatMap(g => g.items).find(i => i.key === hash.value))
const view = computed(() => hash.value)
</script>

<template>
  <AppShell :nav-groups="navGroups" :active="hash">
    <div class="flex-1 overflow-y-auto">
      <!-- Page Header -->
      <div class="bg-card px-7 py-5">
        <h1 class="text-base font-semibold text-foreground">{{ currentNav?.label || 'Agent Bridge' }}</h1>
        <p v-if="currentNav?.description" class="mt-0.5 text-[13px] text-muted-foreground">{{ currentNav.description }}</p>
      </div>
      <!-- Content -->
      <div class="p-7">
        <DashboardView v-if="view === 'dashboard'" />
        <ServicesView v-else-if="view === 'services'" />
        <ToolsView v-else-if="view === 'tools'" />
        <ProfilesView v-else-if="view === 'profiles'" />
        <CodeRepoView v-else-if="view === 'code-repos'" />
        <KnowledgeView v-else-if="view === 'knowledge'" />
        <KnowledgeProcessingConfigView v-else-if="view === 'knowledge-config'" />
        <LogsView v-else-if="view === 'logs'" />
        <StatsView v-else-if="view === 'stats'" />
      </div>
    </div>
  </AppShell>
</template>
