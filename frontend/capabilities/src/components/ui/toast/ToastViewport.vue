<script setup lang="ts">
import { CheckCircle2, CircleAlert, Info, X, XCircle } from '@lucide/vue'
import { useToast, type ToastVariant } from '@/composables/useToast'

const { items, dismiss } = useToast()

const variantClass: Record<ToastVariant, string> = {
  default: 'border-border bg-card text-foreground',
  success: 'border-success/30 bg-success-soft text-success-soft-fg',
  error: 'border-destructive/30 bg-destructive-soft text-destructive-soft-fg',
  warning: 'border-warning/30 bg-warning-soft text-warning-soft-fg',
}

const icons = { default: Info, success: CheckCircle2, error: XCircle, warning: CircleAlert }
</script>

<template>
  <Teleport to="body">
    <ol class="fixed right-5 top-5 z-[100] flex w-[min(24rem,calc(100vw-2.5rem))] flex-col gap-2" aria-live="polite">
      <li
        v-for="item in items"
        :key="item.id"
        class="flex gap-3 rounded-lg border p-3 shadow-pop"
        :class="variantClass[item.variant]"
      >
        <component :is="icons[item.variant]" :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p class="text-sm font-medium">{{ item.title }}</p>
          <p v-if="item.description" class="mt-0.5 text-sm opacity-90">{{ item.description }}</p>
        </div>
        <button class="rounded p-0.5 opacity-70 hover:bg-overlay hover:opacity-100" type="button" aria-label="关闭通知" @click="dismiss(item.id)">
          <X :size="16" />
        </button>
      </li>
    </ol>
  </Teleport>
</template>
