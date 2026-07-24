const reuseReasonLabels: Record<string, string> = {
  normal_mode: '普通模式，不复用',
  force_full: '已要求完整执行',
  no_usable_baseline: '没有可用的历史运行',
  task_lease_must_refresh: '需刷新任务租约',
  new_node: '新增节点',
  incoming_edges_changed: '输入连线已变更',
  node_fingerprint_changed: '节点配置或资源已变更',
  baseline_fingerprint_missing: '历史节点缺少执行指纹',
  baseline_node_missing: '历史运行缺少该节点',
  baseline_node_not_completed: '历史节点未完成',
  baseline_output_missing: '历史节点缺少输出',
  artifact_ids_invalid: '历史产物标识无效',
  artifact_missing: '历史产物不存在',
  artifact_scope_mismatch: '历史产物范围不匹配',
  artifact_not_reusable: '历史产物不可复用',
  artifact_hash_mismatch: '历史产物内容校验失败',
  artifact_expired: '历史产物已过期',
  condition_results_invalid: '历史条件结果无效',
  fingerprint_match: '执行指纹一致',
  legacy_mcp_fingerprint_match: '兼容历史 MCP 指纹',
  resource_fingerprint_unavailable: '无法确认运行资源版本',
  upstream_execute: '上游节点已重新执行',
  dependency_unresolved: '依赖关系无法解析',
  condition_not_matched: '条件未命中',
  no_task: '没有可处理任务',
  plan_node_missing: '执行计划缺少该节点',
  source_artifact_validation_unavailable: '无法校验来源产物',
  source_artifact_missing: '来源产物不存在',
  source_artifact_not_reusable: '来源产物不可复用',
  source_artifact_hash_mismatch: '来源产物内容校验失败',
  source_artifact_expired: '来源产物已过期',
}

const executionModeLabels: Record<string, string> = {
  normal: '普通执行',
  incremental: '增量执行',
  force_full: '强制完整执行',
}

export function workflowReuseReasonText(reason: string | null | undefined): string {
  if (!reason) return '未说明原因'
  return reuseReasonLabels[reason] || reason
}

export function workflowExecutionModeText(mode: string | null | undefined): string {
  if (!mode) return '未指定'
  return executionModeLabels[mode] || mode
}
