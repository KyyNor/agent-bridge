<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import Input from '../ui/input/Input.vue'
import Textarea from '../ui/textarea/Textarea.vue'
import Select from '../ui/select/Select.vue'
import SelectContent from '../ui/select/SelectContent.vue'
import SelectItem from '../ui/select/SelectItem.vue'
import SelectTrigger from '../ui/select/SelectTrigger.vue'
import SelectValue from '../ui/select/SelectValue.vue'
import {
  formatWorkflowValue,
  normalizeWorkflowValueType,
  parseWorkflowValue,
  type WorkflowValueType,
} from '../../lib/workflowValues'

interface InsertableField { focus(): void; insertText(value: string): void }

const props = defineProps<{
  modelValue: unknown
  valueType: string
  placeholder?: string
  invalid?: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: unknown]
  'validity-change': [valid: boolean, message: string]
}>()

const field = ref<InsertableField | null>(null)
const text = ref('')
const message = ref('')
const normalizedType = computed<WorkflowValueType>(() => normalizeWorkflowValueType(props.valueType))
const isReference = computed(() => /^\{\{/.test(text.value.trim()))
const isComplex = computed(() => normalizedType.value === 'object' || normalizedType.value === 'array')

watch(
  () => [props.modelValue, normalizedType.value] as const,
  ([value, type]) => {
    text.value = formatWorkflowValue(value, type)
    setMessage('')
  },
  { immediate: true, deep: true },
)

function setMessage(value: string) {
  message.value = value
  emit('validity-change', !value, value)
}

function update(value: string | number) {
  text.value = String(value)
  const parsed = parseWorkflowValue(text.value, normalizedType.value)
  if (!parsed.ok) {
    setMessage(parsed.message)
    return
  }
  setMessage('')
  emit('update:modelValue', parsed.value)
}

function focus() {
  field.value?.focus()
}

function insertText(value: string) {
  if (normalizedType.value === 'string') {
    field.value?.insertText(value)
    return
  }
  update(value)
  void nextTick(focus)
}

defineExpose({ focus, insertText })
</script>

<template>
  <div class="space-y-1">
    <Select v-if="normalizedType === 'boolean' && !isReference" :model-value="text" @update:model-value="update(String($event))">
      <SelectTrigger :aria-invalid="invalid || Boolean(message)"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="true">true</SelectItem>
        <SelectItem value="false">false</SelectItem>
      </SelectContent>
    </Select>
    <Textarea
      v-else-if="isComplex && !isReference"
      ref="field"
      :model-value="text"
      class="min-h-20 font-mono text-xs"
      :placeholder="placeholder"
      :aria-invalid="invalid || Boolean(message)"
      spellcheck="false"
      @update:model-value="update"
    />
    <Input
      v-else
      ref="field"
      :model-value="text"
      :type="(normalizedType === 'number' || normalizedType === 'integer') && !isReference ? 'number' : 'text'"
      :step="normalizedType === 'integer' ? '1' : 'any'"
      :placeholder="placeholder"
      :aria-invalid="invalid || Boolean(message)"
      @update:model-value="update"
    />
    <p v-if="message" class="text-xs text-destructive">{{ message }}</p>
  </div>
</template>
