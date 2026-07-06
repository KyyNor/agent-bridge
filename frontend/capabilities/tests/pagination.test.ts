import assert from 'node:assert/strict'
import test from 'node:test'

import { clampPage, pageCount, paginate } from '../src/lib/pagination.ts'

test('pageCount returns at least one page', () => {
  assert.equal(pageCount(0, 10), 1)
  assert.equal(pageCount(21, 10), 3)
})

test('clampPage keeps page inside available range', () => {
  assert.equal(clampPage(0, 100, 10), 1)
  assert.equal(clampPage(99, 21, 10), 3)
})

test('paginate returns the requested page slice', () => {
  assert.deepEqual(paginate([1, 2, 3, 4, 5], 2, 2), [3, 4])
})
