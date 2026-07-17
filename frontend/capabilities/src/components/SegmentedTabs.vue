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
  <div class="inline-flex flex-wrap rounded-lg bg-secondary p-[3px] gap-0.5">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      :class="[
        'rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors inline-flex items-center gap-1.5',
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
