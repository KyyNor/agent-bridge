import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { queryClientConfig, queryKeys } from '../src/lib/query'

const root = resolve(import.meta.dirname, '..')

function source(path: string) {
  return readFileSync(resolve(root, 'src', path), 'utf8')
}

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

test('monitoring pages use query keys instead of local request tokens', () => {
  const logs = source('views/monitoring/LogsView.vue')
  const stats = source('views/monitoring/StatsView.vue')
  const agentRuns = source('views/monitoring/AgentRunsView.vue')

  assert.match(logs, /useQuery/)
  assert.match(logs, /queryKeys\.toolCallLogs/)
  assert.doesNotMatch(logs, /listRequestToken/)
  assert.match(stats, /queryKeys\.toolCallStats/)
  assert.match(agentRuns, /queryKeys\.agentRuns/)
  assert.match(agentRuns, /queryKeys\.agentRunEvents/)
  assert.match(agentRuns, /refetchInterval/)
  assert.doesNotMatch(agentRuns, /detailEventsPoll/)
})
