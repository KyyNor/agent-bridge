import { ref } from 'vue'
import { api } from '../api/client'

// 组目录 key → 中文名映射：模块级共享，多个视图只请求一次。
const groupNames = ref<Record<string, string>>({})
let loadPromise: Promise<void> | null = null

async function loadGroupNames() {
  try {
    const groups = await api.listAccessGroupNames()
    groupNames.value = Object.fromEntries(groups.map(group => [group.group_key, group.name]))
  } catch {
    // 组名只是展示辅助信息；失败后清空句柄，下次进入页面可重试。
    loadPromise = null
  }
}

export function useAccessGroups() {
  function ensureLoaded() {
    if (!loadPromise) loadPromise = loadGroupNames()
    return loadPromise
  }

  /** 优先返回组中文名；目录未命中时回退展示 group_key。 */
  function groupDisplayName(groupKey: string | null | undefined) {
    if (!groupKey) return ''
    return groupNames.value[groupKey] || groupKey
  }

  return { groupNames, ensureLoaded, groupDisplayName }
}
