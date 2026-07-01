/**
 * Pure helpers for the workflow task-progress page (筛选 / 搜索 / 排序).
 *
 * Kept side-effect free and framework-agnostic so they can be unit-tested with
 * the native node:test runner, mirroring the pattern in {@link ./navigation.ts}.
 */
import type { WorkflowTask } from '../api/types'

export interface WorkflowTaskFilters {
  status: string
  /** Sentinel `__all__` means "no type filter" (reka-ui Select rejects empty values). */
  type: string
  search: string
  /** Recognised: default | id_asc | id_desc | set_at_asc | set_at_desc | updated_at_desc */
  sort: string
}

export const ALL_TYPE_SENTINEL = '__all__'

/** Canonical display order for task statuses. */
export const TASK_STATUS_ORDER = ['running', 'pending', 'failed', 'abandoned', 'completed']

export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  abandoned: '已放弃',
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] || status
}

export function distinctStatuses(tasks: WorkflowTask[]): string[] {
  const present = new Set(tasks.map(task => task.status))
  return TASK_STATUS_ORDER.filter(status => present.has(status))
}

export function distinctTypes(tasks: WorkflowTask[]): string[] {
  const types = new Set<string>()
  for (const task of tasks) {
    if (task.type) types.add(task.type)
  }
  return Array.from(types).sort()
}

export function taskStats(tasks: WorkflowTask[]): Record<string, number> {
  const stats: Record<string, number> = {}
  for (const task of tasks) stats[task.status] = (stats[task.status] || 0) + 1
  return stats
}

/** Whether a single task passes the active status/type/search filters. */
export function matchTaskFilter(task: WorkflowTask, filters: WorkflowTaskFilters): boolean {
  if (filters.status && task.status !== filters.status) return false
  if (filters.type && filters.type !== ALL_TYPE_SENTINEL && task.type !== filters.type) return false
  const q = filters.search.trim().toLowerCase()
  if (q && !(task.task_key.toLowerCase().includes(q) || (task.type || '').toLowerCase().includes(q))) return false
  return true
}

function compareTask(a: WorkflowTask, b: WorkflowTask, sort: string): number {
  switch (sort) {
    case 'id_asc':
      return a.task_key < b.task_key ? -1 : a.task_key > b.task_key ? 1 : 0
    case 'id_desc':
      return a.task_key < b.task_key ? 1 : a.task_key > b.task_key ? -1 : 0
    case 'set_at_asc':
      return a.set_at < b.set_at ? -1 : a.set_at > b.set_at ? 1 : 0
    case 'set_at_desc':
      return a.set_at < b.set_at ? 1 : a.set_at > b.set_at ? -1 : 0
    case 'updated_at_desc':
      return a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0
    default:
      return 0
  }
}

/**
 * Tasks after client-side filter + sort. The server already applies a default
 * status-priority order; client sort only reshuffles when an explicit
 * (non-`default`) mode is chosen, preserving the server order otherwise.
 */
export function filterAndSortTasks(tasks: WorkflowTask[], filters: WorkflowTaskFilters): WorkflowTask[] {
  const matched = tasks.filter(task => matchTaskFilter(task, filters))
  return filters.sort === 'default' ? matched : [...matched].sort((a, b) => compareTask(a, b, filters.sort))
}
