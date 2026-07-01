/**
 * Pure helpers for grouping/filtering agent-run events by sub-agent
 * (feature 5). Framework-agnostic and unit-tested (see tests/workflowEvents.test.ts).
 */
import type { WorkflowRunEvent } from '../api/types'

/** A logical actor in the event stream: the main agent, or a spawned sub-agent. */
export interface EventActor {
  /** "main" for the top-level agent; otherwise a sub-agent task id. */
  id: string
  role: 'main' | 'subagent'
  /** Human-readable label (sub-agent description, or "主 Agent"). */
  label: string
}

export interface EventGroup {
  actor: EventActor
  events: WorkflowRunEvent[]
}

const MAIN_ACTOR: EventActor = { id: 'main', role: 'main', label: '主 Agent' }

const SUBAGENT_KINDS = new Set(['subagent_start', 'subagent_progress', 'subagent_end', 'subagent_updated'])

/** Build a stable label for a sub-agent task id from the lifecycle events. */
export function subagentLabel(events: WorkflowRunEvent[]): (taskId: string) => string {
  const byTask: Record<string, string> = {}
  for (const ev of events) {
    if (ev.task_id && ev.description && !byTask[ev.task_id]) {
      byTask[ev.task_id] = ev.description
    }
  }
  return (taskId: string) => byTask[taskId] || `子 Agent ${taskId}`
}

/** Distinct actors present in an event stream, main first then sub-agents in
 *  first-seen order. */
export function distinctActors(events: WorkflowRunEvent[]): EventActor[] {
  const actors: EventActor[] = []
  const seen = new Set<string>()
  const labelFor = subagentLabel(events)
  for (const ev of events) {
    const id = ev.agent_role === 'subagent' && ev.task_id ? ev.task_id : 'main'
    if (seen.has(id)) continue
    seen.add(id)
    actors.push(id === 'main' ? MAIN_ACTOR : { id, role: 'subagent', label: labelFor(id) })
  }
  // Ensure main is always first if present.
  const mainIdx = actors.findIndex(a => a.role === 'main')
  if (mainIdx > 0) {
    const [main] = actors.splice(mainIdx, 1)
    actors.unshift(main)
  }
  return actors
}

/** Group events by actor. Sub-agent lifecycle events (subagent_*) are attached
 *  to their own task group; everything else is attributed by agent_role/task_id
 *  (falling back to main). */
export function groupEventsByActor(events: WorkflowRunEvent[]): EventGroup[] {
  const actors = distinctActors(events)
  const buckets: Record<string, WorkflowRunEvent[]> = {}
  for (const ev of events) {
    const belongsToSubagent = Boolean(
      ev.task_id && (ev.agent_role === 'subagent' || SUBAGENT_KINDS.has(ev.kind)),
    )
    const id = belongsToSubagent ? ev.task_id! : 'main'
    ;(buckets[id] ||= []).push(ev)
  }
  return actors
    .map(actor => ({ actor, events: buckets[actor.id] || [] }))
    .filter(group => group.events.length > 0)
}

/** Filter an event stream to a single actor (by actor id, or all when id===""). */
export function filterEventsByActor(events: WorkflowRunEvent[], actorId: string): WorkflowRunEvent[] {
  if (!actorId) return events
  if (actorId === 'main') {
    return events.filter(ev => !(ev.agent_role === 'subagent' || (SUBAGENT_KINDS.has(ev.kind) && ev.task_id)))
  }
  return events.filter(ev => (ev.agent_role === 'subagent' && ev.task_id === actorId) || (SUBAGENT_KINDS.has(ev.kind) && ev.task_id === actorId))
}

/** Summarise a sub-agent's usage from its latest progress/end event. */
export function subagentUsage(events: WorkflowRunEvent[], taskId: string): { total_tokens?: number; tool_uses?: number; duration_ms?: number } | null {
  let latest: WorkflowRunEvent | null = null
  for (const ev of events) {
    if (ev.task_id === taskId && ev.usage && (ev.kind === 'subagent_progress' || ev.kind === 'subagent_end')) {
      latest = ev
    }
  }
  return latest?.usage || null
}

/** Terminal status of a sub-agent (completed/failed/stopped/...), if known. */
export function subagentStatus(events: WorkflowRunEvent[], taskId: string): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]
    if (ev.task_id === taskId && (ev.kind === 'subagent_end' || ev.kind === 'subagent_updated') && ev.status) {
      return ev.status
    }
  }
  return null
}
