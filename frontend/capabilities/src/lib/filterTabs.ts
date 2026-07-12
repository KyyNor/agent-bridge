import type { AgentRun, ToolCallLog } from '../api/types'
import { agentRunBadgeVariant, type AgentRunFilter } from './agentRunStatus.ts'

export interface FilterTabCount<T extends string = string> {
  key: T
  label: string
  count: number
}

export function countToolCallTabs(baseline: ToolCallLog[], _activeRows?: ToolCallLog[]): FilterTabCount[] {
  return [
    { key: '', label: '全部', count: baseline.length },
    { key: 'success', label: '成功', count: baseline.filter(row => row.status === 'success').length },
    { key: 'error', label: '失败', count: baseline.filter(row => row.status === 'error').length },
    { key: 'blocked', label: '拦截', count: baseline.filter(row => row.status === 'blocked').length },
  ]
}

export function countAgentRunTabs(baseline: AgentRun[], _activeRows?: AgentRun[]): FilterTabCount<AgentRunFilter>[] {
  return [
    { key: '', label: '全部', count: baseline.length },
    { key: 'running', label: '执行中', count: baseline.filter(row => row.status === 'running').length },
    { key: 'success', label: '成功', count: baseline.filter(row => agentRunBadgeVariant(row) === 'success').length },
    { key: 'failed', label: '失败', count: baseline.filter(row => agentRunBadgeVariant(row) === 'failed').length },
  ]
}
