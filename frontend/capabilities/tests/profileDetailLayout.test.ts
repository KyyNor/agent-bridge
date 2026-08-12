import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const appSource = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const routerSource = readFileSync(resolve(root, 'src/router/index.ts'), 'utf8')
const profileSource = readFileSync(resolve(root, 'src/views/capabilities/ProfilesView.vue'), 'utf8')
const detailSource = readFileSync(resolve(root, 'src/views/capabilities/ProfileDetailView.vue'), 'utf8')

test('profiles routes provide a dedicated detail component', () => {
  assert.match(routerSource, /path: '\/profiles\/:routeKey\(\.\*\)\*'/)
  assert.match(profileSource, /ProfileDetailView/)
  assert.match(profileSource, /requestListNavigation/)
  assert.match(profileSource, /放弃未保存修改/)
  assert.match(detailSource, /h-\[calc\(100vh-3\.5rem\)\]/)
  assert.match(detailSource, /min-h-0 flex-1 overflow-y-auto/)
  assert.match(detailSource, /defineExpose\([\s\S]*hasUnsavedChanges/)
  assert.match(detailSource, />取消</)
  assert.match(detailSource, /确认/)
})

test('Profile resources use searchable multi-selects and include business ledgers', () => {
  const multiSelect = readFileSync(resolve(root, 'src/components/SearchableMultiSelect.vue'), 'utf8')

  assert.match(detailSource, /api\.listBusinessLedgers\(\)/)
  assert.match(detailSource, /allBusinessLedgers/)
  assert.match(detailSource, /允许访问的业务台账/)
  assert.equal((detailSource.match(/<SearchableMultiSelect\s/g) || []).length, 4)
  assert.match(multiSelect, /搜索选项/)
  assert.match(multiSelect, /role="listbox"/)
  assert.match(multiSelect, /emit\('update:modelValue'/)
  assert.match(multiSelect, /absolute z-30 mt-1 w-full overflow-hidden rounded-md border border-border bg-popover/)
})
