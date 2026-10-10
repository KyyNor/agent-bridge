<script setup lang="ts" generic="T extends string">
/**
 * 工作台进入页的通用选择卡片组：Agent、范围等枚举选项共用同一套可选卡片视觉。
 * 仅负责渲染与抛值，具体选项由调用方提供；T 保持选项字面量类型，避免窄化丢失。
 */
export interface WorkspaceOption<T extends string = string> {
  value: T
  label: string
  /** 卡片副标题（如 Agent 标识、Profile key）。 */
  sublabel?: string
  description?: string
}

defineProps<{
  modelValue: T
  options: WorkspaceOption<T>[]
  /** 每个选项独占一行（列表型，如能力平面）。 */
  stacked?: boolean
}>()

const emit = defineEmits<{ (event: 'update:modelValue', value: T): void }>()
</script>

<template>
  <div class="grid gap-2" :class="stacked ? 'sm:grid-cols-1' : 'sm:grid-cols-2'">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :class="[
        'rounded-lg border px-4 py-3 text-left transition-colors',
        modelValue === option.value ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted',
      ]"
      @click="emit('update:modelValue', option.value)"
    >
      <div class="text-sm font-medium">{{ option.label }}</div>
      <div v-if="option.sublabel" class="mt-0.5 font-mono text-xs text-muted-foreground">
        {{ option.sublabel }}
      </div>
      <div v-if="option.description" class="mt-1 text-xs text-muted-foreground">
        {{ option.description }}
      </div>
    </button>
  </div>
</template>