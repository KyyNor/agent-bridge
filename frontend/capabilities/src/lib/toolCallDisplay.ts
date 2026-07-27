import type { ToolCallLog } from '../api/types'

const TOOL_LABELS: Record<string, string> = {
  'hook:claude_code:version-check': '版本检查',
  'hook:claude_code:start': '启动记忆服务',
  'hook:claude_code:context': '获取记忆上下文',
  'hook:claude_code:session-start': '会话启动',
  'hook:claude_code:session-end': '会话结束',
  'hook:claude_code:session-init': '会话初始化',
  'hook:claude_code:observation': '记录观察',
  'hook:claude_code:file-context': '文件上下文',
  'hook:claude_code:summarize': '总结记忆',
  'hook:claude_code:full-probe': '全量检索探测',
  'hook:claude_code:full_probe': '全量检索探测',
  'builtin:built-in:load_skill': '加载技能',
  'builtin:built-in:run_script': '运行脚本',
  'builtin:built-in:validate_workflow': '校验工作流',
  'builtin:wiki:ask': '知识库问答',
  'builtin:wiki:get_document': '读取知识库文档',
  'builtin:wiki:list_kbs': '列出知识库',
  'builtin:wiki:search_all': '全库检索',
  'builtin:wiki:search': '知识库检索',
  'builtin:codegraph:codegraph_explore': 'CodeGraph 代码探索',
  'builtin:memory:search': '记忆检索',
  'builtin:memory:timeline': '记忆时间线',
  'builtin:memory:get': '读取记忆',
  'builtin:workflow:artifacts_search': '检索工作流产物',
  'builtin:workflow:workflow_get_task': '领取工作流任务',
  'builtin:workflow:workflow_set_task': '更新工作流任务',
  'builtin:workflow:workflow_run_log': '记录工作流日志',
  'builtin:retrieval_probe:retrieval-probe': '检索探测',
}

/** Returns the user-facing label while retaining the raw name for a native title attribute. */
export function toolCallDisplayName(log: Pick<ToolCallLog, 'source_type' | 'source_key' | 'tool_name'>): string {
  const toolName = log.tool_name || ''
  const key = `${log.source_type || ''}:${log.source_key || ''}:${toolName}`
  return TOOL_LABELS[key] || toolName || '—'
}
