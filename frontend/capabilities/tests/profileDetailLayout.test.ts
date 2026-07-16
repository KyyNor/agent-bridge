import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const appSource = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const profileSource = readFileSync(resolve(root, 'src/views/capabilities/ProfilesView.vue'), 'utf8')

test('profiles routes provide a dedicated detail component', () => {
  assert.match(appSource, /<ProfilesView[^>]*:route-key="subRoute"/)
  assert.match(profileSource, /ProfileDetailView/)
  assert.match(profileSource, /requestListNavigation/)
})
