import type { RouteLocationNormalized } from 'vue-router'

export type RouteLeaveGuard = (to: RouteLocationNormalized, from: RouteLocationNormalized) => boolean | Promise<boolean>

const guards = new Set<RouteLeaveGuard>()

/**
 * 注册跨同一视图子路由也会执行的离开确认。
 *
 * Vue Router 负责浏览器历史与取消后的恢复；这里仅聚合页面的业务确认。
 */
export function registerRouteLeaveGuard(guard: RouteLeaveGuard): () => void {
  guards.add(guard)
  return () => guards.delete(guard)
}

export async function canLeaveRoute(to: RouteLocationNormalized, from: RouteLocationNormalized): Promise<boolean> {
  for (const guard of [...guards]) {
    if (!await guard(to, from)) return false
  }
  return true
}
