import type { AgentRun } from '../api/types'

export type AgentRunFilter = '' | 'success' | 'failed' | 'running'
export type AgentRunBadgeVariant = 'success' | 'failed' | 'running' | 'stopped' | 'unknown'

type AgentRunStatusInput = Pick<AgentRun, 'ok'> & { status?: string | null }

export function agentRunStatusLabel(run: AgentRunStatusInput): string {
  if (run.status === 'running') return '执行中'
  if (run.status === 'completed') return '成功'
  if (run.status === 'failed') return '失败'
  if (run.status === 'stopped') return '已停止'
  if (run.ok) return '成功'
  if (run.status) return run.status
  return '失败'
}

export function agentRunBadgeVariant(run: AgentRunStatusInput): AgentRunBadgeVariant {
  if (run.status === 'running') return 'running'
  if (run.status === 'stopped') return 'stopped'
  if (run.status === 'completed' || run.ok) return 'success'
  if (run.status === 'failed' || run.ok === false) return 'failed'
  return 'unknown'
}

export function agentRunOkFilterParam(filter: AgentRunFilter): boolean | undefined {
  if (filter === 'success') return true
  if (filter === 'failed') return false
  return undefined
}

export function agentRunStatusFilterParam(filter: AgentRunFilter): string | undefined {
  if (filter === 'failed' || filter === 'running') return filter
  return undefined
}
