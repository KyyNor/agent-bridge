<script setup lang="ts">
import { defineAsyncComponent, ref, computed, onUnmounted, type Component } from 'vue'
import AppShell from './components/AppShell.vue'
import type { NavGroup } from './components/AppShell.vue'
import AppErrorBoundary from './components/AppErrorBoundary.vue'
import AsyncViewError from './components/AsyncViewError.vue'
import PageHeader from './components/PageHeader.vue'
import ConfirmDialog from './components/ui/dialog/ConfirmDialog.vue'
import { ErrorState, LoadingState } from './components/ui/feedback'
import { ToastViewport } from './components/ui/toast'
import {
  canNavigate,
  currentHash,
  installNavigationController,
  navigateTo,
  normalizeHash,
  shouldShowPageHeader,
} from './lib/navigation'

function asyncView(loader: () => Promise<Component>) {
  return defineAsyncComponent({
    loader,
    loadingComponent: LoadingState,
    errorComponent: AsyncViewError,
    delay: 150,
    timeout: 20_000,
    onError(error, retry, fail, attempts) {
      if (attempts === 1) retry()
      else fail()
    },
  })
}

const DashboardView = asyncView(() => import('./views/dashboard/DashboardView.vue'))
const ServicesView = asyncView(() => import('./views/capabilities/ServicesView.vue'))
const ToolsView = asyncView(() => import('./views/capabilities/ToolsView.vue'))
const ProfilesView = asyncView(() => import('./views/capabilities/ProfilesView.vue'))
const ToolDebugView = asyncView(() => import('./views/capabilities/ToolDebugView.vue'))
const CodeRepoView = asyncView(() => import('./views/knowledge/CodeRepoView.vue'))
const KnowledgeView = asyncView(() => import('./views/knowledge/KnowledgeView.vue'))
const KnowledgeProcessingConfigView = asyncView(() => import('./views/knowledge/KnowledgeProcessingConfigView.vue'))
const ModelEvaluationView = asyncView(() => import('./views/system/ModelEvaluationView.vue'))
const AccessControlView = asyncView(() => import('./views/system/AccessControlView.vue'))
const MemoryView = asyncView(() => import('./views/knowledge/MemoryView.vue'))
const BusinessLedgerView = asyncView(() => import('./views/knowledge/BusinessLedgerView.vue'))
const WorkflowView = asyncView(() => import('./views/workflow/WorkflowView.vue'))
const SkillManagementView = asyncView(() => import('./views/system/SkillManagementView.vue'))
const ScriptsView = asyncView(() => import('./views/system/ScriptsView.vue'))
const LogsView = asyncView(() => import('./views/monitoring/LogsView.vue'))
const StatsView = asyncView(() => import('./views/monitoring/StatsView.vue'))
const AgentRunsView = asyncView(() => import('./views/monitoring/AgentRunsView.vue'))

const routeIndexKey = '__agent_bridge_route_index'
const initialHash = currentHash()
const initialIndex = typeof window.history.state?.[routeIndexKey] === 'number'
  ? window.history.state[routeIndexKey] as number
  : 0
const hash = ref(initialHash)
let committedHash = initialHash
let currentHistoryIndex = initialIndex
let transitionInFlight = false
let restoringCanceledPop = false

if (window.history.state?.[routeIndexKey] !== initialIndex) {
  window.history.replaceState(
    { ...(window.history.state || {}), [routeIndexKey]: initialIndex },
    '',
    `#${initialHash}`,
  )
}

function commitRoute(target: string, historyIndex?: number) {
  committedHash = normalizeHash(target)
  if (historyIndex !== undefined) currentHistoryIndex = historyIndex
  hash.value = committedHash
}

const removeNavigationController = installNavigationController(async (target, options = {}) => {
  const nextHash = normalizeHash(target)
  if (nextHash === committedHash || transitionInFlight) return nextHash === committedHash

  transitionInFlight = true
  try {
    if (!await canNavigate(nextHash, committedHash)) return false

    if (options.replace) {
      window.history.replaceState(
        { ...(window.history.state || {}), [routeIndexKey]: currentHistoryIndex },
        '',
        `#${nextHash}`,
      )
      commitRoute(nextHash, currentHistoryIndex)
    } else {
      const nextIndex = currentHistoryIndex + 1
      window.history.pushState(
        { ...(window.history.state || {}), [routeIndexKey]: nextIndex },
        '',
        `#${nextHash}`,
      )
      commitRoute(nextHash, nextIndex)
    }
    return true
  } finally {
    transitionInFlight = false
  }
})

async function handlePopState() {
  const nextHash = currentHash()
  const nextIndex = typeof window.history.state?.[routeIndexKey] === 'number'
    ? window.history.state[routeIndexKey] as number
    : undefined

  if (restoringCanceledPop) {
    restoringCanceledPop = false
    commitRoute(nextHash, nextIndex)
    return
  }
  if (nextHash === committedHash) {
    if (nextIndex !== undefined) currentHistoryIndex = nextIndex
    return
  }
  if (transitionInFlight) return

  transitionInFlight = true
  try {
    if (await canNavigate(nextHash, committedHash)) {
      commitRoute(nextHash, nextIndex)
      return
    }

    restoringCanceledPop = true
    if (nextIndex !== undefined) {
      window.history.go(currentHistoryIndex - nextIndex)
    } else {
      window.history.forward()
    }
  } finally {
    transitionInFlight = false
  }
}

async function handleHashChange() {
  const nextHash = currentHash()
  if (nextHash === committedHash || transitionInFlight) return

  transitionInFlight = true
  try {
    if (await canNavigate(nextHash, committedHash)) {
      commitRoute(nextHash)
      return
    }

    // A direct fragment navigation has already created a history entry.  Go
    // back to the committed route when its leave guard rejects the change.
    restoringCanceledPop = true
    window.history.back()
  } finally {
    transitionInFlight = false
  }
}

window.addEventListener('popstate', handlePopState)
window.addEventListener('hashchange', handleHashChange)
onUnmounted(() => {
  removeNavigationController()
  window.removeEventListener('popstate', handlePopState)
  window.removeEventListener('hashchange', handleHashChange)
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
      { key: 'business-ledgers', label: '业务台账', description: '管理由 Excel 维护、可受控查询的业务数据' },
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

const currentNav = computed(() => navGroups.flatMap(g => g.items).find(i => i.key === activeNavKey.value))
const view = computed(() => activeNavKey.value)
</script>

<template>
  <AppShell :nav-groups="navGroups" :active="activeNavKey">
    <div class="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-scroll">
      <!-- Page Header（标题取自导航配置；操作/筛选由各视图 Teleport 进 #ph-actions / #ph-filters） -->
      <PageHeader
        v-if="showPageHeader"
        :title="currentNav?.label || 'Agent Bridge'"
      />
      <!-- Content -->
      <div class="min-w-0 p-7">
        <AppErrorBoundary :reset-key="hash">
          <DashboardView v-if="view === 'dashboard'" />
          <ServicesView v-else-if="view === 'services'" :route-key="subRoute" />
          <ToolsView v-else-if="view === 'tools'" />
          <ProfilesView v-else-if="view === 'profiles'" :route-key="subRoute" />
          <ToolDebugView v-else-if="view === 'tool-debug'" />
          <CodeRepoView v-else-if="view === 'code-repos'" :route-key="subRoute" />
          <KnowledgeView v-else-if="view === 'knowledge'" :route-key="subRoute" />
          <MemoryView v-else-if="view === 'memory'" :route-key="subRoute" />
          <BusinessLedgerView v-else-if="view === 'business-ledgers'" :route-key="subRoute" />
          <KnowledgeProcessingConfigView v-else-if="view === 'system-config'" />
          <AccessControlView v-else-if="view === 'access-control'" />
          <ModelEvaluationView v-else-if="view === 'model-evaluations'" />
          <SkillManagementView v-else-if="view === 'skills'" />
          <ScriptsView v-else-if="view === 'scripts'" :route-key="subRoute" />
          <WorkflowView v-else-if="view === 'workflow'" :route-key="subRoute" />
          <LogsView v-else-if="view === 'logs'" />
          <AgentRunsView v-else-if="view === 'agent-runs'" :route-key="subRoute" />
          <StatsView v-else-if="view === 'stats'" />
          <ErrorState
            v-else
            title="页面不存在"
            :description="`找不到 “${activeNavKey}” 对应的页面。`"
            action-label="返回平台概览"
            @action="navigateTo('dashboard', { replace: true })"
          />
        </AppErrorBoundary>
      </div>
    </div>
    <ConfirmDialog />
    <ToastViewport />
  </AppShell>
</template>
