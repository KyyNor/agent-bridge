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

test('workflow detail follows the selected Agent tab when loading prompt and result', () => {
  const progress = readSource('composables/useWorkflowRunProgress.ts')
  const workflow = readSource('views/workflow/WorkflowView.vue')

  assert.match(progress, /const progressAgentRunDetail = ref<AgentRun \| null>\(null\)/)
  assert.match(progress, /const progressAgentRunDetailLoading = ref\(false\)/)
  assert.match(progress, /const progressAgentRunDetailError = ref\(''\)/)
  assert.match(progress, /api\.getAgentRun\(agentRunKey\)/)
  assert.match(progress, /agentRunKey !== progressAgentRunKey\.value/)
  assert.match(progress, /progressAgentRunDetail,/)
  assert.match(workflow, /progressAgentRunDetail,/)
  assert.equal((workflow.match(/:agent-run-detail="progressAgentRunDetail"/g) || []).length, 2)
  assert.equal((workflow.match(/:agent-run-detail-loading="progressAgentRunDetailLoading"/g) || []).length, 2)
  assert.equal((workflow.match(/:agent-run-detail-error="progressAgentRunDetailError"/g) || []).length, 2)
})

test('expanded task logs cache and display their main Agent detail', () => {
  const tasks = readSource('composables/useWorkflowTasks.ts')
  const workflow = readSource('views/workflow/WorkflowView.vue')

  assert.match(tasks, /const taskRunDetails = ref<Record<string, AgentRun>>\(\{\}\)/)
  assert.match(tasks, /function taskAgentRun\(task: WorkflowTask\)/)
  assert.match(tasks, /taskRunDetails\.value = \{[\s\S]*\[task\.lease_run_id\]: agentRun/)
  assert.match(tasks, /taskAgentRun,/)
  assert.match(workflow, /<AgentRunExecutionPanel :run="taskAgentRun\(task\)" :loading="isTaskLogLoading\(task\)" \/>/)
})
