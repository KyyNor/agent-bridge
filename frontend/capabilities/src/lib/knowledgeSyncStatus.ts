const labels: Record<string, string> = {
  not_synced: '未同步',
  synced: '已同步',
  sync_failed: '同步失败',
  sync_unconfirmed: '待核对',
  parsing: '解析中',
  parse_failed: '解析失败',
  delete_pending: '待删除',
  delete_failed: '删除失败',
  pending: '待处理',
  running: '同步中',
  succeeded: '已完成',
  failed: '失败',
  success: '成功',
  error: '错误',
  cancelled: '已取消',
}

export function syncBadgeLabel(status: string) {
  return labels[status] || status || '未知'
}

export function syncBadgeStatus(status: string): 'success' | 'error' | 'running' | 'blocked' | 'disabled' {
  if (['synced', 'succeeded', 'success'].includes(status)) return 'success'
  if (['sync_failed', 'parse_failed', 'delete_failed', 'failed', 'error'].includes(status)) return 'error'
  if (['pending', 'running', 'parsing'].includes(status)) return 'running'
  if (status === 'sync_unconfirmed') return 'blocked'
  return 'disabled'
}
