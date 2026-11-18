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
  name: string
  /** 每个选项独占一行（列表型，如能力平面）。 */
  stacked?: boolean
}>()

const emit = defineEmits<{ (event: 'update:modelValue', value: T): void }>()
</script>

<template>
  <div class="grid gap-2" :class="{ 'sm:grid-cols-2': !stacked }">
    <label v-for="option in options" :key="option.value" class="relative flex min-w-0">
      <input
        type="radio"
        class="peer sr-only"
        :name="name"
        :value="option.value"
        :checked="modelValue === option.value"
        @change="emit('update:modelValue', option.value)"
      />
      <span class="flex w-full cursor-pointer items-start gap-2.5 rounded-lg border border-border px-3 py-2.5 transition-colors hover:bg-muted peer-checked:border-primary peer-checked:bg-primary/5 peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-60">
        <span
          aria-hidden="true"
          class="mt-[3px] size-3.5 shrink-0 rounded-full"
          :class="modelValue === option.value ? 'border-[4px] border-primary' : 'border border-muted-foreground'"
        />
        <span class="min-w-0">
          <span class="block break-words text-sm font-medium">{{ option.label }}</span>
          <span v-if="option.sublabel" class="mt-0.5 block break-all font-mono text-xs text-muted-foreground">
            {{ option.sublabel }}
          </span>
          <span v-if="option.description" class="mt-1 block text-xs text-muted-foreground">
            {{ option.description }}
          </span>
        </span>
      </span>
    </label>
  </div>
</template>
