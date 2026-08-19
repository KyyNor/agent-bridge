<script setup lang="ts">
import type { ResourceVisibility } from '../api/types'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

withDefaults(
  defineProps<{
    modelValue: ResourceVisibility
    /** 范围说明文案；不同资源的共享动作不同（使用/调用）。 */
    hint?: string
  }>(),
  { hint: '共享后所有用户都可使用，维护仍只允许归属小组。' },
)

const emit = defineEmits<{ 'update:modelValue': [value: ResourceVisibility] }>()
</script>

<template>
  <div class="space-y-2">
    <label class="text-sm font-medium">数据可见范围</label>
    <Select :model-value="modelValue" @update:model-value="emit('update:modelValue', $event as ResourceVisibility)">
      <SelectTrigger><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="group">仅本小组</SelectItem>
        <SelectItem value="shared">共享给所有小组</SelectItem>
      </SelectContent>
    </Select>
    <p class="text-xs text-muted-foreground">{{ hint }}</p>
  </div>
</template>
