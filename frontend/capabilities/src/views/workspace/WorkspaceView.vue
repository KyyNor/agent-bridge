<script setup lang="ts">
import { currentWebWorkspaceSupport, type WorkspaceMode } from '../../lib/workspaceBrowser'
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
const webSupported = currentWebWorkspaceSupport()
const mode = ref<WorkspaceMode>(webSupported ? 'web' : 'terminal')
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
  sublabel: item.agent,
  description: item.agent === DEFAULT_WORKSPACE_AGENT
    ? 'DeepSeek Harness Web 工作台，内置终端与插件生态'
    : 'Pi Agent 编码工作台，按托管版本清单启动',
})))
const scopeOptions = computed<Option<WorkspaceScope>[]>(() => {
  const options: Option<WorkspaceScope>[] = [
    { value: 'personal', label: '个人', description: '独立工作台，只保留你自己的 session 与记忆' },
    { value: 'shared', label: '小组', description: '同组成员共享同一个工作台、session 与记忆' },
  ]
  return sharedSupported.value && mode.value === 'web' ? options : options.filter(option => option.value === 'personal')
})
const profileOptions = computed<Option<string>[]>(() => [
  {
    value: '',
    label: '不使用能力平面',
    description: `不向 ${agentLabel.value} 注入任何 MCP 能力，仅使用工作台自身的模型与工具`,
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
      <CardContent class="space-y-4 p-5">
        <div class="flex items-center justify-between gap-4">
          <div>
            <div class="text-sm font-medium">{{ workspaceTitle(agent, scope) }}</div>
            <div class="mt-1 text-xs text-muted-foreground">
              工作台在服务器上以你所属小组的 Linux 用户身份运行，经站内反向代理访问，浏览器地址始终是 Agent Bridge。
            </div>
          </div>
          <Button variant="outline" size="sm" @click="loadRuntime()">刷新</Button>
        </div>
        <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <StatusBadge
            :status="running ? 'running' : 'disabled'"
            :label="runtime ? WORKSPACE_STATUS_LABELS[runtime.status] || runtime.status : '未知'"
          />
          <span v-if="runtime?.linux_user">Linux 用户：<span class="font-mono">{{ runtime.linux_user }}</span></span>
          <span v-if="scope === 'shared' && runtime?.linux_user">
            共享工作空间：<span class="font-mono">{{ runtime.linux_user }}</span>
          </span>
          <span v-if="runtime?.profile_key">当前能力平面：<span class="font-mono">{{ runtime.profile_key }}</span></span>
          <span v-if="runtime?.last_access_at">最近访问：{{ formatLocalDatetime(runtime.last_access_at) }}</span>
          <span v-if="running">空闲 {{ runtime?.idle_minutes ?? 0 }} 分钟（长期未使用会自动回收）</span>
        </div>
        <div class="flex items-center gap-3">
          <Button v-if="running" variant="outline" size="sm" :disabled="stopping" @click="stopRuntime()">
            {{ stopping ? '停止中...' : `停止 ${agentLabel} 工作台` }}
          </Button>
          <span v-if="scope === 'shared'" class="text-xs text-muted-foreground">
            停止共享工作台会影响同组其他成员
          </span>
          <span v-if="actionError" class="text-xs text-destructive">{{ actionError }}</span>
        </div>
      </CardContent>
    </Card>

    <!-- Agent、范围与能力平面选择 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div>
          <div class="text-sm font-medium">Coding Agent</div>
          <div class="mt-1 text-xs text-muted-foreground">
            选择工作台由哪个 Coding Agent 承载；两种 Agent 都支持个人与小组范围，能力平面注入方式一致。
          </div>
        </div>
        <WorkspaceOptionGrid v-model="agent" :options="agentOptions" />

        <div class="border-t border-border pt-4">
          <div class="text-sm font-medium">工作空间范围</div>
          <div class="mt-1 text-xs text-muted-foreground">
            个人使用独立工作台；小组与同组成员共享同一个 Runtime、session 与记忆（按所属小组映射的 Linux 用户确定）。
          </div>
        </div>
        <WorkspaceOptionGrid v-model="scope" :options="scopeOptions" />

        <div class="border-t border-border pt-4">
          <div class="flex items-center justify-between">
            <div class="text-sm font-medium">选择能力平面</div>
            <span v-if="profileLocked" class="rounded-full bg-muted px-2 py-px text-xs text-muted-foreground">
              共享工作台运行中，已锁定为当前能力平面
            </span>
          </div>
          <div class="mt-1 text-xs text-muted-foreground">
            能力平面决定工作台内可用的 Agent Bridge 工具与知识能力，以 MCP 形式注入；不选择则进入不带任何 MCP 的工作台。
          </div>
        </div>
        <div v-if="profilesError" class="space-y-2">
          <ErrorState title="无法加载能力平面" :description="profilesError" compact>
            <Button variant="outline" size="sm" @click="loadProfiles()">重试</Button>
          </ErrorState>
        </div>
        <div v-else class="space-y-2" :class="{ 'pointer-events-none opacity-60': profileLocked }">
          <WorkspaceOptionGrid v-model="selectedProfile" :options="profileOptions" stacked />
        </div>
        <div class="space-y-2 border-t border-border pt-4">
          <div class="text-sm font-medium">界面</div>
          <div class="flex gap-2">
            <Button :variant="mode === 'web' ? 'default' : 'outline'" :disabled="!webSupported" @click="mode = 'web'">Web 工作台</Button>
            <Button :variant="mode === 'terminal' ? 'default' : 'outline'" @click="mode = 'terminal'">网页终端</Button>
          </div>
          <p v-if="!webSupported" class="text-xs text-muted-foreground">当前浏览器不满足 Web 工作台要求（Chrome 111+ 或同等能力），请使用网页终端。</p>
          <p v-if="mode === 'terminal'" class="text-xs text-muted-foreground">终端仅支持个人范围；每个 Agent 一个会话，同时只允许一个页面输入。</p>
        </div>
        <div class="flex items-center gap-3">
          <Button size="sm" @click="openWorkspace()">
            <ExternalLink :size="14" class="mr-1" />进入 {{ agentLabel }}
          </Button>
          <span class="text-xs text-muted-foreground">将在新标签页打开全屏工作台；工作台未运行时启动约需十秒</span>
        </div>
      </CardContent>
    </Card>
  </div>
</template>