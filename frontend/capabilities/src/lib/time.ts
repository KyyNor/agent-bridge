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
