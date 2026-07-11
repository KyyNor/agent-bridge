<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-vue-next'
import type { ManagedScript, SkillPrompt, WorkflowNode } from '../../api/types'
import Button from '../../components/ui/button/Button.vue'
import Input from '../../components/ui/input/Input.vue'
import Textarea from '../../components/ui/textarea/Textarea.vue'
import Select from '../../components/ui/select/Select.vue'
import SelectContent from '../../components/ui/select/SelectContent.vue'
import SelectItem from '../../components/ui/select/SelectItem.vue'
import SelectTrigger from '../../components/ui/select/SelectTrigger.vue'
import SelectValue from '../../components/ui/select/SelectValue.vue'

const props = defineProps<{ node: WorkflowNode; scripts: ManagedScript[]; skills: SkillPrompt[]; backends: string[] }>()
const emit = defineEmits<{ replace: [node: WorkflowNode] }>()
const schemaText = ref('')
watch(() => props.node, node => { schemaText.value = node.type === 'agent' ? JSON.stringify(node.config.output_schema || {}, null, 2) : '' }, { immediate: true, deep: true })
const activeScripts = computed(() => props.scripts.filter(script => script.status === 'active'))
const selectedScript = computed(() => props.node.type === 'script' ? activeScripts.value.find(script => script.script_key === props.node.config.script_key) : undefined)
const scriptProperties = computed(() => (selectedScript.value?.input_schema?.properties || {}) as Record<string, Record<string, unknown>>)
const requiredParams = computed(() => new Set(Array.isArray(selectedScript.value?.input_schema?.required) ? selectedScript.value?.input_schema.required.filter((key): key is string => typeof key === 'string') : []))

function replace(patch: Record<string, unknown>) { emit('replace', { ...props.node, ...patch } as WorkflowNode) }
function config(patch: Record<string, unknown>) { replace({ config: { ...props.node.config, ...patch } }) }
function selectScript(scriptKey: string) { const params = Object.fromEntries(Object.keys((props.scripts.find(script => script.script_key === scriptKey)?.input_schema?.properties || {}) as Record<string, unknown>).map(key => [key, ''])); config({ script_key: scriptKey, params }) }
function setParam(key: string, value: string) { if (props.node.type === 'script') config({ params: { ...props.node.config.params, [key]: value } }) }
function parseSchema() { if (props.node.type !== 'agent') return; try { config({ output_schema: schemaText.value.trim() ? JSON.parse(schemaText.value) : null }) } catch { /* retain text so the server can report a precise error on save */ } }
function currentSkills() { return props.node.type === 'agent' || props.node.type === 'output' ? props.node.config.skill_names : [] }
function moveSkill(index: number, direction: number) { const names = [...currentSkills()]; const other = index + direction; if (other < 0 || other >= names.length) return; [names[index], names[other]] = [names[other], names[index]]; config({ skill_names: names }) }
function addSkill(skillName: string) { if (!skillName || currentSkills().includes(skillName)) return; config({ skill_names: [...currentSkills(), skillName] }) }
function removeSkill(index: number) { config({ skill_names: currentSkills().filter((_, current) => current !== index) }) }
</script>

<template>
  <section class="space-y-3 border-l p-4">
    <div class="text-sm font-semibold">节点配置</div>
    <div><label class="mb-1 block text-xs text-muted-foreground">名称</label><Input :model-value="node.name" @update:model-value="replace({ name: String($event) })" /></div>

    <template v-if="node.type === 'get_task'"><p class="text-sm text-muted-foreground">从当前工作流队列领取一个任务。</p></template>
    <template v-else-if="node.type === 'script'">
      <div><label class="mb-1 block text-xs text-muted-foreground">托管脚本</label><Select :model-value="node.config.script_key" @update:model-value="selectScript(String($event))"><SelectTrigger><SelectValue placeholder="选择启用脚本" /></SelectTrigger><SelectContent><SelectItem v-for="script in activeScripts" :key="script.script_key" :value="script.script_key">{{ script.name }}</SelectItem></SelectContent></Select></div>
      <div v-for="(schema, key) in scriptProperties" :key="key"><label class="mb-1 block text-xs text-muted-foreground">{{ key }}<span v-if="requiredParams.has(key)" class="text-destructive"> *</span></label><Input :model-value="String(node.config.params[key] ?? '')" :placeholder="typeof schema.description === 'string' ? schema.description : '{{ input.value }}'" @update:model-value="setParam(key, String($event))" /></div>
      <div><label class="mb-1 block text-xs text-muted-foreground">超时（秒）</label><Input :model-value="node.config.timeout_seconds" type="number" @update:model-value="config({ timeout_seconds: Number($event) })" /></div>
    </template>
    <template v-else>
      <div><label class="mb-1 block text-xs text-muted-foreground">提示词</label><Textarea :model-value="node.config.prompt" class="min-h-28" @update:model-value="config({ prompt: String($event) })" /></div>
      <div><label class="mb-1 block text-xs text-muted-foreground">后端</label><Select :model-value="node.config.backend_key" @update:model-value="config({ backend_key: String($event) })"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem v-for="backend in backends" :key="backend" :value="backend">{{ backend }}</SelectItem></SelectContent></Select></div>
      <label class="flex items-center gap-2 text-sm"><input :checked="node.config.mcp_enabled" type="checkbox" @change="config({ mcp_enabled: ($event.target as HTMLInputElement).checked })" /> Profile MCP</label>
      <template v-if="node.type === 'agent'"><div><label class="mb-1 block text-xs text-muted-foreground">输出模式</label><Select :model-value="node.config.result_mode" @update:model-value="config({ result_mode: String($event) })"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="text">文本</SelectItem><SelectItem value="json">JSON</SelectItem></SelectContent></Select></div><div v-if="node.config.result_mode === 'json'"><label class="mb-1 block text-xs text-muted-foreground">JSON Schema</label><Textarea v-model="schemaText" class="min-h-28 font-mono text-xs" @blur="parseSchema" /></div></template>
      <template v-else><div><label class="mb-1 block text-xs text-muted-foreground">标题</label><Input :model-value="node.config.title" @update:model-value="config({ title: String($event) })" /></div><div><label class="mb-1 block text-xs text-muted-foreground">路径</label><Input :model-value="node.config.path" @update:model-value="config({ path: String($event) })" /></div><div><label class="mb-1 block text-xs text-muted-foreground">标签（逗号分隔）</label><Input :model-value="node.config.tags.join(', ')" @update:model-value="config({ tags: String($event).split(',').map(item => item.trim()).filter(Boolean) })" /></div></template>
      <div><label class="mb-1 block text-xs text-muted-foreground">技能</label><Select @update:model-value="addSkill(String($event))"><SelectTrigger><SelectValue placeholder="选择技能" /></SelectTrigger><SelectContent><SelectItem v-for="skill in skills" :key="skill.skill_name" :value="skill.skill_name">{{ skill.name || skill.skill_name }}</SelectItem></SelectContent></Select><div class="mt-2 grid gap-1"><div v-for="(skill, index) in node.config.skill_names" :key="skill" class="flex h-8 items-center gap-1 border px-2 text-xs"><span class="min-w-0 flex-1 truncate">{{ skill }}</span><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="上移" @click="moveSkill(index, -1)"><ArrowUp class="h-3.5 w-3.5" /></Button><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="下移" @click="moveSkill(index, 1)"><ArrowDown class="h-3.5 w-3.5" /></Button><Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="删除" @click="removeSkill(index)"><Trash2 class="h-3.5 w-3.5" /></Button></div></div></div>
    </template>
  </section>
</template>
