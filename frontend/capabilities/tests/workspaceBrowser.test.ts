import assert from 'node:assert/strict'
import { test } from 'node:test'
import { normalizeWorkspaceMode, webWorkspaceUnavailableReason } from '../src/lib/workspaceBrowser'
const features = { hasOwn: true, clone: true, colorMix: true }
test('Chrome 90 即使有垫片也不能进入第三方 Web，111 才放行', () => {
  const outdated = '浏览器内核版本低于 Chrome 111，无法使用 Web 模式。'
  assert.equal(webWorkspaceUnavailableReason('Chrome/90.0.4430.212', features), outdated)
  assert.equal(webWorkspaceUnavailableReason('Chromium/110.0.0.0', features), outdated)
  assert.equal(webWorkspaceUnavailableReason('Chrome/111.0.0.0', features), null)
  assert.equal(webWorkspaceUnavailableReason('Chrome/154.0.0.0', { ...features, colorMix: false }), '浏览器缺少所需功能，无法使用 Web 模式。')
})
test('未知内核按特性检测；终端模式只接受显式值', () => {
  assert.equal(webWorkspaceUnavailableReason('Firefox/142', features), null)
  assert.equal(webWorkspaceUnavailableReason('CustomBrowser', { ...features, clone: false }), '浏览器缺少所需功能，无法使用 Web 模式。')
  assert.equal(normalizeWorkspaceMode('terminal'), 'terminal')
  assert.equal(normalizeWorkspaceMode('bad'), 'web')
})
