/**
 * Agent 工作台进入页的 Coding Agent、工作空间范围与能力平面选择偏好（浏览器本地记忆）。
 *
 * 上次选择记在 localStorage（agent + scope + profileKey），下次进入默认选中；按
 * 浏览器各自记忆、不落服务端。首次使用默认 dsh + personal；shared 运行时以
 * Runtime 的 active profile 为准（页面加载时覆盖旧记忆）。记忆的 profileKey
 * 失效（被删/停用/无权限）时清掉并回落到「不使用能力平面」。
 *
 * 记忆结构升级过两次，读取时逐档迁移，保证旧浏览器记忆继续生效：
 *
 * 1. ``agent-bridge:dsh-workspace:selection`` 且含 ``agent`` 字段：当前格式；
 * 2. 同一键但只有 ``{scope, profileKey}``：Agent 工作台之前的 DSH 专属记忆，
 *    迁移为 ``agent: 'dsh'``；
 * 3. ``agent-bridge:dsh-workspace:profile``（仅 profileKey 字符串）：最早的
 *    记忆格式，读到即按 personal 迁移，写回新格式后删除旧键。
 */

import type { WorkspaceAgent, WorkspaceScope } from '../api/types'
import { DEFAULT_WORKSPACE_AGENT, normalizeWorkspaceAgent } from './workspaceAgents'

const STORAGE_KEY = 'agent-bridge:dsh-workspace:selection'
// 旧版（无 scope）记忆键：只存 profileKey 字符串，升级时迁移后移除。
const LEGACY_PROFILE_KEY = 'agent-bridge:dsh-workspace:profile'

export interface DshWorkspaceSelection {
  agent: WorkspaceAgent
  scope: WorkspaceScope
  profileKey: string
}

export const DEFAULT_WORKSPACE_SELECTION: DshWorkspaceSelection = {
  agent: DEFAULT_WORKSPACE_AGENT,
  scope: 'personal',
  profileKey: '',
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    // 隐私模式等场景下访问 localStorage 可能直接抛异常。
    return null
  }
}

function normalizeScope(value: unknown): WorkspaceScope {
  return value === 'shared' ? 'shared' : 'personal'
}

/** 读取记忆的选择；无记忆或存储不可用时返回默认（dsh + personal + 空 profile）。 */
export function readPreferredSelection(): DshWorkspaceSelection {
  const store = storage()
  if (store === null) return { ...DEFAULT_WORKSPACE_SELECTION }
  try {
    const raw = store.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DshWorkspaceSelection>
      // 第二档迁移：记忆里没有 agent 字段的 DSH 时代记录一律视为 dsh。
      return {
        agent: normalizeWorkspaceAgent(parsed.agent),
        scope: normalizeScope(parsed.scope),
        profileKey: typeof parsed.profileKey === 'string' ? parsed.profileKey : '',
      }
    }
    // 迁移第三档：仅记忆 profileKey 的最早格式。
    const legacy = store.getItem(LEGACY_PROFILE_KEY)
    if (legacy) return { agent: DEFAULT_WORKSPACE_AGENT, scope: 'personal', profileKey: legacy }
  } catch {
    /* 损坏内容按无记忆处理 */
  }
  return { ...DEFAULT_WORKSPACE_SELECTION }
}

/** 记忆（或清除 profileKey 传 ''）选择；存储不可用时静默降级为不记忆。 */
export function writePreferredSelection(selection: DshWorkspaceSelection): void {
  const store = storage()
  if (store === null) return
  try {
    store.setItem(STORAGE_KEY, JSON.stringify({
      agent: normalizeWorkspaceAgent(selection.agent),
      scope: normalizeScope(selection.scope),
      profileKey: typeof selection.profileKey === 'string' ? selection.profileKey : '',
    }))
    store.removeItem(LEGACY_PROFILE_KEY)
  } catch {
    /* 写失败不影响本次进入 */
  }
}

/** 校验记忆仍指向可用能力平面；失效则清除记忆并返回 ''。 */
export function resolvePreferredProfile(activeProfileKeys: string[]): string {
  const stored = readPreferredSelection().profileKey
  if (stored && activeProfileKeys.includes(stored)) return stored
  if (stored) {
    const selection = readPreferredSelection()
    writePreferredSelection({ ...selection, profileKey: '' })
  }
  return ''
}