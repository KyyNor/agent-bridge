import { ref } from 'vue'
import type { WorkflowSubagentDetail } from '../api/types'

type DetailLoader = (scopeId: string, taskId: string) => Promise<WorkflowSubagentDetail>

/**
 * 按“运行作用域 + 子任务”管理子 Agent 详情的懒加载状态。
 *
 * Workflow 与 Agent Runs 页面共用同一套加载、错误和静默刷新语义；调用方
 * 只负责把自己的运行标识解析成 API 所需的 run_key。
 */
export function useSubagentDetails(loadDetail: DetailLoader) {
  const details = ref<Record<string, WorkflowSubagentDetail>>({})
  const loadingKeys = ref<Set<string>>(new Set())
  const errors = ref<Record<string, string>>({})

  const keyOf = (scopeId: string, taskId: string) => `${scopeId}\u0000${taskId}`
  const scopePrefix = (scopeId: string) => `${scopeId}\u0000`

  function detailFor(scopeId: string | null | undefined, taskId: string) {
    return scopeId ? details.value[keyOf(scopeId, taskId)] || null : null
  }

  function isLoading(scopeId: string | null | undefined, taskId: string) {
    return scopeId ? loadingKeys.value.has(keyOf(scopeId, taskId)) : false
  }

  function errorFor(scopeId: string | null | undefined, taskId: string) {
    return scopeId ? errors.value[keyOf(scopeId, taskId)] || '' : ''
  }

  async function ensure(scopeId: string | null | undefined, taskId: string) {
    if (!scopeId) return
    const key = keyOf(scopeId, taskId)
    if (details.value[key] || loadingKeys.value.has(key)) return

    loadingKeys.value = new Set(loadingKeys.value).add(key)
    const nextErrors = { ...errors.value }
    delete nextErrors[key]
    errors.value = nextErrors

    try {
      const detail = await loadDetail(scopeId, taskId)
      details.value = { ...details.value, [key]: detail }
    } catch (error: unknown) {
      errors.value = {
        ...errors.value,
        [key]: error instanceof Error ? error.message : '加载子 Agent 详情失败',
      }
    } finally {
      const nextLoading = new Set(loadingKeys.value)
      nextLoading.delete(key)
      loadingKeys.value = nextLoading
    }
  }

  /** 只刷新用户已经展开过的详情，轮询失败时保留上一次成功结果。 */
  async function refreshLoaded(scopeId: string) {
    const prefix = scopePrefix(scopeId)
    const taskIds = Object.keys(details.value)
      .filter(key => key.startsWith(prefix))
      .map(key => key.slice(prefix.length))
    if (!taskIds.length) return

    const entries = await Promise.all(taskIds.map(async taskId => {
      try {
        return [keyOf(scopeId, taskId), await loadDetail(scopeId, taskId)] as const
      } catch {
        return null
      }
    }))
    const nextDetails = { ...details.value }
    for (const entry of entries) {
      if (entry) nextDetails[entry[0]] = entry[1]
    }
    details.value = nextDetails
  }

  return { detailFor, isLoading, errorFor, ensure, refreshLoaded }
}
