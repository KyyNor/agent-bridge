<script setup lang="ts">
import { Inbox } from '@lucide/vue'

withDefaults(defineProps<{
  title?: string
  description?: string
  compact?: boolean
}>(), {
  title: '暂无内容',
  description: '',
  compact: false,
})
</script>

<template>
  <div
    class="flex flex-col items-center justify-center rounded-lg border border-dashed border-border text-center"
    :class="compact ? 'gap-1 px-4 py-6' : 'min-h-[14rem] gap-2 px-6 py-12'"
  >
    <Inbox :size="compact ? 18 : 24" class="text-muted-foreground" aria-hidden="true" />
    <p class="text-sm font-medium text-foreground">{{ title }}</p>
    <p v-if="description" class="max-w-md text-sm text-muted-foreground">{{ description }}</p>
    <slot />
  </div>
</template>
