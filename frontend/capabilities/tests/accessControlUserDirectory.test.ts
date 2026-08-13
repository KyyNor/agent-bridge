import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const source = (path: string) => readFileSync(resolve(root, 'src', path), 'utf8')

test('小组权限把用户目录、归属列表和小组维护页拆开', () => {
  const view = source('views/system/AccessControlView.vue')
  const groups = source('views/system/AccessGroupManagementView.vue')
  const router = source('router/index.ts')

  assert.match(view, /api\.listAccessUsers/)
  assert.match(view, /api\.createAccessUser/)
  assert.match(view, /维护小组/)
  assert.match(view, /<AccessGroupManagementView/)
  assert.match(router, /path: '\/access-control\/:routeKey\(\.\*\)\*'/)
  assert.match(groups, /展示名称/)
  assert.match(groups, /小组 ID/)
})

test('换组在用户列表当前行完成，取消归属不是删除用户', () => {
  const view = source('views/system/AccessControlView.vue')

  assert.match(view, /beginMembershipEdit/)
  assert.match(view, /确认保存/)
  assert.match(view, /暂不分配/)
  assert.match(view, /group_key: editingGroupKey\.value === UNASSIGNED_GROUP \? null/)
  assert.doesNotMatch(view, /Trash2|removeMembership|deleteUserGroup/)
})

test('删除有成员的小组时先提示并拒绝删除', () => {
  const groups = source('views/system/AccessGroupManagementView.vue')

  assert.match(groups, /group\.member_count > 0/)
  assert.match(groups, /请先在用户列表中为这些成员换组或选择“暂不分配”/)
  assert.match(groups, /api\.deleteAccessGroup/)
})
