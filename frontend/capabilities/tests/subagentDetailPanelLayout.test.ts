import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const detailSource = readFileSync(resolve(import.meta.dirname, '../src/components/SubagentDetailPanel.vue'), 'utf8')
const timelineSource = readFileSync(resolve(import.meta.dirname, '../src/components/RunEventTimeline.vue'), 'utf8')

test('sub-agent transcript panel keeps the intro spaced and timeline markers aligned', () => {
  assert.doesNotMatch(detailSource, /Workflow 工具只产生一个外层 Task；这里按 Claude transcript 里的内部 agent 拆开。/)
  assert.match(timelineSource, /\.tl-sub-body\{[^}]*padding:8px 14px 14px/)
  assert.match(timelineSource, /\.tl-mini::before\{[^}]*left:9px/)
})
