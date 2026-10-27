<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import type { WorkspaceAgent } from '../../api/types'
import { Button } from '../ui/button'
import { api } from '../../api/client'
import { resolveTerminalTheme } from '../../lib/workspaceTerminalTheme'
import { isWorkspaceAlive } from '../../lib/workspaceAgents'
import {
  TERMINAL_MAX_RETRIES,
  TERMINAL_STABLE_RESET_MS,
  terminalCloseAction,
  terminalCloseMessage,
  terminalRetryDelay,
} from '../../lib/terminalReconnect'

const props = withDefaults(defineProps<{ agent: WorkspaceAgent; themeId?: string }>(), { themeId: 'midnight' })
const emit = defineEmits<{ ended: [] }>()
const container = ref<HTMLElement | null>(null)
const theme = computed(() => resolveTerminalTheme(props.themeId))
const terminalStyle = computed(() => ({ backgroundColor: theme.value.colors?.background, color: theme.value.colors?.foreground }))
const status = ref('正在连接终端…')
const canReconnect = ref(false)
const ended = ref(false)
let terminal: Terminal | null = null
let fit: FitAddon | null = null
let socket: WebSocket | null = null
let observer: ResizeObserver | null = null
let heartbeat: number | undefined
let retry: number | undefined
let stableTimer: number | undefined
let destroyed = false
let retries = 0
let hasConnected = false
let exitCode: number | null = null

function applyTheme() {
  if (!terminal || !container.value) return
  terminal.options.theme = { ...theme.value.colors }
}
watch(theme, applyTheme, { flush: 'post' })

function send(payload: object) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload))
}
function resize() {
  if (!container.value?.clientWidth || !fit || !terminal) return
  fit.fit()
  send({ type: 'resize', cols: terminal.cols, rows: terminal.rows })
}
function markEnded(code: number) {
  ended.value = true
  canReconnect.value = false
  status.value = terminalCloseMessage(code, exitCode)
  emit('ended')
}

/** 重试前确认 runtime 仍然存在：Agent 已退出时重连只会空转。 */
async function runtimeAlive() {
  try {
    const runtime = await api.getDshRuntimeStatus('personal', props.agent, 'terminal')
    return isWorkspaceAlive(runtime.status)
  } catch {
    // 状态查询失败不阻断有限重试，交给重试次数上限兜底。
    return true
  }
}

async function retryAfterCheck() {
  if (!(await runtimeAlive())) {
    if (!destroyed) markEnded(4000)
    return
  }
  if (destroyed) return
  canReconnect.value = true
  window.clearTimeout(retry)
  retry = window.setTimeout(() => { retries += 1; connect() }, terminalRetryDelay(retries))
}

function connect() {
  if (destroyed) return
  window.clearTimeout(retry)
  socket?.close()
  status.value = '正在连接终端…'
  canReconnect.value = false
  ended.value = false
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/v1/agent-workspace/terminal/${props.agent}`)
  socket = connection
  connection.binaryType = 'arraybuffer'
  connection.onopen = () => {
    if (socket !== connection || destroyed) { connection.close(); return }
    hasConnected = true
    status.value = '已连接；关闭页面后保留终端两分钟'
    resize()
    terminal?.focus()
    window.clearInterval(heartbeat)
    heartbeat = window.setInterval(() => send({ type: 'ping' }), 15000)
    // 稳定存活足够久才允许清零重试计数：连上即断不能把无限重连刷回来。
    window.clearTimeout(stableTimer)
    stableTimer = window.setTimeout(() => { retries = 0 }, TERMINAL_STABLE_RESET_MS)
  }
  connection.onmessage = (event) => {
    if (socket !== connection) return
    if (event.data instanceof ArrayBuffer) terminal?.write(new Uint8Array(event.data))
    else {
      try {
        const message = JSON.parse(event.data)
        if (message.type === 'exit') {
          if (typeof message.code === 'number') exitCode = message.code
          markEnded(4000)
        }
      } catch { status.value = '终端返回了无法识别的消息' }
    }
  }
  connection.onclose = (event) => {
    if (socket !== connection || destroyed) return
    window.clearInterval(heartbeat)
    window.clearTimeout(stableTimer)
    const action = terminalCloseAction(event.code, retries, hasConnected)
    if (action === 'ended') { markEnded(event.code); return }
    if (action === 'stop') {
      canReconnect.value = true
      status.value = terminalCloseMessage(event.code)
      return
    }
    status.value = `连接已断开，正在重试（${retries + 1}/${TERMINAL_MAX_RETRIES}）…`
    void retryAfterCheck()
  }
}

function reconnect() {
  retries = 0
  exitCode = null
  connect()
}

function closePage() {
  window.close()
  if (!window.closed) status.value = '浏览器不允许自动关闭此页面，请手动关闭标签页'
}

onMounted(() => {
  terminal = new Terminal({ cursorBlink: true, scrollback: 3000, fontSize: 14,
    fontFamily: 'Menlo, Consolas, monospace' })
  applyTheme()
  fit = new FitAddon()
  terminal.loadAddon(fit)
  terminal.open(container.value!)
  terminal.onData(data => send({ type: 'input', data }))
  observer = new ResizeObserver(resize)
  observer.observe(container.value!)
  connect()
})
onBeforeUnmount(() => {
  destroyed = true
  window.clearTimeout(retry)
  window.clearTimeout(stableTimer)
  window.clearInterval(heartbeat)
  observer?.disconnect()
  socket?.close()
  terminal?.dispose()
})
</script>

<template>
  <div class="flex min-h-0 min-w-0 flex-1 flex-col">
    <div class="flex min-h-10 shrink-0 flex-wrap items-center justify-between gap-2 py-2 border-b border-border bg-card px-4 text-xs text-muted-foreground">
      <span role="status">{{ status }}</span>
      <div class="flex items-center gap-2">
        <Button v-if="canReconnect" variant="outline" size="sm" @click="reconnect">重新连接</Button>
        <Button v-if="ended" variant="outline" size="sm" @click="closePage">关闭页面</Button>
      </div>
    </div>
    <div ref="container" :style="terminalStyle" class="workspace-terminal min-h-0 flex-1 overflow-hidden p-2" />
  </div>
</template>
