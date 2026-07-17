<script setup lang="ts">
/**
 * 分段切换控件 —— 统一 Tools / Logs 等页面的筛选分段。
 * 替代各页面手写的 bg-secondary p-0.5 + 选中 bg-card shadow-sm。
 */
export interface SegTab {
  key: string
  label: string
  count?: number
}

const props = defineProps<{
  modelValue: string
  tabs: SegTab[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function select(key: string) {
  if (key !== props.modelValue) emit('update:modelValue', key)
}
</script>

<template>
  <div class="inline-flex h-9 flex-wrap items-center gap-0.5 rounded-md bg-secondary p-1">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      :class="[
        'inline-flex h-7 items-center gap-1.5 rounded-sm px-3 text-[13px] font-medium transition-colors',
        modelValue === tab.key
          ? 'bg-card text-foreground shadow-card'
          : 'text-muted-foreground hover:text-foreground'
      ]"
      @click="select(tab.key)"
    >
      {{ tab.label }}
      <span v-if="tab.count != null" class="font-normal tabular-nums text-muted-foreground/80">{{ tab.count }}</span>
    </button>
  </div>
</template>
