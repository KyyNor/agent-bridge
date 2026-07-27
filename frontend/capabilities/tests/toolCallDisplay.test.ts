import assert from 'node:assert/strict'
import test from 'node:test'

import { toolCallDisplayName } from '../src/lib/toolCallDisplay.ts'

test('toolCallDisplayName maps known hook and builtin tools with their source identity', () => {
  assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name: 'session-start' }), '会话启动')
  assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name: 'full-probe' }), '全量检索探测')
  assert.equal(toolCallDisplayName({ source_type: 'builtin', source_key: 'codegraph', tool_name: 'codegraph_explore' }), 'CodeGraph 代码探索')
  assert.equal(toolCallDisplayName({ source_type: 'builtin', source_key: 'memory', tool_name: 'search' }), '记忆检索')
})

test('toolCallDisplayName maps every supported Claude memory Hook action', () => {
  const actions = {
    'version-check': '版本检查',
    start: '启动记忆服务',
    context: '获取记忆上下文',
    'session-start': '会话启动',
    'session-end': '会话结束',
    'session-init': '会话初始化',
    observation: '记录观察',
    'file-context': '文件上下文',
    summarize: '总结记忆',
    'full-probe': '全量检索探测',
    full_probe: '全量检索探测',
  }

  for (const [tool_name, label] of Object.entries(actions)) {
    assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name }), label)
  }
})

test('toolCallDisplayName preserves unknown names and uses a placeholder for missing names', () => {
  assert.equal(toolCallDisplayName({ source_type: 'mcp_service', source_key: 'custom', tool_name: 'lookup' }), 'lookup')
  assert.equal(toolCallDisplayName({ source_type: null, source_key: null, tool_name: null }), '—')
})

test('toolCallDisplayName retains the full_probe historical alias', () => {
  assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name: 'full_probe' }), '全量检索探测')
})
