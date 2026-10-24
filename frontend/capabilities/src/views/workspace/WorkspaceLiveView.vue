<script setup lang="ts">
import { currentWebWorkspaceSupport, normalizeWorkspaceMode } from '../../lib/workspaceBrowser'
import WorkspaceTerminal from '../../components/dsh/WorkspaceTerminal.vue'
import TerminalDirectoryPicker from '../../components/dsh/TerminalDirectoryPicker.vue'
import WorkspaceFiles from '../../components/dsh/WorkspaceFiles.vue'
import { TERMINAL_THEMES, readTerminalTheme, saveTerminalTheme } from '../../lib/workspaceTerminalTheme'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import type { WorkspaceAgent } from '../../api/types'
import {
  isWorkspaceAlive,
  normalizeWorkspaceAgent,
  normalizeWorkspaceScope,
  workspaceAgentLabel,
  workspaceEntryUrl,
  workspaceTitle,
} from '../../lib/workspaceAgents'
import { Button } from '../../components/ui/button'
import { LoadingState } from '../../components/ui/feedback'
import ErrorState from '../../components/ui/feedback/ErrorState.vue'

type Phase = 'choosing' | 'starting' | 'active' | 'error'

const route = useRoute()
const phase = ref<Phase>('starting')
const terminalTheme = ref(readTerminalTheme())
watch(terminalTheme, saveTerminalTheme)
const activeProfile = ref<string | null>(null)
const errorMessage = ref('')
const iframeKey = ref(0)
const workingDirectory = ref('')
const keepAliveTimer: { id: number | null } = { id: null }

const requestedProfile = typeof route.query.profile === 'string' ? route.query.profile : ''
const agent = computed<WorkspaceAgent>(() => normalizeWorkspaceAgent(route.query.agent))
const mode = normalizeWorkspaceMode(route.query.mode)
const scope = computed(() => normalizeWorkspaceScope(route.query.scope))
const agentLabel = computed(() => workspaceAgentLabel(agent.value))
const title = computed(() => workspaceTitle(agent.value, scope.value))
// scope 与 agent 决定 iframe 指向的代理前缀：个人与共享是两个独立的工作台入口；
// 授权成功后改用后端返回的 workspace_url（权威值）。
const iframeSrc = ref(workspaceEntryUrl(agent.value, scope.value))

onMounted(() => {
  if (mode === 'terminal') phase.value = 'choosing'
  else void enterWorkspace()
})
onUnmounted(stopKeepAlive)

function chooseDirectory() {
  stopKeepAlive()
  phase.value = 'choosing'
  errorMessage.value = ''
}

async function enterWorkspace(directory?: string) {
  if (directory) workingDirectory.value = directory
  if (mode === 'terminal' && !workingDirectory.value) { chooseDirectory(); return }
  phase.value = 'starting'
  errorMessage.value = ''
  stopKeepAlive()
  try {
    if (mode === 'web' && !currentWebWorkspaceSupport()) throw new Error('当前浏览器不支持 Web 工作台，请返回入口选择网页终端。')
    if (mode === 'terminal' && scope.value !== 'personal') throw new Error('网页终端目前仅支持个人范围。')
    const result = mode === 'terminal'
      ? await api.startTerminal(agent.value, workingDirectory.value, requestedProfile || null)
      : await api.authorizeDshWorkspace(requestedProfile || null, scope.value, agent.value, mode)
    if (mode === 'terminal') workingDirectory.value = result.working_directory || workingDirectory.value
    activeProfile.value = result.profile_key
    iframeSrc.value = result.workspace_url || workspaceEntryUrl(agent.value, scope.value)
    phase.value = 'active'
    iframeKey.value += 1
    startKeepAlive()
  } catch (e: any) {
    errorMessage.value = e.message || '进入工作台失败'
    phase.value = 'error'
  }
}

function reloadWorkspace() {
  iframeKey.value += 1
}

function closeTab() {
  stopKeepAlive()
  window.close()
}

function startKeepAlive() {
  stopKeepAlive()
  // 定期查询自身 runtime：既能展示空闲状态，也能发现进程异常退出
  keepAliveTimer.id = window.setInterval(async () => {
    try {
      const status = await api.getDshRuntimeStatus(scope.value, agent.value, mode)
      if (!isWorkspaceAlive(status.status)) {
        stopKeepAlive()
        errorMessage.value = `${title.value}进程已停止或异常退出，请重新进入。`
        phase.value = 'error'
      }
    } catch {
      /* 状态查询失败不打断工作台 */
    }
  }, 60000)
}

function stopKeepAlive() {
  if (keepAliveTimer.id !== null) {
    window.clearInterval(keepAliveTimer.id)
    keepAliveTimer.id = null
  }
}
</script>

<template>
  <!-- 伪全屏工作台：只在独立标签页打开，浏览器地址保持 Agent Bridge 域名 -->
  <div class="fixed inset-0 z-[60] flex flex-col bg-background">
    <div class="flex h-11 shrink-0 items-center justify-between border-b border-border bg-card px-4">
      <div class="flex items-center gap-3">
        <span class="text-sm font-medium">{{ title }}</span>
        <span v-if="activeProfile" class="rounded-full bg-primary/10 px-2 py-px text-xs text-primary">
          能力平面：{{ activeProfile }}
        </span>
        <span v-else-if="phase === 'active'" class="rounded-full bg-muted px-2 py-px text-xs text-muted-foreground">
          未注入能力平面
        </span>
      </div>
      <div class="flex items-center gap-2">
        <Button v-if="phase === 'active' && mode === 'web'" variant="outline" size="sm" @click="reloadWorkspace()">刷新</Button>
        <label v-if="mode === 'terminal' && phase === 'active'" class="flex items-center gap-2 text-xs text-muted-foreground">
          <span>配色</span>
          <select v-model="terminalTheme" aria-label="终端配色" class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <option v-for="item in TERMINAL_THEMES" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>
        <Button v-if="mode === 'terminal' && phase === 'active'" variant="outline" size="sm" @click="chooseDirectory()">重新进入</Button>
        <Button variant="outline" size="sm" @click="closeTab()">关闭工作台</Button>
      </div>
    </div>

    <TerminalDirectoryPicker v-if="phase === 'choosing'" :agent="agent" :initial-directory="workingDirectory || undefined" @start="enterWorkspace" @cancel="enterWorkspace()" />

    <div v-else-if="phase === 'starting'" class="flex flex-1 flex-col items-center justify-center gap-3">
      <LoadingState :label="`正在启动 ${title}，首次启动可能需要约一分钟...`" />
    </div>

    <div v-else-if="phase === 'active' && mode === 'terminal'" :key="iframeKey" class="flex min-h-0 flex-1 overflow-hidden">
      <WorkspaceFiles :agent="agent" />
      <WorkspaceTerminal :agent="agent" :theme-id="terminalTheme" @ended="stopKeepAlive" />
    </div>
    <iframe
      v-else-if="phase === 'active'"
      :key="iframeKey"
      :src="iframeSrc"
      class="h-full w-full flex-1 border-0 bg-background"
      :title="title"
      allow="clipboard-read; clipboard-write"
    />

    <div v-else class="flex flex-1 flex-col items-center justify-center gap-3 p-6">
      <ErrorState :title="`无法进入${title}`" :description="errorMessage || '请稍后重试。'" compact>
        <div class="flex gap-2">
          <Button v-if="mode === 'terminal'" variant="outline" size="sm" @click="chooseDirectory()">重新选择目录</Button>
          <Button size="sm" @click="enterWorkspace()">重试</Button>
        </div>
      </ErrorState>
    </div>
  </div>
</template>