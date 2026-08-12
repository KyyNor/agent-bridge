<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ErrorState } from '@/components/ui/feedback'

const router = useRouter()
</script>

<template>
  <ErrorState
    title="页面不存在"
    description="当前地址没有对应的管理页面。"
    action-label="返回平台概览"
    @action="router.replace({ name: 'dashboard' })"
  />
</template>
