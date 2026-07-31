/** Parse a UTC datetime string (with or without TZ indicator) into a Date. */
function parseUTCDate(dateStr: string): Date {
  let s = dateStr.trim()
  // Normalize space separator to T
  if (!s.includes('T')) {
    s = s.replace(' ', 'T')
  }
  // Append Z if no timezone indicator present
  if (!s.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(s) && !/[+-]\d{4}$/.test(s)) {
    s += 'Z'
  }
  return new Date(s)
}

/** Format a UTC datetime string to local YYYY-MM-DD HH:mm:ss. */
export function formatLocalDatetime(dateStr: string | null): string {
  if (!dateStr) return '—'
  const d = parseUTCDate(dateStr)
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  const s = String(d.getSeconds()).padStart(2, '0')
  return `${y}-${mo}-${day} ${h}:${mi}:${s}`
}

/**
 * 将毫秒耗时格式化为以秒为单位的展示文本（始终用秒，不使用毫秒单位）。
 *
 * 入参为内部数据模型统一存储的毫秒；这里仅负责展示转换，数据层保持毫秒不变。
 * 按量级自适应小数位：<1s 保留 2 位（如 0.5s、0.02s），<10s 保留 1 位，
 * <60s 取整，≥60s 折算为 `1m 2s` 形式。null 或负值返回空串（由调用方兜底 '—'）。
 */
export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs == null || durationMs < 0) return ''
  if (durationMs < 1000) return `${(durationMs / 1000).toFixed(2)}s`
  if (durationMs < 10_000) return `${(durationMs / 1000).toFixed(1)}s`
  if (durationMs < 60_000) return `${Math.round(durationMs / 1000)}s`
  return `${Math.floor(durationMs / 60_000)}m ${Math.round((durationMs % 60_000) / 1000)}s`
}

/** Return a Chinese relative-time string (e.g. "3 分钟前") for a UTC datetime string. */
export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—'
  const diff = Date.now() - parseUTCDate(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}
