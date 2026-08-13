import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import {
  knowledgeDetailFirstUseTour,
  knowledgeFirstUseTour,
  memoryFirstUseTour,
  onboardingTourForRoute,
  profileConfigFirstUseTour,
  scriptDetailFirstUseTour,
  scriptsFirstUseTour,
  servicesFirstUseTour,
  toolDebugFirstUseTour,
  workflowDetailFirstUseTour,
  workflowEditorFirstUseTour,
  workflowFirstUseTour,
} from '../src/lib/onboardingTours'

const root = resolve(import.meta.dirname, '..')

test('workflow first-use tour has a versioned script with stable UI anchors', () => {
  assert.equal(workflowFirstUseTour.key, 'workflow-first-use')
  assert.equal(workflowFirstUseTour.version, 1)
  assert.equal(workflowFirstUseTour.steps.length, 3)
  assert.deepEqual(
    workflowFirstUseTour.steps.map(step => step.element),
    [
      '[data-tour="workflow-create"]',
      '[data-tour="workflow-import"]',
      '[data-tour="workflow-list"]',
    ],
  )
})

test('onboarding controller separates sidebar replay from automatic per-user persistence', () => {
  const composable = readFileSync(resolve(root, 'src/composables/useOnboardingTour.ts'), 'utf8')
  const view = readFileSync(resolve(root, 'src/views/workflow/WorkflowView.vue'), 'utf8')
  const sidebarButton = readFileSync(resolve(root, 'src/components/SidebarTourButton.vue'), 'utf8')
  const app = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
  const styles = readFileSync(resolve(root, 'src/styles/base.css'), 'utf8')

  assert.match(composable, /getOnboardingTourProgress/)
  assert.match(composable, /saveOnboardingTourProgress/)
  assert.match(composable, /status: OnboardingTourStatus/)
  assert.match(composable, /TOUR_TARGET_WAIT_FRAMES/)
  assert.match(composable, /退出指南/)
  assert.match(composable, /driver-popover-footer-btn driver-popover-prev-btn agent-bridge-tour-skip/)
  assert.match(composable, /新手指南 · \$\{current\} \/ \$\{total\}/)
  assert.match(composable, /progressTrack\.setAttribute\('role', 'progressbar'\)/)
  assert.match(composable, /progress\.replaceChildren\(\.\.\.Array\.from\(\{ length: total \}/)
  assert.match(composable, /classList\.toggle\('is-complete', index < current\)/)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-skip \{[^}]*background: var\(--popover\);[^}]*border: 1px solid var\(--border\);/s)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-progress-track \{[^}]*gap: 4px;/s)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-skip:focus-visible/)
  assert.match(sidebarButton, /onboardingTourForRoute\(route\.name, route\.params\.routeKey\)/)
  assert.match(sidebarButton, /startTour\(tour\.value\)/)
  assert.match(sidebarButton, /查看指南/)
  assert.match(sidebarButton, /当前页面暂未配置指南/)
  assert.match(app, /<SidebarTourButton \/>/)
  assert.doesNotMatch(view, /TourReplayButton/)
  assert.match(view, /data-tour="workflow-create"/)
  assert.match(view, /data-tour="workflow-import"/)
  assert.match(view, /data-tour="workflow-list"/)
})

test('sidebar guide selects the tour matching the current route and mode', () => {
  assert.equal(onboardingTourForRoute('workflow', []), workflowFirstUseTour)
  assert.equal(onboardingTourForRoute('workflow', ['new']), workflowEditorFirstUseTour)
  assert.equal(onboardingTourForRoute('workflow', ['daily-report', 'edit']), workflowEditorFirstUseTour)
  assert.equal(onboardingTourForRoute('workflow', ['daily-report', 'detail']), workflowDetailFirstUseTour)
  assert.equal(onboardingTourForRoute('workflow', ['daily-report']), workflowDetailFirstUseTour)
  assert.equal(onboardingTourForRoute('workflow', ['daily-report', 'tasks']), null)
  assert.equal(onboardingTourForRoute('knowledge', []), knowledgeFirstUseTour)
  assert.equal(onboardingTourForRoute('knowledge', ['docs']), knowledgeDetailFirstUseTour)
  assert.equal(onboardingTourForRoute('services', []), servicesFirstUseTour)
  assert.equal(onboardingTourForRoute('tool-debug', undefined), toolDebugFirstUseTour)
  assert.equal(onboardingTourForRoute('profiles', ['default']), profileConfigFirstUseTour)
  assert.equal(onboardingTourForRoute('memory', []), memoryFirstUseTour)
  assert.equal(onboardingTourForRoute('scripts', ['builtin', 'edit']), scriptDetailFirstUseTour)
  assert.equal(onboardingTourForRoute('dashboard', undefined), null)
})

test('additional management tours are independently versioned and use stable anchors', () => {
  const tours = [
    knowledgeFirstUseTour,
    knowledgeDetailFirstUseTour,
    servicesFirstUseTour,
    toolDebugFirstUseTour,
    profileConfigFirstUseTour,
    workflowEditorFirstUseTour,
    workflowDetailFirstUseTour,
    memoryFirstUseTour,
    scriptsFirstUseTour,
    scriptDetailFirstUseTour,
  ]
  assert.equal(new Set(tours.map(tour => tour.key)).size, tours.length)
  for (const tour of tours) {
    assert.ok(Number.isInteger(tour.version) && tour.version >= 1)
    assert.ok(tour.steps.length >= 2 && tour.steps.length <= 8)
    for (const step of tour.steps) assert.match(step.element, /^\[data-tour="[a-z0-9-]+"\]$/)
  }
})

test('management pages wait for stable loading and keep their automatic tour anchors', () => {
  const cases = [
    ['views/knowledge/KnowledgeView.vue', 'knowledgeFirstUseTour'],
    ['views/knowledge/KnowledgeView.vue', 'knowledgeDetailFirstUseTour'],
    ['views/capabilities/ServicesView.vue', 'servicesFirstUseTour'],
    ['views/capabilities/ToolDebugView.vue', 'toolDebugFirstUseTour'],
    ['views/capabilities/ProfileDetailView.vue', 'profileConfigFirstUseTour'],
    ['views/workflow/WorkflowView.vue', 'workflowEditorFirstUseTour'],
    ['views/workflow/WorkflowView.vue', 'workflowDetailFirstUseTour'],
    ['views/knowledge/MemoryView.vue', 'memoryFirstUseTour'],
    ['views/system/ScriptsView.vue', 'scriptsFirstUseTour'],
    ['views/system/ScriptsView.vue', 'scriptDetailFirstUseTour'],
  ] as const
  const composable = readFileSync(resolve(root, 'src/composables/useOnboardingTour.ts'), 'utf8')
  assert.match(composable, /仅展示已稳定出现的步骤/)
  assert.match(composable, /filter\(step => document\.querySelector\(step\.element\)\)/)
  for (const [path, tour] of cases) {
    const source = readFileSync(resolve(root, 'src', path), 'utf8')
    assert.match(source, new RegExp(`maybeStartTour\\(${tour}\\)`))
    assert.match(source, /data-tour=/)
    assert.doesNotMatch(source, /TourReplayButton/)
  }
})

test('workflow tours open temporary previews and clean them up without changing business data', () => {
  const definitions = readFileSync(resolve(root, 'src/lib/onboardingTours.ts'), 'utf8')
  const controller = readFileSync(resolve(root, 'src/composables/useOnboardingTour.ts'), 'utf8')
  const workflow = readFileSync(resolve(root, 'src/views/workflow/WorkflowView.vue'), 'utf8')
  const preview = readFileSync(resolve(root, 'src/views/workflow/WorkflowDetailTourPreview.vue'), 'utf8')

  assert.match(definitions, /startAction: 'workflow-editor:agent-preview:start'/)
  assert.match(definitions, /endAction: 'workflow-editor:agent-preview:stop'/)
  assert.match(definitions, /data-tour="workflow-editor-agent-panel"/)
  for (const tab of ['overview', 'tasks', 'artifacts', 'runs', 'versions']) {
    assert.match(definitions, new RegExp(`action: 'workflow-detail:preview:${tab}'`))
  }
  assert.match(controller, /dispatchTourAction\(tour\.startAction\)/)
  assert.match(controller, /dispatchTourAction\(tour\.endAction\)/)
  assert.match(workflow, /workflowEditorTourAgentNode/)
  assert.match(workflow, /workflowDetailTourPreview/)
  assert.match(preview, /以下为临时示例数据，退出指南后恢复真实页面/)
})
