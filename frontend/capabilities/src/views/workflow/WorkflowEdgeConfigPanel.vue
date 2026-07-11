<script setup lang="ts">
import { computed } from 'vue'
import type { WorkflowEdge } from '../../api/types'
import Input from '../../components/ui/input/Input.vue'
import Select from '../../components/ui/select/Select.vue'
import SelectContent from '../../components/ui/select/SelectContent.vue'
import SelectItem from '../../components/ui/select/SelectItem.vue'
import SelectTrigger from '../../components/ui/select/SelectTrigger.vue'
import SelectValue from '../../components/ui/select/SelectValue.vue'

const props = defineProps<{ edge: WorkflowEdge }>()
const emit = defineEmits<{ replace: [edge: WorkflowEdge] }>()
const condition = computed(() => props.edge.condition || { field: '', operator: 'equals' as const, value: '' })
function change(patch: Partial<NonNullable<WorkflowEdge['condition']>>) { emit('replace', { ...props.edge, condition: { ...condition.value, ...patch } }) }
function changeOperator(value: string) { change({ operator: value as NonNullable<WorkflowEdge['condition']>['operator'] }) }
</script>

<template>
  <section class="space-y-3 border-l p-4">
    <div class="text-sm font-semibold">连线条件</div>
    <Input :model-value="condition.field" placeholder="nodes.classify.output.category" @update:model-value="change({ field: String($event) })" />
    <Select :model-value="condition.operator" @update:model-value="changeOperator(String($event))"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="equals">等于</SelectItem><SelectItem value="not_equals">不等于</SelectItem><SelectItem value="exists">存在</SelectItem><SelectItem value="not_exists">不存在</SelectItem><SelectItem value="contains">包含</SelectItem></SelectContent></Select>
    <Input v-if="condition.operator !== 'exists' && condition.operator !== 'not_exists'" :model-value="String(condition.value ?? '')" placeholder="期望值" @update:model-value="change({ value: $event })" />
  </section>
</template>
