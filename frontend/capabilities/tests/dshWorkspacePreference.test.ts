import assert from 'node:assert/strict'
import test from 'node:test'

import {
  readPreferredDshProfile,
  resolvePreferredDshProfile,
  writePreferredDshProfile,
} from '../src/lib/dshWorkspacePreference.ts'

/** 用可替换的内存 Storage 桩替换 window.localStorage。 */
function installStorageStub(initial: Record<string, string> = {}, options: { throws?: boolean } = {}) {
  const data = new Map(Object.entries(initial))
  const store: Storage = {
    get length() { return data.size },
    clear: () => data.clear(),
    getItem: (key: string) => (options.throws ? (() => { throw new Error('denied') })() : (data.get(key) ?? null)),
    key: (index: number) => [...data.keys()][index] ?? null,
    removeItem: (key: string) => { data.delete(key) },
    setItem: (key: string, value: string) => { data.set(key, value) },
  } as Storage
  const previous = (globalThis as any).window
  ;(globalThis as any).window = { localStorage: store }
  return {
    restore: () => { if (previous === undefined) delete (globalThis as any).window; else (globalThis as any).window = previous },
    dump: () => Object.fromEntries(data),
  }
}

test('write persists the selected profile key and empty string clears it', () => {
  const stub = installStorageStub()
  try {
    writePreferredDshProfile('profile-a')
    assert.equal(readPreferredDshProfile(), 'profile-a')
    writePreferredDshProfile('')
    assert.equal(readPreferredDshProfile(), '')
    assert.equal(stub.dump()['agent-bridge:dsh-workspace:profile'], undefined)
  } finally {
    stub.restore()
  }
})

test('resolve returns the remembered profile when it is still active', () => {
  const stub = installStorageStub({ 'agent-bridge:dsh-workspace:profile': 'profile-a' })
  try {
    assert.equal(resolvePreferredDshProfile(['profile-a', 'profile-b']), 'profile-a')
    // 记忆仍有效时不触盘清除。
    assert.equal(stub.dump()['agent-bridge:dsh-workspace:profile'], 'profile-a')
  } finally {
    stub.restore()
  }
})

test('resolve clears a stale memory when the profile is gone', () => {
  const stub = installStorageStub({ 'agent-bridge:dsh-workspace:profile': 'deleted-profile' })
  try {
    assert.equal(resolvePreferredDshProfile(['profile-b']), '')
    assert.equal(stub.dump()['agent-bridge:dsh-workspace:profile'], undefined)
  } finally {
    stub.restore()
  }
})

test('missing memory resolves to empty without touching storage', () => {
  const stub = installStorageStub()
  try {
    assert.equal(resolvePreferredDshProfile(['profile-a']), '')
    assert.deepEqual(stub.dump(), {})
  } finally {
    stub.restore()
  }
})

test('storage failures degrade to no memory instead of throwing', () => {
  // localStorage 抛异常（隐私模式等）：读返回 ''，写不抛。
  const stub = installStorageStub({}, { throws: true })
  try {
    assert.equal(readPreferredDshProfile(), '')
    assert.doesNotThrow(() => writePreferredDshProfile('profile-a'))
    assert.equal(resolvePreferredDshProfile(['profile-a']), '')
  } finally {
    stub.restore()
  }
})

test('no window (SSR/test env) degrades to no memory', () => {
  const previous = (globalThis as any).window
  delete (globalThis as any).window
  try {
    assert.equal(readPreferredDshProfile(), '')
    assert.doesNotThrow(() => writePreferredDshProfile('profile-a'))
    assert.equal(resolvePreferredDshProfile(['profile-a']), '')
  } finally {
    if (previous !== undefined) (globalThis as any).window = previous
  }
})
