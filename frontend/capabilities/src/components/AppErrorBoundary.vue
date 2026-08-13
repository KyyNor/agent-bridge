<script setup lang="ts">
import { computed, onErrorCaptured, ref, watch } from 'vue'
import { HttpRequestError } from '../api/client'
import ErrorState from './ui/feedback/ErrorState.vue'

const props = defineProps<{ resetKey: string }>()
const failure = ref<Error | null>(null)
const contentKey = ref(0)

const errorPresentation = computed(() => {
  const status = failure.value instanceof HttpRequestError ? failure.value.status : null
  if (status === 401) {
    return {
      title: '需要先完成登录',
      description: '当前浏览器没有有效的 Agent Bridge 登录信息，因此暂时无法加载此页面。请从统一登录入口重新进入 Agent Bridge；如果仍无法访问，请联系管理员确认账号已开通。',
      actionLabel: '',
    }
  }
  if (status === 403) {
    return {
      title: '暂无页面访问权限',
      description: '已确认你的登录身份，但账号还没有访问此页面所需的小组权限。请联系管理员开通权限后重试。',
      actionLabel: '重试',
    }
  }
  return {
    title: '页面渲染失败',
    description: failure.value?.message || '页面发生意外错误，请重试。',
    actionLabel: '重试',
  }
})

onErrorCaptured((error) => {
  failure.value = error instanceof Error ? error : new Error(String(error))
  return false
})

watch(() => props.resetKey, () => {
  failure.value = null
})

function retry() {
  failure.value = null
  contentKey.value += 1
}
</script>

<template>
  <ErrorState
    v-if="failure"
    :title="errorPresentation.title"
    :description="errorPresentation.description"
    :action-label="errorPresentation.actionLabel"
    @action="retry"
  />
  <div v-else :key="contentKey">
    <slot />
  </div>
</template>
