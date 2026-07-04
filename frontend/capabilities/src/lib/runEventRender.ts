/**
 * Pure presentation helpers for agent / workflow run events.
 *
 * Extracted from WorkflowView.vue and AgentRunsView.vue where they were
 * duplicated nearly verbatim. All functions here are framework-agnostic and
 * operate on the canonical {@link WorkflowRunEvent} shape produced by
 * `agent_runtime/events.py`.
 */
import type { WorkflowRunEvent } from '../api/types'

/** Badge label for an event kind, accounting for status variants (failed/success). */
export function eventKindLabel(event: WorkflowRunEvent): string {
  if (event.kind === 'agent_message') {
    return event.agent_role === 'subagent' ? '子 Agent' : (event.agent_name || 'agent')
  }
  if (event.kind === 'tool_call') return '工具调用'
  if (event.kind === 'tool_result') return event.status === 'failed' ? '工具失败' : '工具完成'
  if (event.kind === 'result') return event.status === 'failed' ? '运行失败' : '运行结果'
  if (event.kind === 'error') return '异常'
  if (event.kind === 'status') return '状态'
  if (event.kind === 'subagent_start') return '子 Agent 启动'
  if (event.kind === 'subagent_progress') return '子 Agent 进度'
  if (event.kind === 'subagent_end') return event.status === 'failed' ? '子 Agent 失败' : '子 Agent 完成'
  if (event.kind === 'subagent_updated') return '子 Agent 更新'
  return event.kind
}

/** Build a readable message for an event, filling in blanks for kinds that
 *  carry no `message` field (e.g. subagent_progress). */
export function eventMessage(event: WorkflowRunEvent): string {
  if (event.message) return event.message
  if (event.tool_name && event.kind === 'tool_call') return `调用工具 ${event.tool_name}`
  if (event.tool_name && event.kind === 'tool_result') {
    return `工具 ${event.tool_name} 调用${event.status === 'failed' ? '失败' : '成功'}`
  }
  if (event.kind === 'subagent_progress') {
    const parts: string[] = []
    if (event.last_tool_name) parts.push(`当前工具: ${event.last_tool_name}`)
    if (event.usage) {
      const usageParts: string[] = []
      if (event.usage.total_tokens != null) usageParts.push(`${event.usage.total_tokens} tokens`)
      if (event.usage.tool_uses != null) usageParts.push(`${event.usage.tool_uses} 次工具`)
      if (usageParts.length) parts.push(usageParts.join(' · '))
    }
    if (parts.length) return parts.join(' · ')
  }
  return event.status || ''
}

/** Tailwind classes for an event's Badge (background tint by kind/status). */
export function eventKindClass(kind: string, status?: string): string {
  if (kind === 'error' || status === 'failed') return 'bg-red-50 text-red-700'
  if (kind === 'result' || status === 'success') return 'bg-green-50 text-green-700'
  if (kind === 'tool_call') return 'bg-blue-50 text-blue-700'
  if (kind === 'tool_result') return 'bg-violet-50 text-violet-700'
  if (kind === 'subagent_end' && status !== 'failed') return 'bg-green-50 text-green-700'
  if (kind === 'subagent_end' && status === 'failed') return 'bg-red-50 text-red-700'
  return 'bg-secondary text-muted-foreground'
}

/** Tailwind classes for a timeline node's left border accent. */
export function eventClass(event: WorkflowRunEvent): string {
  if (event.kind === 'error' || event.status === 'failed') return 'border-red-400'
  if (event.kind === 'tool_call') return 'border-blue-400'
  if (event.kind === 'tool_result') return event.status === 'failed' ? 'border-red-400' : 'border-green-400'
  if (event.kind === 'result') return 'border-foreground/40'
  return 'border-border'
}

/** Semantic visual family for timeline styling: collapses the many event.kind
 *  values into a small set of timeline node variants. */
export type TimelineKind = 'message' | 'think' | 'tool' | 'result' | 'error' | 'status'

export function timelineKind(event: WorkflowRunEvent): TimelineKind {
  if (event.kind === 'error' || event.status === 'failed') return 'error'
  if (event.kind === 'tool_call') return 'tool'
  if (event.kind === 'tool_result') return event.status === 'failed' ? 'error' : 'result'
  if (event.kind === 'result') return event.status === 'failed' ? 'error' : 'result'
  if (event.kind === 'status') return 'status'
  return 'message'
}

/** Tailwind classes for a sub-agent status Badge. */
export function subagentStatusBadgeClass(status: string | null | undefined): string {
  if (!status) return 'bg-blue-50 text-blue-700' // running
  if (status === 'completed') return 'bg-green-50 text-green-700'
  if (status === 'failed' || status === 'error') return 'bg-red-50 text-red-700'
  return 'bg-secondary text-muted-foreground'
}

/** A timeline node: one main-agent event, or the first event of a sub-agent
 *  thread (which expands inline to show the whole thread). */
export interface TimelineEntry {
  actor: { id: string; role: 'main' | 'subagent'; label: string }
  event: WorkflowRunEvent
}

/** Interleave main-agent and subagent events into a single reading order.
 *  - Main events stay as individual timeline nodes (in order).
 *  - For each subagent, only the FIRST lifecycle event (usually subagent_start)
 *    becomes a timeline node; it carries the actor so the UI can render the
 *    whole subagent thread inline. Later subagent lifecycle events are dropped
 *    from the top-level list (they live inside the thread card). */
export function buildTimeline(
  groups: { actor: { id: string; role: 'main' | 'subagent'; label: string }; events: WorkflowRunEvent[] }[],
): TimelineEntry[] {
  const subFirstSeen = new Set<string>()
  const entries: TimelineEntry[] = []
  // Re-walk all events in original order. We need the raw stream; rebuild it
  // from groups by stable merge is complex, so we rely on each group preserving
  // its own order and interleave by created_at when available.
  const all: { actor: TimelineEntry['actor']; event: WorkflowRunEvent }[] = []
  for (const g of groups) {
    for (const e of g.events) all.push({ actor: g.actor, event: e })
  }
  all.sort((a, b) => {
    const ta = a.event.created_at ? Date.parse(a.event.created_at) : NaN
    const tb = b.event.created_at ? Date.parse(b.event.created_at) : NaN
    if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb
    return 0 // keep original order when timestamps missing
  })
  for (const { actor, event } of all) {
    if (actor.role === 'subagent') {
      if (subFirstSeen.has(actor.id)) continue
      subFirstSeen.add(actor.id)
    }
    entries.push({ actor, event })
  }
  return entries
}
