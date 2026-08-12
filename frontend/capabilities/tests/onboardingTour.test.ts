import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import {
  knowledgeFirstUseTour,
  memoryFirstUseTour,
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

test('onboarding controller separates replay from automatic per-user persistence', () => {
  const composable = readFileSync(resolve(root, 'src/composables/useOnboardingTour.ts'), 'utf8')
  const view = readFileSync(resolve(root, 'src/views/workflow/WorkflowView.vue'), 'utf8')
  const replayButton = readFileSync(resolve(root, 'src/components/TourReplayButton.vue'), 'utf8')

  assert.match(composable, /getOnboardingTourProgress/)
  assert.match(composable, /saveOnboardingTourProgress/)
  assert.match(composable, /status: OnboardingTourStatus/)
  assert.match(composable, /TOUR_TARGET_WAIT_FRAMES/)
  assert.match(composable, /跳过导览/)
  assert.match(view, /<TourReplayButton :tour="workflowFirstUseTour" @start="startTour"/)
  assert.match(view, /data-tour="workflow-create"/)
  assert.match(view, /data-tour="workflow-import"/)
  assert.match(view, /data-tour="workflow-list"/)
  assert.match(replayButton, /defineProps<\{ tour: ProductTourDefinition \}>\(\)/)
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

test('management pages wait for stable loading, reuse the replay component, and declare their tour anchors', () => {
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
    assert.match(source, new RegExp(`<TourReplayButton :tour="${tour}" @start="startTour"`))
    assert.match(source, new RegExp(`maybeStartTour\\(${tour}\\)`))
    assert.match(source, /data-tour=/)
  }
})
