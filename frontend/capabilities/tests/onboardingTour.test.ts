import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { workflowFirstUseTour } from '../src/lib/onboardingTours'

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
