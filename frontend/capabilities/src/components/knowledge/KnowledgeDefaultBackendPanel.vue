<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { BackendAgent, BackendInfo, KnowledgeBaseSummary } from '../../api/types'
import { Button } from '../ui/button'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'

const props = defineProps<{
  kb: KnowledgeBaseSummary
  backends: BackendInfo[]
  readOnly?: boolean
}>()

const emit = defineEmits<{
  saved: [defaults: { default_backend_slug: string | null; default_agent_id: string | null }]
}>()

const editing = ref(false)
const backendSlug = ref('')
const agentId = ref('')
const agents = ref<BackendAgent[]>([])
const agentsLoading = ref(false)
const saving = ref(false)
const editToken = ref<string | undefined>()
const saveError = ref('')
const agentsByBackend = ref<Record<string, BackendAgent[]>>({})

function isWeknoraBackend(slug: string | null | undefined) {
  return !!slug && props.backends.some(backend => backend.slug === slug && backend.backend_type === 'weknora')
}

async function loadAgentsForBackend(slug: string): Promise<BackendAgent[]> {
  if (!slug || !isWeknoraBackend(slug)) return []
  if (slug in agentsByBackend.value) return agentsByBackend.value[slug]
  try {
    const list = await api.listBackendAgents(slug)
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: list }
    return list
  } catch {
    agentsByBackend.value = { ...agentsByBackend.value, [slug]: [] }
    return []
  }
}

const agentLabel = computed(() => {
  const id = props.kb.default_agent_id
  if (!id) return ''
  const agent = agents.value.find(candidate => candidate.agent_id === id)
  return agent ? `${agent.name}${agent.agent_type ? ` · ${agent.agent_type}` : ''}` : id
})

async function syncFromKnowledgeBase() {
  editing.value = false
  backendSlug.value = props.kb.default_backend_slug || ''
  agentId.value = props.kb.default_agent_id || ''
  editToken.value = props.kb.edit_token
  agents.value = isWeknoraBackend(backendSlug.value) ? await loadAgentsForBackend(backendSlug.value) : []
}

async function startEditing() {
  saveError.value = ''
  try {
    const latest = (await api.listWikiKbs()).find(kb => kb.slug === props.kb.slug)
    if (!latest) throw new Error('文档知识库不存在')
    backendSlug.value = latest.default_backend_slug || ''
    agentId.value = latest.default_agent_id || ''
    editToken.value = latest.edit_token
    agents.value = isWeknoraBackend(backendSlug.value) ? await loadAgentsForBackend(backendSlug.value) : []
    editing.value = true
  } catch (e: unknown) {
    saveError.value = e instanceof Error ? e.message : '刷新默认检索配置失败'
  }
}

async function onBackendChange() {
  agents.value = []
  agentId.value = ''
  if (!isWeknoraBackend(backendSlug.value)) return
  agentsLoading.value = true
  agents.value = await loadAgentsForBackend(backendSlug.value)
  agentsLoading.value = false
}

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    const default_backend_slug = backendSlug.value || null
    const default_agent_id = isWeknoraBackend(backendSlug.value) ? (agentId.value || null) : null
    const saved = await api.updateKbDefaults(props.kb.slug, {
      default_backend_slug,
      default_agent_id,
      expected_edit_token: editToken.value,
    })
    editToken.value = saved.edit_token
    emit('saved', { default_backend_slug, default_agent_id })
    editing.value = false
  } catch (e: unknown) {
    saveError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

watch(() => [props.kb.slug, props.kb.default_backend_slug, props.kb.default_agent_id] as const, () => {
  void syncFromKnowledgeBase()
}, { immediate: true })
</script>

<template>
  <div class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-2.5">
    <span class="text-xs text-muted-foreground shrink-0">默认检索后端</span>
    <template v-if="!editing">
      <span class="text-sm font-medium">{{ kb.default_backend_slug || '自动（跟随系统）' }}</span>
      <template v-if="kb.default_agent_id">
        <span class="text-xs text-muted-foreground">· 默认 Agent:</span>
        <span class="text-sm font-medium">{{ agentLabel }}</span>
      </template>
      <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="startEditing" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">修改</Button>
    </template>
    <template v-else>
      <select v-model="backendSlug" @change="onBackendChange" class="h-8 rounded-md border border-border bg-background px-2 text-sm flex-1 min-w-[180px]">
        <option value="">自动（跟随系统）</option>
        <option v-for="backend in backends" :key="backend.slug" :value="backend.slug">{{ backend.slug }} ({{ backend.backend_type }})</option>
      </select>
      <select v-if="isWeknoraBackend(backendSlug)" v-model="agentId" :disabled="agentsLoading" class="h-8 rounded-md border border-border bg-background px-2 text-sm flex-1 min-w-[180px]">
        <option value="">无（使用后端默认问答）</option>
        <option v-for="agent in agents" :key="agent.agent_id" :value="agent.agent_id">{{ agent.name }}{{ agent.agent_type ? ' · ' + agent.agent_type : '' }}</option>
      </select>
      <Button variant="ghost" size="sm" class="h-6 text-xs" @click="syncFromKnowledgeBase">取消</Button>
      <Button size="sm" class="h-6 text-xs" @click="save" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</Button>
    </template>
    <div v-if="saveError" class="w-full text-xs text-destructive">{{ saveError }}</div>
  </div>
</template>
