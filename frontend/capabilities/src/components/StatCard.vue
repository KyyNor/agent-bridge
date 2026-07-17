<script setup lang="ts">
/**
 * 统计卡。默认 neutral（信息型 KPI），图标底 bg-accent text-primary。
 * tone 为状态型指标着色：卡身始终白底，仅图标底 + 数值随 tone 变色。
 *   neutral（默认）→ 中性；ok → success；err → destructive；info → info
 * 反对整卡 bg+border+text 三件套染色（视觉过载、淹没语义）。
 */
import { computed } from 'vue'

type Tone = 'neutral' | 'ok' | 'err' | 'info'

const props = withDefaults(defineProps<{
  label: string
  value: string | number
  /** 辅助说明，可含 delta（delta 自行用 <span class="text-success"> 等上色） */
  sub?: string
  /** 状态型指标着色：ok/err/info；默认 neutral */
  tone?: Tone
}>(), { tone: 'neutral' })

// tone → 图标底色 + 数值色（卡身始终白底）
const TONE_CLASS: Record<Tone, { icon: string; value: string }> = {
  neutral: { icon: 'bg-accent text-primary',                       value: '' },
  ok:      { icon: 'bg-success-soft text-success-soft-fg',         value: 'text-success' },
  err:     { icon: 'bg-destructive-soft text-destructive-soft-fg', value: 'text-destructive' },
  info:    { icon: 'bg-info-soft text-info-soft-fg',               value: 'text-info' },
}

const toneClass = computed(() => TONE_CLASS[props.tone])
</script>

<template>
  <div class="rounded-lg border border-border bg-card p-5 shadow-card">
    <div class="flex items-center justify-between">
      <div class="text-xs text-muted-foreground">{{ label }}</div>
      <div class="flex h-9 w-9 items-center justify-center rounded-md" :class="toneClass.icon">
        <slot name="icon" />
      </div>
    </div>
    <div class="mt-2.5 text-2xl font-bold tabular-nums" :class="toneClass.value">{{ value }}</div>
    <div v-if="sub || $slots.sub" class="mt-1 text-xs text-muted-foreground">
      <slot name="sub">{{ sub }}</slot>
    </div>
  </div>
</template>
