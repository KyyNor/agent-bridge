<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { WorkflowEdge } from '../../api/types'
import type { WorkflowReferenceItem } from '../../lib/workflowReferences'
import Input from '../../components/ui/input/Input.vue'
import WorkflowReferencePicker from '../../components/workflow/WorkflowReferencePicker.vue'
import Select from '../../components/ui/select/Select.vue'
import SelectContent from '../../components/ui/select/SelectContent.vue'
import SelectItem from '../../components/ui/select/SelectItem.vue'
import SelectTrigger from '../../components/ui/select/SelectTrigger.vue'
import SelectValue from '../../components/ui/select/SelectValue.vue'

interface InsertableField { focus(): void; insertText(value: string): void }

const props = defineProps<{ edge: WorkflowEdge; locked?: boolean; referenceItems?: WorkflowReferenceItem[] }>()
const emit = defineEmits<{ replace: [edge: WorkflowEdge] }>()
const condition = computed(() => props.edge.condition || { field: '', operator: 'equals' as const, value: '' })
const fieldInput = ref<InsertableField | null>(null)
const activeField = ref<InsertableField | null>(null)
const referenceItems = computed(() => props.referenceItems || [])
watch(() => props.edge.id, () => { activeField.value = null })
function change(patch: Partial<NonNullable<WorkflowEdge['condition']>>) { emit('replace', { ...props.edge, condition: { ...condition.value, ...patch } }) }
function changeOperator(value: string) { change({ operator: value as NonNullable<WorkflowEdge['condition']>['operator'] }) }
function toggleConditional(enabled: boolean) { emit('replace', { ...props.edge, condition: enabled ? condition.value : null }) }
function insertReference(value: string) { if (activeField.value) activeField.value.insertText(value); else void navigator.clipboard?.writeText(value) }
</script>

<template>
  <section class="space-y-3 border-l p-4">
    <div class="text-sm font-semibold">连线条件</div>
    <p v-if="locked" class="text-sm text-muted-foreground">总结型工作流的 Markdown 到 HTML 连线固定为无条件。</p>
    <template v-else>
      <WorkflowReferencePicker v-if="referenceItems.length" :items="referenceItems" mode="condition" @insert="insertReference" />
      <label class="flex items-center gap-2 text-sm"><input :checked="edge.condition !== null" type="checkbox" @change="toggleConditional(($event.target as HTMLInputElement).checked)" />启用条件</label>
      <template v-if="edge.condition">
        <Input ref="fieldInput" :model-value="condition.field" placeholder="nodes.classify.output.category" @focusin="activeField = fieldInput" @update:model-value="change({ field: String($event) })" />
        <Select :model-value="condition.operator" @update:model-value="changeOperator(String($event))"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="equals">等于</SelectItem><SelectItem value="not_equals">不等于</SelectItem><SelectItem value="exists">存在</SelectItem><SelectItem value="not_exists">不存在</SelectItem><SelectItem value="contains">包含</SelectItem></SelectContent></Select>
        <Input v-if="condition.operator !== 'exists' && condition.operator !== 'not_exists'" :model-value="String(condition.value ?? '')" placeholder="期望值" @update:model-value="change({ value: $event })" />
      </template>
    </template>
  </section>
</template>
