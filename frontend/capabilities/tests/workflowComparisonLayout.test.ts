import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const comparisonPath = resolve(root, 'public/design/workflow-comparison.html')

test('workflow comparison prototype exposes old and new views with the full information model', () => {
  assert.ok(existsSync(comparisonPath), 'workflow comparison prototype should exist')
  const file = readFileSync(comparisonPath, 'utf-8')

  assert.match(file, /data-mode="split"/)
  assert.match(file, /data-mode="before"/)
  assert.match(file, /data-mode="after"/)
  assert.match(file, /当前页面：信息堆叠/)
  assert.match(file, /优化方案：分层工作台/)
  assert.match(file, /工作流图/)
  assert.match(file, /工作流产物/)
  assert.match(file, /运行记录/)
  assert.match(file, /任务队列/)
  assert.match(file, /--surface-page:/)
  assert.match(file, /prefers-reduced-motion/)
  assert.match(file, /function setMode\(/)
})

test('workflow detail implements the approved layered workbench without replacing deep-link routes', () => {
  const workflowPath = resolve(root, 'src/views/workflow/WorkflowView.vue')
  const file = readFileSync(workflowPath, 'utf-8')

  assert.match(file, /import SegmentedTabs from ['"]\.\.\/\.\.\/components\/SegmentedTabs\.vue['"]/)
  assert.match(file, /import StatCard from ['"]\.\.\/\.\.\/components\/StatCard\.vue['"]/)
  assert.match(file, /const detailTab = ref<'overview' \| 'tasks' \| 'artifacts' \| 'runs' \| 'versions'>\('overview'\)/)
  assert.match(file, /const detailTabs = computed\(/)
  assert.match(file, /await Promise\.all\(\[searchArtifacts\(\), loadRuns\(item\.workflow_key\), loadTasks\(item\.workflow_key\)\]\)/)
  assert.match(file, /<SegmentedTabs v-model="detailTab" :tabs="detailTabs"/)
  assert.match(file, /detailTab === 'overview'/)
  assert.match(file, /detailTab === 'artifacts'/)
  assert.match(file, /detailTab === 'runs'/)
  assert.match(file, /routeMode === 'tasks' \|\| \(routeMode === 'detail' && detailTab === 'tasks'\)/)
  assert.match(file, /window\.location\.hash = `workflow\/\$\{item\.workflow_key\}\/progress\/\$\{run\.run_id\}`/)
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
