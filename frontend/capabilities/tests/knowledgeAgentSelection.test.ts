import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isWeknoraBackend, resolveDefaultAgentId } from '../src/lib/knowledgeAgentSelection'

const backends = [
  { slug: 'weknora', backend_type: 'weknora', runtime_status: 'active', supports_agents: true },
  { slug: 'ragflow', backend_type: 'ragflow', runtime_status: 'active', supports_agents: false },
]

test('Weknora 后端取所选 Agent，显式清空置 null', () => {
  assert.equal(resolveDefaultAgentId({ backendSlug: 'weknora', agentId: 'a1', backends, savedAgentId: null }), 'a1')
  assert.equal(resolveDefaultAgentId({ backendSlug: 'weknora', agentId: '', backends, savedAgentId: 'old' }), null)
})

test('已知非 Weknora 后端清空 Agent', () => {
  assert.equal(resolveDefaultAgentId({ backendSlug: 'ragflow', agentId: '', backends, savedAgentId: 'old' }), null)
})

test('后端类型无法判定时保留已保存的 Agent（目录为空或选择自动）', () => {
  assert.equal(resolveDefaultAgentId({ backendSlug: 'weknora', agentId: '', backends: [], savedAgentId: 'old' }), 'old')
  assert.equal(resolveDefaultAgentId({ backendSlug: '', agentId: '', backends, savedAgentId: 'old' }), 'old')
  assert.equal(resolveDefaultAgentId({ backendSlug: 'weknora', agentId: '', backends: [], savedAgentId: null }), null)
})

test('isWeknoraBackend 只匹配 weknora 类型', () => {
  assert.equal(isWeknoraBackend('weknora', backends), true)
  assert.equal(isWeknoraBackend('ragflow', backends), false)
  assert.equal(isWeknoraBackend('', backends), false)
  assert.equal(isWeknoraBackend('weknora', []), false)
})