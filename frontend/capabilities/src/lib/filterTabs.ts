import type { AgentRun, AgentRunCounts, ToolCallLog, ToolCallLogCounts } from '../api/types'
import { agentRunBadgeVariant, type AgentRunFilter } from './agentRunStatus'

export interface FilterTabCount<T extends string = string> {
  key: T
  label: string
  count: number
}

export function countToolCallTabs(baseline: ToolCallLog[] | ToolCallLogCounts, _activeRows?: ToolCallLog[]): FilterTabCount[] {
  if (!Array.isArray(baseline)) {
    return [
      { key: '', label: '全部', count: baseline.all },
      { key: 'success', label: '成功', count: baseline.success },
      { key: 'error', label: '失败', count: baseline.error },
      { key: 'blocked', label: '拦截', count: baseline.blocked },
    ]
  }
  return [
    { key: '', label: '全部', count: baseline.length },
    { key: 'success', label: '成功', count: baseline.filter(row => row.status === 'success').length },
    { key: 'error', label: '失败', count: baseline.filter(row => row.status === 'error').length },
    { key: 'blocked', label: '拦截', count: baseline.filter(row => row.status === 'blocked').length },
  ]
}

export function countAgentRunTabs(baseline: AgentRun[] | AgentRunCounts, _activeRows?: AgentRun[]): FilterTabCount<AgentRunFilter>[] {
  if (!Array.isArray(baseline)) {
    return [
      { key: '', label: '全部', count: baseline.all },
      { key: 'running', label: '执行中', count: baseline.running },
      { key: 'success', label: '成功', count: baseline.success },
      { key: 'failed', label: '失败', count: baseline.failed },
      { key: 'stopped', label: '已停止', count: baseline.stopped },
    ]
  }
  return [
    { key: '', label: '全部', count: baseline.length },
    { key: 'running', label: '执行中', count: baseline.filter(row => row.status === 'running').length },
    { key: 'success', label: '成功', count: baseline.filter(row => agentRunBadgeVariant(row) === 'success').length },
    { key: 'failed', label: '失败', count: baseline.filter(row => agentRunBadgeVariant(row) === 'failed').length },
    { key: 'stopped', label: '已停止', count: baseline.filter(row => agentRunBadgeVariant(row) === 'stopped').length },
  ]
}
