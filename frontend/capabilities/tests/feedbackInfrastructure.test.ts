import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')

function source(path: string) {
  return readFileSync(resolve(root, 'src', path), 'utf8')
}

test('app provides loading, error boundary, 404, and toast feedback at the shell level', () => {
  const app = source('App.vue')
  assert.match(app, /loadingComponent: LoadingState/)
  assert.match(app, /errorComponent: AsyncViewError/)
  assert.match(app, /<AppErrorBoundary/)
  assert.match(app, /title="页面不存在"/)
  assert.match(app, /<ToastViewport/)
})

test('shared feedback primitives and toast store are available to views', () => {
  for (const path of [
    'components/ui/feedback/LoadingState.vue',
    'components/ui/feedback/EmptyState.vue',
    'components/ui/feedback/ErrorState.vue',
    'components/ui/toast/ToastViewport.vue',
    'composables/useToast.ts',
  ]) assert.ok(source(path).length > 0, `${path} must exist`)

  const workflow = source('views/workflow/WorkflowView.vue')
  const editor = source('composables/useWorkflowEditorState.ts')
  assert.match(workflow, /useToast/)
  assert.match(editor, /工作流已保存/)
})
