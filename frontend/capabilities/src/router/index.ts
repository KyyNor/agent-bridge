import { defineAsyncComponent, type Component } from 'vue'
import { createRouter, createWebHistory, type RouteLocationNormalized, type RouteRecordRaw } from 'vue-router'
import { canLeaveRoute } from './guards'
import AsyncViewError from '@/components/AsyncViewError.vue'
import { LoadingState } from '@/components/ui/feedback'

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

const DashboardView = asyncView(() => import('@/views/dashboard/DashboardView.vue'))
const ServicesView = asyncView(() => import('@/views/capabilities/ServicesView.vue'))
const ToolsView = asyncView(() => import('@/views/capabilities/ToolsView.vue'))
const ProfilesView = asyncView(() => import('@/views/capabilities/ProfilesView.vue'))
const ToolDebugView = asyncView(() => import('@/views/capabilities/ToolDebugView.vue'))
const CodeRepoView = asyncView(() => import('@/views/knowledge/CodeRepoView.vue'))
const KnowledgeView = asyncView(() => import('@/views/knowledge/KnowledgeView.vue'))
const KnowledgeProcessingConfigView = asyncView(() => import('@/views/knowledge/KnowledgeProcessingConfigView.vue'))
const ModelEvaluationView = asyncView(() => import('@/views/system/ModelEvaluationView.vue'))
const AccessControlView = asyncView(() => import('@/views/system/AccessControlView.vue'))
const MemoryView = asyncView(() => import('@/views/knowledge/MemoryView.vue'))
const BusinessLedgerView = asyncView(() => import('@/views/knowledge/BusinessLedgerView.vue'))
const WorkflowView = asyncView(() => import('@/views/workflow/WorkflowView.vue'))
const SkillManagementView = asyncView(() => import('@/views/system/SkillManagementView.vue'))
const ScriptsView = asyncView(() => import('@/views/system/ScriptsView.vue'))
const LogsView = asyncView(() => import('@/views/monitoring/LogsView.vue'))
const StatsView = asyncView(() => import('@/views/monitoring/StatsView.vue'))
const AgentRunsView = asyncView(() => import('@/views/monitoring/AgentRunsView.vue'))
const NotFoundView = asyncView(() => import('@/views/NotFoundView.vue'))

export interface NavigationMeta {
  navKey?: string
  title?: string
  hideHeaderOnSubRoute?: boolean
  backTo?: string
}

function subRoute(route: RouteLocationNormalized): string {
  const segments = route.params.routeKey
  const path = Array.isArray(segments) ? segments.join('/') : String(segments || '')
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(route.query)) {
    if (Array.isArray(value)) value.forEach(item => { if (item != null) query.append(key, item) })
    else if (value != null) query.set(key, value)
  }
  const queryString = query.toString()
  return queryString ? `${path}?${queryString}` : path
}

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: { name: 'dashboard' } },
  { path: '/dashboard', name: 'dashboard', component: DashboardView, meta: { navKey: 'dashboard', title: '平台概览' } },
  { path: '/services/:routeKey(.*)*', name: 'services', component: ServicesView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'services', title: '能力接入', hideHeaderOnSubRoute: true } },
  { path: '/tools', name: 'tools', component: ToolsView, meta: { navKey: 'tools', title: '工具目录', backTo: '/services' } },
  { path: '/profiles/:routeKey(.*)*', name: 'profiles', component: ProfilesView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'profiles', title: '知识平面', hideHeaderOnSubRoute: true } },
  { path: '/tool-debug', name: 'tool-debug', component: ToolDebugView, meta: { navKey: 'tool-debug', title: '工具调试', backTo: '/services' } },
  { path: '/knowledge/:routeKey(.*)*', name: 'knowledge', component: KnowledgeView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'knowledge', title: '文档知识', hideHeaderOnSubRoute: true } },
  { path: '/code-repos/:routeKey(.*)*', name: 'code-repos', component: CodeRepoView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'code-repos', title: '代码知识', hideHeaderOnSubRoute: true } },
  { path: '/memory/:routeKey(.*)*', name: 'memory', component: MemoryView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'memory', title: '记忆区块', hideHeaderOnSubRoute: true } },
  { path: '/business-ledgers/:routeKey(.*)*', name: 'business-ledgers', component: BusinessLedgerView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'business-ledgers', title: '业务台账', hideHeaderOnSubRoute: true } },
  { path: '/workflow/:routeKey(.*)*', name: 'workflow', component: WorkflowView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'workflow', title: '工作流管理', hideHeaderOnSubRoute: true } },
  { path: '/logs', name: 'logs', component: LogsView, meta: { navKey: 'logs', title: '调用日志' } },
  { path: '/agent-runs/:routeKey(.*)*', name: 'agent-runs', component: AgentRunsView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'agent-runs', title: 'Agent 运行', hideHeaderOnSubRoute: true } },
  { path: '/stats', name: 'stats', component: StatsView, meta: { navKey: 'stats', title: '调用统计' } },
  { path: '/system-config', name: 'system-config', component: KnowledgeProcessingConfigView, meta: { navKey: 'system-config', title: '系统管理' } },
  { path: '/access-control/:routeKey(.*)*', name: 'access-control', component: AccessControlView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'access-control', title: '小组权限', hideHeaderOnSubRoute: true } },
  { path: '/model-evaluations', name: 'model-evaluations', component: ModelEvaluationView, meta: { navKey: 'model-evaluations', title: '模型评估' } },
  { path: '/skills', name: 'skills', component: SkillManagementView, meta: { navKey: 'skills', title: 'Skill 管理' } },
  { path: '/scripts/:routeKey(.*)*', name: 'scripts', component: ScriptsView, props: route => ({ routeKey: subRoute(route) }), meta: { navKey: 'scripts', title: '脚本管理', hideHeaderOnSubRoute: true } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView, meta: { title: '页面不存在' } },
]

export function hasSubRoute(route: RouteLocationNormalized): boolean {
  const value = route.params.routeKey
  return Array.isArray(value) ? value.length > 0 : Boolean(value)
}

export const router = createRouter({
  history: createWebHistory('/agent-bridge/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to, from) => canLeaveRoute(to, from))

export { routes, subRoute }
