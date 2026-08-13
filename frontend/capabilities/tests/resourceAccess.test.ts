import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canModifyResource,
  isSharedResourceReadOnly,
  SHARED_RESOURCE_BADGE_CLASS,
  SHARED_RESOURCE_READ_ONLY_HINT,
} from '../src/lib/resourceAccess'

const groupA = {
  user_id: 'alice',
  group_key: 'team-a',
  group_name: 'A 组',
  is_maintenance_admin: false,
}

test('共享资源对归属组可写、对其他组只读', () => {
  const owned = { owner_group_key: 'team-a', visibility: 'shared' as const }
  const foreign = { owner_group_key: 'team-b', visibility: 'shared' as const }

  assert.equal(canModifyResource(groupA, owned), true)
  assert.equal(isSharedResourceReadOnly(groupA, owned), false)
  assert.equal(canModifyResource(groupA, foreign), false)
  assert.equal(isSharedResourceReadOnly(groupA, foreign), true)
  assert.equal(SHARED_RESOURCE_READ_ONLY_HINT, '共享资源只能查看和使用，不能修改')
  assert.equal(SHARED_RESOURCE_BADGE_CLASS, 'bg-info-soft text-info-soft-fg')
})

test('维护管理员可以修改所有资源，组内私有资源不误标成共享只读', () => {
  const admin = { ...groupA, is_maintenance_admin: true }
  const shared = { owner_group_key: 'team-b', visibility: 'shared' as const }
  const privateResource = { owner_group_key: 'team-b', visibility: 'group' as const }

  assert.equal(canModifyResource(admin, shared), true)
  assert.equal(isSharedResourceReadOnly(admin, shared), false)
  assert.equal(canModifyResource(groupA, privateResource), false)
  assert.equal(isSharedResourceReadOnly(groupA, privateResource), false)
})
