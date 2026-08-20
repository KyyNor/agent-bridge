<script setup lang="ts">
/**
 * 归属小组标签 —— 各资源列表统一以软底 tag 展示组中文名，title 显示 group_key。
 */
import { onMounted } from 'vue'
import { useAccessGroups } from '../composables/useAccessGroups'

defineProps<{
  groupKey: string | null | undefined
}>()

const { ensureLoaded, groupDisplayName } = useAccessGroups()
onMounted(() => { void ensureLoaded() })
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center h-[21px] px-2 rounded-sm text-[11.5px] font-semibold whitespace-nowrap bg-neutral-soft text-neutral-soft-fg"
    :title="groupKey || undefined"
  >{{ groupKey ? groupDisplayName(groupKey) : '—' }}</span>
</template>
