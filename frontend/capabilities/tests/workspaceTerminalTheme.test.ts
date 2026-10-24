import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readTerminalTheme, resolveTerminalTheme, saveTerminalTheme } from '../src/lib/workspaceTerminalTheme'

test('终端配色选择可恢复；未知配色回落到界面默认', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const items = new Map<string, string>()
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    localStorage: { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => items.set(key, value) },
  } })
  try {
    assert.equal(readTerminalTheme(), 'midnight')
    saveTerminalTheme('ocean')
    assert.equal(readTerminalTheme(), 'ocean')
    assert.equal(resolveTerminalTheme(readTerminalTheme()).colors?.background, '#0c2933')
    saveTerminalTheme('system')
    assert.equal(readTerminalTheme(), 'midnight')
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('浏览器禁止本地存储时仍可获取配色，不阻塞终端', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    get localStorage() { throw new Error('blocked') },
  } })
  try {
    assert.equal(readTerminalTheme(), 'midnight')
    assert.doesNotThrow(() => saveTerminalTheme('paper'))
    assert.equal(resolveTerminalTheme('paper').colors?.background, '#f6f0e4')
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
