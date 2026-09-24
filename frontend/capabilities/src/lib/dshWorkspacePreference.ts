/**
 * DSH 工作台进入页的工作空间范围与能力平面选择偏好（浏览器本地记忆）。
 *
 * 上次选择记在 localStorage（scope + profileKey），下次进入默认选中；按浏览器
 * 各自记忆、不落服务端。首次使用默认 personal；shared 运行时以 Runtime 的
 * active profile 为准（页面加载时覆盖旧记忆）。记忆的 profileKey 失效（被
 * 删/停用/无权限）时清掉并回落到「不使用能力平面」。
 */

import type { DshWorkspaceScope } from '../api/types'

const STORAGE_KEY = 'agent-bridge:dsh-workspace:selection'
// 旧版（无 scope）记忆键：只存 profileKey 字符串，升级时迁移后移除。
const LEGACY_PROFILE_KEY = 'agent-bridge:dsh-workspace:profile'

export interface DshWorkspaceSelection {
  scope: DshWorkspaceScope
  profileKey: string
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    // 隐私模式等场景下访问 localStorage 可能直接抛异常。
    return null
  }
}

/** 读取记忆的选择；无记忆或存储不可用时返回默认（personal + 空 profile）。 */
export function readPreferredSelection(): DshWorkspaceSelection {
  const store = storage()
  if (store === null) return { scope: 'personal', profileKey: '' }
  try {
    const raw = store.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DshWorkspaceSelection>
      const scope = parsed.scope === 'shared' ? 'shared' : 'personal'
      return { scope, profileKey: typeof parsed.profileKey === 'string' ? parsed.profileKey : '' }
    }
    // 迁移旧版仅记忆 profileKey 的格式。
    const legacy = store.getItem(LEGACY_PROFILE_KEY)
    if (legacy) return { scope: 'personal', profileKey: legacy }
  } catch {
    /* 损坏内容按无记忆处理 */
  }
  return { scope: 'personal', profileKey: '' }
}

/** 记忆（或清除 profileKey 传 ''）选择；存储不可用时静默降级为不记忆。 */
export function writePreferredSelection(selection: DshWorkspaceSelection): void {
  const store = storage()
  if (store === null) return
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(selection))
    store.removeItem(LEGACY_PROFILE_KEY)
  } catch {
    /* 写失败不影响本次进入 */
  }
}

/** 校验记忆仍指向可用能力平面；失效则清除记忆并返回 ''。 */
export function resolvePreferredProfile(activeProfileKeys: string[]): string {
  const stored = readPreferredSelection().profileKey
  if (stored && activeProfileKeys.includes(stored)) return stored
  if (stored) writePreferredSelection({ ...readPreferredSelection(), profileKey: '' })
  return ''
}
