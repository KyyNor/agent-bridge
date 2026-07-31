/**
 * Pure helpers for the workflow task-progress page (筛选 / 搜索 / 排序).
 *
 * Kept side-effect free and framework-agnostic so they can be unit-tested with
 * the native node:test runner, mirroring the pattern in {@link ./navigation.ts}.
 */
import type { WorkflowRun, WorkflowTask } from '../api/types'

export interface WorkflowTaskFilters {
  /** Sentinel {@link ALL_STATUS_SENTINEL} means "no status filter" (reka-ui Select rejects empty values). */
  status: string
  /** Sentinel {@link ALL_TYPE_SENTINEL} means "no type filter" (reka-ui Select rejects empty values). */
  type: string
  search: string
  /** Recognised: default | task_key_asc | task_key_desc | set_at_asc | set_at_desc | updated_at_desc */
  sort: string
}

export const ALL_STATUS_SENTINEL = '__all_status__'
export const ALL_TYPE_SENTINEL = '__all__'

/** Canonical display order for task statuses. */
export const TASK_STATUS_ORDER = ['running', 'pending', 'stale', 'failed', 'abandoned', 'completed', 'superseded']

export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  stale: '待增量执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  abandoned: '已放弃',
  superseded: '已取代',
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] || status
}

/** 普通执行：仅允许尚未完成的任务，或租约已经过期的运行中任务。 */
export function canRunNormally(task: WorkflowTask, nowMs = Date.now()): boolean {
  if (task.status === 'pending' || task.status === 'stale') return true
  if (task.status === 'running' && task.lease_expires_at) {
    return new Date(task.lease_expires_at).getTime() < nowMs
  }
  return false
}

/** 强制全量执行：只用于已有产物的已完成任务。 */
export function canForceRun(task: WorkflowTask): boolean {
  return task.status === 'completed'
}

/** 任务行是否应展示任一种可执行操作。 */
export function canRunTask(task: WorkflowTask, nowMs = Date.now()): boolean {
  return canRunNormally(task, nowMs) || canForceRun(task)
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
  if (filters.status && filters.status !== ALL_STATUS_SENTINEL && task.status !== filters.status) return false
  if (filters.type && filters.type !== ALL_TYPE_SENTINEL && task.type !== filters.type) return false
  const q = filters.search.trim().toLowerCase()
  if (q && !(task.task_key.toLowerCase().includes(q) || (task.type || '').toLowerCase().includes(q))) return false
  return true
}

function compareTask(a: WorkflowTask, b: WorkflowTask, sort: string): number {
  switch (sort) {
    case 'task_key_asc':
      return a.task_key < b.task_key ? -1 : a.task_key > b.task_key ? 1 : 0
    case 'task_key_desc':
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

/** Stable client-side identity for a task/version across task-list pages. */
export function taskId(task: WorkflowTask): string {
  return `${task.workflow_key}\u0000${task.task_key}\u0000${task.task_version || ''}`
}

export function toggleTaskSelection(selected: Set<string>, task: WorkflowTask, checked: boolean): Set<string> {
  const next = new Set(selected)
  const id = taskId(task)
  if (checked) next.add(id)
  else next.delete(id)
  return next
}

export function togglePageTaskSelection(selected: Set<string>, tasks: WorkflowTask[], checked: boolean): Set<string> {
  const next = new Set(selected)
  for (const task of tasks) {
    const id = taskId(task)
    if (checked) next.add(id)
    else next.delete(id)
  }
  return next
}

export interface WorkflowTaskQueueOutcome {
  task: WorkflowTask
  status: 'success' | 'failed' | 'skipped' | 'stopped'
  run?: WorkflowRun
  error?: string
}

export interface WorkflowTaskQueueResult {
  outcomes: WorkflowTaskQueueOutcome[]
  stopped: boolean
  remaining: WorkflowTask[]
}

export interface WorkflowTaskQueueOptions {
  canExecute: (task: WorkflowTask) => boolean
  execute: (task: WorkflowTask) => Promise<{ run_id?: string | null }>
  waitForRun: (
    runId: string,
    onUpdate?: (run: WorkflowRun) => void | Promise<void>,
  ) => Promise<WorkflowRun>
  onTaskStart?: (task: WorkflowTask, index: number, total: number) => void
  onRunStart?: (task: WorkflowTask, runId: string) => void | Promise<void>
  onRunUpdate?: (task: WorkflowTask, run: WorkflowRun) => void | Promise<void>
  onTaskFinish?: (outcome: WorkflowTaskQueueOutcome) => void | Promise<void>
  isCancelled?: () => boolean
  /** Stop a run that was created after the caller cancelled the queue. */
  stopRun?: (runId: string) => void | Promise<void>
  /** Return true for errors that should stop the page-local queue. */
  shouldStopOnError?: (error: unknown) => boolean
}

/**
 * Runs selected tasks serially. The queue intentionally lives in the caller's
 * page; it has no persistence or backend batch lifecycle.
 */
export async function runWorkflowTaskQueue(
  tasks: WorkflowTask[],
  options: WorkflowTaskQueueOptions,
): Promise<WorkflowTaskQueueResult> {
  const outcomes: WorkflowTaskQueueOutcome[] = []
  const shouldStopOnError = options.shouldStopOnError || (() => false)

  for (let index = 0; index < tasks.length; index += 1) {
    const task = tasks[index]
    if (options.isCancelled?.()) {
      return { outcomes, stopped: true, remaining: tasks.slice(index) }
    }
    if (!options.canExecute(task)) {
      const outcome = { task, status: 'skipped' as const, error: '任务当前不可执行' }
      outcomes.push(outcome)
      await options.onTaskFinish?.(outcome)
      continue
    }

    try {
      options.onTaskStart?.(task, index, tasks.length)
      const started = await options.execute(task)
      if (!started.run_id) {
        const outcome = { task, status: 'failed' as const, error: '执行未返回 run_id' }
        outcomes.push(outcome)
        await options.onTaskFinish?.(outcome)
        continue
      }
      if (options.isCancelled?.()) {
        try {
          await options.stopRun?.(started.run_id)
        } catch {
          // Keep polling until the backend reports a terminal state even if
          // the stop request itself fails transiently.
        }
      }
      await options.onRunStart?.(task, started.run_id)
      const run = await options.waitForRun(
        started.run_id,
        currentRun => options.onRunUpdate?.(task, currentRun),
      )
      if (run.status === 'stopped') {
        const outcome = { task, status: 'stopped' as const, run, error: run.error || undefined }
        outcomes.push(outcome)
        await options.onTaskFinish?.(outcome)
        return { outcomes, stopped: true, remaining: tasks.slice(index + 1) }
      }
      const status: WorkflowTaskQueueOutcome['status'] = run.status === 'completed' || run.status === 'no_task'
        ? 'success'
        : 'failed'
      const outcome = { task, status, run, error: run.error || undefined }
      outcomes.push(outcome)
      await options.onTaskFinish?.(outcome)
    } catch (error: unknown) {
      const outcome = {
        task,
        status: 'failed',
        error: error instanceof Error ? error.message : String(error),
      } as const
      outcomes.push(outcome)
      await options.onTaskFinish?.(outcome)
      if (shouldStopOnError(error)) {
        return { outcomes, stopped: true, remaining: tasks.slice(index + 1) }
      }
    }
  }

  return { outcomes, stopped: false, remaining: [] }
}

export interface WorkflowTaskResetQueueOptions {
  canReset: (task: WorkflowTask) => boolean
  reset: (task: WorkflowTask) => Promise<void>
  onTaskStart?: (task: WorkflowTask, index: number, total: number) => void
  isCancelled?: () => boolean
  shouldStopOnError?: (error: unknown) => boolean
}

/** Runs page-local task resets in order without introducing a batch API. */
export async function runWorkflowTaskResetQueue(
  tasks: WorkflowTask[],
  options: WorkflowTaskResetQueueOptions,
): Promise<WorkflowTaskQueueResult> {
  const outcomes: WorkflowTaskQueueOutcome[] = []
  const shouldStopOnError = options.shouldStopOnError || (() => false)

  for (let index = 0; index < tasks.length; index += 1) {
    const task = tasks[index]
    if (options.isCancelled?.()) {
      return { outcomes, stopped: true, remaining: tasks.slice(index) }
    }
    if (!options.canReset(task)) {
      outcomes.push({ task, status: 'skipped', error: '任务当前不可重置' })
      continue
    }
    try {
      options.onTaskStart?.(task, index, tasks.length)
      await options.reset(task)
      outcomes.push({ task, status: 'success' })
    } catch (error: unknown) {
      outcomes.push({
        task,
        status: 'failed',
        error: error instanceof Error ? error.message : String(error),
      })
      if (shouldStopOnError(error)) {
        return { outcomes, stopped: true, remaining: tasks.slice(index + 1) }
      }
    }
  }

  return { outcomes, stopped: false, remaining: [] }
}
