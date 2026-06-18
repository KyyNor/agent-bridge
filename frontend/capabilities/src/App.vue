<script setup lang="ts">
import { defineAsyncComponent, ref, computed } from 'vue'
import AppShell from './components/AppShell.vue'
import type { NavGroup } from './components/AppShell.vue'

const DashboardView = defineAsyncComponent(() => import('./views/dashboard/DashboardView.vue'))
const ServicesView = defineAsyncComponent(() => import('./views/capabilities/ServicesView.vue'))
const ToolsView = defineAsyncComponent(() => import('./views/capabilities/ToolsView.vue'))
const ProfilesView = defineAsyncComponent(() => import('./views/capabilities/ProfilesView.vue'))
const CodeRepoView = defineAsyncComponent(() => import('./views/knowledge/CodeRepoView.vue'))
const KnowledgeView = defineAsyncComponent(() => import('./views/knowledge/KnowledgeView.vue'))
const KnowledgeProcessingConfigView = defineAsyncComponent(() => import('./views/knowledge/KnowledgeProcessingConfigView.vue'))
const WorkflowView = defineAsyncComponent(() => import('./views/workflow/WorkflowView.vue'))
const SkillManagementView = defineAsyncComponent(() => import('./views/system/SkillManagementView.vue'))
const LogsView = defineAsyncComponent(() => import('./views/monitoring/LogsView.vue'))
const StatsView = defineAsyncComponent(() => import('./views/monitoring/StatsView.vue'))

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
    ],
  },
  {
    label: '工作流',
    items: [
      { key: 'workflow', label: '工作流管理', description: '管理和编排知识工作流' },
    ],
  },
  {
    label: '调用观测',
    items: [
      { key: 'logs', label: '调用日志', description: '查看和分析工具调用记录' },
      { key: 'stats', label: '调用统计', description: '查看工具调用统计和趋势' },
    ],
  },
  {
    label: '系统配置',
    items: [
      { key: 'system-config', label: '系统配置', description: '配置调度计划、仓库分类和知识后端' },
      { key: 'skills', label: 'Skill 管理', description: '维护内置技能提示词' },
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
        <KnowledgeProcessingConfigView v-else-if="view === 'system-config'" />
        <SkillManagementView v-else-if="view === 'skills'" />
        <WorkflowView v-else-if="view === 'workflow'" />
        <LogsView v-else-if="view === 'logs'" />
        <StatsView v-else-if="view === 'stats'" />
      </div>
    </div>
  </AppShell>
</template>
