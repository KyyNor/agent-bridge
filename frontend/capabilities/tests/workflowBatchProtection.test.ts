import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const source = readFileSync(resolve(root, 'src/composables/useWorkflowTasks.ts'), 'utf8')

test('batch operations protect app navigation and browser unload while active', () => {
  assert.match(source, /registerNavigationGuard\(\(\) => \{[\s\S]*if \(!batchBusy\.value\) return true/)
  assert.match(source, /title: '批量操作进行中'/)
  assert.match(source, /addEventListener\('beforeunload', handleBeforeUnload\)/)
  assert.match(source, /event\.preventDefault\(\)/)
  assert.match(source, /event\.returnValue = ''/)
  assert.match(source, /removeEventListener\('beforeunload', handleBeforeUnload\)/)
})
