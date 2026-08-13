<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import AppShell from './components/AppShell.vue'
import AdminAccessControl from './components/AdminAccessControl.vue'
import type { NavGroup } from './components/AppShell.vue'
import AppErrorBoundary from './components/AppErrorBoundary.vue'
import PageHeader from './components/PageHeader.vue'
import SidebarTourButton from './components/SidebarTourButton.vue'
import ConfirmDialog from './components/ui/dialog/ConfirmDialog.vue'
import { ToastViewport } from './components/ui/toast'
import { hasSubRoute, type NavigationMeta } from './router'

const route = useRoute()
const router = useRouter()

// 一级入口不显示分组标题；其余项目沿用现有的信息架构分组。
const navGroups: NavGroup[] = [
  {
    items: [
      { key: 'dashboard', label: '平台概览', description: '查看平台运行状态和关键指标' },
      { key: 'profiles', label: '知识平面', description: '管理能力访问策略和权限' },
    ],
  },
  {
    label: '知识管理',
    items: [
      { key: 'knowledge', label: '文档知识', description: '管理文档知识库和文档' },
      { key: 'code-repos', label: '代码知识', description: '管理代码仓库和知识图谱' },
      { key: 'memory', label: '记忆区块', description: '管理 profile 绑定的 claude-mem 记忆区块' },
      { key: 'business-ledgers', label: '业务台账', description: '管理由 Excel 维护、可受控查询的业务数据' },
      { key: 'services', label: '能力接入', description: '管理和配置 MCP/OpenAPI 服务连接' },
    ],
  },
  { label: '自动化', items: [{ key: 'workflow', label: '工作流管理', description: '管理和编排知识工作流' }] },
  {
    label: '调用观测',
    items: [
      { key: 'logs', label: '调用日志', description: '查看和分析工具调用记录' },
      { key: 'agent-runs', label: 'Agent 运行', description: '查看 Agent 运行记录、Prompt 和事件流' },
      { key: 'stats', label: '调用统计', description: '查看工具调用统计和趋势' },
    ],
  },
  {
    label: '系统管理',
    items: [
      { key: 'system-config', label: '系统管理', description: '配置调度计划、公共模型、仓库分类和知识后端' },
      { key: 'access-control', label: '小组权限', description: '维护用户与数据小组映射' },
      { key: 'model-evaluations', label: '模型评估', description: '以 Docker 隔离执行通用、数学、指令、代码和 Agent 评测' },
      { key: 'skills', label: 'Skill 管理', description: '维护内置技能提示词' },
      { key: 'scripts', label: '脚本管理', description: '管理受控脚本并在线测试运行' },
    ],
  },
]

const navMeta = computed(() => route.meta as NavigationMeta)
const activeNavKey = computed(() => navMeta.value.navKey || '')
const showPageHeader = computed(() => !navMeta.value.hideHeaderOnSubRoute || !hasSubRoute(route))

function returnToParent() {
  if (navMeta.value.backTo) void router.push(navMeta.value.backTo)
}
</script>

<template>
  <AppShell :nav-groups="navGroups" :active="activeNavKey">
    <template #footer>
      <div class="space-y-2">
        <SidebarTourButton />
        <AdminAccessControl />
      </div>
    </template>
    <div class="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-scroll">
      <PageHeader
        v-if="showPageHeader"
        :title="navMeta.title || 'Agent Bridge'"
        :show-back="Boolean(navMeta.backTo)"
        @back="returnToParent"
      />
      <div class="min-w-0 p-7">
        <RouterView v-slot="{ Component }">
          <AppErrorBoundary :reset-key="route.fullPath">
            <component :is="Component" />
          </AppErrorBoundary>
        </RouterView>
      </div>
    </div>
    <ConfirmDialog />
    <ToastViewport />
  </AppShell>
</template>
