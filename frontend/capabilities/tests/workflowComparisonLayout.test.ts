import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const comparisonPath = resolve(root, 'public/design/workflow-comparison.html')

test('workflow comparison prototype exposes old and new views with the full information model', () => {
  assert.ok(existsSync(comparisonPath), 'workflow comparison prototype should exist')
  const file = readFileSync(comparisonPath, 'utf-8')

  assert.match(file, /data-mode="split"/)
  assert.match(file, /data-mode="before"/)
  assert.match(file, /data-mode="after"/)
  assert.match(file, /当前页面：信息堆叠/)
  assert.match(file, /优化方案：分层工作台/)
  assert.match(file, /工作流图/)
  assert.match(file, /工作流产物/)
  assert.match(file, /运行记录/)
  assert.match(file, /任务队列/)
  assert.match(file, /--surface-page:/)
  assert.match(file, /prefers-reduced-motion/)
  assert.match(file, /function setMode\(/)
})

