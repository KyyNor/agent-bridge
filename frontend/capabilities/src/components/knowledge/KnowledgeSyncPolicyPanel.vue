<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary, KnowledgeSyncPolicy } from '../../api/types'
import { Button } from '../ui/button'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'

const props = defineProps<{
  kb: KnowledgeBaseSummary
  readOnly?: boolean
}>()

const emit = defineEmits<{
  saved: []
}>()

const editing = ref(false)
const policyLabels: Record<KnowledgeSyncPolicy, string> = {
  immediate: '上传后立即同步',
  scheduled: '定时同步',
  paused: '暂不同步',
}

const policies = ref<Record<string, KnowledgeSyncPolicy>>({})
const savedPolicies = ref<Record<string, KnowledgeSyncPolicy>>({})
const changes = computed(() => Object.fromEntries(
  Object.entries(policies.value).filter(([slug, policy]) => policy !== savedPolicies.value[slug]),
))
const editToken = ref<string | undefined>()
const saving = ref(false)
const saveError = ref('')

function syncFromKnowledgeBase() {
  editing.value = false
  policies.value = Object.fromEntries(props.kb.backend_targets.map(target => [target.slug, target.sync_policy]))
  editToken.value = props.kb.edit_token
}

const activeTargets = computed(() => props.kb.backend_targets.filter(target => target.status === 'active'))

async function startEditing() {
  saveError.value = ''
  try {
    const latest = (await api.listWikiKbs()).find(kb => kb.slug === props.kb.slug)
    if (!latest) throw new Error('文档知识库不存在')
    policies.value = Object.fromEntries(latest.backend_targets.map(target => [target.slug, target.sync_policy]))
    savedPolicies.value = { ...policies.value }
    editToken.value = latest.edit_token
    editing.value = true
  } catch (error: unknown) {
    saveError.value = error instanceof Error ? error.message : '刷新同步策略失败'
  }
}

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    const saved = await api.updateKbSyncPolicy(props.kb.slug, {
      backend_sync_policies: changes.value,
      expected_edit_token: editToken.value,
    })
    editToken.value = saved.edit_token
    emit('saved')
    editing.value = false
  } catch (error: unknown) {
    saveError.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    saving.value = false
  }
}

watch(() => [props.kb.slug, props.kb.backend_targets] as const, syncFromKnowledgeBase, { deep: true, immediate: true })
</script>

<template>
  <div class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-2.5">
    <span class="text-xs text-muted-foreground shrink-0">上传同步策略</span>
    <template v-if="!editing">
      <span v-if="activeTargets.length === 0" class="text-sm text-muted-foreground">暂无启用的知识后端</span>
      <template v-for="target in activeTargets" :key="target.slug">
        <span class="text-sm font-medium">{{ target.slug }}</span>
        <span :class="target.sync_policy === 'immediate' ? 'text-xs text-success' : 'text-xs text-muted-foreground'">{{ policyLabels[target.sync_policy] }}</span>
        <span v-if="target.last_error" class="max-w-[280px] truncate text-xs text-destructive" :title="target.last_error">建库失败：{{ target.last_error }}</span>
      </template>
      <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="startEditing" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">修改</Button>
    </template>
    <template v-else>
      <div v-if="activeTargets.length === 0" class="text-sm text-muted-foreground">暂无启用的知识后端</div>
      <label v-for="target in activeTargets" :key="target.slug" class="flex items-center gap-2 text-sm">
        {{ target.slug }}
        <select v-model="policies[target.slug]" class="rounded-md border border-input bg-background px-2 py-1 text-sm" :disabled="saving">
          <option v-for="(label, policy) in policyLabels" :key="policy" :value="policy">{{ label }}</option>
        </select>
      </label>
      <span class="text-xs text-muted-foreground">暂不同步会保留待处理任务，恢复同步后继续处理。</span>
      <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="syncFromKnowledgeBase">取消</Button>
      <Button size="sm" class="h-6 text-xs" @click="save" :disabled="saving || Object.keys(changes).length === 0">{{ saving ? '保存中...' : '保存' }}</Button>
    </template>
    <div v-if="saveError" class="w-full text-xs text-destructive">{{ saveError }}</div>
  </div>
</template>
