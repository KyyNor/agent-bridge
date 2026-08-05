import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { BusinessLedgerDesignResult, DesignAgentResponse } from '../api/types'

type BusinessLedgerDesignerOptions = {
  current: (mode: 'create' | 'modify') => Record<string, unknown>
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

function createDesignRunKey() {
  const suffix = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `design-business-ledger-${suffix}`
}

/** 管理业务台账定义设计 Agent 的请求、停止和草案状态。 */
export function useBusinessLedgerDesigner(options: BusinessLedgerDesignerOptions) {
  const showDesigner = ref(false)
  const designMode = ref<'create' | 'modify'>('create')
  const designPrompt = ref('')
  const designing = ref(false)
  const designError = ref('')
  const designResponse = ref<DesignAgentResponse<BusinessLedgerDesignResult> | null>(null)
  const designRunKey = ref('')
  const designStopRequested = ref(false)
  const ledgerDesignDraft = computed(() => designResponse.value?.result?.ledger || null)

  function openBusinessLedgerDesigner(mode: 'create' | 'modify') {
    designMode.value = mode
    designError.value = ''
    showDesigner.value = true
  }

  async function runBusinessLedgerDesigner() {
    designError.value = ''
    if (!designPrompt.value.trim()) {
      designError.value = '请输入提示词'
      return
    }
    const runKey = createDesignRunKey()
    designRunKey.value = runKey
    designStopRequested.value = false
    designResponse.value = null
    designing.value = true
    try {
      const response = await api.designBusinessLedger({
        run_key: runKey,
        mode: designMode.value,
        prompt: designPrompt.value,
        current: options.current(designMode.value),
      })
      if (designRunKey.value !== runKey || designStopRequested.value) return
      designResponse.value = response
      if (!response.ok) designError.value = response.error || '设计 agent 执行失败'
    } catch (error: unknown) {
      if (designRunKey.value !== runKey || designStopRequested.value) return
      designError.value = errorMessage(error)
    } finally {
      if (designRunKey.value === runKey) {
        if (designStopRequested.value && !designError.value) designError.value = '已停止'
        designing.value = false
        designStopRequested.value = false
      }
    }
  }

  async function stopBusinessLedgerDesigner() {
    const runKey = designRunKey.value
    if (!designing.value || !runKey || designStopRequested.value) return
    designStopRequested.value = true
    designError.value = ''
    try {
      await api.stopAgentRun(runKey)
    } catch (error: unknown) {
      designError.value = errorMessage(error)
    }
  }

  return {
    showDesigner,
    designMode,
    designPrompt,
    designing,
    designError,
    designResponse,
    designStopRequested,
    ledgerDesignDraft,
    openBusinessLedgerDesigner,
    runBusinessLedgerDesigner,
    stopBusinessLedgerDesigner,
  }
}
