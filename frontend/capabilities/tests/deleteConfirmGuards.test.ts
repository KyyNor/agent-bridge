import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

function source(path: string): string {
  return readFileSync(resolve(root, path), 'utf-8')
}

test('knowledge document deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeView.vue')
  assert.match(file, /async function deleteDoc[\s\S]*confirm\(`确定删除文档「\$\{docTitle\}」？/)
})

test('knowledge backend deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeProcessingConfigView.vue')
  assert.match(file, /async function deleteBackend[\s\S]*confirm\(`确定删除知识后端「\$\{slug\}」？/)
})

test('code repository category deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeProcessingConfigView.vue')
  assert.match(file, /async function deleteCategory[\s\S]*confirm\(`确定删除代码仓库分类「\$\{key\}」？/)
})
