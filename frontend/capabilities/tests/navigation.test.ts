import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldShowPageHeader } from '../src/lib/navigation.ts'

test('shouldShowPageHeader keeps the page title on the scripts list route', () => {
  assert.equal(shouldShowPageHeader('scripts', ''), true)
})

test('shouldShowPageHeader hides the page title on script detail routes', () => {
  assert.equal(shouldShowPageHeader('scripts', 'test_dd'), false)
  assert.equal(shouldShowPageHeader('scripts', 'new'), false)
})

test('shouldShowPageHeader keeps the page title for non-script routes', () => {
  assert.equal(shouldShowPageHeader('workflow', ''), true)
  assert.equal(shouldShowPageHeader('workflow', 'abc'), true)
})
