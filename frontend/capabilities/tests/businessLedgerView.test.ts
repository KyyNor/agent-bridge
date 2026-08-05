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
