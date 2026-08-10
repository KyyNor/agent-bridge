<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Plus, Trash2, WandSparkles } from '@lucide/vue'
import { api } from '../../api/client'
import type { BusinessLedger, BusinessLedgerField, ResourceVisibility } from '../../api/types'
import BusinessLedgerDesignerDrawer from '../../components/business-ledger/BusinessLedgerDesignerDrawer.vue'
import { useBusinessLedgerDesigner } from '../../composables/useBusinessLedgerDesigner'
import { businessLedgerFieldsFromDesign } from '../../lib/businessLedger'
import { navigateTo } from '../../lib/navigation'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Input } from '../../components/ui/input'

const props = defineProps<{ ledgerKey: string | null }>()
type LedgerDefinition = { ledger_key: string; name: string; description: string; fields: BusinessLedgerField[]; visibility: ResourceVisibility }

function newField(): BusinessLedgerField {
  return { field_key: '', name: '', field_type: 'text', required: false, query_modes: [], sortable: true, agent_readable: true, enum_values: [] }
}

const isEdit = computed(() => Boolean(props.ledgerKey))
const definition = ref<LedgerDefinition>({ ledger_key: '', name: '', description: '', fields: [newField()], visibility: 'group' })
const editToken = ref<string | undefined>()
const loading = ref(false)
const saving = ref(false)
const error = ref('')

function designerCurrent(mode: 'create' | 'modify') {
  if (mode === 'modify') {
    return {
      ledger_key: definition.value.ledger_key,
      name: definition.value.name,
      description: definition.value.description,
      fields: definition.value.fields.map(field => ({
        field_key: field.field_key,
        name: field.name,
        field_type: field.field_type,
        required: field.required,
        fuzzy_match: field.field_type === 'text' && field.query_modes.includes('contains'),
        agent_readable: field.agent_readable,
        enum_values: [...field.enum_values],
      })),
    }
  }
  return { fields: [] }
}

const {
  showDesigner,
  designMode,
  designPrompt,
  designing,
  designError,
  designResponse,
  designStopRequested,
  ledgerDesignDraft,
  openBusinessLedgerDesigner,
  runBusinessLedgerDesigner,
  stopBusinessLedgerDesigner,
} = useBusinessLedgerDesigner({ current: designerCurrent })

function copyFields(fields: BusinessLedgerField[]) {
  return fields.map(field => ({ ...field, query_modes: [...field.query_modes], enum_values: [...field.enum_values] }))
}

function addField() { definition.value.fields.push(newField()) }
function removeField(index: number) { if (definition.value.fields.length > 1) definition.value.fields.splice(index, 1) }
function changeFieldType(field: BusinessLedgerField, value: string) {
  field.field_type = value as BusinessLedgerField['field_type']
  if (field.field_type !== 'text') field.query_modes = []
  if (field.field_type !== 'enum') field.enum_values = []
}
function toggleContains(field: BusinessLedgerField) { field.query_modes = field.query_modes.includes('contains') ? [] : ['contains'] }
function updateEnumValues(field: BusinessLedgerField, value: string) {
  field.enum_values = value.split(/[,，\n]/).map(item => item.trim()).filter(Boolean)
}

async function loadDefinition() {
  if (!props.ledgerKey) return
  loading.value = true
  try {
    const ledger: BusinessLedger = await api.getBusinessLedger(props.ledgerKey)
    definition.value = { ledger_key: ledger.ledger_key, name: ledger.name, description: ledger.description, fields: copyFields(ledger.fields), visibility: ledger.visibility }
    editToken.value = ledger.edit_token
  } catch (e: any) { error.value = e.message || '加载台账定义失败' }
  finally { loading.value = false }
}

async function save(): Promise<boolean> {
  error.value = ''
  saving.value = true
  try {
    const payload = { name: definition.value.name, description: definition.value.description, fields: copyFields(definition.value.fields), visibility: definition.value.visibility, expected_edit_token: editToken.value }
    if (isEdit.value && props.ledgerKey) await api.updateBusinessLedger(props.ledgerKey, payload)
    else await api.createBusinessLedger({ ledger_key: definition.value.ledger_key, ...payload })
    await navigateTo(`business-ledgers/${definition.value.ledger_key}`)
    return true
  } catch (e: any) { error.value = e.message || '保存失败'; return false }
  finally { saving.value = false }
}

async function acceptBusinessLedgerDesign() {
  if (designing.value || designStopRequested.value || !ledgerDesignDraft.value) return
  const draft = ledgerDesignDraft.value
  definition.value = {
    ledger_key: isEdit.value && props.ledgerKey ? props.ledgerKey : draft.ledger_key,
    name: draft.name,
    description: draft.description,
    fields: businessLedgerFieldsFromDesign(draft.fields),
    visibility: definition.value.visibility,
  }
  if (await save()) showDesigner.value = false
}

onMounted(loadDefinition)
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <main v-else class="flex min-h-[calc(100vh-8rem)] min-w-0 flex-col space-y-4">
    <div class="flex shrink-0 items-start gap-3">
      <Button variant="ghost" size="sm" class="-ml-2 mt-0.5" @click="navigateTo(props.ledgerKey ? `business-ledgers/${props.ledgerKey}` : 'business-ledgers')"><ArrowLeft :size="16" />返回业务台账</Button>
      <div class="min-w-0 flex-1 border-l border-border pl-3"><h1 class="truncate text-lg font-semibold">{{ isEdit ? '编辑台账定义' : '新建业务台账' }}</h1><p class="truncate text-xs text-muted-foreground">配置数据录入、浏览和 Agent 受控查询所使用的字段规则。</p></div>
      <Button variant="outline" size="sm" :disabled="designing" @click="openBusinessLedgerDesigner(isEdit ? 'modify' : 'create')"><WandSparkles :size="15" />AI 设计</Button>
    </div>
    <div v-if="error" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
    <Card><CardContent class="grid gap-4 p-5 md:grid-cols-2"><label class="text-sm">标识<Input v-model="definition.ledger_key" :disabled="isEdit" placeholder="例如 asset_inventory" /></label><label class="text-sm">名称<Input v-model="definition.name" placeholder="例如 资产台账" /></label><label class="text-sm md:col-span-2">描述<Input v-model="definition.description" placeholder="说明这份台账包含什么数据" /></label><label class="text-sm">可见范围<select v-model="definition.visibility" class="mt-1 h-9 w-full rounded-md border border-input bg-background px-2"><option value="group">仅本小组</option><option value="shared">共享给所有小组</option></select><span class="mt-1 block text-xs text-muted-foreground">共享后所有小组可查询和导出，维护仍只允许归属小组。</span></label></CardContent></Card>
    <Card class="min-h-0 flex-1"><CardContent class="p-0"><div class="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4"><div><h2 class="text-base font-medium">字段定义</h2><p class="mt-1 text-sm text-muted-foreground">数字、日期与日期时间字段支持范围筛选。</p></div><Button size="sm" variant="outline" @click="addField"><Plus :size="14" />添加字段</Button></div><div class="overflow-x-auto"><table class="w-full min-w-[980px] text-sm"><thead><tr class="border-b text-left text-xs text-muted-foreground"><th class="p-3 font-medium">字段名称</th><th class="p-3 font-medium">字段标识</th><th class="p-3 font-medium">类型 / 枚举选项</th><th class="p-3 font-medium">字段属性</th><th class="p-3 font-medium">文本检索</th><th class="p-3" /></tr></thead><tbody><tr v-for="(field, index) in definition.fields" :key="index" class="border-b border-border/60 align-top"><td class="p-3"><Input v-model="field.name" placeholder="例如 IP 地址" /></td><td class="p-3"><Input v-model="field.field_key" placeholder="例如 ip_address" /></td><td class="space-y-2 p-3"><select :value="field.field_type" class="h-9 w-full rounded-md border border-input bg-background px-2" @change="changeFieldType(field, ($event.target as HTMLSelectElement).value)"><option value="text">文本</option><option value="number">数字</option><option value="enum">枚举</option><option value="date">日期</option><option value="datetime">日期时间</option></select><Input v-if="field.field_type === 'enum'" :model-value="field.enum_values.join(', ')" placeholder="选项用逗号分隔" @update:model-value="updateEnumValues(field, String($event))" /></td><td class="p-3"><div class="grid gap-2"><label class="flex items-center gap-2"><input v-model="field.required" type="checkbox" class="size-4 rounded" />必填</label><label class="flex items-center gap-2"><input v-model="field.agent_readable" type="checkbox" class="size-4 rounded" />允许 Agent 返回</label></div></td><td class="p-3"><label v-if="field.field_type === 'text'" class="flex items-center gap-2"><input type="checkbox" :checked="field.query_modes.includes('contains')" class="size-4 rounded" @change="toggleContains(field)" />支持模糊匹配</label><span v-else class="text-xs text-muted-foreground">—</span></td><td class="p-3"><Button size="sm" variant="ghost" :disabled="definition.fields.length === 1" title="删除字段" @click="removeField(index)"><Trash2 :size="14" /></Button></td></tr></tbody></table></div></CardContent></Card>
    <div class="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-background/95 py-4 backdrop-blur"><Button variant="outline" @click="navigateTo(props.ledgerKey ? `business-ledgers/${props.ledgerKey}` : 'business-ledgers')">取消</Button><Button :disabled="saving" @click="save">{{ saving ? '保存中...' : '保存' }}</Button></div>
    <BusinessLedgerDesignerDrawer :open="showDesigner" :mode="designMode" :prompt="designPrompt" :busy="designing" :stop-requested="designStopRequested" :error="designError" :response="designResponse" :draft="ledgerDesignDraft" :saving="saving" @update:open="showDesigner = $event" @update:mode="designMode = $event" @update:prompt="designPrompt = $event" @run="runBusinessLedgerDesigner" @stop="stopBusinessLedgerDesigner" @accept="acceptBusinessLedgerDesign" />
  </main>
</template>
