import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const source = (path: string) => readFileSync(resolve(root, 'src', path), 'utf8')

test('long editor pages keep their primary actions in one sticky action bar', () => {
  const actionBar = source('components/EditorActionBar.vue')
  assert.match(actionBar, /sticky top-0 z-30/)
  assert.match(actionBar, /bg-background\/95/)

  for (const path of [
    'views/system/ScriptsView.vue',
    'views/workflow/WorkflowView.vue',
    'views/system/SkillManagementView.vue',
    'views/capabilities/ServicesView.vue',
  ]) {
    const view = source(path)
    assert.match(view, /import EditorActionBar/)
    assert.match(view, /<EditorActionBar(?:\s|>)/)
  }
})
