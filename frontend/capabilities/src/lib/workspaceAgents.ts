/**
 * Agent 工作台的共用展示逻辑：Coding Agent 目录、入口 URL 前缀与运行状态文案。
 *
 * 两个工作台页面（进入页与伪全屏页）都要按所选 Agent 渲染标题、判断运行状态，
 * 这里集中一份，避免各页面各写一套前缀字符串与状态文案。
 */

import type { WorkspaceAgent, WorkspaceRuntimeStatusKind, WorkspaceScope } from '../api/types'

/** 未显式选择时的默认 Coding Agent；与后端 `/agent-workspace/*` 的默认值一致。 */
export const DEFAULT_WORKSPACE_AGENT: WorkspaceAgent = 'dsh'

export const WORKSPACE_AGENTS: WorkspaceAgent[] = ['dsh', 'pi']

/** Agent 目录的兜底展示名：后端 `/agent-workspace/agents` 不可用时使用。 */
export const WORKSPACE_AGENT_LABELS: Record<WorkspaceAgent, string> = {
  dsh: 'DeepSeek Harness',
  pi: 'Pi Agent',
}

/**
 * 浏览器入口前缀（含尾斜杠）。与后端代理前缀一一对应：进入授权返回的
 * `workspace_url` 是权威值，本表只在授权完成前占位与路由解析时兜底。
 */
const WORKSPACE_ENTRY_URLS: Record<WorkspaceAgent, Record<WorkspaceScope, string>> = {
  dsh: { personal: '/agent-workspace/', shared: '/agent-workspace-shared/' },
  pi: { personal: '/agent-workspace-pi/', shared: '/agent-workspace-pi-shared/' },
}

export const WORKSPACE_STATUS_LABELS: Record<WorkspaceRuntimeStatusKind, string> = {
  running: '运行中',
  starting: '启动中',
  unhealthy: '异常',
  stopped: '未运行',
  unassigned: '未分配小组',
  unconfigured: '未配置',
}

/** 归一化外部输入的 Agent 标识；未知值回落为默认 Agent。 */
export function normalizeWorkspaceAgent(value: unknown): WorkspaceAgent {
  return value === 'pi' ? 'pi' : DEFAULT_WORKSPACE_AGENT
}

/** 归一化工作空间范围；未知值回落为 personal。 */
export function normalizeWorkspaceScope(value: unknown): WorkspaceScope {
  return value === 'shared' ? 'shared' : 'personal'
}

/** Agent 的展示名。 */
export function workspaceAgentLabel(agent: WorkspaceAgent): string {
  return WORKSPACE_AGENT_LABELS[agent] || WORKSPACE_AGENT_LABELS[DEFAULT_WORKSPACE_AGENT]
}

/** 工作台浏览器入口前缀（伪全屏 iframe 的 src）。 */
export function workspaceEntryUrl(agent: WorkspaceAgent, scope: WorkspaceScope): string {
  return WORKSPACE_ENTRY_URLS[normalizeWorkspaceAgent(agent)][normalizeWorkspaceScope(scope)]
}

/** 页面标题，如「小组共享 DeepSeek Harness 工作台」。 */
export function workspaceTitle(agent: WorkspaceAgent, scope: WorkspaceScope): string {
  const label = workspaceAgentLabel(agent)
  return scope === 'shared' ? `小组共享 ${label} 工作台` : `${label} 工作台`
}

/** 进程可用（可展示工作台页面）的状态取值。 */
export function isWorkspaceAlive(status: WorkspaceRuntimeStatusKind | undefined | null): boolean {
  return status === 'running' || status === 'starting'
}