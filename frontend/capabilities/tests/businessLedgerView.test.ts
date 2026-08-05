import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')

test('业务台账有单一导航入口及其浏览页 API 契约', () => {
  const app = read('src/App.vue')
  const client = read('src/api/client.ts')
  const view = read('src/views/knowledge/BusinessLedgerView.vue')
  assert.match(app, /key: 'business-ledgers', label: '业务台账'/)
  assert.match(app, /BusinessLedgerView v-else-if="view === 'business-ledgers'"/)
  assert.match(client, /queryBusinessLedgerRecords/)
  assert.match(client, /previewBusinessLedgerImport/)
  assert.match(view, /预览导入/)
  assert.match(view, /编辑定义/)
})

test('业务台账以可视字段卡片编辑定义，不向用户暴露 JSON', () => {
  const editor = read('src/views/knowledge/BusinessLedgerDefinitionView.vue')
  assert.match(editor, /添加字段/)
  assert.match(editor, /字段标识/)
  assert.match(editor, /允许查询/)
  assert.doesNotMatch(editor, /字段定义（JSON）/)
})

test('业务台账的新建与定义编辑使用二级页面，不使用弹窗', () => {
  const view = read('src/views/knowledge/BusinessLedgerView.vue')
  const editor = read('src/views/knowledge/BusinessLedgerDefinitionView.vue')
  assert.match(view, /BusinessLedgerDefinitionView/)
  assert.match(view, /business-ledgers\/new/)
  assert.match(view, /business-ledgers\/\$\{ledger\.ledger_key\}\/edit/)
  assert.match(editor, /返回业务台账/)
  assert.doesNotMatch(editor, /Dialog/)
})

test('业务台账详情使用页内返回导航，数据编辑和导入使用弹窗', () => {
  const view = read('src/views/knowledge/BusinessLedgerView.vue')
  assert.match(view, /返回业务台账/)
  assert.match(view, /showRecordDialog/)
  assert.match(view, /showImportDialog/)
})

test('台账定义以可编辑字段列表呈现，而非字段卡片', () => {
  const editor = read('src/views/knowledge/BusinessLedgerDefinitionView.vue')
  assert.match(editor, /<table/)
  assert.match(editor, /v-for="\(field, index\) in definition\.fields"/)
  assert.doesNotMatch(editor, /<Card v-for/)
})
