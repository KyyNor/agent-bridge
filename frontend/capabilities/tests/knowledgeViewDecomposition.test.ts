import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const source = (path: string) => readFileSync(resolve(root, path), 'utf-8')

test('knowledge view assembles independent settings, retrieval, detail, and sync regions', () => {
  const view = source('src/views/knowledge/KnowledgeView.vue')
  assert.match(view, /KnowledgeDefaultBackendPanel/)
  assert.match(view, /KnowledgePlaneDialog/)
  assert.match(view, /KnowledgeSearchPanel/)
  assert.match(view, /KnowledgeDocumentDetailDialog/)
  assert.match(view, /KnowledgeSyncJobsPanel/)
  assert.ok(view.split('\n').length < 1200)
})

test('knowledge subcomponents retain their explicit API boundaries', () => {
  assert.match(source('src/components/knowledge/KnowledgeDefaultBackendPanel.vue'), /api\.updateKbDefaults/)
  assert.match(source('src/components/knowledge/KnowledgePlaneDialog.vue'), /api\.setResourceProfiles/)
  assert.match(source('src/components/knowledge/KnowledgeSearchPanel.vue'), /api\.search/)
  assert.match(source('src/components/knowledge/KnowledgeSearchPanel.vue'), /api\.ask/)
  assert.match(source('src/components/knowledge/KnowledgeDocumentDetailDialog.vue'), /api\.getDoc/)
  assert.match(source('src/components/knowledge/KnowledgeSyncJobsPanel.vue'), /api\.triggerKbSync/)
})
