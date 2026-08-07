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

test('model evaluation only polls while an evaluation is active', () => {
  const evaluations = source('views/system/ModelEvaluationView.vue')

  assert.match(evaluations, /queryKeys\.modelEvaluationRuns/)
  assert.match(evaluations, /refetchInterval/)
  assert.match(evaluations, /run\.status === 'queued' \|\| run\.status === 'running'/)
  assert.doesNotMatch(evaluations, /setInterval/)
  assert.doesNotMatch(evaluations, /refreshTimer/)
})

test('workflow artifact and run readers share the query cache', () => {
  const artifacts = source('composables/useWorkflowArtifacts.ts')
  const progress = source('composables/useWorkflowRunProgress.ts')

  assert.match(artifacts, /queryClient\.fetchQuery/)
  assert.match(artifacts, /queryKeys\.workflowArtifacts/)
  assert.match(artifacts, /cancelQueries/)
  assert.doesNotMatch(artifacts, /requestToken/)
  assert.match(progress, /queryKeys\.workflowRun/)
  assert.match(progress, /invalidateQueries\(\{ queryKey: \['workflow-artifacts'\] \}\)/)
})

test('knowledge catalog reads cache stable resources and invalidate after writes', () => {
  const knowledge = source('views/knowledge/KnowledgeView.vue')

  assert.match(knowledge, /queryKeys\.knowledgeBases/)
  assert.match(knowledge, /queryKeys\.knowledgeBackends/)
  assert.match(knowledge, /loadKbs\(\{ fresh: true \}\)/)
})
