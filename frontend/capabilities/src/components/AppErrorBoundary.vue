<script setup lang="ts">
import { computed, onErrorCaptured, ref, watch } from 'vue'
import { HttpRequestError } from '../api/client'
import { accessDeniedPresentation, authenticationRequiredPresentation } from '../lib/accessFeedback'
import ErrorState from './ui/feedback/ErrorState.vue'

const props = defineProps<{ resetKey: string }>()
const failure = ref<Error | null>(null)
const contentKey = ref(0)

const errorPresentation = computed(() => {
  const status = failure.value instanceof HttpRequestError ? failure.value.status : null
  if (status === 401) {
    return {
      ...authenticationRequiredPresentation,
      actionLabel: '',
    }
  }
  if (status === 403) {
    return {
      ...accessDeniedPresentation,
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
