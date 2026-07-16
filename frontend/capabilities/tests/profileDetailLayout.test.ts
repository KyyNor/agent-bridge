import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const appSource = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const profileSource = readFileSync(resolve(root, 'src/views/capabilities/ProfilesView.vue'), 'utf8')
const detailSource = readFileSync(resolve(root, 'src/views/capabilities/ProfileDetailView.vue'), 'utf8')

test('profiles routes provide a dedicated detail component', () => {
  assert.match(appSource, /<ProfilesView[^>]*:route-key="subRoute"/)
  assert.match(profileSource, /ProfileDetailView/)
  assert.match(profileSource, /requestListNavigation/)
  assert.match(profileSource, /放弃未保存修改/)
  assert.match(detailSource, /h-\[calc\(100vh-3\.5rem\)\]/)
  assert.match(detailSource, /min-h-0 flex-1 overflow-y-auto/)
  assert.match(detailSource, /defineExpose\([\s\S]*hasUnsavedChanges/)
  assert.match(detailSource, />取消</)
  assert.match(detailSource, /确认/)
})
