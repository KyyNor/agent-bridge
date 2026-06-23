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

test('shouldShowPageHeader hides the page title on service and workflow detail routes', () => {
  assert.equal(shouldShowPageHeader('services', 'new'), false)
  assert.equal(shouldShowPageHeader('services', 'edit/mcp_service/mysql'), false)
  assert.equal(shouldShowPageHeader('memory', 'dev-memory'), false)
  assert.equal(shouldShowPageHeader('workflow', 'new'), false)
  assert.equal(shouldShowPageHeader('workflow', 'sales_report/detail'), false)
  assert.equal(shouldShowPageHeader('workflow', 'sales_report/edit'), false)
  assert.equal(shouldShowPageHeader('workflow', 'sales_report/tasks'), false)
  assert.equal(shouldShowPageHeader('workflow', 'sales_report/progress/run-1'), false)
})

test('shouldShowPageHeader keeps the page title for plain non-script routes', () => {
  assert.equal(shouldShowPageHeader('workflow', ''), true)
  assert.equal(shouldShowPageHeader('services', ''), true)
  assert.equal(shouldShowPageHeader('tool-debug', ''), true)
})
