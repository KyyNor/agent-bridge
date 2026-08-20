<script setup lang="ts">
import { alert, confirm } from '../../composables/useConfirm'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary } from '../../api/types'
import { Button } from '../ui/button'

const props = defineProps<{
  kb: KnowledgeBaseSummary
}>()

const emit = defineEmits<{ changed: [] }>()

/** 操作列的范围切换入口；归属组成员或管理员才由列表渲染本组件。 */
async function toggleVisibility() {
  const target = props.kb.visibility === 'shared' ? 'group' : 'shared'
  const ok = await confirm({
    title: '调整数据可见范围',
    description: target === 'shared'
      ? `确定把「${props.kb.name}」共享给所有小组？共享后所有用户都可使用，维护仍只允许归属小组。`
      : `确定把「${props.kb.name}」改为仅小组可见？其他小组将立即失去访问权限。`,
    confirmText: target === 'shared' ? '共享' : '改为仅小组可见',
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
  <Button variant="outline" size="sm" class="h-8 text-xs" @click="toggleVisibility">
    {{ kb.visibility === 'shared' ? '改为仅小组可见' : '改为共享' }}
  </Button>
</template>
