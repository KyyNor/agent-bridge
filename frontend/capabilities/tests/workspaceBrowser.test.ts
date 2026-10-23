import assert from 'node:assert/strict'
import { test } from 'node:test'
import { normalizeWorkspaceMode, supportsWebWorkspace } from '../src/lib/workspaceBrowser'
const features = { hasOwn: true, clone: true, colorMix: true }
test('Chrome 90 即使有垫片也不能进入第三方 Web，111 才放行', () => {
  assert.equal(supportsWebWorkspace('Chrome/90.0.4430.212', features), false)
  assert.equal(supportsWebWorkspace('Chrome/111.0.0.0', features), true)
  assert.equal(supportsWebWorkspace('Chrome/154.0.0.0', { ...features, colorMix: false }), false)
})
test('未知内核按特性检测；终端模式只接受显式值', () => {
  assert.equal(supportsWebWorkspace('Firefox/142', features), true)
  assert.equal(supportsWebWorkspace('CustomBrowser', { ...features, clone: false }), false)
  assert.equal(normalizeWorkspaceMode('terminal'), 'terminal')
  assert.equal(normalizeWorkspaceMode('bad'), 'web')
})
