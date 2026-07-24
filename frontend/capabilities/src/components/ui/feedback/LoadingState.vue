<script setup lang="ts">
withDefaults(defineProps<{
  label?: string
  compact?: boolean
}>(), {
  label: '加载中…',
  compact: false,
})
</script>

<template>
  <div
    class="flex items-center justify-center gap-2 text-sm text-muted-foreground"
    :class="compact ? 'py-4' : 'min-h-[14rem] px-6 py-12'"
    role="status"
  >
    <span class="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" aria-hidden="true" />
    <span>{{ label }}</span>
  </div>
</template>
