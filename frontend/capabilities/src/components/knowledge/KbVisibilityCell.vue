<script setup lang="ts">
import ResourceScopeBadge from '../ResourceScopeBadge.vue'
import { alert, confirm } from '../../composables/useConfirm'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary } from '../../api/types'

const props = defineProps<{
  kb: KnowledgeBaseSummary
  readOnly: boolean
  /** 归属组成员或管理员可切换范围；共享只读用户不显示入口。 */
  canModify: boolean
}>()

const emit = defineEmits<{ changed: [] }>()

async function toggleVisibility() {
  const target = props.kb.visibility === 'shared' ? 'group' : 'shared'
  const ok = await confirm({
    title: '调整数据可见范围',
    description: target === 'shared'
      ? `确定把「${props.kb.name}」共享给所有小组？共享后所有用户都可使用，维护仍只允许归属小组。`
      : `确定把「${props.kb.name}」改回仅本小组？其他小组将立即失去访问权限。`,
    confirmText: target === 'shared' ? '共享' : '改为仅本小组',
    destructive: target !== 'shared',
  })
  if (!ok) return
  try {
    await api.updateKbVisibility(props.kb.slug, {
      visibility: target,
      expected_edit_token: props.kb.edit_token ?? null,
    })
    emit('changed')
  } catch (e: any) {
    await alert({ title: '调整范围失败', description: e.message || '调整范围失败', destructive: true })
  }
}
</script>

<template>
  <div>
    <ResourceScopeBadge :resource="kb" :read-only="readOnly" />
    <button
      v-if="canModify"
      type="button"
      class="mt-1 text-[10px] text-primary hover:underline"
      @click="toggleVisibility"
    >{{ kb.visibility === 'shared' ? '改为仅本小组' : '改为共享' }}</button>
  </div>
</template>
