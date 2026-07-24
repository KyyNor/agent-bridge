<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import type { BackendAgent, BackendInfo, KnowledgeBaseSummary, ProfileResourceRule, ProjectProfile } from '../../api/types'
import { Button } from '../ui/button'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'

type ResourceProfileAssignment = ProfileResourceRule & { profile_key: string }

const props = defineProps<{
  open: boolean
  kb: KnowledgeBaseSummary | null
  backends: BackendInfo[]
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
}>()

const profiles = ref<ProjectProfile[]>([])
const pendingProfileKeys = ref<string[]>([])
const saving = ref(false)
const backendOverrides = ref<Record<string, string>>({})
const agentOverrides = ref<Record<string, string>>({})
const agentsByBackend = ref<Record<string, BackendAgent[]>>({})

function isWeknoraBackend(slug: string | null | undefined) {
  return !!slug && props.backends.some(backend => backend.slug === slug && backend.backend_type === 'weknora')
}

async function loadAgentsForBackend(slug: string): Promise<BackendAgent[]> {
  if (!slug || !isWeknoraBackend(slug)) return []
  if (slug in agentsByBackend.value) return agentsByBackend.value[slug]
  try {
    const agents = await api.listBackendAgents(slug)
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: agents }
    return agents
  } catch {
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: [] }
    return []
  }
}

async function load() {
  const kb = props.kb
  if (!kb) return
  profiles.value = []
  pendingProfileKeys.value = []
  backendOverrides.value = {}
  agentOverrides.value = {}
  agentsByBackend.value = {}
  try {
    const [availableProfiles, rules] = await Promise.all([
      api.listProfiles(),
      api.getResourceProfiles('wiki_kb', kb.slug),
    ])
    profiles.value = availableProfiles
    const assignments = rules as ResourceProfileAssignment[]
    pendingProfileKeys.value = assignments.map(rule => rule.profile_key)
    for (const rule of assignments) {
      backendOverrides.value[rule.profile_key] = rule.retrieval_backend_slug || ''
      agentOverrides.value[rule.profile_key] = rule.retrieval_agent_id || ''
    }
    const backendSlugs = new Set(
      pendingProfileKeys.value
        .map(profileKey => backendOverrides.value[profileKey])
        .filter((slug): slug is string => !!slug && isWeknoraBackend(slug)),
    )
    await Promise.all([...backendSlugs].map(loadAgentsForBackend))
  } catch {
    // 加载失败时保留空面板，避免旧知识库的规则残留在新目标上。
  }
}

function toggleProfile(profileKey: string) {
  const index = pendingProfileKeys.value.indexOf(profileKey)
  if (index >= 0) {
    pendingProfileKeys.value.splice(index, 1)
    delete backendOverrides.value[profileKey]
    delete agentOverrides.value[profileKey]
    return
  }
  pendingProfileKeys.value.push(profileKey)
  if (!(profileKey in backendOverrides.value)) backendOverrides.value[profileKey] = ''
  if (!(profileKey in agentOverrides.value)) agentOverrides.value[profileKey] = ''
}

async function onProfileBackendChange(profileKey: string) {
  const slug = backendOverrides.value[profileKey]
  if (slug && isWeknoraBackend(slug)) {
    await loadAgentsForBackend(slug)
  } else {
    agentOverrides.value[profileKey] = ''
  }
}

async function save() {
  const kb = props.kb
  if (!kb) return
  saving.value = true
  try {
    const overrides: Record<string, { retrieval_backend_slug: string | null; retrieval_agent_id: string | null }> = {}
    for (const profileKey of pendingProfileKeys.value) {
      const slug = backendOverrides.value[profileKey] ?? ''
      overrides[profileKey] = {
        retrieval_backend_slug: slug || null,
        retrieval_agent_id: slug && isWeknoraBackend(slug) ? (agentOverrides.value[profileKey] || null) : null,
      }
    }
    await api.setResourceProfiles('wiki_kb', kb.slug, [...pendingProfileKeys.value], Object.keys(overrides).length > 0 ? overrides : undefined)
    emit('update:open', false)
  } catch {
    // 与原页面一致：接口错误由全局请求层记录，弹层保留供用户重试。
  } finally {
    saving.value = false
  }
}

watch(() => [props.open, props.kb?.slug] as const, ([open]) => {
  if (open) void load()
})
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="sm:max-w-[520px]">
      <DialogHeader><DialogTitle>{{ kb?.name || '' }} — 归属能力平面</DialogTitle></DialogHeader>
      <div class="space-y-2">
        <div class="text-xs text-muted-foreground">选择此知识库归属于哪些能力平面，可为每个平面单独指定检索后端。</div>
        <div v-if="profiles.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无能力平面</div>
        <div v-else class="max-h-[400px] space-y-1 overflow-y-auto rounded-lg border border-border p-1">
          <div v-for="profile in profiles" :key="profile.profile_key" class="list-row-interactive rounded-md px-3 py-2">
            <label class="flex cursor-pointer items-center gap-3">
              <input type="checkbox" :value="profile.profile_key" :checked="pendingProfileKeys.includes(profile.profile_key)" @change="toggleProfile(profile.profile_key)" class="size-4 rounded" />
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium truncate">{{ profile.name || profile.profile_key }}</div>
                <div class="text-xs text-muted-foreground">{{ profile.profile_key }}</div>
              </div>
            </label>
            <div v-if="pendingProfileKeys.includes(profile.profile_key) && backends.length > 0" class="mt-2 ml-7 space-y-1.5">
              <div class="flex items-center gap-2">
                <span class="text-xs text-muted-foreground shrink-0 w-14">检索后端</span>
                <select v-model="backendOverrides[profile.profile_key]" @change="onProfileBackendChange(profile.profile_key)" class="h-7 rounded border border-border bg-background px-2 text-xs flex-1">
                  <option value="">跟随默认</option>
                  <option v-for="backend in backends" :key="backend.slug" :value="backend.slug">{{ backend.slug }} ({{ backend.backend_type }})</option>
                </select>
              </div>
              <div v-if="isWeknoraBackend(backendOverrides[profile.profile_key])" class="flex items-center gap-2">
                <span class="text-xs text-muted-foreground shrink-0 w-14">Agent</span>
                <select v-model="agentOverrides[profile.profile_key]" class="h-7 rounded border border-border bg-background px-2 text-xs flex-1">
                  <option value="">跟随默认</option>
                  <option v-for="agent in (agentsByBackend[backendOverrides[profile.profile_key]] || [])" :key="agent.agent_id" :value="agent.agent_id">{{ agent.name }}{{ agent.agent_type ? ' · ' + agent.agent_type : '' }}</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>
      <DialogFooter>
        <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
        <Button @click="save" :disabled="saving">{{ saving ? '保存中...' : '确认' }}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
