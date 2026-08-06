import type { WorkflowRunEvent } from '../api/types'

/** 为历史 REST 事件投影与服务端 SSE 相同的稳定序号。 */
export function normalizeAgentRunEvents(events: WorkflowRunEvent[]): WorkflowRunEvent[] {
  return events.map((event, index) => ({
    ...event,
    event_id: validEventId(event.event_id) ? event.event_id : index + 1,
  }))
}

export function lastAgentRunEventId(events: WorkflowRunEvent[]): number {
  return events.reduce((last, event) => Math.max(last, validEventId(event.event_id) ? event.event_id : 0), 0)
}

/** SSE 重连时只追加未见的事件；同一 id 的重放不产生重复时间轴节点。 */
export function mergeAgentRunEvent(events: WorkflowRunEvent[], incoming: WorkflowRunEvent): WorkflowRunEvent[] {
  const eventId = incoming.event_id
  if (!validEventId(eventId)) return [...events, incoming]
  const index = events.findIndex(event => event.event_id === eventId)
  if (index < 0) return [...events, incoming]
  const next = [...events]
  next[index] = incoming
  return next
}

function validEventId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}
