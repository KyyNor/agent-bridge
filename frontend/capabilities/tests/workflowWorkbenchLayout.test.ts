import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
test('workflow detail uses the real layered workbench component and all information tabs', () => {
  const file = readFileSync(resolve(root, 'src/views/workflow/WorkflowView.vue'), 'utf-8')

  assert.match(file, /<SegmentedTabs v-model="detailTab" :tabs="detailTabs"/)
  assert.match(file, /key: 'overview'/)
  assert.match(file, /key: 'tasks'/)
  assert.match(file, /key: 'artifacts'/)
  assert.match(file, /key: 'runs'/)
  assert.match(file, /key: 'versions'/)
  assert.match(file, /<WorkflowEditorCanvas/)
  assert.match(file, /<PaginationBar/)
})

test('workflow detail implements the approved layered workbench without replacing deep-link routes', () => {
  const workflowPath = resolve(root, 'src/views/workflow/WorkflowView.vue')
  const file = readFileSync(workflowPath, 'utf-8')
  const progress = readFileSync(resolve(root, 'src/composables/useWorkflowRunProgress.ts'), 'utf-8')

  assert.match(file, /import SegmentedTabs from ['"]\.\.\/\.\.\/components\/SegmentedTabs\.vue['"]/)
  assert.match(file, /import StatCard from ['"]\.\.\/\.\.\/components\/StatCard\.vue['"]/)
  assert.match(file, /const detailTab = ref<'overview' \| 'tasks' \| 'artifacts' \| 'runs' \| 'versions'>\('overview'\)/)
  assert.match(file, /const detailTabs = computed\(/)
  assert.match(file, /async function prepareDetail\(item: WorkflowDefinition\)[\s\S]*await loadRecentArtifacts\(\)/)
  assert.match(file, /if \(value === 'tasks'[\s\S]*await loadTasks\(/)
  assert.match(file, /if \(value === 'artifacts'\) await searchArtifacts\(\)/)
  assert.match(file, /if \(value === 'runs'[\s\S]*await loadRuns\(/)
  assert.match(file, /<SegmentedTabs v-model="detailTab" :tabs="detailTabs"/)
  assert.match(file, /detailTab === 'overview'/)
  assert.match(file, /detailTab === 'artifacts'/)
  assert.match(file, /detailTab === 'runs'/)
  assert.match(file, /routeMode === 'tasks' \|\| \(routeMode === 'detail' && detailTab === 'tasks'\)/)
  assert.match(progress, /navigateTo\(`workflow\/\$\{item\.workflow_key\}\/progress\/\$\{run\.run_id\}`\)/)
})

test('workflow detail header uses the shared large control size for its primary action', () => {
  const workflowPath = resolve(root, 'src/views/workflow/WorkflowView.vue')
  const file = readFileSync(workflowPath, 'utf-8')

  assert.match(file, /v-if="runningRunFor\(selectedWorkflow\.workflow_key\)"[\s\S]*?variant="default"[\s\S]*?size="lg"/)
  assert.match(file, /v-else[\s\S]*?size="lg"[\s\S]*?:disabled="hasAnyRunningRun"/)
})

test('workflow view error surfaces use the destructive soft token pair', () => {
  const workflowPath = resolve(root, 'src/views/workflow/WorkflowView.vue')
  const file = readFileSync(workflowPath, 'utf-8')

  assert.doesNotMatch(file, /bg-destructive\/(?:5|10)/)
  assert.match(file, /bg-destructive-soft/)
  assert.match(file, /text-destructive-soft-fg/)
})

test('workflow editor refreshes detail and sends an optimistic edit version', () => {
  const workflowPath = resolve(root, 'src/views/workflow/WorkflowView.vue')
  const editorPath = resolve(root, 'src/composables/useWorkflowEditorState.ts')
  const view = readFileSync(workflowPath, 'utf-8')
  const editor = readFileSync(editorPath, 'utf-8')

  assert.match(
    view,
    /async function ensureWorkflowDetail[\s\S]*const detail = await api\.getWorkflow\(workflow\.workflow_key\)/,
  )
  assert.doesNotMatch(view, /if \(workflow\.definition\) return workflow/)
  assert.match(editor, /expectedEditVersion\.value = Number\.isInteger\(item\.edit_version\)/)
  assert.match(editor, /expected_edit_version: expectedEditVersion\.value/)
})
