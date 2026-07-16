import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const read = (path: string) => readFileSync(`${root}/src/${path}`, 'utf8')

test('wires code repository detail as a route-driven independent component', () => {
  const app = read('App.vue')
  const navigation = read('lib/navigation.ts')
  const list = read('views/knowledge/CodeRepoView.vue')
  const detail = read('views/knowledge/CodeRepoDetailView.vue')

  assert.match(app, /<CodeRepoView[^>]*:route-key="subRoute"/)
  assert.match(navigation, /'code-repos'/)
  assert.match(list, /CodeRepoDetailView/)
  assert.match(list, /code-repos\//)
  assert.doesNotMatch(list, /<!-- Repo Detail Dialog -->/)
  assert.match(detail, /defineEmits/)
  assert.match(detail, /@click="goBack"|function goBack/)
  assert.match(detail, /watch\(\[\(\) => props\.repoKey, \(\) => props\.repo\]/)
  assert.match(detail, /detailError\.value = '详情加载失败，请稍后重试'/)
  assert.match(detail, /onBeforeUnmount\(\(\) => \{[\s\S]*stopTouchTimer\(\)/)
  assert.match(detail, /clearInterval\(uaTouchTimer\)/)
  assert.match(list, /@back="backToList"/)
  assert.match(detail, /概览/)
  assert.match(detail, /查询/)
  assert.match(detail, /探索/)
  assert.match(detail, /理解/)
  assert.match(detail, /overflow-y-auto/)
})
