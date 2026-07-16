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

test('WorkflowView protects each design request with a run key and immediate stop', () => {
  const content = source('workflow/WorkflowView.vue')

  assert.match(content, /const designRunKey = ref\('\'\)/)
  assert.match(content, /run_key:\s*runKey/)
  assert.match(content, /api\.stopAgentRun\(runKey\)/)
  assert.match(content, /立即停止/)
  assert.match(content, /designRunKey\.value !== runKey \|\| designStopRequested\.value\) return/)
  assert.match(content, /designing \|\| !workflowDesignDraft/)
})
