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
  if (event.kind === 'tool_call') {
    if (event.status === 'failed') return '工具调用失败'
    if (event.status === 'success') return '工具调用完成'
    if (event.status === 'unknown') return '工具调用未完成'
    return '工具调用'
  }
  if (event.kind === 'tool_result') {
    if (event.status === 'failed') return '工具失败'
    if (event.status === 'unknown') return '工具未完成'
    return '工具完成'
  }
  if (event.kind === 'result') return event.status === 'failed' ? '运行失败' : '运行结果'
  if (event.kind === 'structured_output') return event.status === 'failed' ? '结构化输出失败' : '结构化输出'
  if (event.kind === 'error') return '异常'
  if (event.kind === 'status') return '状态'
  if (event.kind === 'subagent_start') return '子 Agent 启动'
  if (event.kind === 'subagent_progress') return '子 Agent 进度'
  if (event.kind === 'subagent_end') return event.status === 'failed' ? '子 Agent 失败' : '子 Agent 完成'
  if (event.kind === 'subagent_updated') return '子 Agent 更新'
  if (event.kind === 'stage') return event.stage_name ? `阶段 · ${event.stage_name}` : '阶段'
  return event.kind
}

/** Build a readable message for an event, filling in blanks for kinds that
 *  carry no `message` field (e.g. subagent_progress). */
export function eventMessage(event: WorkflowRunEvent): string {
  if (event.message) return event.message
  if (event.kind === 'structured_output') return '结构化输出已生成'
  if (event.tool_name && event.kind === 'tool_call') return `调用工具 ${event.tool_name}`
  if (event.tool_name && event.kind === 'tool_result') {
    const outcome = event.status === 'failed' ? '失败' : event.status === 'unknown' ? '未完成' : '成功'
    return `工具 ${event.tool_name} 调用${outcome}`
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
  if (event.kind === 'stage' && event.stage_name) {
    return event.duration_ms != null
      ? `${event.stage_name} · ${event.duration_ms}ms`
      : event.stage_name
  }
  return event.status || ''
}

/** Semantic classes for an event's Badge (background tint by kind/status). */
export function eventKindClass(kind: string, status?: string): string {
  if (kind === 'error' || status === 'failed') return 'bg-destructive-soft text-destructive-soft-fg'
  if (kind === 'result' || kind === 'structured_output' || status === 'success') return 'bg-success-soft text-success-soft-fg'
  if (kind === 'tool_call') return 'bg-info-soft text-info-soft-fg'
  if (kind === 'tool_result') return 'bg-cat-violet text-cat-violet-fg'
  if (kind === 'subagent_end' && status !== 'failed') return 'bg-success-soft text-success-soft-fg'
  if (kind === 'subagent_end' && status === 'failed') return 'bg-destructive-soft text-destructive-soft-fg'
  return 'bg-secondary text-muted-foreground'
}

/** Semantic classes for a timeline node's left border accent. */
export function eventClass(event: WorkflowRunEvent): string {
  if (event.kind === 'error' || event.status === 'failed') return 'border-destructive/50'
  if (event.kind === 'tool_call') return 'border-info/50'
  if (event.kind === 'tool_result') return event.status === 'failed' ? 'border-destructive/50' : 'border-success/50'
  if (event.kind === 'result' || event.kind === 'structured_output') return 'border-foreground/40'
  return 'border-border'
}

/** Semantic visual family for timeline styling: collapses the many event.kind
 *  values into a small set of timeline node variants. */
export type TimelineKind = 'message' | 'think' | 'tool' | 'result' | 'error' | 'status'

export function timelineKind(event: WorkflowRunEvent): TimelineKind {
  if (event.kind === 'error' || event.status === 'failed') return 'error'
  if (event.kind === 'tool_call') return 'tool'
  if (event.kind === 'tool_result') {
    return event.status === 'failed' || event.status === 'unknown' ? 'error' : 'result'
  }
  if (event.kind === 'result' || event.kind === 'structured_output') return event.status === 'failed' ? 'error' : 'result'
  if (event.kind === 'status') return 'status'
  if (event.kind === 'stage') return 'status'
  return 'message'
}

/** Semantic classes for a sub-agent status Badge. */
export function subagentStatusBadgeClass(status: string | null | undefined): string {
  if (!status) return 'bg-info-soft text-info-soft-fg' // running
  if (status === 'completed') return 'bg-success-soft text-success-soft-fg'
  if (status === 'failed' || status === 'error') return 'bg-destructive-soft text-destructive-soft-fg'
  return 'bg-secondary text-muted-foreground'
}

/** A timeline node: one main-agent event, or the first event of a sub-agent
 *  thread (which expands inline to show the whole thread). */
export interface TimelineEntry {
  actor: { id: string; role: 'main' | 'subagent'; label: string }
  event: WorkflowRunEvent
}

/**
 * 把高频增量消息和工具完成事件合并成时间轴可读的单个节点。
 *
 * 原始事件流仍然保留逐条记录，便于调试和重放；这里仅改变展示形态：
 * 同一 stream_id 的文本 delta 拼接，同一 tool_use_id 的调用和结果合并。
 */
function coalesceMainEvents(
  entries: { actor: TimelineEntry['actor']; event: WorkflowRunEvent }[],
): { actor: TimelineEntry['actor']; event: WorkflowRunEvent }[] {
  const result: { actor: TimelineEntry['actor']; event: WorkflowRunEvent }[] = []
  const partialMessages = new Map<string, number>()
  const toolCalls = new Map<string, number>()

  for (const entry of entries) {
    const { event, actor } = entry
    if (actor.role !== 'main') {
      result.push(entry)
      continue
    }

    if (event.kind === 'agent_message' && typeof event.message === 'string') {
      const streamId = typeof event.stream_id === 'string' ? event.stream_id : ''
      if (streamId && event.partial === true) {
        const existingIndex = partialMessages.get(streamId)
        if (existingIndex != null) {
          const existing = result[existingIndex]
          existing.event = {
            ...existing.event,
            message: `${existing.event.message || ''}${event.message}`,
            finished_at: event.created_at || existing.event.finished_at,
          }
        } else {
          partialMessages.set(streamId, result.length)
          result.push(entry)
        }
        continue
      }
      if (streamId) {
        const existingIndex = partialMessages.get(streamId)
        if (existingIndex != null) {
          const existing = result[existingIndex]
          existing.event = {
            ...existing.event,
            ...event,
            kind: 'agent_message',
            message: event.message,
            created_at: existing.event.created_at,
            partial: false,
          }
          continue
        }
      }

      // Pi's JSON mode may emit assistant deltas without stream metadata.
      // Merge only adjacent unkeyed messages so separate turns/tools remain
      // distinct. A later long snapshot replaces the accumulated fragments;
      // Pi emits that snapshot after the token-level deltas.
      if (!streamId && event.partial !== false) {
        const previous = result[result.length - 1]
        if (
          previous?.actor.role === 'main'
          && previous.event.kind === 'agent_message'
          && typeof previous.event.message === 'string'
          && !previous.event.stream_id
          && previous.event.partial !== false
        ) {
          previous.event = {
            ...previous.event,
            message: mergeAdjacentMessageText(previous.event.message, event.message),
            finished_at: event.created_at || previous.event.finished_at,
          }
          continue
        }
      }
    }

    // Pi's final result repeats the streamed assistant message. Keep the
    // canonical result node and avoid displaying the same large output twice.
    if (
      (event.kind === 'result' || event.kind === 'structured_output')
      && (event.kind === 'structured_output' || typeof event.message === 'string')
    ) {
      const previous = result[result.length - 1]
      if (
        previous?.actor.role === 'main'
        && (previous.event.kind === 'agent_message' || previous.event.kind === 'result')
        && typeof previous.event.message === 'string'
        && (
          event.kind === 'structured_output'
          || (typeof event.message === 'string' && sameRenderedMessage(previous.event.message, event.message))
        )
      ) {
        result[result.length - 1] = { actor, event }
        continue
      }
    }

    if (event.kind === 'tool_call' && typeof event.tool_use_id === 'string') {
      toolCalls.set(event.tool_use_id, result.length)
      result.push(entry)
      continue
    }

    if (event.kind === 'tool_result' && typeof event.tool_use_id === 'string') {
      const callIndex = toolCalls.get(event.tool_use_id)
      if (callIndex != null) {
        const call = result[callIndex]
        call.event = {
          ...call.event,
          ...event,
          kind: 'tool_call',
          created_at: call.event.created_at,
          started_at: call.event.started_at || event.started_at,
          input: call.event.input ?? event.input,
          tool_name: call.event.tool_name || event.tool_name,
          message: event.message || call.event.message,
        }
        toolCalls.delete(event.tool_use_id)
        continue
      }
    }

    result.push(entry)
  }
  return result
}

function mergeAdjacentMessageText(existing: string, incoming: string): string {
  if (!existing) return incoming
  if (!incoming) return existing
  if (existing === incoming) return existing

  // A Pi message_end snapshot can differ from the streamed text only by a
  // short framing prefix (for example a duplicated Markdown fence).
  if (
    incoming.length >= 256
    && incoming.length >= existing.length * 0.8
    && existing.indexOf(incoming) >= 0
    && existing.indexOf(incoming) <= 32
  ) {
    return incoming
  }
  return existing + incoming
}

function sameRenderedMessage(left: string, right: string): boolean {
  if (left === right) return true
  const longer = left.length >= right.length ? left : right
  const shorter = left.length >= right.length ? right : left
  return shorter.length >= 256 && longer.includes(shorter) && longer.length - shorter.length <= 64
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
  for (const { actor, event } of coalesceMainEvents(all)) {
    if (actor.role === 'subagent') {
      if (subFirstSeen.has(actor.id)) continue
      subFirstSeen.add(actor.id)
    }
    entries.push({ actor, event })
  }
  return entries
}
