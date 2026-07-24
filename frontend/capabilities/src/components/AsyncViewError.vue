<script setup lang="ts">
import ErrorState from './ui/feedback/ErrorState.vue'

defineProps<{ error: Error }>()
</script>

<template>
  <ErrorState title="页面加载失败" :description="error.message || '资源加载超时，请检查网络后重试。'" />
</template>
