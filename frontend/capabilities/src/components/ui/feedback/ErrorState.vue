<script setup lang="ts">
import { AlertTriangle } from '@lucide/vue'
import Button from '../button/Button.vue'

withDefaults(defineProps<{
  title?: string
  description?: string
  actionLabel?: string
  compact?: boolean
}>(), {
  title: '加载失败',
  description: '请稍后重试。',
  actionLabel: '',
  compact: false,
})

defineEmits<{ action: [] }>()
</script>

<template>
  <div
    class="flex flex-col items-center justify-center rounded-lg border border-destructive/30 bg-destructive-soft text-center"
    :class="compact ? 'gap-1 px-4 py-5' : 'min-h-[14rem] gap-2 px-6 py-12'"
    role="alert"
  >
    <AlertTriangle :size="compact ? 18 : 24" class="text-destructive-soft-fg" aria-hidden="true" />
    <p class="text-sm font-medium text-destructive-soft-fg">{{ title }}</p>
    <p v-if="description" class="max-w-xl whitespace-pre-line text-sm text-destructive-soft-fg">{{ description }}</p>
    <Button v-if="actionLabel" class="mt-2" variant="outline" size="sm" @click="$emit('action')">
      {{ actionLabel }}
    </Button>
    <slot />
  </div>
</template>
