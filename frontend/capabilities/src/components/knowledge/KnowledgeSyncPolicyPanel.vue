<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary } from '../../api/types'
import { Button } from '../ui/button'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'

const props = defineProps<{
  kb: KnowledgeBaseSummary
  readOnly?: boolean
}>()

const emit = defineEmits<{
  saved: [policy: { sync_on_upload: boolean }]
}>()

const editing = ref(false)
const syncOnUpload = ref(false)
const editToken = ref<string | undefined>()
const saving = ref(false)
const saveError = ref('')

function syncFromKnowledgeBase() {
  editing.value = false
  syncOnUpload.value = props.kb.sync_on_upload
  editToken.value = props.kb.edit_token
}

async function startEditing() {
  saveError.value = ''
  try {
    const latest = (await api.listWikiKbs()).find(kb => kb.slug === props.kb.slug)
    if (!latest) throw new Error('文档知识库不存在')
    syncOnUpload.value = latest.sync_on_upload
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
      sync_on_upload: syncOnUpload.value,
      expected_edit_token: editToken.value,
    })
    editToken.value = saved.edit_token
    emit('saved', { sync_on_upload: saved.sync_on_upload })
    editing.value = false
  } catch (error: unknown) {
    saveError.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    saving.value = false
  }
}

watch(() => [props.kb.slug, props.kb.sync_on_upload] as const, syncFromKnowledgeBase, { immediate: true })
</script>

<template>
  <div class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-2.5">
    <span class="text-xs text-muted-foreground shrink-0">上传同步策略</span>
    <template v-if="!editing">
      <span class="text-sm font-medium">{{ kb.sync_on_upload ? '上传后立即同步' : '仅定时同步' }}</span>
      <span class="text-xs text-muted-foreground">{{ kb.sync_on_upload ? '文件入库后自动开始同步。' : '文件入库后等待文档同步计划处理。' }}</span>
      <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="startEditing" :disabled="readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">修改</Button>
    </template>
    <template v-else>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="syncOnUpload" type="checkbox" />
        上传完成后立即同步
      </label>
      <span class="text-xs text-muted-foreground">关闭时仅创建待同步任务，由定时计划处理。</span>
      <Button variant="ghost" size="sm" class="h-6 ml-auto text-xs" @click="syncFromKnowledgeBase">取消</Button>
      <Button size="sm" class="h-6 text-xs" @click="save" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</Button>
    </template>
    <div v-if="saveError" class="w-full text-xs text-destructive">{{ saveError }}</div>
  </div>
</template>
