import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import {
  knowledgeFirstUseTour,
  memoryFirstUseTour,
  onboardingTourForRoute,
  profileConfigFirstUseTour,
  scriptsFirstUseTour,
  servicesFirstUseTour,
  toolDebugFirstUseTour,
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
  assert.match(composable, /progressElement\.setAttribute\('aria-label', '导览进度'\)/)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-skip \{[^}]*background: var\(--popover\);[^}]*border: 1px solid var\(--border\);/s)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-progress progress/)
  assert.match(styles, /\.agent-bridge-tour-popover \.agent-bridge-tour-skip:focus-visible/)
  assert.match(sidebarButton, /onboardingTourForRoute\(route\.name, route\.params\.routeKey\)/)
  assert.match(sidebarButton, /startTour\(tour\.value\)/)
  assert.match(sidebarButton, /当前页面指南/)
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
  assert.equal(onboardingTourForRoute('workflow', ['daily-report']), null)
  assert.equal(onboardingTourForRoute('knowledge', []), knowledgeFirstUseTour)
  assert.equal(onboardingTourForRoute('knowledge', ['docs']), null)
  assert.equal(onboardingTourForRoute('services', []), servicesFirstUseTour)
  assert.equal(onboardingTourForRoute('tool-debug', undefined), toolDebugFirstUseTour)
  assert.equal(onboardingTourForRoute('profiles', ['default']), profileConfigFirstUseTour)
  assert.equal(onboardingTourForRoute('memory', []), memoryFirstUseTour)
  assert.equal(onboardingTourForRoute('scripts', ['builtin', 'edit']), scriptsFirstUseTour)
  assert.equal(onboardingTourForRoute('dashboard', undefined), null)
})

test('additional management tours are independently versioned and use stable anchors', () => {
  const tours = [
    knowledgeFirstUseTour,
    servicesFirstUseTour,
    toolDebugFirstUseTour,
    profileConfigFirstUseTour,
    workflowEditorFirstUseTour,
    memoryFirstUseTour,
    scriptsFirstUseTour,
  ]
  assert.equal(new Set(tours.map(tour => tour.key)).size, tours.length)
  for (const tour of tours) {
    assert.equal(tour.version, 1)
    assert.ok(tour.steps.length >= 3 && tour.steps.length <= 5)
    for (const step of tour.steps) assert.match(step.element, /^\[data-tour="[a-z0-9-]+"\]$/)
  }
})

test('management pages wait for stable loading and keep their automatic tour anchors', () => {
  const cases = [
    ['views/knowledge/KnowledgeView.vue', 'knowledgeFirstUseTour'],
    ['views/capabilities/ServicesView.vue', 'servicesFirstUseTour'],
    ['views/capabilities/ToolDebugView.vue', 'toolDebugFirstUseTour'],
    ['views/capabilities/ProfileDetailView.vue', 'profileConfigFirstUseTour'],
    ['views/workflow/WorkflowView.vue', 'workflowEditorFirstUseTour'],
    ['views/knowledge/MemoryView.vue', 'memoryFirstUseTour'],
    ['views/system/ScriptsView.vue', 'scriptsFirstUseTour'],
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
