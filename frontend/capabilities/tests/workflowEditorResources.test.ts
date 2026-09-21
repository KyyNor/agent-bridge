import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')

test('workflow editor loads the logged-in-readable backend catalog', () => {
  // 编辑器对归属组普通用户开放，因此不能依赖管理员专用的
  // /agent-runtime/config；后端下拉只取登录即可读的后端目录。
  const view = readFileSync(resolve(root, 'src/views/workflow/WorkflowView.vue'), 'utf8')
  assert.doesNotMatch(view, /getAgentRuntimeConfig/)
  assert.match(view, /api\.listAgentBackends\(\)/)

  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf8')
  assert.match(
    client,
    /listAgentBackends: \(\) => get<AgentBackendCatalog>\('\/agent-runtime\/backends'\)/,
  )
})
