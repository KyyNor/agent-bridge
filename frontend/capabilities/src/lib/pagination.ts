export const DEFAULT_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
export const LOG_PAGE_SIZE_OPTIONS = [50, 100, 200] as const

export function pageCount(total: number, pageSize: number): number {
  if (pageSize <= 0) return 1
  return Math.max(1, Math.ceil(total / pageSize))
}

export function clampPage(page: number, total: number, pageSize: number): number {
  if (!Number.isFinite(page) || page < 1) return 1
  return Math.min(Math.floor(page), pageCount(total, pageSize))
}

export function paginate<T>(items: T[], page: number, pageSize: number): T[] {
  const safePageSize = Math.max(1, pageSize)
  const safePage = clampPage(page, items.length, safePageSize)
  const start = (safePage - 1) * safePageSize
  return items.slice(start, start + safePageSize)
}
