import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const readSource = (path: string) => readFileSync(`${root}/src/${path}`, 'utf8')

test('reuses a sticky Agent tabs header across workflow run contexts', () => {
  const tabsPath = `${root}/src/components/AgentRunTabs.vue`
  assert.ok(existsSync(tabsPath), 'AgentRunTabs.vue should exist as the reusable header')
  const tabs = readFileSync(tabsPath, 'utf8')
  const detail = readSource('components/WorkflowRunDetailPanel.vue')
  const workflow = readSource('views/workflow/WorkflowView.vue')

  assert.match(tabs, /workflow-agent-tabs/)
  assert.match(tabs, /sticky top-0 z-30/)
  assert.match(tabs, /Agent 输出/)
  assert.match(tabs, /select-agent-run/)
  assert.match(tabs, /refresh/)
  assert.match(detail, /showHeader/)
  assert.match(detail, /AgentRunTabs/)
  assert.match(workflow, /workflow-batch-run-context sticky top-0 z-30/)
  assert.match(workflow, /workflow-progress-agent-context sticky top-0 z-30/)
  assert.equal((workflow.match(/<AgentRunTabs/g) || []).length, 2)
  assert.equal((workflow.match(/:show-header="false"/g) || []).length, 2)
})

test('keeps the App content host as the actual viewport scroll container', () => {
  const appShell = readSource('components/AppShell.vue')
  const app = readSource('App.vue')

  assert.match(appShell, /class="flex h-screen min-h-0 overflow-hidden"/)
  assert.match(appShell, /class="ml-\[210px\] flex h-screen min-h-0 min-w-0 flex-1 flex-col"/)
  assert.match(app, /class="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto"/)
})
