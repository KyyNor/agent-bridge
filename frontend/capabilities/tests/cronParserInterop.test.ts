import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { CronExpressionParser } from 'cron-parser'

const defaultExpressions = [
  '0 * * * *',
  '0 2 * * *',
  '0 3 * * 0',
  '30 3 * * 0',
  '*/30 * * * *',
]

test('系统配置中的默认 Cron 表达式均可解析', () => {
  for (const expression of defaultExpressions) {
    const interval = CronExpressionParser.parse(expression)
    assert.ok(interval.next().toDate() instanceof Date, expression)
  }
})

test('配置页使用 cron-parser 具名导入以兼容 Vite 8 构建', () => {
  const component = readFileSync(
    resolve(import.meta.dirname, '../src/views/knowledge/KnowledgeProcessingConfigView.vue'),
    'utf-8',
  )

  assert.match(component, /import \{ CronExpressionParser \} from 'cron-parser'/)
  assert.doesNotMatch(component, /import CronExpressionParser from 'cron-parser'/)
})
