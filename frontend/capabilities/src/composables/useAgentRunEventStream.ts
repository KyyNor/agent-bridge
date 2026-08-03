import { ref } from 'vue'
import { openAgentRunEventStream } from '../api/client'
import type { WorkflowRunEvent } from '../api/types'

export type AgentRunStreamHandlers = {
  onAgentEvent(event: WorkflowRunEvent): void | Promise<void>
  onTerminal(payload: { run_key: string, status: string, ok: boolean, error?: string | null }): void | Promise<void>
  onResyncRequired(payload: { reason?: string }): void | Promise<void>
  onError?(error: Error): void
}

type ParsedSseFrame = {
  event: string
  id: number | null
  data: unknown
}

const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000]

function parseSseFrame(frame: string): ParsedSseFrame | null {
  let event = 'message'
  let id: number | null = null
  const data: string[] = []
  for (const line of frame.replace(/\r/g, '').split('\n')) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator >= 0 ? line.slice(0, separator) : line
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, '') : ''
    if (field === 'event') event = value
    if (field === 'id') {
      const parsed = Number(value)
      if (Number.isInteger(parsed) && parsed > 0) id = parsed
    }
    if (field === 'data') data.push(value)
  }
  if (!data.length) return null
  const raw = data.join('\n')
  try {
    return { event, id, data: JSON.parse(raw) }
  } catch {
    throw new Error('SSE 返回了无效 JSON 数据')
  }
}

export async function readSseFrames(
  body: ReadableStream<Uint8Array>,
  onFrame: (frame: ParsedSseFrame) => void | Promise<void>,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const result = await reader.read()
      if (result.done) break
      buffer = (buffer + decoder.decode(result.value, { stream: true })).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = parseSseFrame(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        if (frame) await onFrame(frame)
        boundary = buffer.indexOf('\n\n')
      }
    }
    buffer = (buffer + decoder.decode()).replace(/\r\n/g, '\n')
    const frame = parseSseFrame(buffer)
    if (frame) await onFrame(frame)
  } finally {
    reader.releaseLock()
  }
}

function isAbortError(error: unknown): boolean {
  return typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError'
}

function retryDelay(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)]
}

export function useAgentRunEventStream() {
  const connected = ref(false)
  const lastEventId = ref(0)
  let controller: AbortController | null = null
  let generation = 0

  function stop() {
    generation += 1
    controller?.abort()
    controller = null
    connected.value = false
  }

  function start(runKey: string, initialEventId: number, handlers: AgentRunStreamHandlers) {
    stop()
    const activeGeneration = generation
    lastEventId.value = initialEventId
    void consume(runKey, activeGeneration, handlers)
  }

  async function consume(runKey: string, activeGeneration: number, handlers: AgentRunStreamHandlers) {
    let retryAttempt = 0
    while (activeGeneration === generation) {
      const streamController = new AbortController()
      controller = streamController
      let terminal = false
      try {
        const response = await openAgentRunEventStream(runKey, lastEventId.value, streamController.signal)
        connected.value = true
        retryAttempt = 0
        await readSseFrames(response.body as ReadableStream<Uint8Array>, async frame => {
          if (frame.id != null) lastEventId.value = Math.max(lastEventId.value, frame.id)
          if (frame.event === 'agent_event') {
            await handlers.onAgentEvent(frame.data as WorkflowRunEvent)
          } else if (frame.event === 'run_terminal') {
            terminal = true
            await handlers.onTerminal(frame.data as { run_key: string, status: string, ok: boolean, error?: string | null })
          } else if (frame.event === 'resync_required') {
            terminal = true
            await handlers.onResyncRequired(frame.data as { reason?: string })
          }
        })
      } catch (error: unknown) {
        if (activeGeneration !== generation || isAbortError(error)) return
        handlers.onError?.(error instanceof Error ? error : new Error('Agent 事件流连接失败'))
      } finally {
        // 旧连接在回调中触发重同步后，不得把新连接的控制器或连接状态清空。
        if (controller === streamController) {
          connected.value = false
          controller = null
        }
      }
      if (terminal || activeGeneration !== generation) return
      await new Promise(resolve => setTimeout(resolve, retryDelay(retryAttempt)))
      retryAttempt += 1
    }
  }

  return { connected, lastEventId, start, stop }
}
