<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Braces, ListTree, Plus, Trash2 } from 'lucide-vue-next'

import { Button } from './ui/button'
import { Input } from './ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select'
import { Textarea } from './ui/textarea'
import {
  SCHEMA_FIELD_TYPES,
  fieldsToSchema,
  isSimpleObjectSchema,
  parseSchemaObjectText as parseSchemaText,
  schemaToFields,
  type SchemaField,
  validateSchemaFieldNames,
} from '../lib/schemaFields'

const props = defineProps<{
  modelValue: Record<string, unknown> | null
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  'validity-change': [valid: boolean, message: string]
}>()

const mode = ref<'fields' | 'advanced'>('fields')
const fields = ref<SchemaField[]>([])
const schemaText = ref('')
const validationMessage = ref('')

const fieldModeAvailable = computed(() => {
  const parsed = parseSchemaText(schemaText.value)
  return parsed.ok && isSimpleObjectSchema(parsed.value)
})

watch(
  () => props.modelValue,
  (value) => {
    const nextSchema = normalizeSchema(value)
    const nextText = prettyJson(nextSchema)
    const currentParsed = parseSchemaText(schemaText.value)

    if (!(
      mode.value === 'advanced'
      && currentParsed.ok
      && serializeSchema(currentParsed.value) === serializeSchema(nextSchema)
    )) {
      schemaText.value = nextText
    }

    if (isSimpleObjectSchema(nextSchema)) {
      fields.value = schemaToFields(nextSchema)
    } else {
      mode.value = 'advanced'
    }
    setValidationMessage('')
  },
  { immediate: true },
)

function defaultSchema(): Record<string, unknown> {
  return fieldsToSchema([])
}

function normalizeSchema(value: Record<string, unknown> | null): Record<string, unknown> {
  return value ? structuredClone(value) : defaultSchema()
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function serializeSchema(value: unknown): string {
  return JSON.stringify(value)
}

function validateFieldRows(): string {
  return validateSchemaFieldNames(fields.value, props.label)
}

function setValidationMessage(message: string) {
  validationMessage.value = message
  emit('validity-change', !message, message)
}

function syncFieldSchema() {
  const message = validateFieldRows()
  setValidationMessage(message)
  if (message) return false

  const normalizedFields = fields.value.map(field => ({
    ...field,
    name: field.name.trim(),
    description: field.description.trim(),
  }))
  emit('update:modelValue', fieldsToSchema(normalizedFields, props.modelValue))
  return true
}

function addField() {
  mode.value = 'fields'
  fields.value.push({ name: '', type: 'string', required: false, description: '' })
  syncFieldSchema()
}

function removeField(index: number) {
  fields.value.splice(index, 1)
  syncFieldSchema()
}

function updateJson(value: string | number) {
  schemaText.value = String(value)
  const parsed = parseSchemaText(schemaText.value)
  if (!parsed.ok) {
    setValidationMessage(parsed.message)
    return
  }
  setValidationMessage('')
  emit('update:modelValue', parsed.value)
}

function switchToFields() {
  const parsed = parseSchemaText(schemaText.value)
  if (!parsed.ok) {
    setValidationMessage(parsed.message)
    return
  }
  if (!isSimpleObjectSchema(parsed.value)) {
    setValidationMessage(`${props.label}包含高级 Schema 结构，请继续使用高级 JSON`)
    return
  }
  setValidationMessage('')
  fields.value = schemaToFields(parsed.value)
  mode.value = 'fields'
  emit('update:modelValue', parsed.value)
}

function switchToAdvanced() {
  mode.value = 'advanced'
  setValidationMessage('')
  schemaText.value = prettyJson(normalizeSchema(props.modelValue))
}

function validate(): boolean {
  if (mode.value === 'fields') return syncFieldSchema()

  const parsed = parseSchemaText(schemaText.value)
  if (!parsed.ok) {
    setValidationMessage(parsed.message)
    return false
  }
  setValidationMessage('')
  emit('update:modelValue', parsed.value)
  return true
}

defineExpose({
  validate,
  isValid: () => !validationMessage.value,
  getValidationMessage: () => validationMessage.value,
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div class="text-xs font-medium text-foreground">{{ label }}</div>
        <p class="mt-1 text-xs text-muted-foreground">
          常见顶层 object 字段可直接编辑；复杂结构切到高级 JSON。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          type="button"
          class="h-8"
          :disabled="mode === 'fields'"
          @click="switchToFields"
        >
          <ListTree class="mr-1.5 h-4 w-4" />
          字段列表
        </Button>
        <Button
          variant="outline"
          size="sm"
          type="button"
          class="h-8"
          :disabled="mode === 'advanced'"
          @click="switchToAdvanced"
        >
          <Braces class="mr-1.5 h-4 w-4" />
          高级 JSON
        </Button>
      </div>
    </div>

    <div
      v-if="validationMessage"
      class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
    >
      {{ validationMessage }}
    </div>

    <div v-if="mode === 'fields'" class="space-y-3">
      <div class="flex items-center justify-end">
        <Button variant="outline" size="sm" type="button" class="h-8" @click="addField">
          <Plus class="mr-1.5 h-4 w-4" />
          添加字段
        </Button>
      </div>
      <div v-if="fields.length" class="divide-y border">
        <div
          v-for="(field, index) in fields"
          :key="index"
          class="grid gap-2 p-3 md:grid-cols-[minmax(120px,1fr)_120px_72px_minmax(160px,1.5fr)_32px] md:items-center"
        >
          <Input v-model="field.name" placeholder="字段名" @update:model-value="syncFieldSchema" />
          <Select v-model="field.type" @update:model-value="syncFieldSchema">
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="type in SCHEMA_FIELD_TYPES" :key="type" :value="type">{{ type }}</SelectItem>
            </SelectContent>
          </Select>
          <label class="flex items-center gap-2 text-xs">
            <input v-model="field.required" type="checkbox" @change="syncFieldSchema" />
            必填
          </label>
          <Input v-model="field.description" placeholder="字段说明" @update:model-value="syncFieldSchema" />
          <Button
            variant="ghost"
            size="sm"
            class="h-8 w-8 p-0"
            type="button"
            title="删除字段"
            @click="removeField(index)"
          >
            <Trash2 class="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div v-else class="rounded-md border px-3 py-5 text-center text-xs text-muted-foreground">
        暂未声明字段。
      </div>
    </div>

    <div v-else class="space-y-2">
      <Textarea
        :model-value="schemaText"
        class="min-h-[220px] font-mono text-xs"
        spellcheck="false"
        @update:model-value="updateJson"
      />
      <div v-if="fieldModeAvailable" class="text-xs text-muted-foreground">
        当前 JSON 已兼容字段列表，可切回简化编辑。
      </div>
    </div>
  </div>
</template>
