import assert from 'node:assert/strict'
import test from 'node:test'

import {
  readPreferredSelection,
  resolvePreferredProfile,
  writePreferredSelection,
} from '../src/lib/dshWorkspacePreference.ts'

const SELECTION_KEY = 'agent-bridge:dsh-workspace:selection'
const LEGACY_KEY = 'agent-bridge:dsh-workspace:profile'

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

test('default selection is personal with empty profile', () => {
  const stub = installStorageStub()
  try {
    assert.deepEqual(readPreferredSelection(), { scope: 'personal', profileKey: '' })
  } finally {
    stub.restore()
  }
})

test('write persists scope and profile selection; empty profile keeps scope', () => {
  const stub = installStorageStub()
  try {
    writePreferredSelection({ scope: 'shared', profileKey: 'profile-a' })
    assert.deepEqual(readPreferredSelection(), { scope: 'shared', profileKey: 'profile-a' })
    writePreferredSelection({ scope: 'shared', profileKey: '' })
    assert.deepEqual(readPreferredSelection(), { scope: 'shared', profileKey: '' })
  } finally {
    stub.restore()
  }
})

test('legacy profile-only memory migrates to personal selection', () => {
  const stub = installStorageStub({ [LEGACY_KEY]: 'profile-a' })
  try {
    assert.deepEqual(readPreferredSelection(), { scope: 'personal', profileKey: 'profile-a' })
    // 写入新格式后旧键被清理。
    writePreferredSelection({ scope: 'personal', profileKey: 'profile-a' })
    assert.equal(stub.dump()[LEGACY_KEY], undefined)
    assert.ok(stub.dump()[SELECTION_KEY])
  } finally {
    stub.restore()
  }
})

test('resolve returns the remembered profile when it is still active', () => {
  const stub = installStorageStub({
    [SELECTION_KEY]: JSON.stringify({ scope: 'personal', profileKey: 'profile-a' }),
  })
  try {
    assert.equal(resolvePreferredProfile(['profile-a', 'profile-b']), 'profile-a')
    assert.equal(stub.dump()[SELECTION_KEY], JSON.stringify({ scope: 'personal', profileKey: 'profile-a' }))
  } finally {
    stub.restore()
  }
})

test('resolve clears a stale profile but keeps the remembered scope', () => {
  const stub = installStorageStub({
    [SELECTION_KEY]: JSON.stringify({ scope: 'shared', profileKey: 'deleted-profile' }),
  })
  try {
    assert.equal(resolvePreferredProfile(['profile-b']), '')
    assert.deepEqual(readPreferredSelection(), { scope: 'shared', profileKey: '' })
  } finally {
    stub.restore()
  }
})

test('corrupted storage degrades to defaults', () => {
  const stub = installStorageStub({ [SELECTION_KEY]: '{not json' })
  try {
    assert.deepEqual(readPreferredSelection(), { scope: 'personal', profileKey: '' })
  } finally {
    stub.restore()
  }
})

test('storage failures degrade to no memory instead of throwing', () => {
  // localStorage 抛异常（隐私模式等）：读返回默认，写不抛。
  const stub = installStorageStub({}, { throws: true })
  try {
    assert.deepEqual(readPreferredSelection(), { scope: 'personal', profileKey: '' })
    assert.doesNotThrow(() => writePreferredSelection({ scope: 'shared', profileKey: 'profile-a' }))
    assert.equal(resolvePreferredProfile(['profile-a']), '')
  } finally {
    stub.restore()
  }
})

test('no window (SSR/test env) degrades to no memory', () => {
  const previous = (globalThis as any).window
  delete (globalThis as any).window
  try {
    assert.deepEqual(readPreferredSelection(), { scope: 'personal', profileKey: '' })
    assert.doesNotThrow(() => writePreferredSelection({ scope: 'shared', profileKey: 'profile-a' }))
    assert.equal(resolvePreferredProfile(['profile-a']), '')
  } finally {
    if (previous !== undefined) (globalThis as any).window = previous
  }
})
