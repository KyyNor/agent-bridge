<script setup lang="ts">
import { currentWebWorkspaceUnavailableReason, type WorkspaceMode } from '../../lib/workspaceBrowser'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ExternalLink } from '@lucide/vue'
import { api } from '../../api/client'
import type { DshRuntimeStatus, ProjectProfile, WorkspaceAgent, WorkspaceAgentDescriptor, WorkspaceScope } from '../../api/types'
import {
  readPreferredSelection,
  resolvePreferredProfile,
  writePreferredSelection,
} from '../../lib/dshWorkspacePreference'
import { formatLocalDatetime } from '../../lib/time'
import {
  DEFAULT_WORKSPACE_AGENT,
  WORKSPACE_AGENTS,
  WORKSPACE_STATUS_LABELS,
  isWorkspaceAlive,
  workspaceAgentLabel,
  workspaceEntryUrl,
  workspaceTitle,
} from '../../lib/workspaceAgents'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import StatusBadge from '../../components/StatusBadge.vue'
import ErrorState from '../../components/ui/feedback/ErrorState.vue'
import WorkspaceOptionGrid from '../../components/dsh/WorkspaceOptionGrid.vue'

/** 选择卡片的数据形状：与 WorkspaceOptionGrid 的泛型 props 对应。 */
type Option<T extends string> = {
  value: T
  label: string
  sublabel?: string
  description?: string
}

const router = useRouter()
const loading = ref(true)
const profiles = ref<ProjectProfile[]>([])
const profilesError = ref('')
const selectedProfile = ref('')
const agent = ref<WorkspaceAgent>(DEFAULT_WORKSPACE_AGENT)
const scope = ref<WorkspaceScope>('personal')
const webUnavailableReason = currentWebWorkspaceUnavailableReason()
const mode = ref<WorkspaceMode>(webUnavailableReason ? 'terminal' : 'web')
const runtime = ref<DshRuntimeStatus | null>(null)
const actionError = ref('')
const stopping = ref(false)

const activeProfiles = computed(() => profiles.value.filter(profile => profile.status === 'active'))
const running = computed(() => isWorkspaceAlive(runtime.value?.status))
// 共享 runtime 运行期间能力平面锁定为其 active profile，不允许修改。
const profileLocked = computed(() => scope.value === 'shared' && (running.value || runtime.value?.status === 'unhealthy'))

/** 后端目录不可用时的兜底目录：两种 Agent 的能力平面入口固定存在。 */
const agents = ref<WorkspaceAgentDescriptor[]>([])
const descriptor = computed(() => agents.value.find(item => item.agent === agent.value) || null)
// 展示名优先取后端目录，取不到时用前端兜底文案。
const agentLabel = computed(() => descriptor.value?.display_name || workspaceAgentLabel(agent.value))
const sharedSupported = computed(() => descriptor.value ? descriptor.value.supports_shared : true)

const agentOptions = computed<Option<WorkspaceAgent>[]>(() => agents.value.map(item => ({
  value: item.agent,
  label: item.display_name || workspaceAgentLabel(item.agent),
})))
const scopeOptions = computed<Option<WorkspaceScope>[]>(() => {
  const options: Option<WorkspaceScope>[] = [
    { value: 'personal', label: '个人', description: '独立工作台、会话与记忆' },
    { value: 'shared', label: '小组', description: '与同组成员共享工作台、会话与记忆' },
  ]
  return sharedSupported.value && mode.value === 'web' ? options : options.filter(option => option.value === 'personal')
})
const profileOptions = computed<Option<string>[]>(() => [
  {
    value: '',
    label: '不使用能力平面',
    description: '仅使用工作台自带的模型与工具',
  },
  ...activeProfiles.value.map(profile => ({
    value: profile.profile_key,
    label: profile.name,
    sublabel: profile.profile_key,
    description: profile.description || '',
  })),
])

watch(mode, () => {
  if (mode.value === 'terminal') scope.value = 'personal'
  void loadRuntime()
})
watch(scope, () => {
  actionError.value = ''
  void loadRuntime()
})
watch(agent, () => {
  actionError.value = ''
  // Agent 不支持小组共享时回落到个人范围，避免进入必然失败的组合。
  if (scope.value === 'shared' && !sharedSupported.value) scope.value = 'personal'
  else void loadRuntime()
})

onMounted(async () => {
  const preferred = readPreferredSelection()
  agent.value = preferred.agent
  scope.value = mode.value === 'terminal' ? 'personal' : preferred.scope
  await Promise.all([loadAgents(), loadProfiles(), loadRuntime()])
  loading.value = false
})

async function loadAgents() {
  try {
    const list = await api.listWorkspaceAgents()
    agents.value = list.length ? list : fallbackAgents()
  } catch {
    agents.value = fallbackAgents()
  }
  // 记忆中的 Agent 不在当前部署目录内时回落到默认 Agent。
  if (!agents.value.some(item => item.agent === agent.value)) agent.value = DEFAULT_WORKSPACE_AGENT
  if (scope.value === 'shared' && !sharedSupported.value) scope.value = 'personal'
}

function fallbackAgents(): WorkspaceAgentDescriptor[] {
  return WORKSPACE_AGENTS.map(item => ({
    agent: item,
    display_name: workspaceAgentLabel(item),
    workspace_url: workspaceEntryUrl(item, 'personal'),
    shared_workspace_url: workspaceEntryUrl(item, 'shared'),
    supports_shared: true,
  }))
}

async function loadProfiles() {
  profilesError.value = ''
  try {
    profiles.value = await api.listProfiles()
    // 默认选中浏览器记忆的能力平面；记忆失效（被删/停用/无权限）时清除并回落。
    selectedProfile.value = resolvePreferredProfile(
      activeProfiles.value.map(profile => profile.profile_key),
    )
  } catch (e: any) {
    profiles.value = []
    profilesError.value = e.message || '无法加载能力平面'
  }
}

async function loadRuntime() {
  const requestedAgent = agent.value
  const requestedScope = scope.value
  const requestedMode = mode.value
  try {
    const status = await api.getDshRuntimeStatus(requestedScope, requestedAgent, requestedMode)
    // 快速切换 Agent/范围时丢弃过期响应，避免状态卡展示上一个实例。
    if (requestedAgent !== agent.value || requestedScope !== scope.value || requestedMode !== mode.value) return
    runtime.value = status
  } catch {
    runtime.value = null
    return
  }
  // 共享 runtime 已运行：以 Runtime 当前 active profile 为准，覆盖浏览器旧选择。
  if (scope.value === 'shared' && running.value && runtime.value?.profile_key !== undefined) {
    selectedProfile.value = runtime.value.profile_key || ''
  }
}

/** 在新标签页打开伪全屏工作台；Agent、范围与能力平面经查询串传递，授权在目标页完成。
 *  当前选择记入浏览器 localStorage，下次进入默认选中同一组合。 */
function openWorkspace() {
  writePreferredSelection({ agent: agent.value, scope: scope.value, profileKey: selectedProfile.value })
  const target = router.resolve({
    name: 'workspace-live',
    query: {
      agent: agent.value,
      mode: mode.value,
      scope: scope.value,
      ...(selectedProfile.value ? { profile: selectedProfile.value } : {}),
    },
  })
  window.open(target.href, '_blank', 'noopener')
}

async function stopRuntime() {
  if (scope.value === 'shared'
    && !window.confirm(`停止小组共享 ${agentLabel.value} 会影响同组的其他成员，确定停止吗？`)) {
    return
  }
  stopping.value = true
  actionError.value = ''
  try {
    await api.stopDshRuntime(scope.value, agent.value, mode.value)
    await loadRuntime()
  } catch (e: any) {
    actionError.value = e.message || '停止工作台失败'
  } finally {
    stopping.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <div v-else class="space-y-4">
    <!-- 当前工作台状态 -->
    <Card>
      <CardContent class="p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 space-y-2">
            <div class="flex flex-wrap items-center gap-2" role="status">
              <h2 class="text-sm font-semibold">{{ workspaceTitle(agent, scope) }}</h2>
              <StatusBadge
                :status="running ? 'running' : 'disabled'"
                :label="runtime ? WORKSPACE_STATUS_LABELS[runtime.status] || runtime.status : '未知'"
              />
            </div>
            <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground empty:hidden">
              <span v-if="runtime?.linux_user">运行身份：<span class="font-mono">{{ runtime.linux_user }}</span>（小组 Linux 用户）</span>
              <span v-if="runtime?.profile_key">当前能力平面：<span class="font-mono">{{ runtime.profile_key }}</span></span>
              <span v-if="runtime?.last_access_at">最近访问：{{ formatLocalDatetime(runtime.last_access_at) }}</span>
              <span v-if="running">空闲 {{ runtime?.idle_minutes ?? 0 }} 分钟（长期未使用会自动回收）</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" @click="loadRuntime()">刷新</Button>
            <Button v-if="running" variant="outline" size="sm" :disabled="stopping" @click="stopRuntime()">
              {{ stopping ? '停止中...' : `停止 ${agentLabel} 工作台` }}
            </Button>
          </div>
        </div>
        <p v-if="scope === 'shared' && running" class="mt-2 text-xs text-muted-foreground">停止共享工作台会影响同组其他成员</p>
        <p v-if="actionError" role="alert" class="mt-2 text-xs text-destructive">{{ actionError }}</p>
      </CardContent>
    </Card>

    <!-- Agent、范围与能力平面选择 -->
    <Card>
      <CardContent class="space-y-5 p-5">
        <div class="grid gap-5 xl:grid-cols-2">
          <fieldset class="min-w-0">
            <legend class="mb-2 text-sm font-medium">Coding Agent</legend>
            <WorkspaceOptionGrid v-model="agent" name="workspace-agent" :options="agentOptions" />
          </fieldset>
          <fieldset class="min-w-0">
            <legend class="mb-2 text-sm font-medium">界面</legend>
            <div class="flex flex-wrap items-center gap-2">
              <Button variant="outline" :aria-pressed="mode === 'web'" class="aria-pressed:border-primary aria-pressed:bg-primary/5" :disabled="!!webUnavailableReason" :aria-describedby="webUnavailableReason ? 'workspace-web-unavailable' : undefined" @click="mode = 'web'">Web 工作台</Button>
              <Button variant="outline" :aria-pressed="mode === 'terminal'" class="aria-pressed:border-primary aria-pressed:bg-primary/5" @click="mode = 'terminal'">网页终端</Button>
              <p v-if="webUnavailableReason" id="workspace-web-unavailable" class="text-xs text-warning-soft-fg">{{ webUnavailableReason }}</p>
            </div>
            <p v-if="mode === 'terminal'" class="mt-2 text-xs text-muted-foreground">终端仅支持个人范围。每个 Agent 一个会话，同时只允许一个页面输入。</p>
          </fieldset>
        </div>

        <div class="grid gap-5 border-t border-border pt-5 xl:grid-cols-2">
          <fieldset class="min-w-0">
            <legend class="mb-2 text-sm font-medium">工作空间范围</legend>
            <WorkspaceOptionGrid v-model="scope" name="workspace-scope" :options="scopeOptions" />
          </fieldset>
          <div class="min-w-0">
            <ErrorState v-if="profilesError" title="无法加载能力平面" :description="profilesError" compact>
              <Button variant="outline" size="sm" @click="loadProfiles()">重试</Button>
            </ErrorState>
            <fieldset v-else :disabled="profileLocked">
              <legend class="mb-2 text-sm font-medium">能力平面</legend>
              <p class="mb-2 text-xs text-muted-foreground">通过 MCP 提供 Agent Bridge 的工具与知识。</p>
              <p v-if="profileLocked" class="mb-2 text-xs text-muted-foreground">共享工作台的能力平面已锁定。</p>
              <WorkspaceOptionGrid v-model="selectedProfile" name="workspace-profile" :options="profileOptions" stacked />
            </fieldset>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3 border-t border-border pt-5">
          <Button size="sm" @click="openWorkspace()">
            <ExternalLink :size="14" aria-hidden="true" />进入 {{ agentLabel }}
          </Button>
          <span class="text-xs text-muted-foreground">在新标签页打开。工作台未运行时，启动约需 10 秒。</span>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
