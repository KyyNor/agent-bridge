<script setup lang="ts">
/**
 * 语义状态徽标 —— 统一「成功/失败/拦截/运行中/启用/停用」。
 *
 * 状态 → 软状态色对的映射只存在于此处（base.css 提供令牌）。
 * 颜色只走令牌派生，禁止裸用 Tailwind 调色盘。
 */
import { computed } from 'vue'

type Status = 'success' | 'error' | 'blocked' | 'running' | 'enabled' | 'disabled'

const props = defineProps<{
  status: Status
  /** 覆盖默认文案；不传则用映射内置文案 */
  label?: string
}>()

// 唯一映射：status → { 软底/软字 + 圆点实心色 + 默认文案 }
const MAP: Record<Status, { bg: string; fg: string; dot: string; text: string }> = {
  success:  { bg: 'bg-success-soft',     fg: 'text-success-soft-fg',     dot: 'bg-success',     text: '成功' },
  enabled:  { bg: 'bg-success-soft',     fg: 'text-success-soft-fg',     dot: 'bg-success',     text: '已启用' },
  error:    { bg: 'bg-destructive-soft', fg: 'text-destructive-soft-fg', dot: 'bg-destructive', text: '失败' },
  blocked:  { bg: 'bg-warning-soft',     fg: 'text-warning-soft-fg',     dot: 'bg-warning',     text: '拦截' },
  running:  { bg: 'bg-info-soft',        fg: 'text-info-soft-fg',        dot: 'bg-info',        text: '运行中' },
  disabled: { bg: 'bg-neutral-soft',     fg: 'text-neutral-soft-fg',     dot: 'bg-muted-foreground/40', text: '停用' },
}

const m = computed(() => MAP[props.status] ?? MAP.disabled)
const text = computed(() => props.label ?? m.value.text)
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center gap-1 h-[21px] px-2 rounded-sm text-[11.5px] font-semibold whitespace-nowrap"
    :class="[m.bg, m.fg]"
  >
    <span class="h-[6px] w-[6px] rounded-full" :class="m.dot" />
    {{ text }}
  </span>
</template>
