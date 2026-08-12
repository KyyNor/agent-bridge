import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const source = (path: string) => readFileSync(resolve(root, 'src', path), 'utf8')

test('侧边栏始终提供管理员切换入口', () => {
  const app = source('App.vue')
  const shell = source('components/AppShell.vue')
  const control = source('components/AdminAccessControl.vue')

  assert.match(app, /<AdminAccessControl/)
  assert.match(shell, /<slot name="footer"/)
  assert.match(control, /首次设置管理员密码/)
  assert.match(control, /切换为管理员/)
  assert.match(control, /api\.createAdminSession/)
  assert.match(control, /api\.deleteAdminSession/)
})

test('系统管理页面提供管理员改密并说明会话失效', () => {
  const view = source('views/knowledge/KnowledgeProcessingConfigView.vue')
  assert.match(view, /管理员访问密码/)
  assert.match(view, /api\.changeAdminPassword/)
  assert.match(view, /所有已签发的管理员会话会立即失效/)
  assert.match(view, /当前管理员密码/)
  assert.match(view, /确认新密码/)
})

test('前端管理员 API 契约覆盖状态、进入、退出和改密', () => {
  const client = source('api/client.ts')
  assert.match(client, /getAdminAccessStatus/)
  assert.match(client, /createAdminSession/)
  assert.match(client, /deleteAdminSession/)
  assert.match(client, /changeAdminPassword/)
})
