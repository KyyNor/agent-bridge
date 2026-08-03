import assert from 'node:assert/strict'
import test from 'node:test'
import { queryClientConfig, queryKeys } from '../src/lib/query'

test('query client defaults favour explicit refreshes over background retries', () => {
  const queries = queryClientConfig.defaultOptions?.queries
  assert.equal(queries?.staleTime, 15_000)
  assert.equal(queries?.gcTime, 300_000)
  assert.equal(queries?.retry, false)
  assert.equal(queries?.refetchOnWindowFocus, false)
  assert.equal(queryClientConfig.defaultOptions?.mutations?.retry, false)
})

test('query keys isolate each resource identity and filter set', () => {
  assert.notDeepEqual(
    queryKeys.toolCallLogs({ limit: 100, offset: 0, status: 'success' }),
    queryKeys.toolCallLogs({ limit: 100, offset: 100, status: 'success' }),
  )
  assert.notDeepEqual(queryKeys.agentRun('run-a'), queryKeys.agentRun('run-b'))
  assert.notDeepEqual(
    queryKeys.workflowArtifacts({ workflow_key: 'first', format: 'html' }),
    queryKeys.workflowArtifacts({ workflow_key: 'second', format: 'html' }),
  )
})
