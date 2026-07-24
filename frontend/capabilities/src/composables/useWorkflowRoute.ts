import { computed, onUnmounted, type MaybeRefOrGetter, toValue } from 'vue'
import { confirm } from './useConfirm'
import { parseSubRoute, registerNavigationGuard } from '@/lib/navigation'

export type WorkflowRouteMode = 'list' | 'new' | 'edit' | 'detail' | 'tasks' | 'progress'

export function useWorkflowRoute(routeKey: MaybeRefOrGetter<string>, hasUnsavedChanges: MaybeRefOrGetter<boolean>) {
  const routeParts = computed(() => parseSubRoute(toValue(routeKey)).segments)
  const workflowKey = computed(() => routeParts.value[0] || '')
  const mode = computed<WorkflowRouteMode>(() => {
    if (!toValue(routeKey)) return 'list'
    if (routeParts.value[0] === 'new') return 'new'
    const action = routeParts.value[1]
    if (action === 'edit' || action === 'tasks' || action === 'progress') return action
    return 'detail'
  })
  const isFormPage = computed(() => mode.value === 'new' || mode.value === 'edit')

  const removeNavigationGuard = registerNavigationGuard(() => {
    if (!isFormPage.value || !toValue(hasUnsavedChanges)) return true
    return confirm({
      title: mode.value === 'new' ? '放弃新建' : '放弃修改',
      description: mode.value === 'new'
        ? '当前表单有未保存内容，确认离开？'
        : '当前工作流有未保存的改动，确认离开？',
      destructive: true,
      confirmText: '离开',
    })
  })
  onUnmounted(removeNavigationGuard)

  return { routeParts, workflowKey, mode, isFormPage }
}
