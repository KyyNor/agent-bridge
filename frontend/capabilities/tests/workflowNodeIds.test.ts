import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, reactive, ref, toRaw } from 'vue'
import { useWorkflowEditorState } from '../src/composables/useWorkflowEditorState.ts'
import { migrateWorkflowGraph } from '../src/lib/workflowDefinition.ts'
import { applyNodeIdRename, collectNodeRemovalImpact, edgeConditionNodeId, removeNodesWithReferences, rewriteTemplateReferences, stripTemplateReferences, validateNodeId } from '../src/lib/workflowNodeIds.ts'
import type { WorkflowGraph, WorkflowNode } from '../src/api/types.ts'

function agent(id: string, prompt: string, x = 0): WorkflowNode {
  return { id, type: 'agent', name: id, position: { x, y: 0 }, config: { prompt, backend_key: 'codex', mcp_enabled: true, skill_names: [], timeout_seconds: 600, result_mode: 'text', output_schema: null } }
}
function output(id: string, format: 'markdown' | 'html', path: string, prompt: string): WorkflowNode {
  return { id, type: 'output', name: id, position: { x: 0, y: 0 }, config: { format, title: id, path, tags: [], prompt, backend_key: 'codex', mcp_enabled: false, skill_names: [], timeout_seconds: 600 } }
}

test('工作流类型切换接受 Vue 响应式图，不再抛 DataCloneError', () => {
  // structuredClone 无法克隆 reactive Proxy，这是切换总结/操作类型报错的真实根因。
  const form = ref<WorkflowGraph>({
    nodes: [agent('agent-1', '采集 {{ nodes.agent-2.output }}', 0), agent('agent-2', '整理', 200)],
    edges: [{ id: 'e1', source: 'agent-1', target: 'agent-2', condition: null }],
  })

  const toOperation = migrateWorkflowGraph(form.value, 'operation', 'summary', 'codex')
  assert.deepEqual(toOperation.nodes.slice(0, 2).map(node => node.id), ['agent-1', 'agent-2'])
  // 只有末端节点会被接到 Markdown 汇总节点（agent-1 已有下游，不重复接）。
  assert.equal(toOperation.edges.filter(edge => edge.target === toOperation.nodes[2].id).length, 1)

  const back = migrateWorkflowGraph(toOperation, 'summary', 'operation', 'codex')
  assert.deepEqual(back.nodes.map(node => node.id), ['agent-1', 'agent-2'])
  // 迁移结果必须是普通对象，后续再挂到响应式状态上才不会再次触发克隆失败。
  assert.equal(reactive(back).nodes[0].config.prompt, '采集 {{ nodes.agent-2.output }}')
  assert.deepEqual(migrateWorkflowGraph(form.value, 'operation', 'operation', 'codex').nodes.map(node => node.id), ['agent-1', 'agent-2'])
})

for (const edit of ['add', 'rename', 'delete', 'replace'] as const) {
  test(`编辑器 ${edit} 后仍可往返切换工作流类型，且不共享嵌套对象`, () => {
    const scope = effectScope()
    try {
      scope.run(() => {
        const editor = useWorkflowEditorState({
          defaultBackend: ref('codex'), profiles: ref([]), onSaved: async () => {}, toast: () => 0,
        })
        editor.form.value.definition = {
          nodes: [agent('a', '采集'), agent('b', '整理 {{ nodes.a.output.text }}')],
          edges: [{ id: 'a-b', source: 'a', target: 'b', condition: { field: 'nodes.a.output.text', operator: 'exists', value: null } }],
        }
        if (edit === 'add') editor.addNode('agent')
        if (edit === 'rename') editor.renameWorkflowNode('a', 'gather')
        if (edit === 'delete') editor.form.value.definition = removeNodesWithReferences(editor.form.value.definition, ['a'])
        if (edit === 'replace') editor.replaceNode({ ...editor.form.value.definition.nodes[0], name: '改名' })

        const source = editor.form.value.definition
        const before = JSON.parse(JSON.stringify(source)) as WorkflowGraph
        editor.changeWorkflowType('summary')
        assert.equal(editor.form.value.workflow_type, 'summary')
        editor.changeWorkflowType('operation')
        assert.deepEqual(structuredClone(toRaw(editor.form.value.definition)), before)
        editor.form.value.definition.nodes[0].config.prompt = '修改迁移后的图'
        assert.deepEqual(JSON.parse(JSON.stringify(source)), before)
      })
    } finally {
      scope.stop()
    }
  })
}

test('删除节点会清理下游悬空引用、相连边与失效条件边', () => {
  const graph: WorkflowGraph = {
    nodes: [
      agent('agent-1', '采集', 0),
      agent('agent-2', '基于 {{ nodes.agent-1.output }} 改写', 200),
      output('md', 'markdown', 'reports/{{ nodes.agent-1.output }}.md', '合并 {{ nodes.agent-1.output }} 与 {{ nodes.agent-2.output }}'),
    ],
    edges: [
      { id: 'e1', source: 'agent-1', target: 'agent-2', condition: null },
      { id: 'e2', source: 'agent-2', target: 'md', condition: { field: 'nodes.agent-1.output.text', operator: 'eq', value: null } },
      { id: 'e3', source: 'agent-2', target: 'md', condition: null },
    ],
  }

  const impact = collectNodeRemovalImpact(graph, ['agent-1'])
  assert.deepEqual(impact.danglingNodes.map(item => [item.node.id, item.fields]), [['agent-2', ['prompt']], ['md', ['prompt', 'path']]])
  assert.deepEqual(impact.orphanedEdges.map(edge => edge.id), ['e2'])

  const next = removeNodesWithReferences(graph, ['agent-1'])
  assert.deepEqual(next.nodes.map(node => node.id), ['agent-2', 'md'])
  // 被删节点的引用整段移除，其余引用与原有排版不受影响。
  assert.equal(next.nodes[0].config.prompt, '基于 改写')
  assert.equal(next.nodes[1].config.prompt, '合并 与 {{ nodes.agent-2.output }}')
  assert.equal(next.nodes[1].config.path, 'reports/.md')
  // 条件边随引用一并删除，其余边保留。
  assert.deepEqual(next.edges.map(edge => edge.id), ['e3'])
})

test('删除无引用的节点不会牵动其他节点的排版', () => {
  const graph: WorkflowGraph = {
    nodes: [agent('a', '保留  双空格'), agent('b', '无关')],
    edges: [{ id: 'e1', source: 'a', target: 'b', condition: null }],
  }
  const next = removeNodesWithReferences(graph, ['b'])
  assert.equal(next.nodes[0].config.prompt, '保留  双空格')
  assert.deepEqual(next.edges, [])
})

test('重命名节点级联更新边端点、模板引用与条件字段', () => {
  const graph: WorkflowGraph = {
    nodes: [agent('agent-1', '采集', 0), agent('agent-2', '基于 {{ nodes.agent-1.output }} 改写', 200), { id: 'collect', type: 'script', name: 'C', position: { x: 0, y: 0 }, config: { script_key: 's', params: { repo: '{{ nodes.agent-1.output }}' }, timeout_seconds: 60 } }],
    edges: [
      { id: 'e1', source: 'agent-1', target: 'agent-2', condition: null },
      { id: 'e2', source: 'agent-1', target: 'collect', condition: { field: 'nodes.agent-1.output.text', operator: 'eq', value: null } },
    ],
  }

  const next = applyNodeIdRename(graph, 'agent-1', 'gather')
  assert.deepEqual(next.nodes.map(node => node.id), ['gather', 'agent-2', 'collect'])
  assert.equal(next.nodes[1].config.prompt, '基于 {{ nodes.gather.output }} 改写')
  assert.equal((next.nodes[2].config as { params: Record<string, string> }).params.repo, '{{ nodes.gather.output }}')
  assert.deepEqual(next.edges.map(edge => [edge.source, edge.target]), [['gather', 'agent-2'], ['gather', 'collect']])
  assert.equal(next.edges[1].condition?.field, 'nodes.gather.output.text')
})

test('重命名不会误伤前缀相同的其他节点引用', () => {
  const graph: WorkflowGraph = {
    nodes: [agent('agent-1', 'x', 0), agent('agent-11', 'y', 100), agent('agent-2', '{{ nodes.agent-11.output }}', 200)],
    edges: [],
  }
  const next = applyNodeIdRename(graph, 'agent-1', 'gather')
  assert.equal(next.nodes[2].config.prompt, '{{ nodes.agent-11.output }}')
})

test('重命名和删除覆盖嵌套脚本参数、数组与输出标题，预览列出完整字段路径', () => {
  const report = output('report', 'markdown', 'report.md', '报告')
  if (report.type === 'output') report.config.title = '标题 {{ nodes.a.output.text }}'
  const params = {
    payload: { text: '{{ nodes.a.output.text }}', keep: '{{ nodes.b.output.text }}' },
    items: ['{{ nodes.a.output.text }}', { text: '依据 {{ nodes.a.output.text }} 生成' }, 3, false, null],
  }
  const graph: WorkflowGraph = {
    nodes: [agent('a', '采集'), agent('b', '整理'), {
      id: 'script', type: 'script', name: '脚本', position: { x: 0, y: 0 },
      config: { script_key: 's', params, timeout_seconds: 60 },
    }, report],
    edges: [
      { id: 'a-b', source: 'a', target: 'b', condition: null },
      { id: 'b-script', source: 'b', target: 'script', condition: null },
      { id: 'script-report', source: 'script', target: 'report', condition: null },
    ],
  }
  const before = structuredClone(graph)
  const impact = collectNodeRemovalImpact(graph, ['a'])
  assert.deepEqual(impact.danglingNodes.map(({ node, fields }) => [node.id, fields]), [
    ['script', ['params.payload.text', 'params.items.0', 'params.items.1.text']],
    ['report', ['title']],
  ])

  const renamed = applyNodeIdRename(graph, 'a', 'gather')
  assert.deepEqual(renamed.nodes[2].config.params, {
    payload: { text: '{{ nodes.gather.output.text }}', keep: '{{ nodes.b.output.text }}' },
    items: ['{{ nodes.gather.output.text }}', { text: '依据 {{ nodes.gather.output.text }} 生成' }, 3, false, null],
  })
  assert.equal(renamed.nodes[3].config.title, '标题 {{ nodes.gather.output.text }}')

  const deleted = removeNodesWithReferences(graph, ['a'])
  assert.deepEqual(deleted.nodes[1].config.params, {
    payload: { text: '', keep: '{{ nodes.b.output.text }}' },
    items: ['', { text: '依据 生成' }, 3, false, null],
  })
  assert.equal(deleted.nodes[2].config.title, '标题')
  assert.deepEqual(collectNodeRemovalImpact(deleted, ['a']).danglingNodes, [])
  assert.deepEqual(graph, before)
})

test('引用只按首个节点路径段匹配，不误改输出数据中的 nodes 字段', () => {
  const other = '{{ nodes.b.output.nodes.a }}'
  const prefix = '{{ nodes.a-long.output.text }}'
  const own = '{{  nodes.a.output.nodes.a  }}'
  const template = `${other} / ${prefix} / ${own}`
  assert.equal(rewriteTemplateReferences(template, new Map([['a', 'gather']])), `${other} / ${prefix} / {{  nodes.gather.output.nodes.a  }}`)
  assert.equal(stripTemplateReferences(template, new Set(['a'])), `${other} / ${prefix} /`)
  assert.equal(stripTemplateReferences(other, new Set(['a'])), other)
  assert.equal(stripTemplateReferences(prefix, new Set(['a'])), prefix)
  // 非法模板不是后端可解析的引用，不应被节点操作顺带改写。
  assert.equal(rewriteTemplateReferences('{{ nodes.a + 1 }}', new Map([['a', 'gather']])), '{{ nodes.a + 1 }}')
})

test('节点 ID 校验拦截非法字符与重复占用', () => {
  assert.equal(validateNodeId('gather_docs', []), null)
  assert.match(validateNodeId('Agent-1', []) || '', /小写字母/)
  assert.match(validateNodeId('1agent', []) || '', /小写字母开头/)
  assert.match(validateNodeId('agent.1', []) || '', /小写字母/)
  assert.match(validateNodeId('', []) || '', /不能为空/)
  assert.match(validateNodeId('a'.repeat(65), []) || '', /不能超过/)
  assert.match(validateNodeId('gather', ['gather']) || '', /已被占用/)
})

test('边条件节点 ID 解析只认 nodes 命名空间', () => {
  const base = { id: 'e1', source: 'a', target: 'b' }
  assert.equal(edgeConditionNodeId({ ...base, condition: { field: 'nodes.a.output.text', operator: 'eq', value: null } }), 'a')
  assert.equal(edgeConditionNodeId({ ...base, condition: { field: 'input.value', operator: 'eq', value: null } }), null)
  assert.equal(edgeConditionNodeId({ ...base, condition: null }), null)
})
