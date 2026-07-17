<script setup lang="ts">
/**
 * 统计卡（Dashboard 用）。
 * 信息型卡片的图标底一律中性 bg-accent text-primary；
 * 颜色只留给真正的状态（状态徽标/圆点/健康条/delta）。
 */
defineProps<{
  label: string
  value: string | number
  /** 辅助说明，可含 delta（delta 自行用 <span class="text-success"> 等上色） */
  sub?: string
}>()
</script>

<template>
  <div class="rounded-xl border border-border bg-card p-5 shadow-card">
    <div class="flex items-center justify-between">
      <div class="text-xs text-muted-foreground">{{ label }}</div>
      <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-primary">
        <slot name="icon" />
      </div>
    </div>
    <div class="mt-2.5 text-2xl font-bold tabular-nums">{{ value }}</div>
    <div v-if="sub || $slots.sub" class="mt-1 text-xs text-muted-foreground">
      <slot name="sub">{{ sub }}</slot>
    </div>
  </div>
</template>
