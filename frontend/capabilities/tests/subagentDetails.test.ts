import assert from 'node:assert/strict'
import test from 'node:test'

import type { WorkflowSubagentDetail } from '../src/api/types.ts'
import { useSubagentDetails } from '../src/composables/useSubagentDetails.ts'

function detail(output: string): WorkflowSubagentDetail {
  return { task_id: 'task-1', agents: [], task_output: output, transcript_dir: '' }
}

test('子 Agent 详情按运行隔离、避免重复加载并支持静默刷新', async () => {
  const calls: string[] = []
  let revision = 1
  const state = useSubagentDetails(async (scopeId, taskId) => {
    calls.push(`${scopeId}:${taskId}`)
    return detail(`${scopeId}/${taskId}/v${revision}`)
  })

  await Promise.all([
    state.ensure('run-a', 'task-1'),
    state.ensure('run-a', 'task-1'),
  ])
  await state.ensure('run-b', 'task-1')

  assert.deepEqual(calls, ['run-a:task-1', 'run-b:task-1'])
  assert.equal(state.detailFor('run-a', 'task-1')?.task_output, 'run-a/task-1/v1')
  assert.equal(state.detailFor('run-b', 'task-1')?.task_output, 'run-b/task-1/v1')

  revision = 2
  await state.refreshLoaded('run-a')
  assert.equal(state.detailFor('run-a', 'task-1')?.task_output, 'run-a/task-1/v2')
  assert.equal(state.detailFor('run-b', 'task-1')?.task_output, 'run-b/task-1/v1')
})

test('子 Agent 详情加载失败时记录中文错误并清理加载状态', async () => {
  const state = useSubagentDetails(async () => {
    throw new Error('详情服务不可用')
  })

  await state.ensure('run-a', 'task-1')

  assert.equal(state.detailFor('run-a', 'task-1'), null)
  assert.equal(state.isLoading('run-a', 'task-1'), false)
  assert.equal(state.errorFor('run-a', 'task-1'), '详情服务不可用')
})
