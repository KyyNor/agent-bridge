/**
 * Shared composable for managing per-context sub-agent collapse state.
 *
 * Extracted from WorkflowView.vue (expandedRunSubagents / collapsedTaskSubagents)
 * and AgentRunsView.vue (collapsedSubagents), where the same Record<key,
 * Set<taskId>> + toggle + isCollapsed pattern was duplicated three times.
 *
 * Each "context" (a run id, a task id, ...) tracks its own set of collapsed
 * sub-agent task ids independently.
 */
import { ref } from 'vue'
import { subagentTaskIds } from '../lib/workflowEvents'
import type { WorkflowRunEvent } from '../api/types'

export function useSubagentCollapse() {
  /** Map of context-key -> set of collapsed sub-agent task ids. */
  const collapsed = ref<Record<string, Set<string>>>({})
  const known = ref<Record<string, Set<string>>>({})

  function isCollapsed(contextKey: string, taskId: string): boolean {
    return !!collapsed.value[contextKey]?.has(taskId)
  }

  function toggle(contextKey: string, taskId: string): void {
    if (!contextKey) return
    const set = new Set(collapsed.value[contextKey] ?? [])
    if (set.has(taskId)) set.delete(taskId)
    else set.add(taskId)
    collapsed.value = { ...collapsed.value, [contextKey]: set }
  }

  /** Initialise a context so all its sub-agents start collapsed.
   *  Safe to call again once full event data loads: existing choices are
   *  preserved, and only newly discovered sub-agents start collapsed. */
  function initCollapsed(contextKey: string, events: WorkflowRunEvent[] | undefined): void {
    if (!contextKey) return
    const ids = subagentTaskIds(events || [])
    const current = collapsed.value[contextKey]
    const currentKnown = known.value[contextKey]
    if (!current || !currentKnown) {
      known.value = {
        ...known.value,
        [contextKey]: new Set(ids),
      }
      collapsed.value = {
        ...collapsed.value,
        [contextKey]: new Set(ids),
      }
      return
    }
    const next = new Set(current)
    const nextKnown = new Set(currentKnown)
    for (const taskId of ids) {
      if (!nextKnown.has(taskId)) {
        next.add(taskId)
        nextKnown.add(taskId)
      }
    }
    known.value = {
      ...known.value,
      [contextKey]: nextKnown,
    }
    collapsed.value = {
      ...collapsed.value,
      [contextKey]: next,
    }
  }

  return { collapsed, isCollapsed, toggle, initCollapsed }
}
