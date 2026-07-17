<script setup lang="ts">
/**
 * 低饱和分类徽标 —— 统一「工具层级 / 调用来源 / 任务类型」三套分类。
 *
 * 用 kind 区分映射，避免各页面重复定义。
 * 颜色成对来自 base.css 类别色令牌（cat-* / neutral-soft / 软状态色），禁止裸用调色盘。
 */
import { computed } from 'vue'

type Kind = 'toolType' | 'source' | 'taskType'

const props = defineProps<{
  kind: Kind
  value: string
}>()

// 唯一映射：value → { 软底/软字 + 默认文案 }
const TOOL_TYPE: Record<string, { bg: string; fg: string; text: string }> = {
  overview:     { bg: 'bg-cat-blue',   fg: 'text-cat-blue-fg',   text: '概览' },
  search:       { bg: 'bg-cat-violet', fg: 'text-cat-violet-fg', text: '检索' },
  detail:       { bg: 'bg-cat-teal',   fg: 'text-cat-teal-fg',   text: '明细' },
  action:       { bg: 'bg-cat-amber',  fg: 'text-cat-amber-fg',  text: '操作' },
  unconfigured: { bg: 'bg-neutral-soft', fg: 'text-neutral-soft-fg', text: '未配置' },
}

const SOURCE: Record<string, { bg: string; fg: string; text: string }> = {
  hook:           { bg: 'bg-cat-violet', fg: 'text-cat-violet-fg', text: 'Hook' },
  mcp_service:    { bg: 'bg-cat-blue',   fg: 'text-cat-blue-fg',   text: 'MCP' },
  openapi_service:{ bg: 'bg-cat-teal',   fg: 'text-cat-teal-fg',   text: 'OpenAPI' },
  builtin:        { bg: 'bg-neutral-soft', fg: 'text-neutral-soft-fg', text: 'Builtin' },
}

// 任务导入操作类型：新增/更新/跳过/重开/错误（含细分值）
const TASK_TYPE: Record<string, { bg: string; fg: string; text: string }> = {
  created:           { bg: 'bg-cat-blue',   fg: 'text-cat-blue-fg',   text: '新增' },
  updated:           { bg: 'bg-cat-violet', fg: 'text-cat-violet-fg', text: '更新' },
  skipped:           { bg: 'bg-cat-amber',  fg: 'text-cat-amber-fg',  text: '跳过' },
  skipped_running:   { bg: 'bg-cat-amber',  fg: 'text-cat-amber-fg',  text: '跳过' },
  skipped_completed: { bg: 'bg-cat-amber',  fg: 'text-cat-amber-fg',  text: '跳过' },
  reopened:          { bg: 'bg-cat-teal',   fg: 'text-cat-teal-fg',   text: '重开' },
  reopened_expired:  { bg: 'bg-cat-teal',   fg: 'text-cat-teal-fg',   text: '重开' },
  error:             { bg: 'bg-destructive-soft', fg: 'text-destructive-soft-fg', text: '错误' },
}

const MAPS = { toolType: TOOL_TYPE, source: SOURCE, taskType: TASK_TYPE } as const

const m = computed(() => {
  const map = MAPS[props.kind]
  return map[props.value] ?? { bg: 'bg-neutral-soft', fg: 'text-neutral-soft-fg', text: props.value || '未标记' }
})
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center h-[21px] px-2 rounded-md text-[11.5px] font-semibold whitespace-nowrap"
    :class="[m.bg, m.fg]"
  >{{ m.text }}</span>
</template>
