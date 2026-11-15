import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { DesignAgentResponse, AgentNodeDesignResult } from '../api/types'

type WorkflowDesignerOptions = {
  current: (mode: 'create' | 'modify') => Record<string, unknown>
  profileKey: () => string | undefined
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '未知错误'
}

function createDesignRunKey() {
  const suffix = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `design-agent-node-${suffix}`
}

/** 管理Agent 节点助手 的请求、停止和结果状态。 */
export function useAgentNodeDesigner(options: WorkflowDesignerOptions) {
  const showDesigner = ref(false)
  const designMode = ref<'create' | 'modify'>('modify')
  const designPrompt = ref('')
  const designing = ref(false)
  const designError = ref('')
  const designResponse = ref<DesignAgentResponse<AgentNodeDesignResult> | null>(null)
  const designRunKey = ref('')
  const designStopRequested = ref(false)
  const designSnapshot = ref('')
  const workflowDesignDraft = computed(() => designResponse.value?.result?.node || null)

  function openWorkflowDesigner(mode: 'create' | 'modify' = 'modify') {
    designResponse.value = null
    designPrompt.value = ''
    designMode.value = mode
    showDesigner.value = true
    designError.value = ''
  }

  async function runWorkflowDesigner() {
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
      const current = options.current(designMode.value)
      designSnapshot.value = JSON.stringify(current)
      const response = await api.designAgentNode({
        run_key: runKey,
        mode: designMode.value,
        prompt: designPrompt.value,
        current,
        profile_key: options.profileKey(),
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

  async function stopWorkflowDesigner() {
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
    designSnapshot,
    showDesigner,
    designMode,
    designPrompt,
    designing,
    designError,
    designResponse,
    designStopRequested,
    workflowDesignDraft,
    openWorkflowDesigner,
    runWorkflowDesigner,
    stopWorkflowDesigner,
  }
}
