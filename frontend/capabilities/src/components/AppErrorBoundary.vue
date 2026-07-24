<script setup lang="ts">
import { onErrorCaptured, ref, watch } from 'vue'
import ErrorState from './ui/feedback/ErrorState.vue'

const props = defineProps<{ resetKey: string }>()
const failure = ref<Error | null>(null)
const contentKey = ref(0)

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
    title="页面渲染失败"
    :description="failure.message || '页面发生意外错误，请重试。'"
    action-label="重试"
    @action="retry"
  />
  <div v-else :key="contentKey">
    <slot />
  </div>
</template>
