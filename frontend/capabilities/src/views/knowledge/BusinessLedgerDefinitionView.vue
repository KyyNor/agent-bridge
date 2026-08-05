<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Plus, Trash2 } from '@lucide/vue'
import { api } from '../../api/client'
import type { BusinessLedger, BusinessLedgerField } from '../../api/types'
import { navigateTo } from '../../lib/navigation'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'

const props = defineProps<{ ledgerKey: string | null }>()
type LedgerDefinition = { ledger_key: string; name: string; description: string; fields: BusinessLedgerField[] }

function newField(): BusinessLedgerField {
  return { field_key: '', name: '', field_type: 'text', required: false, query_modes: ['contains'], sortable: false, agent_readable: true, enum_values: [] }
}

const isEdit = computed(() => Boolean(props.ledgerKey))
const definition = ref<LedgerDefinition>({ ledger_key: '', name: '', description: '', fields: [newField()] })
const editToken = ref<string | undefined>()
const loading = ref(false)
const saving = ref(false)
const error = ref('')

function copyFields(fields: BusinessLedgerField[]) {
  return fields.map(field => ({ ...field, query_modes: [...field.query_modes], enum_values: [...field.enum_values] }))
}

function addField() { definition.value.fields.push(newField()) }
function removeField(index: number) { if (definition.value.fields.length > 1) definition.value.fields.splice(index, 1) }
function queryModesFor(field: BusinessLedgerField): Array<[string, string]> {
  if (field.field_type === 'text') return [['exact', '精确匹配'], ['prefix', '前缀匹配'], ['contains', '模糊包含']]
  if (field.field_type === 'number') return [['exact', '等于'], ['gt', '大于'], ['gte', '大于等于'], ['lt', '小于'], ['lte', '小于等于'], ['between', '范围']]
  if (field.field_type === 'enum') return [['exact', '精确匹配'], ['in', '多选匹配']]
  return [['exact', '精确匹配'], ['before', '早于'], ['after', '晚于'], ['between', '范围']]
}
function changeFieldType(field: BusinessLedgerField, value: string) {
  field.field_type = value as BusinessLedgerField['field_type']
  field.query_modes = field.field_type === 'text' ? ['contains'] : []
  if (field.field_type !== 'enum') field.enum_values = []
}
function toggleQueryMode(field: BusinessLedgerField, mode: string) {
  const index = field.query_modes.indexOf(mode)
  if (index >= 0) field.query_modes.splice(index, 1)
  else field.query_modes.push(mode)
}
function updateEnumValues(field: BusinessLedgerField, value: string) {
  field.enum_values = value.split(/[,，\n]/).map(item => item.trim()).filter(Boolean)
}

async function loadDefinition() {
  if (!props.ledgerKey) return
  loading.value = true
  try {
    const ledger: BusinessLedger = await api.getBusinessLedger(props.ledgerKey)
    definition.value = { ledger_key: ledger.ledger_key, name: ledger.name, description: ledger.description, fields: copyFields(ledger.fields) }
    editToken.value = ledger.edit_token
  } catch (e: any) { error.value = e.message || '加载台账定义失败' }
  finally { loading.value = false }
}

async function save() {
  error.value = ''
  saving.value = true
  try {
    const payload = { name: definition.value.name, description: definition.value.description, fields: copyFields(definition.value.fields), expected_edit_token: editToken.value }
    if (isEdit.value && props.ledgerKey) await api.updateBusinessLedger(props.ledgerKey, payload)
    else await api.createBusinessLedger({ ledger_key: definition.value.ledger_key, ...payload })
    await navigateTo(`business-ledgers/${definition.value.ledger_key}`)
  } catch (e: any) { error.value = e.message || '保存失败' }
  finally { saving.value = false }
}

onMounted(loadDefinition)
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <main v-else class="mx-auto max-w-5xl space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h1 class="text-xl font-semibold tracking-tight">{{ isEdit ? '编辑台账定义' : '新建业务台账' }}</h1><p class="mt-1 text-sm text-muted-foreground">配置数据录入、浏览和 Agent 受控查询所使用的字段规则。</p></div>
      <Button variant="outline" @click="navigateTo(props.ledgerKey ? `business-ledgers/${props.ledgerKey}` : 'business-ledgers')"><ArrowLeft :size="15" />返回业务台账</Button>
    </div>
    <div v-if="error" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
    <Card><CardContent class="grid gap-4 p-5 md:grid-cols-2"><label class="text-sm">标识<Input v-model="definition.ledger_key" :disabled="isEdit" placeholder="例如 asset_inventory" /></label><label class="text-sm">名称<Input v-model="definition.name" placeholder="例如 资产台账" /></label><label class="text-sm md:col-span-2">描述<Input v-model="definition.description" placeholder="说明这份台账包含什么数据" /></label></CardContent></Card>
    <section>
      <div class="mb-3 flex items-center justify-between"><div><h2 class="text-base font-medium">字段定义</h2><p class="mt-1 text-sm text-muted-foreground">每个字段可以独立配置数据类型、查询方式与 Agent 可见性。</p></div><Button size="sm" variant="outline" @click="addField"><Plus :size="14" />添加字段</Button></div>
      <div class="space-y-3">
        <Card v-for="(field, index) in definition.fields" :key="index" class="border-border/80"><CardContent class="p-5"><div class="mb-4 flex items-center justify-between"><span class="text-sm font-medium">字段 {{ index + 1 }}</span><Button size="sm" variant="ghost" :disabled="definition.fields.length === 1" title="删除字段" @click="removeField(index)"><Trash2 :size="14" /></Button></div><div class="grid gap-3 md:grid-cols-3"><label class="text-sm">字段名称<Input v-model="field.name" placeholder="例如 IP 地址" /></label><label class="text-sm">字段标识<Input v-model="field.field_key" placeholder="例如 ip_address" /></label><label class="text-sm">字段类型<select :value="field.field_type" class="mt-1 h-9 w-full rounded-md border border-input bg-background px-2" @change="changeFieldType(field, ($event.target as HTMLSelectElement).value)"><option value="text">文本</option><option value="number">数字</option><option value="enum">枚举</option><option value="date">日期</option><option value="datetime">日期时间</option></select></label></div><label v-if="field.field_type === 'enum'" class="mt-3 block text-sm">枚举选项<Input :value="field.enum_values.join(', ')" placeholder="用逗号分隔，例如 Linux, Windows" @input="updateEnumValues(field, ($event.target as HTMLInputElement).value)" /></label><div class="mt-4 grid gap-3 md:grid-cols-[190px_1fr]"><div class="flex flex-wrap gap-x-4 gap-y-2 text-sm"><label class="flex items-center gap-2"><input v-model="field.required" type="checkbox" class="size-4 rounded" />必填</label><label class="flex items-center gap-2"><input v-model="field.sortable" type="checkbox" class="size-4 rounded" />允许排序</label><label class="flex items-center gap-2"><input v-model="field.agent_readable" type="checkbox" class="size-4 rounded" />允许 Agent 返回</label></div><div><span class="mb-1 block text-sm">允许查询</span><div class="flex flex-wrap gap-x-4 gap-y-2"><label v-for="option in queryModesFor(field)" :key="option[0]" class="flex items-center gap-2 text-sm"><input type="checkbox" :checked="field.query_modes.includes(option[0])" class="size-4 rounded" @change="toggleQueryMode(field, option[0])" />{{ option[1] }}</label></div></div></div></CardContent></Card>
      </div>
    </section>
    <div class="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-background/95 py-4 backdrop-blur"><Button variant="outline" @click="navigateTo(props.ledgerKey ? `business-ledgers/${props.ledgerKey}` : 'business-ledgers')">取消</Button><Button :disabled="saving" @click="save">{{ saving ? '保存中...' : '保存' }}</Button></div>
  </main>
</template>
