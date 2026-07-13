import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import { parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const drawerPath = new URL('../src/views/workflow/WorkflowConfigDrawer.vue', import.meta.url)

async function loadDrawerModule() {
  const source = await readFile(drawerPath, 'utf8')
  const descriptor = parse(source, { filename: 'WorkflowConfigDrawer.vue' }).descriptor
  assert.ok(descriptor.script?.content, 'WorkflowConfigDrawer.vue must export drawer state helpers')
  const js = ts.transpileModule(descriptor.script.content, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  }).outputText
  return import(`data:text/javascript,${encodeURIComponent(js)}`)
}

test('drawer starts as overlay and can expand to full editor area', async () => {
  const { createWorkflowDrawerState, toggleDrawerFullscreen, closeDrawer } = await loadDrawerModule()

  const state = createWorkflowDrawerState()

  assert.equal(state.open, true)
  assert.equal(state.mode, 'overlay')
  toggleDrawerFullscreen(state)
  assert.equal(state.mode, 'fullscreen')
  closeDrawer(state)
  assert.equal(state.open, false)
})

test('drawer CSS uses overlay width and mobile fullscreen fallback', async () => {
  const source = await readFile(drawerPath, 'utf8')

  assert.match(source, /min\(560px,\s*52vw\)/)
  assert.match(source, /\.workflow-config-drawer--fullscreen/)
  assert.match(source, /@media\s*\(max-width:\s*1024px\)/)
})

test('workflow form keeps configuration in the editor drawer region', async () => {
  const view = await readFile(new URL('../src/views/workflow/WorkflowView.vue', import.meta.url), 'utf8')

  assert.ok(!view.includes('xl:grid-cols-[132px_minmax(0,1fr)_340px]'))
  assert.match(view, /workflow-editor-region/)
  assert.match(view, /<WorkflowConfigDrawer/)
  assert.match(view, /@select-node="selectWorkflowNode"/)
  assert.match(view, /@update:open="setConfigDrawerOpen"/)
})

test('node skill_names validation issue is surfaced in the skill picker', async () => {
  const panel = await readFile(new URL('../src/views/workflow/WorkflowNodeConfigPanel.vue', import.meta.url), 'utf8')

  assert.match(panel, /issueFor\('skill_names'\)/)
  assert.match(panel, /:aria-invalid="Boolean\(issueFor\('skill_names'\)\)"/)
  assert.match(panel, /技能配置有误/)
})
