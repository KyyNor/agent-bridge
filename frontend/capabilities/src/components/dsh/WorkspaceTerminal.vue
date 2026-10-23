<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import type { WorkspaceAgent } from '../../api/types'
import { Button } from '../ui/button'

const props = defineProps<{ agent: WorkspaceAgent }>()
const emit = defineEmits<{ ended: [] }>()
const container = ref<HTMLElement | null>(null)
const status = ref('正在连接终端…')
const canReconnect = ref(false)
let terminal: Terminal | null = null
let fit: FitAddon | null = null
let socket: WebSocket | null = null
let observer: ResizeObserver | null = null
let heartbeat: number | undefined
let retry: number | undefined
let destroyed = false
let retries = 0
let hasConnected = false

function send(payload: object) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload))
}
function resize() {
  if (!container.value?.clientWidth || !fit || !terminal) return
  fit.fit()
  send({ type: 'resize', cols: terminal.cols, rows: terminal.rows })
}
function connect() {
  if (destroyed) return
  window.clearTimeout(retry)
  socket?.close()
  status.value = '正在连接终端…'
  canReconnect.value = false
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/v1/agent-workspace/terminal/${props.agent}`)
  socket = connection
  connection.binaryType = 'arraybuffer'
  connection.onopen = () => {
    if (socket !== connection || destroyed) { connection.close(); return }
    hasConnected = true
    retries = 0
    status.value = '已连接；关闭页面后保留终端两分钟'
    resize()
    terminal?.focus()
    window.clearInterval(heartbeat)
    heartbeat = window.setInterval(() => send({ type: 'ping' }), 15000)
  }
  connection.onmessage = (event) => {
    if (socket !== connection) return
    if (event.data instanceof ArrayBuffer) terminal?.write(new Uint8Array(event.data))
    else {
      try {
        const message = JSON.parse(event.data)
        if (message.type === 'exit') {
          status.value = `Agent 已退出${message.code == null ? '' : `（${message.code}）`}`
          emit('ended')
        }
      } catch { status.value = '终端返回了无法识别的消息' }
    }
  }
  connection.onclose = (event) => {
    if (socket !== connection || destroyed) return
    window.clearInterval(heartbeat)
    if (event.code === 4000) { status.value = 'Agent 已退出，请重新进入工作台'; emit('ended'); return }
    if (event.code === 4009) { status.value = '该终端已在其他页面连接，请先关闭另一页面'; canReconnect.value = true; return }
    if (event.code === 1008 || !hasConnected) { status.value = '无法连接终端，请检查登录状态并重新进入'; canReconnect.value = true; return }
    canReconnect.value = true
    if (retries < 5) {
      status.value = '连接已断开，正在重连原终端…'
      retry = window.setTimeout(connect, Math.min(1000 * 2 ** retries++, 10000))
    } else status.value = '重连失败；超过两分钟后需重新进入工作台'
  }
}

onMounted(() => {
  terminal = new Terminal({ cursorBlink: true, scrollback: 3000, fontSize: 14,
    fontFamily: 'Menlo, Consolas, monospace' })
  const colors = getComputedStyle(container.value!)
  terminal.options.theme = { background: colors.backgroundColor, foreground: colors.color }
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
  window.clearInterval(heartbeat)
  observer?.disconnect()
  socket?.close()
  terminal?.dispose()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="flex h-10 shrink-0 items-center justify-between border-b border-border bg-card px-4 text-xs text-muted-foreground">
      <span role="status">{{ status }}</span>
      <Button v-if="canReconnect" variant="outline" size="sm" @click="connect">重新连接</Button>
    </div>
    <div ref="container" class="workspace-terminal min-h-0 flex-1 overflow-hidden p-2" />
  </div>
</template>
