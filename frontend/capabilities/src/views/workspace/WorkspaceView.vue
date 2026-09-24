<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ExternalLink } from '@lucide/vue'
import { api } from '../../api/client'
import type { DshRuntimeStatus, DshWorkspaceScope, ProjectProfile } from '../../api/types'
import {
  readPreferredSelection,
  resolvePreferredProfile,
  writePreferredSelection,
} from '../../lib/dshWorkspacePreference'
import { formatLocalDatetime } from '../../lib/time'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import StatusBadge from '../../components/StatusBadge.vue'
import ErrorState from '../../components/ui/feedback/ErrorState.vue'

const router = useRouter()
const loading = ref(true)
const profiles = ref<ProjectProfile[]>([])
const profilesError = ref('')
const selectedProfile = ref('')
const scope = ref<'personal' | 'shared'>('personal')
const runtime = ref<DshRuntimeStatus | null>(null)
const actionError = ref('')
const stopping = ref(false)

const activeProfiles = computed(() => profiles.value.filter(profile => profile.status === 'active'))
const running = computed(() => runtime.value?.status === 'running' || runtime.value?.status === 'starting')
// 共享 runtime 运行期间能力平面锁定为其 active profile，不允许修改。
const profileLocked = computed(() =>
  scope.value === 'shared' && (running.value || runtime.value?.status === 'unhealthy'),
)

const runtimeStatusLabel: Record<string, string> = {
  running: '运行中',
  starting: '启动中',
  unhealthy: '异常',
  stopped: '未运行',
  unassigned: '未分配小组',
  unconfigured: '未配置',
}

const scopeOptions: { value: DshWorkspaceScope; label: string; description: string }[] = [
  { value: 'personal', label: '个人', description: '独立工作台，只保留你自己的 session 与记忆' },
  { value: 'shared', label: '小组', description: '同组成员共享同一个工作台、session 与记忆' },
]

watch(scope, () => {
  actionError.value = ''
  void loadRuntime()
})

onMounted(async () => {
  const preferred = readPreferredSelection()
  scope.value = preferred.scope
  await Promise.all([loadProfiles(), loadRuntime()])
  loading.value = false
})

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
  try {
    runtime.value = await api.getDshRuntimeStatus(scope.value)
  } catch {
    runtime.value = null
    return
  }
  // 共享 runtime 已运行：以 Runtime 当前 active profile 为准，覆盖浏览器旧选择。
  if (scope.value === 'shared' && running.value && runtime.value?.profile_key !== undefined) {
    selectedProfile.value = runtime.value.profile_key || ''
  }
}

/** 在新标签页打开伪全屏工作台；范围与能力平面经查询串传递，授权在目标页完成。
 *  当前选择记入浏览器 localStorage，下次进入默认选中同一组合。 */
function openWorkspace() {
  writePreferredSelection({ scope: scope.value, profileKey: selectedProfile.value })
  const target = router.resolve({
    name: 'workspace-live',
    query: {
      scope: scope.value,
      ...(selectedProfile.value ? { profile: selectedProfile.value } : {}),
    },
  })
  window.open(target.href, '_blank', 'noopener')
}

async function stopRuntime() {
  if (scope.value === 'shared'
    && !window.confirm('停止小组共享工作台会影响同组的其他成员，确定停止吗？')) {
    return
  }
  stopping.value = true
  actionError.value = ''
  try {
    await api.stopDshRuntime(scope.value)
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
            <div class="text-sm font-medium">
              {{ scope === 'shared' ? '小组共享 DSH 工作台' : '我的 DSH 工作台' }}
            </div>
            <div class="mt-1 text-xs text-muted-foreground">
              工作台在服务器上以你所属小组的 Linux 用户身份运行，经站内反向代理访问，浏览器地址始终是 Agent Bridge。
            </div>
          </div>
          <Button variant="outline" size="sm" @click="loadRuntime()">刷新</Button>
        </div>
        <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <StatusBadge
            :status="running ? 'running' : 'disabled'"
            :label="runtime ? runtimeStatusLabel[runtime.status] || runtime.status : '未知'"
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
            {{ stopping ? '停止中...' : '停止工作台' }}
          </Button>
          <span v-if="scope === 'shared'" class="text-xs text-muted-foreground">
            停止共享工作台会影响同组其他成员
          </span>
          <span v-if="actionError" class="text-xs text-destructive">{{ actionError }}</span>
        </div>
      </CardContent>
    </Card>

    <!-- 范围与能力平面选择 -->
    <Card>
      <CardContent class="space-y-4 p-5">
        <div>
          <div class="text-sm font-medium">工作空间范围</div>
          <div class="mt-1 text-xs text-muted-foreground">
            个人使用独立工作台；小组与同组成员共享同一个 DSH Runtime、session 与记忆（按所属小组映射的 Linux 用户确定）。
          </div>
        </div>
        <div class="grid gap-2 sm:grid-cols-2">
          <button
            v-for="option in scopeOptions"
            :key="option.value"
            type="button"
            :class="[
              'rounded-lg border px-4 py-3 text-left transition-colors',
              scope === option.value ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted',
            ]"
            @click="scope = option.value"
          >
            <div class="text-sm font-medium">{{ option.label }}</div>
            <div class="mt-1 text-xs text-muted-foreground">{{ option.description }}</div>
          </button>
        </div>

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
          <button
            type="button"
            :class="[
              'w-full rounded-lg border px-4 py-3 text-left transition-colors',
              selectedProfile === '' ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted',
            ]"
            @click="selectedProfile = ''"
          >
            <div class="text-sm font-medium">不使用能力平面</div>
            <div class="mt-1 text-xs text-muted-foreground">不向 DSH 注入任何 MCP 能力，仅使用工作台自身的模型与工具</div>
          </button>
          <button
            v-for="profile in activeProfiles"
            :key="profile.profile_key"
            type="button"
            :class="[
              'w-full rounded-lg border px-4 py-3 text-left transition-colors',
              selectedProfile === profile.profile_key ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted',
            ]"
            @click="selectedProfile = profile.profile_key"
          >
            <div class="text-sm font-medium">{{ profile.name }}</div>
            <div class="mt-0.5 font-mono text-xs text-muted-foreground">{{ profile.profile_key }}</div>
            <div v-if="profile.description" class="mt-1 text-xs text-muted-foreground">{{ profile.description }}</div>
          </button>
        </div>
        <div class="flex items-center gap-3">
          <Button size="sm" @click="openWorkspace()">
            <ExternalLink :size="14" class="mr-1" />进入 DSH
          </Button>
          <span class="text-xs text-muted-foreground">将在新标签页打开全屏工作台；工作台未运行时启动约需十秒</span>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
