import assert from 'node:assert/strict'
import test from 'node:test'

import type { WorkflowRun, WorkflowTask } from '../src/api/types.ts'
import { runWorkflowTaskQueue, runWorkflowTaskResetQueue } from '../src/lib/workflowTasks.ts'

function task(taskKey: string, status = 'pending'): WorkflowTask {
  return {
    workflow_key: 'workflow',
    task_key: taskKey,
    task_version: '',
    type: '',
    payload: {},
    status,
    set_at: '2026-01-01T00:00:00Z',
    lease_run_id: null,
    lease_expires_at: null,
    attempt_count: 0,
    last_error: null,
    priority_flag: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    completed_at: null,
    priority_flag: null,
    has_artifacts: false,
    needs_refresh: false,
    last_completed_revision_no: null,
  }
}

function run(runId: string, status: string): WorkflowRun {
  return {
    run_id: runId,
    workflow_key: 'workflow',
    profile_key: 'profile',
    task_key: null,
    status,
    temp_dir: '',
    exit_code: null,
    stdout_path: null,
    stderr_path: null,
    error: null,
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
    duration_ms: 1000,
  }
}

test('runWorkflowTaskQueue waits for each run before executing the next task', async () => {
  const events: string[] = []
  const tasks = [task('first'), task('second')]
  const result = await runWorkflowTaskQueue(tasks, {
    canExecute: () => true,
    execute: async current => {
      events.push(`execute:${current.task_key}`)
      return { run_id: `run-${current.task_key}` }
    },
    waitForRun: async runId => {
      events.push(`wait:${runId}`)
      return run(runId, 'completed')
    },
  })

  assert.deepEqual(events, [
    'execute:first', 'wait:run-first',
    'execute:second', 'wait:run-second',
  ])
  assert.deepEqual(result.outcomes.map(item => item.status), ['success', 'success'])
  assert.equal(result.stopped, false)
})

test('runWorkflowTaskQueue marks a stopped run and leaves later tasks selected', async () => {
  const executed: string[] = []
  const result = await runWorkflowTaskQueue([task('first'), task('second')], {
    canExecute: () => true,
    execute: async current => {
      executed.push(current.task_key)
      return { run_id: `run-${current.task_key}` }
    },
    waitForRun: async runId => run(runId, 'stopped'),
  })

  assert.deepEqual(executed, ['first'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['stopped'])
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})

test('runWorkflowTaskQueue stops a run returned after queue cancellation and still waits for its terminal state', async () => {
  const events: string[] = []
  let cancelled = false
  const result = await runWorkflowTaskQueue([task('first'), task('second')], {
    canExecute: () => true,
    execute: async () => {
      cancelled = true
      return { run_id: 'run-first' }
    },
    stopRun: async runId => {
      events.push(`stop:${runId}`)
    },
    waitForRun: async runId => {
      events.push(`wait:${runId}`)
      return run(runId, 'stopped')
    },
    isCancelled: () => cancelled,
  })

  assert.deepEqual(events, ['stop:run-first', 'wait:run-first'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['stopped'])
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})

test('runWorkflowTaskQueue emits task and run lifecycle callbacks in order', async () => {
  const events: string[] = []
  const result = await runWorkflowTaskQueue([task('first')], {
    canExecute: () => true,
    execute: async () => ({ run_id: 'run-first' }),
    waitForRun: async (runId, onUpdate) => {
      events.push(`wait:${runId}`)
      await onUpdate?.(run('run-first', 'running'))
      return run('run-first', 'completed')
    },
    onTaskStart: current => events.push(`task-start:${current.task_key}`),
    onRunStart: (current, runId) => events.push(`run-start:${current.task_key}:${runId}`),
    onRunUpdate: (_current, currentRun) => events.push(`run-update:${currentRun.status}`),
    onTaskFinish: outcome => events.push(`task-finish:${outcome.status}`),
  })

  assert.deepEqual(events, [
    'task-start:first',
    'run-start:first:run-first',
    'wait:run-first',
    'run-update:running',
    'task-finish:success',
  ])
  assert.equal(result.outcomes[0].status, 'success')
})

test('runWorkflowTaskQueue skips unavailable tasks and continues after a task failure', async () => {
  const executed: string[] = []
  const result = await runWorkflowTaskQueue([task('skip', 'completed'), task('bad'), task('good')], {
    canExecute: current => current.status === 'pending',
    execute: async current => {
      executed.push(current.task_key)
      if (current.task_key === 'bad') throw new Error('task failed')
      return { run_id: 'run-good' }
    },
    waitForRun: async runId => run(runId, 'completed'),
    shouldStopOnError: () => false,
  })

  assert.deepEqual(executed, ['bad', 'good'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['skipped', 'failed', 'success'])
  assert.equal(result.stopped, false)
})

test('runWorkflowTaskQueue queues stale tasks for incremental execution', async () => {
  const modes: string[] = []
  const result = await runWorkflowTaskQueue([task('stale', 'stale')], {
    canExecute: current => current.status === 'pending' || current.status === 'stale',
    execute: async current => {
      modes.push(current.status === 'stale' ? 'incremental' : 'normal')
      return { run_id: 'run-stale' }
    },
    waitForRun: async runId => run(runId, 'completed'),
  })

  assert.deepEqual(modes, ['incremental'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['success'])
})

test('runWorkflowTaskQueue stops after a transport or conflict error', async () => {
  const executed: string[] = []
  const result = await runWorkflowTaskQueue([task('first'), task('second')], {
    canExecute: () => true,
    execute: async current => {
      executed.push(current.task_key)
      throw new Error('workflow is already running')
    },
    waitForRun: async runId => run(runId, 'completed'),
    shouldStopOnError: error => error instanceof Error && error.message.includes('already running'),
  })

  assert.deepEqual(executed, ['first'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['failed'])
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})

test('runWorkflowTaskQueue stops when the page queue is cancelled', async () => {
  let cancelled = false
  const result = await runWorkflowTaskQueue([task('first'), task('second')], {
    canExecute: () => true,
    execute: async current => {
      if (current.task_key === 'first') cancelled = true
      return { run_id: `run-${current.task_key}` }
    },
    waitForRun: async runId => run(runId, 'completed'),
    isCancelled: () => cancelled,
  })

  assert.deepEqual(result.outcomes.map(item => item.task.task_key), ['first'])
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})

test('runWorkflowTaskResetQueue runs resets serially and stops on a transport error', async () => {
  const events: string[] = []
  const result = await runWorkflowTaskResetQueue([task('first'), task('second')], {
    canReset: () => true,
    reset: async current => {
      events.push(`reset:${current.task_key}`)
      throw new Error('409: workflow is already running')
    },
    shouldStopOnError: error => error instanceof Error && error.message.startsWith('409:'),
  })

  assert.deepEqual(events, ['reset:first'])
  assert.deepEqual(result.outcomes.map(item => item.status), ['failed'])
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})
