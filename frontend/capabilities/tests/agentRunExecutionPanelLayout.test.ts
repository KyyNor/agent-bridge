import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const readSource = (path: string) => readFileSync(resolve(root, 'src', path), 'utf8')

test('Agent input/result panel is shared by standalone and workflow details', () => {
  const panel = readSource('components/AgentRunExecutionPanel.vue')
  const workflowDetail = readSource('components/WorkflowRunDetailPanel.vue')
  const agentRuns = readSource('views/monitoring/AgentRunsView.vue')

  assert.match(panel, /<PayloadDetailDialog/)
  assert.match(panel, /输入提示词/)
  assert.match(panel, /执行结果/)
  assert.match(panel, /border-border/)
  assert.match(panel, />详情</)
  assert.match(workflowDetail, /agentRunDetail/)
  assert.match(workflowDetail, /agentRunDetailLoading/)
  assert.match(workflowDetail, /agentRunDetailError/)
  assert.match(workflowDetail, /<AgentRunExecutionPanel[\s\S]*<RunEventTimeline/)
  assert.match(agentRuns, /<AgentRunExecutionPanel :run="detailRun"/)
})
