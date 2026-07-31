import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ALL_ARTIFACTS_SENTINEL,
  ALL_STATUS_SENTINEL,
  ALL_TYPE_SENTINEL,
  filterAndSortTasks,
  distinctStatuses,
  distinctTypes,
  taskStats,
  taskStatusLabel,
  canForceRun,
  canRunNormally,
  canRunTask,
  matchTaskFilter,
  taskId,
  toggleTaskSelection,
  togglePageTaskSelection,
} from '../src/lib/workflowTasks.ts'
import type { WorkflowTask } from '../src/api/types.ts'

function makeTask(overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return {
    workflow_key: 'w',
    task_key: 'page:a',
    task_version: '',
    type: '',
    payload: {},
    status: 'pending',
    set_at: '2026-01-01T00:00:00Z',
    lease_run_id: null,
    lease_expires_at: null,
    attempt_count: 0,
    last_error: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    completed_at: null,
    has_artifacts: false,
    ...overrides,
  }
}

const sample = [
  makeTask({ task_key: 'page:alpha', type: 'report', status: 'pending', set_at: '2026-01-01T00:00:00Z', has_artifacts: true }),
  makeTask({ task_key: 'page:beta', type: 'report', status: 'completed', set_at: '2026-01-02T00:00:00Z' }),
  makeTask({ task_key: 'page:gamma', type: 'index', status: 'running', set_at: '2026-01-03T00:00:00Z' }),
]

const staleTask = makeTask({ task_key: 'page:stale', status: 'stale' })

function filters(overrides: Partial<{ status: string; type: string; hasArtifacts: string; search: string; sort: string }> = {}) {
  return {
    status: ALL_STATUS_SENTINEL,
    type: ALL_TYPE_SENTINEL,
    hasArtifacts: ALL_ARTIFACTS_SENTINEL,
    search: '',
    sort: 'default',
    ...overrides,
  }
}

test('distinctStatuses returns statuses in canonical display order', () => {
  // sample has completed/running/pending — expect running first, then pending, then completed.
  assert.deepEqual(distinctStatuses(sample), ['running', 'pending', 'completed'])
})

test('stale tasks have an incremental label, order, stats, and status filter', () => {
  const tasks = [...sample, staleTask]
  assert.deepEqual(distinctStatuses(tasks), ['running', 'pending', 'stale', 'completed'])
  assert.equal(taskStatusLabel('stale'), '待增量执行')
  assert.deepEqual(taskStats(tasks), { pending: 1, completed: 1, running: 1, stale: 1 })
  assert.deepEqual(
    filterAndSortTasks(tasks, filters({ status: 'stale' })).map(task => task.task_key),
    ['page:stale'],
  )
})

test('任务执行规则区分普通执行与强制全量执行', () => {
  const now = Date.parse('2026-07-22T08:00:00Z')
  const pending = makeTask({ status: 'pending' })
  const stale = makeTask({ status: 'stale' })
  const completed = makeTask({ status: 'completed' })
  const expired = makeTask({ status: 'running', lease_expires_at: '2026-07-22T07:59:59Z' })
  const active = makeTask({ status: 'running', lease_expires_at: '2026-07-22T08:00:01Z' })

  assert.equal(canRunNormally(pending, now), true)
  assert.equal(canRunNormally(stale, now), true)
  assert.equal(canRunNormally(expired, now), true)
  assert.equal(canRunNormally(active, now), false)
  assert.equal(canRunNormally(completed, now), false)
  assert.equal(canForceRun(completed), true)
  assert.equal(canForceRun(pending), false)
  assert.equal(canRunTask(completed, now), true)
  assert.equal(canRunTask(active, now), false)
})

test('distinctTypes returns sorted, non-empty types', () => {
  assert.deepEqual(distinctTypes(sample), ['index', 'report'])
})

test('taskStats counts each status', () => {
  assert.deepEqual(taskStats(sample), { pending: 1, completed: 1, running: 1 })
})

test('taskStatusLabel maps known statuses and falls back for unknown', () => {
  assert.equal(taskStatusLabel('pending'), '待处理')
  assert.equal(taskStatusLabel('completed'), '已完成')
  assert.equal(taskStatusLabel('weird'), 'weird')
})

test('filterAndSortTasks with no filters returns all unchanged (server order preserved)', () => {
  const result = filterAndSortTasks(sample, filters())
  assert.equal(result.length, 3)
  // Default sort must not reorder — original array order kept.
  assert.deepEqual(result.map(t => t.task_key), ['page:alpha', 'page:beta', 'page:gamma'])
})

test('filterAndSortTasks filters by status', () => {
  const result = filterAndSortTasks(sample, filters({ status: 'completed' }))
  assert.deepEqual(result.map(t => t.task_key), ['page:beta'])
})

test('filterAndSortTasks treats ALL_STATUS_SENTINEL like no status filter', () => {
  // Both the sentinel and the legacy empty string must mean "all statuses".
  assert.equal(filterAndSortTasks(sample, filters({ status: ALL_STATUS_SENTINEL })).length, 3)
  assert.equal(filterAndSortTasks(sample, filters({ status: '' })).length, 3)
})

test('filterAndSortTasks filters by type, respecting the __all__ sentinel', () => {
  assert.equal(filterAndSortTasks(sample, filters({ type: 'index' })).length, 1)
  assert.equal(filterAndSortTasks(sample, filters({ type: ALL_TYPE_SENTINEL })).length, 3)
})

test('filterAndSortTasks filters tasks without artifacts', () => {
  // sample 只有 alpha 有产物。
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ hasArtifacts: 'without' })).map(t => t.task_key),
    ['page:beta', 'page:gamma'],
  )
})

test('filterAndSortTasks filters tasks with artifacts', () => {
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ hasArtifacts: 'with' })).map(t => t.task_key),
    ['page:alpha'],
  )
})

test('filterAndSortTasks treats ALL_ARTIFACTS_SENTINEL like no artifact filter', () => {
  assert.equal(filterAndSortTasks(sample, filters({ hasArtifacts: ALL_ARTIFACTS_SENTINEL })).length, 3)
})

test('filterAndSortTasks search matches task_key (case-insensitive) and type', () => {
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ search: 'ALPHA' })).map(t => t.task_key),
    ['page:alpha'],
  )
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ search: 'index' })).map(t => t.task_key),
    ['page:gamma'],
  )
})

test('filterAndSortTasks sorts by task_key_asc / task_key_desc', () => {
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ sort: 'task_key_asc' })).map(t => t.task_key),
    ['page:alpha', 'page:beta', 'page:gamma'],
  )
  assert.deepEqual(
    filterAndSortTasks(sample, filters({ sort: 'task_key_desc' })).map(t => t.task_key),
    ['page:gamma', 'page:beta', 'page:alpha'],
  )
})

test('filterAndSortTasks sorts by set_at ascending', () => {
  const result = filterAndSortTasks(sample, filters({ sort: 'set_at_asc' })).map(t => t.task_key)
  assert.deepEqual(result, ['page:alpha', 'page:beta', 'page:gamma'])
})

test('filterAndSortTasks combines filter and sort', () => {
  // Only reports, oldest set_at first.
  const result = filterAndSortTasks(sample, filters({ type: 'report', sort: 'set_at_asc' }))
  assert.deepEqual(result.map(t => t.task_key), ['page:alpha', 'page:beta'])
})

test('matchTaskFilter returns true with empty filters', () => {
  assert.equal(matchTaskFilter(sample[0], filters()), true)
})

test('matchTaskFilter excludes a non-matching status', () => {
  assert.equal(matchTaskFilter(sample[0], filters({ status: 'completed' })), false)
})

test('task selection uses workflow, task, and version so versions stay distinct', () => {
  const v1 = sample[0]
  const v2 = makeTask({ task_key: v1.task_key, task_version: 'v2' })
  assert.notEqual(taskId(v1), taskId(v2))
  assert.equal(taskId(v1), 'w\u0000page:alpha\u0000')
})

test('togglePageTaskSelection only changes the visible page ids', () => {
  const selected = new Set([taskId(sample[2])])
  const next = togglePageTaskSelection(selected, sample.slice(0, 2), true)
  assert.deepEqual([...next], [taskId(sample[2]), taskId(sample[0]), taskId(sample[1])])
  const deselected = togglePageTaskSelection(next, sample.slice(0, 2), false)
  assert.deepEqual([...deselected], [taskId(sample[2])])
})

test('toggleTaskSelection changes one task without losing selections from other pages', () => {
  const selected = new Set([taskId(sample[0])])
  const next = toggleTaskSelection(selected, sample[1], true)
  assert.deepEqual([...next], [taskId(sample[0]), taskId(sample[1])])
  assert.deepEqual([...toggleTaskSelection(next, sample[0], false)], [taskId(sample[1])])
})
