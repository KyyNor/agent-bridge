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
  const router = source('router/index.ts')
  const notFound = source('views/NotFoundView.vue')
  assert.match(router, /loadingComponent: LoadingState/)
  assert.match(router, /errorComponent: AsyncViewError/)
  assert.match(app, /<AppErrorBoundary/)
  assert.match(notFound, /title="页面不存在"/)
  assert.match(app, /<ToastViewport/)
})

test('error boundary explains authentication and authorization failures separately from rendering errors', () => {
  const app = source('App.vue')
  const boundary = source('components/AppErrorBoundary.vue')
  const client = source('api/client.ts')
  const accessFeedback = source('lib/accessFeedback.ts')

  assert.match(client, /export class HttpRequestError extends Error/)
  assert.match(client, /reportAuthenticationRequired\(status\)/)
  assert.match(client, /throw httpError\(r\.status, await r\.text\(\)\)/)
  assert.match(accessFeedback, /export const authenticationRequired = ref\(false\)/)
  assert.match(app, /v-if="authenticationRequired"/)
  assert.match(app, /authenticationRequiredPresentation\.description/)
  assert.match(boundary, /status === 401/)
  assert.match(accessFeedback, /需要先完成登录/)
  assert.match(accessFeedback, /统一登录入口重新进入 Agent Bridge/)
  assert.match(boundary, /status === 403/)
  assert.match(accessFeedback, /暂无页面访问权限/)
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
