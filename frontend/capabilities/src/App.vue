<script setup lang="ts">
import { defineAsyncComponent, ref, computed } from 'vue'
import AppShell from './components/AppShell.vue'
import type { NavGroup } from './components/AppShell.vue'
import PageHeader from './components/PageHeader.vue'
import ConfirmDialog from './components/ui/dialog/ConfirmDialog.vue'
import { shouldShowPageHeader } from './lib/navigation'

const DashboardView = defineAsyncComponent(() => import('./views/dashboard/DashboardView.vue'))
const ServicesView = defineAsyncComponent(() => import('./views/capabilities/ServicesView.vue'))
const ToolsView = defineAsyncComponent(() => import('./views/capabilities/ToolsView.vue'))
const ProfilesView = defineAsyncComponent(() => import('./views/capabilities/ProfilesView.vue'))
const ToolDebugView = defineAsyncComponent(() => import('./views/capabilities/ToolDebugView.vue'))
const CodeRepoView = defineAsyncComponent(() => import('./views/knowledge/CodeRepoView.vue'))
const KnowledgeView = defineAsyncComponent(() => import('./views/knowledge/KnowledgeView.vue'))
const KnowledgeProcessingConfigView = defineAsyncComponent(() => import('./views/knowledge/KnowledgeProcessingConfigView.vue'))
const MemoryView = defineAsyncComponent(() => import('./views/knowledge/MemoryView.vue'))
const WorkflowView = defineAsyncComponent(() => import('./views/workflow/WorkflowView.vue'))
const SkillManagementView = defineAsyncComponent(() => import('./views/system/SkillManagementView.vue'))
const ScriptsView = defineAsyncComponent(() => import('./views/system/ScriptsView.vue'))
const LogsView = defineAsyncComponent(() => import('./views/monitoring/LogsView.vue'))
const StatsView = defineAsyncComponent(() => import('./views/monitoring/StatsView.vue'))
const AgentRunsView = defineAsyncComponent(() => import('./views/monitoring/AgentRunsView.vue'))

const hash = ref(window.location.hash.slice(1) || 'dashboard')
window.addEventListener('hashchange', () => {
  hash.value = window.location.hash.slice(1) || 'dashboard'
})

// 复合 hash 支持（如 #scripts/<key>）：取首个段为顶级 nav key，剩余为子路由参数
const routeSegments = computed(() => hash.value.split('/'))
const activeNavKey = computed(() => routeSegments.value[0] || 'dashboard')
const subRoute = computed(() => routeSegments.value.slice(1).join('/'))
const showPageHeader = computed(() => shouldShowPageHeader(activeNavKey.value, subRoute.value))

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
      { key: 'services', label: '能力接入', description: '管理和配置 MCP/OpenAPI 服务连接' },
      { key: 'tools', label: '工具目录', description: '浏览和配置已同步的工具' },
      { key: 'profiles', label: '能力平面', description: '管理能力访问策略和权限' },
      { key: 'tool-debug', label: '工具调试', description: '按能力平面选择并手动调试对外提供的工具' },
    ],
  },
  {
    label: '知识管理',
    items: [
      { key: 'knowledge', label: '文档知识', description: '管理文档知识库和文档' },
      { key: 'code-repos', label: '代码知识', description: '管理代码仓库和知识图谱' },
      { key: 'memory', label: '记忆区块', description: '管理 profile 绑定的 claude-mem 记忆区块' },
    ],
  },
  {
    label: '自动化',
    items: [
      { key: 'workflow', label: '工作流管理', description: '管理和编排知识工作流' },
    ],
  },
  {
    label: '调用观测',
      items: [
        { key: 'logs', label: '调用日志', description: '查看和分析工具调用记录' },
        { key: 'agent-runs', label: 'Agent 运行', description: '查看 Agent 运行记录、Prompt 和事件流' },
        { key: 'stats', label: '调用统计', description: '查看工具调用统计和趋势' },
      ],
  },
  {
    label: '系统配置',
    items: [
      { key: 'system-config', label: '系统配置', description: '配置调度计划、仓库分类和知识后端' },
      { key: 'skills', label: 'Skill 管理', description: '维护内置技能提示词' },
      { key: 'scripts', label: '脚本管理', description: '管理受控脚本并在线测试运行' },
    ],
  },
]

const currentNav = computed(() => navGroups.flatMap(g => g.items).find(i => i.key === activeNavKey.value))
const view = computed(() => activeNavKey.value)
</script>

<template>
  <AppShell :nav-groups="navGroups" :active="activeNavKey">
    <div class="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
      <!-- Page Header（标题取自导航配置；操作/筛选由各视图 Teleport 进 #ph-actions / #ph-filters） -->
      <PageHeader
        v-if="showPageHeader"
        :title="currentNav?.label || 'Agent Bridge'"
        :description="currentNav?.description"
      />
      <!-- Content -->
      <div class="min-w-0 p-7">
        <DashboardView v-if="view === 'dashboard'" />
        <ServicesView v-else-if="view === 'services'" :route-key="subRoute" />
        <ToolsView v-else-if="view === 'tools'" />
        <ProfilesView v-else-if="view === 'profiles'" :route-key="subRoute" />
        <ToolDebugView v-else-if="view === 'tool-debug'" />
        <CodeRepoView v-else-if="view === 'code-repos'" :route-key="subRoute" />
        <KnowledgeView v-else-if="view === 'knowledge'" :route-key="subRoute" />
        <MemoryView v-else-if="view === 'memory'" :route-key="subRoute" />
        <KnowledgeProcessingConfigView v-else-if="view === 'system-config'" />
        <SkillManagementView v-else-if="view === 'skills'" />
        <ScriptsView v-else-if="view === 'scripts'" :route-key="subRoute" />
        <WorkflowView v-else-if="view === 'workflow'" :route-key="subRoute" />
        <LogsView v-else-if="view === 'logs'" />
        <AgentRunsView v-else-if="view === 'agent-runs'" :route-key="subRoute" />
        <StatsView v-else-if="view === 'stats'" />
      </div>
    </div>
    <ConfirmDialog />
  </AppShell>
</template>
