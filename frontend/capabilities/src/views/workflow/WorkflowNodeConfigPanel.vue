<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-vue-next'
import type { ManagedScript, SkillPrompt, WorkflowNode, WorkflowValidationError } from '../../api/types'
import type { WorkflowReferenceItem } from '../../lib/workflowReferences'
import Button from '../../components/ui/button/Button.vue'
import Input from '../../components/ui/input/Input.vue'
import Textarea from '../../components/ui/textarea/Textarea.vue'
import SchemaFieldEditor from '../../components/SchemaFieldEditor.vue'
import WorkflowReferencePicker from '../../components/workflow/WorkflowReferencePicker.vue'
import WorkflowTypedValueInput from '../../components/workflow/WorkflowTypedValueInput.vue'
import { defaultWorkflowValue, workflowValueType } from '../../lib/workflowValues'
import Select from '../../components/ui/select/Select.vue'
import SelectContent from '../../components/ui/select/SelectContent.vue'
import SelectItem from '../../components/ui/select/SelectItem.vue'
import SelectTrigger from '../../components/ui/select/SelectTrigger.vue'
import SelectValue from '../../components/ui/select/SelectValue.vue'

interface InsertableField { focus(): void; insertText(value: string): void }

const props = defineProps<{ node: WorkflowNode; scripts: ManagedScript[]; skills: SkillPrompt[]; backends: string[]; referenceItems?: WorkflowReferenceItem[]; issues?: WorkflowValidationError[] }>()
const emit = defineEmits<{
  replace: [node: WorkflowNode]
  'schema-validity': [nodeId: string, valid: boolean, message: string]
}>()
const activeField = ref<InsertableField | null>(null)
const promptInput = ref<InsertableField | null>(null)
const titleInput = ref<InsertableField | null>(null)
const pathInput = ref<InsertableField | null>(null)
const paramInputs = new Map<string, InsertableField>()
watch(() => props.node.id, () => { activeField.value = null })
const referenceItems = computed(() => props.referenceItems || [])
const issues = computed(() => props.issues || [])
const activeScripts = computed(() => props.scripts.filter(script => script.status === 'active'))
const selectedScript = computed(() => {
  const node = props.node
  return node.type === 'script'
    ? activeScripts.value.find(script => script.script_key === node.config.script_key)
    : undefined
})
const scriptProperties = computed(() => (selectedScript.value?.input_schema?.properties || {}) as Record<string, Record<string, unknown>>)
const requiredParams = computed(() => new Set(Array.isArray(selectedScript.value?.input_schema?.required) ? selectedScript.value?.input_schema.required.filter((key): key is string => typeof key === 'string') : []))

function replace(patch: Record<string, unknown>) { emit('replace', { ...props.node, ...patch } as WorkflowNode) }
function config(patch: Record<string, unknown>) { replace({ config: { ...props.node.config, ...patch } }) }
function selectScript(scriptKey: string) {
  const properties = (props.scripts.find(script => script.script_key === scriptKey)?.input_schema?.properties || {}) as Record<string, unknown>
  const params = Object.fromEntries(
    Object.entries(properties).map(([key, schema]) => [key, defaultWorkflowValue(workflowValueType(schema))]),
  )
  config({ script_key: scriptKey, params })
}
function setParam(key: string, value: unknown) { if (props.node.type === 'script') config({ params: { ...props.node.config.params, [key]: value } }) }
function currentSkills() { return props.node.type === 'agent' || props.node.type === 'output' ? props.node.config.skill_names : [] }
function moveSkill(index: number, direction: number) { const names = [...currentSkills()]; const other = index + direction; if (other < 0 || other >= names.length) return; [names[index], names[other]] = [names[other], names[index]]; config({ skill_names: names }) }
function addSkill(skillName: string) { if (!skillName || currentSkills().includes(skillName)) return; config({ skill_names: [...currentSkills(), skillName] }) }
function removeSkill(index: number) { config({ skill_names: currentSkills().filter((_, current) => current !== index) }) }
function isInsertableField(value: unknown): value is InsertableField { return Boolean(value) && typeof (value as InsertableField).insertText === 'function' }
function setParamInputRef(key: string, value: unknown) { if (isInsertableField(value)) paramInputs.set(key, value); else paramInputs.delete(key) }
function activateParamInput(key: string) { activeField.value = paramInputs.get(key) || null }
function insertReference(value: string, rawPath: string) { if (activeField.value) activeField.value.insertText(value); else void navigator.clipboard?.writeText(rawPath) }
function issueFor(...fields: string[]) {
  const fieldSet = new Set(fields.flatMap(field => [field, `config.${field}`]))
  return issues.value.find(issue => issue.field && fieldSet.has(issue.field)) || null
}
function issueId(field: string) {
  return `workflow-node-${props.node.id}-${field.replace(/[^a-zA-Z0-9_-]/g, '-')}-error`
}
function updateSchemaValidity(valid: boolean, message: string) {
  emit('schema-validity', props.node.id, valid, message)
}
function setResultMode(value: string) {
  config({ result_mode: value })
  if (value !== 'json') updateSchemaValidity(true, '')
}
function getTaskEmptyMode() {
  return props.node.type === 'get_task' && props.node.config.on_empty === 'continue' ? 'continue' : 'terminate'
}
</script>

<template>
  <section class="space-y-3 p-4">
    <div class="text-sm font-semibold">节点配置</div>
    <WorkflowReferencePicker v-if="referenceItems.length" :items="referenceItems" mode="template" @insert="insertReference" />
    <div>
      <label class="mb-1 block text-xs text-muted-foreground">名称</label>
      <Input :model-value="node.name" :aria-invalid="Boolean(issueFor('name'))" :aria-describedby="issueFor('name') ? issueId('name') : undefined" @update:model-value="replace({ name: String($event) })" />
      <p v-if="issueFor('name')" :id="issueId('name')" class="mt-1 text-xs text-destructive">{{ issueFor('name')?.message }}</p>
    </div>

    <template v-if="node.type === 'get_task'">
      <p class="text-sm text-muted-foreground">从当前工作流队列领取一个任务。</p>
      <div>
        <label class="mb-1 block text-xs text-muted-foreground">没有任务时</label>
        <Select :model-value="getTaskEmptyMode()" @update:model-value="config({ on_empty: String($event) })">
          <SelectTrigger :aria-invalid="Boolean(issueFor('on_empty'))"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="terminate">结束本次运行</SelectItem>
            <SelectItem value="continue">继续条件分支</SelectItem>
          </SelectContent>
        </Select>
        <p class="mt-1 text-xs text-muted-foreground">选择“继续条件分支”后，可用 task 为 null 的条件边连接到灌入任务脚本或重试获取任务节点。</p>
      </div>
    </template>
    <template v-else-if="node.type === 'script'">
      <div><label class="mb-1 block text-xs text-muted-foreground">托管脚本</label><Select :model-value="node.config.script_key" @update:model-value="selectScript(String($event))"><SelectTrigger :aria-invalid="Boolean(issueFor('script_key'))"><SelectValue placeholder="选择启用脚本" /></SelectTrigger><SelectContent><SelectItem v-for="script in activeScripts" :key="script.script_key" :value="script.script_key">{{ script.name }}</SelectItem></SelectContent></Select><p v-if="issueFor('script_key')" class="mt-1 text-xs text-destructive">{{ issueFor('script_key')?.message }}</p></div>
      <div v-for="(schema, key) in scriptProperties" :key="key"><label class="mb-1 block text-xs text-muted-foreground">{{ key }}<span v-if="requiredParams.has(key)" class="text-destructive"> *</span></label><WorkflowTypedValueInput :ref="(el) => setParamInputRef(String(key), el)" :model-value="node.config.params[key]" :value-type="workflowValueType(schema)" :placeholder="typeof schema.description === 'string' ? schema.description : '{{ input.value }}'" :invalid="Boolean(issueFor(`params.${String(key)}`, `config.params.${String(key)}`))" @focusin="activateParamInput(String(key))" @update:model-value="setParam(String(key), $event)" /><p v-if="issueFor(`params.${String(key)}`, `config.params.${String(key)}`)" :id="issueId(`params-${String(key)}`)" class="mt-1 text-xs text-destructive">{{ issueFor(`params.${String(key)}`, `config.params.${String(key)}`)?.message }}</p></div>
      <div><label class="mb-1 block text-xs text-muted-foreground">超时（秒）</label><Input :model-value="node.config.timeout_seconds" type="number" :aria-invalid="Boolean(issueFor('timeout_seconds'))" @update:model-value="config({ timeout_seconds: Number($event) })" /><p v-if="issueFor('timeout_seconds')" class="mt-1 text-xs text-destructive">{{ issueFor('timeout_seconds')?.message }}</p></div>
    </template>
    <template v-else-if="node.type === 'agent' || node.type === 'output'">
      <div><label class="mb-1 block text-xs text-muted-foreground">提示词</label><Textarea ref="promptInput" :model-value="node.config.prompt" class="min-h-28" :aria-invalid="Boolean(issueFor('prompt'))" @focusin="activeField = promptInput" @update:model-value="config({ prompt: String($event) })" /><p v-if="issueFor('prompt')" class="mt-1 text-xs text-destructive">{{ issueFor('prompt')?.message }}</p></div>
      <div><label class="mb-1 block text-xs text-muted-foreground">后端</label><Select :model-value="node.config.backend_key" @update:model-value="config({ backend_key: String($event) })"><SelectTrigger :aria-invalid="Boolean(issueFor('backend_key'))"><SelectValue /></SelectTrigger><SelectContent><SelectItem v-for="backend in backends" :key="backend" :value="backend">{{ backend }}</SelectItem></SelectContent></Select><p v-if="issueFor('backend_key')" class="mt-1 text-xs text-destructive">{{ issueFor('backend_key')?.message }}</p></div>
      <label class="flex items-center gap-2 text-sm"><input :checked="node.config.mcp_enabled" type="checkbox" @change="config({ mcp_enabled: ($event.target as HTMLInputElement).checked })" /> Profile MCP</label>
      <template v-if="node.type === 'agent'"><div><label class="mb-1 block text-xs text-muted-foreground">输出模式</label><Select :model-value="node.config.result_mode" @update:model-value="setResultMode(String($event))"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="text">文本</SelectItem><SelectItem value="json">JSON</SelectItem></SelectContent></Select></div><div v-if="node.config.result_mode === 'json'"><SchemaFieldEditor :model-value="node.config.output_schema" label="JSON Schema" @update:model-value="config({ output_schema: $event })" @validity-change="updateSchemaValidity" /><p v-if="issueFor('output_schema')" class="mt-1 text-xs text-destructive">{{ issueFor('output_schema')?.message }}</p></div></template>
      <template v-else><div><label class="mb-1 block text-xs text-muted-foreground">格式</label><Input :model-value="node.config.format" disabled /></div><div><label class="mb-1 block text-xs text-muted-foreground">标题</label><Input ref="titleInput" :model-value="node.config.title" :aria-invalid="Boolean(issueFor('title'))" @focusin="activeField = titleInput" @update:model-value="config({ title: String($event) })" /><p v-if="issueFor('title')" class="mt-1 text-xs text-destructive">{{ issueFor('title')?.message }}</p></div><div><label class="mb-1 block text-xs text-muted-foreground">路径</label><Input ref="pathInput" :model-value="node.config.path" :aria-invalid="Boolean(issueFor('path'))" @focusin="activeField = pathInput" @update:model-value="config({ path: String($event) })" /><p v-if="issueFor('path')" class="mt-1 text-xs text-destructive">{{ issueFor('path')?.message }}</p></div><div><label class="mb-1 block text-xs text-muted-foreground">标签（逗号分隔）</label><Input :model-value="node.config.tags.join(', ')" @update:model-value="config({ tags: String($event).split(',').map(item => item.trim()).filter(Boolean) })" /></div></template>
      <div><label class="mb-1 block text-xs text-muted-foreground">技能</label><Select @update:model-value="addSkill(String($event))"><SelectTrigger :aria-invalid="Boolean(issueFor('skill_names'))" :aria-describedby="issueFor('skill_names') ? issueId('skill_names') : undefined"><SelectValue placeholder="选择技能" /></SelectTrigger><SelectContent><SelectItem v-for="skill in skills" :key="skill.skill_name" :value="skill.skill_name">{{ skill.name || skill.skill_name }}</SelectItem></SelectContent></Select><p v-if="issueFor('skill_names')" :id="issueId('skill_names')" class="mt-1 text-xs text-destructive">{{ issueFor('skill_names')?.message || '技能配置有误' }}</p><div class="mt-2 grid gap-1"><div v-for="(skill, index) in node.config.skill_names" :key="skill" class="flex h-8 items-center gap-1 border px-2 text-xs"><span class="min-w-0 flex-1 truncate">{{ skill }}</span><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="上移" @click="moveSkill(index, -1)"><ArrowUp class="h-3.5 w-3.5" /></Button><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="下移" @click="moveSkill(index, 1)"><ArrowDown class="h-3.5 w-3.5" /></Button><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="删除" @click="removeSkill(index)"><Trash2 class="h-3.5 w-3.5" /></Button></div></div></div>
    </template>
  </section>
</template>
