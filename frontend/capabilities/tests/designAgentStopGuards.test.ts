import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../src/views/', import.meta.url)

function source(view: string) {
  return readFileSync(new URL(view, root), 'utf8')
}

test('ScriptsView protects each design request with a run key and immediate stop', () => {
  const content = source('system/ScriptsView.vue')

  assert.match(content, /const designRunKey = ref\('\'\)/)
  assert.match(content, /run_key:\s*runKey/)
  assert.match(content, /api\.stopAgentRun\(runKey\)/)
  assert.match(content, /立即停止/)
  assert.match(content, /designRunKey\.value !== runKey \|\| designStopRequested\.value\) return/)
  assert.match(content, /designing \|\| !scriptDesignDraft/)
})

test('工作流设计器 protects each design request with a run key and immediate stop', () => {
  const content = readFileSync(new URL('../src/composables/useWorkflowDesigner.ts', import.meta.url), 'utf8')
  const view = source('workflow/WorkflowView.vue')

  assert.match(content, /const designRunKey = ref\('\'\)/)
  assert.match(content, /run_key:\s*runKey/)
  assert.match(content, /api\.stopAgentRun\(runKey\)/)
  assert.match(content, /designRunKey\.value !== runKey \|\| designStopRequested\.value\) return/)
  assert.match(view, /立即停止/)
  assert.match(view, /designing \|\| !workflowDesignDraft/)
})
