<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import type { DshWorkspaceScope } from '../../api/types'
import { Button } from '../../components/ui/button'
import { LoadingState } from '../../components/ui/feedback'
import ErrorState from '../../components/ui/feedback/ErrorState.vue'

type Phase = 'starting' | 'active' | 'error'

const route = useRoute()
const phase = ref<Phase>('starting')
const activeProfile = ref<string | null>(null)
const errorMessage = ref('')
const iframeKey = ref(0)
const keepAliveTimer: { id: number | null } = { id: null }

const requestedProfile = typeof route.query.profile === 'string' ? route.query.profile : ''
const scope = computed<DshWorkspaceScope>(() =>
  route.query.scope === 'shared' ? 'shared' : 'personal',
)
// scope 决定 iframe 指向的代理前缀：个人与共享是两个独立的工作台入口。
const iframeSrc = computed(() =>
  scope.value === 'shared' ? '/agent-workspace-shared/' : '/agent-workspace/',
)

onMounted(enterWorkspace)
onUnmounted(stopKeepAlive)

async function enterWorkspace() {
  phase.value = 'starting'
  errorMessage.value = ''
  stopKeepAlive()
  try {
    const result = await api.authorizeDshWorkspace(requestedProfile || null, scope.value)
    activeProfile.value = result.profile_key
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
      const status = await api.getDshRuntimeStatus(scope.value)
      if (status.status !== 'running' && status.status !== 'starting') {
        stopKeepAlive()
        errorMessage.value = 'DSH 工作台进程已停止或异常退出，请重新进入。'
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
        <span class="text-sm font-medium">
          {{ scope === 'shared' ? '小组共享 DSH 工作台' : 'DSH 工作台' }}
        </span>
        <span v-if="activeProfile" class="rounded-full bg-primary/10 px-2 py-px text-xs text-primary">
          能力平面：{{ activeProfile }}
        </span>
        <span v-else-if="phase === 'active'" class="rounded-full bg-muted px-2 py-px text-xs text-muted-foreground">
          未注入能力平面
        </span>
      </div>
      <div class="flex items-center gap-2">
        <Button v-if="phase === 'active'" variant="outline" size="sm" @click="reloadWorkspace()">刷新</Button>
        <Button variant="outline" size="sm" @click="closeTab()">关闭工作台</Button>
      </div>
    </div>

    <div v-if="phase === 'starting'" class="flex flex-1 flex-col items-center justify-center gap-3">
      <LoadingState label="正在启动 DSH 工作台，首次启动可能需要约一分钟..." />
    </div>

    <iframe
      v-else-if="phase === 'active'"
      :key="iframeKey"
      :src="iframeSrc"
      class="h-full w-full flex-1 border-0 bg-background"
      title="DSH 工作台"
      allow="clipboard-read; clipboard-write"
    />

    <div v-else class="flex flex-1 flex-col items-center justify-center gap-3 p-6">
      <ErrorState title="无法进入 DSH 工作台" :description="errorMessage || '请稍后重试。'" compact>
        <Button size="sm" @click="enterWorkspace()">重试</Button>
      </ErrorState>
    </div>
  </div>
</template>
