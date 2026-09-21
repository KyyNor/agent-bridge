/**
 * DSH 工作台进入页的能力平面选择偏好（浏览器本地记忆）。
 *
 * 用户上次选择的能力平面记在 localStorage，下次进入默认选中；按浏览器
 * 各自记忆、不落服务端。记忆的 key 失效（Profile 被删除/停用/无权限）
 * 时清掉存储并回落到「不使用能力平面」。
 */

const STORAGE_KEY = 'agent-bridge:dsh-workspace:profile'

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    // 隐私模式等场景下访问 localStorage 可能直接抛异常。
    return null
  }
}

/** 读取记忆的能力平面 key；无记忆或存储不可用时返回 ''。 */
export function readPreferredDshProfile(): string {
  try {
    return storage()?.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

/** 记忆（或清除，传 ''）能力平面选择；存储不可用时静默降级为不记忆。 */
export function writePreferredDshProfile(profileKey: string): void {
  const store = storage()
  if (store === null) return
  try {
    if (profileKey) store.setItem(STORAGE_KEY, profileKey)
    else store.removeItem(STORAGE_KEY)
  } catch {
    /* 写失败不影响本次进入 */
  }
}

/** 校验记忆仍指向可用能力平面；失效则清除记忆并返回 ''。 */
export function resolvePreferredDshProfile(activeProfileKeys: string[]): string {
  const stored = readPreferredDshProfile()
  if (stored && activeProfileKeys.includes(stored)) return stored
  if (stored) writePreferredDshProfile('')
  return ''
}
