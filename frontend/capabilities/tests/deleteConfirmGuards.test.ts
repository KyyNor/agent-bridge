import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

function source(path: string): string {
  return readFileSync(resolve(root, path), 'utf-8')
}

function assertDeleteConfirmation(file: string, functionName: string, description: RegExp): void {
  const functionBody = file.match(new RegExp(`async function ${functionName}[\\s\\S]*?(?=\\n(?:async )?function |\\n</script>|$)`))?.[0]
  assert.ok(functionBody, `could not find ${functionName} function`)
  assert.match(functionBody, /confirm\(\{/)
  assert.match(functionBody, description)
}

test('knowledge document deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeView.vue')
  assertDeleteConfirmation(file, 'deleteDoc', /description:\s*`确定删除文档「\$\{docTitle\}」？/)
})

test('knowledge backend deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeProcessingConfigView.vue')
  assertDeleteConfirmation(file, 'deleteBackend', /description:\s*`确定删除知识后端「\$\{slug\}」？/)
})

test('code repository category deletion requires explicit confirmation', () => {
  const file = source('src/views/knowledge/KnowledgeProcessingConfigView.vue')
  assertDeleteConfirmation(file, 'deleteCategory', /description:\s*`确定删除代码仓库分类「\$\{key\}」？/)
})
