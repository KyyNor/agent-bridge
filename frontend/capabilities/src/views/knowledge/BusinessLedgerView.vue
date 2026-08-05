<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Database, Download, FileUp, Pencil, Plus, Settings2, Trash2 } from '@lucide/vue'
import { api } from '../../api/client'
import type { BusinessLedger, BusinessLedgerField, BusinessLedgerRecords, ProjectProfile } from '../../api/types'
import { navigateTo } from '../../lib/navigation'
import { confirm } from '../../composables/useConfirm'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'

const props = defineProps<{ routeKey: string }>()
const ledgerKey = computed(() => props.routeKey.split('/')[0])
const isDetail = computed(() => Boolean(ledgerKey.value))
const ledgers = ref<BusinessLedger[]>([])
const ledger = ref<BusinessLedger | null>(null)
const records = ref<BusinessLedgerRecords | null>(null)
const loading = ref(true)
const error = ref('')
const showDefinition = ref(false)
const definitionMode = ref<'create' | 'edit'>('create')
const definition = ref({ ledger_key: '', name: '', description: '', fields: '[\n  {"field_key":"name","name":"名称","field_type":"text","query_modes":["contains"],"agent_readable":true}\n]' })
const recordValues = ref<Record<string, any>>({})
const editingRecordId = ref<string | null>(null)
const keyword = ref('')
const importFile = ref<File | null>(null)
const importPreview = ref<{ preview_id: string; rows: number; errors: Array<{ row: number; error: string }> } | null>(null)
const showPlaneDialog = ref(false)
const allProfiles = ref<ProjectProfile[]>([])
const pendingProfileKeys = ref<string[]>([])
const planeSaving = ref(false)

async function load() {
  loading.value = true; error.value = ''
  try {
    if (!isDetail.value) ledgers.value = await api.listBusinessLedgers()
    else {
      ledger.value = await api.getBusinessLedger(ledgerKey.value)
      recordValues.value = Object.fromEntries(ledger.value.fields.map(field => [field.field_key, '']))
      editingRecordId.value = null
      await loadRecords()
    }
  } catch (e: any) { error.value = e.message || '加载业务台账失败' }
  loading.value = false
}
async function loadRecords() { if (ledgerKey.value) records.value = await api.queryBusinessLedgerRecords(ledgerKey.value, { keyword: keyword.value || undefined }) }
function openCreate() { definitionMode.value = 'create'; definition.value = { ledger_key: '', name: '', description: '', fields: definition.value.fields }; showDefinition.value = true }
function openEdit() { if (!ledger.value) return; definitionMode.value = 'edit'; definition.value = { ledger_key: ledger.value.ledger_key, name: ledger.value.name, description: ledger.value.description, fields: JSON.stringify(ledger.value.fields, null, 2) }; showDefinition.value = true }
async function saveDefinition() {
  try {
    const fields = JSON.parse(definition.value.fields) as BusinessLedgerField[]
    if (definitionMode.value === 'create') await api.createBusinessLedger({ ledger_key: definition.value.ledger_key, name: definition.value.name, description: definition.value.description, fields })
    else await api.updateBusinessLedger(definition.value.ledger_key, { name: definition.value.name, description: definition.value.description, fields, expected_edit_token: ledger.value?.edit_token })
    showDefinition.value = false
    if (definitionMode.value === 'create') await navigateTo(`business-ledgers/${definition.value.ledger_key}`)
    await load()
  } catch (e: any) { error.value = e.message || '保存失败' }
}
function resetRecordForm() { if (!ledger.value) return; editingRecordId.value = null; recordValues.value = Object.fromEntries(ledger.value.fields.map(field => [field.field_key, ''])) }
function editRecord(row: { record_id: string; values: Record<string, unknown> }) { editingRecordId.value = row.record_id; recordValues.value = { ...row.values }; window.scrollTo({ top: 0, behavior: 'smooth' }) }
async function saveRecord() {
  if (!ledger.value) return
  try {
    if (editingRecordId.value) await api.updateBusinessLedgerRecord(ledger.value.ledger_key, editingRecordId.value, recordValues.value)
    else await api.addBusinessLedgerRecord(ledger.value.ledger_key, recordValues.value)
    await loadRecords(); resetRecordForm()
  } catch (e: any) { error.value = e.message || '保存记录失败' }
}
async function deleteRecord(recordId: string) { if (!ledger.value || !await confirm({ title: '删除数据', description: '确定删除该条台账数据？', destructive: true, confirmText: '删除' })) return; await api.deleteBusinessLedgerRecord(ledger.value.ledger_key, recordId); await loadRecords() }
async function deleteLedger() { if (!ledger.value || !await confirm({ title: '删除业务台账', description: `确定删除「${ledger.value.name}」及全部数据？`, destructive: true, confirmText: '删除' })) return; await api.deleteBusinessLedger(ledger.value.ledger_key); await navigateTo('business-ledgers') }
async function previewImport() { if (!ledger.value || !importFile.value) return; try { importPreview.value = await api.previewBusinessLedgerImport(ledger.value.ledger_key, importFile.value) } catch (e: any) { error.value = e.message || '导入预览失败' } }
async function confirmImport() { if (!ledger.value || !importPreview.value) return; await api.confirmBusinessLedgerImport(ledger.value.ledger_key, importPreview.value.preview_id); importPreview.value = null; importFile.value = null; await loadRecords() }
async function exportLedger() {
  if (!ledger.value) return
  try {
    const blob = await api.exportBusinessLedger(ledger.value.ledger_key)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `${ledger.value.ledger_key}.xlsx`; link.click()
    URL.revokeObjectURL(url)
  } catch (e: any) { error.value = e.message || '导出失败' }
}
async function openPlaneDialog() {
  if (!ledger.value) return
  try {
    const [profiles, rules] = await Promise.all([api.listProfiles(), api.getResourceProfiles('business_ledger', ledger.value.ledger_key)])
    allProfiles.value = profiles; pendingProfileKeys.value = rules.map((rule: any) => rule.profile_key)
    showPlaneDialog.value = true
  } catch (e: any) { error.value = e.message || '加载能力平面失败' }
}
function togglePlaneProfile(profileKey: string) { const index = pendingProfileKeys.value.indexOf(profileKey); if (index >= 0) pendingProfileKeys.value.splice(index, 1); else pendingProfileKeys.value.push(profileKey) }
async function savePlaneProfiles() { if (!ledger.value) return; planeSaving.value = true; try { await api.setResourceProfiles('business_ledger', ledger.value.ledger_key, [...pendingProfileKeys.value]); showPlaneDialog.value = false } catch (e: any) { error.value = e.message || '保存能力平面失败' } finally { planeSaving.value = false } }
function chooseFile(event: Event) { importFile.value = (event.target as HTMLInputElement).files?.[0] || null }
onMounted(load)
watch(() => props.routeKey, load)
</script>

<template>
  <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
  <template v-else-if="!isDetail">
    <Teleport defer to="#ph-actions"><Button @click="openCreate"><Plus :size="15" />新建台账</Button></Teleport>
    <div v-if="error" class="mb-4 rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
    <div v-if="!ledgers.length" class="py-20 text-center text-sm text-muted-foreground">暂无业务台账，创建后可维护 Excel 数据并按能力平面授权查询。</div>
    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card v-for="item in ledgers" :key="item.ledger_key" class="cursor-pointer transition-shadow hover:shadow-sm" @click="navigateTo(`business-ledgers/${item.ledger_key}`)"><CardContent class="p-5"><div class="flex items-start justify-between gap-3"><div><h2 class="font-medium">{{ item.name }}</h2><p class="mt-1 font-mono text-xs text-muted-foreground">{{ item.ledger_key }}</p></div><Database :size="18" class="text-primary" /></div><p class="mt-4 line-clamp-2 text-sm text-muted-foreground">{{ item.description || '暂无描述' }}</p><div class="mt-4 flex gap-2"><Badge variant="secondary">{{ item.record_count }} 行</Badge><Badge variant="outline">{{ item.fields.length }} 字段</Badge></div></CardContent></Card>
    </div>
  </template>
  <template v-else-if="ledger">
    <Teleport defer to="#ph-actions"><Button variant="outline" @click="navigateTo('business-ledgers')"><ArrowLeft :size="15" />返回列表</Button><Button variant="outline" @click="openPlaneDialog"><Settings2 :size="15" />能力平面</Button><Button variant="outline" @click="openEdit"><Pencil :size="15" />编辑定义</Button><Button variant="destructive" @click="deleteLedger"><Trash2 :size="15" />删除</Button></Teleport>
    <div v-if="error" class="mb-4 rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
    <Card><CardContent class="p-5"><h2 class="font-medium">{{ editingRecordId ? '编辑数据' : ledger.name }}</h2><p class="mt-1 text-sm text-muted-foreground">{{ editingRecordId ? '修改后保存即可更新该条记录。' : (ledger.description || '暂无描述') }}</p><div class="mt-4 grid gap-3 md:grid-cols-3"><label v-for="field in ledger.fields" :key="field.field_key" class="text-sm"><span class="mb-1 block text-muted-foreground">{{ field.name }}</span><select v-if="field.field_type === 'enum'" v-model="recordValues[field.field_key]" class="h-9 w-full rounded-md border border-input bg-background px-2"><option value="">请选择</option><option v-for="value in field.enum_values" :key="value">{{ value }}</option></select><Input v-else v-model="recordValues[field.field_key]" :type="field.field_type === 'number' ? 'number' : field.field_type === 'date' ? 'date' : field.field_type === 'datetime' ? 'datetime-local' : 'text'" /></label></div><div class="mt-4 flex gap-2"><Button @click="saveRecord"><Plus :size="15" />{{ editingRecordId ? '保存修改' : '新增数据' }}</Button><Button v-if="editingRecordId" variant="outline" @click="resetRecordForm">取消</Button></div></CardContent></Card>
    <Card class="mt-5"><CardContent class="p-5"><div class="flex flex-wrap items-end gap-3"><label class="text-sm">关键词<Input v-model="keyword" class="mt-1" @keyup.enter="loadRecords" /></label><Button variant="outline" @click="loadRecords">查询</Button><label class="text-sm">Excel 导入<input class="mt-1 block text-xs" type="file" accept=".xlsx" @change="chooseFile" /></label><Button variant="outline" :disabled="!importFile" @click="previewImport"><FileUp :size="15" />预览导入</Button><Button variant="outline" @click="exportLedger"><Download :size="15" />导出 Excel</Button></div><div v-if="importPreview" class="mt-3 rounded-md bg-muted p-3 text-sm">将导入 {{ importPreview.rows }} 行；错误 {{ importPreview.errors.length }} 行。<Button class="ml-3" size="sm" @click="confirmImport">确认导入</Button></div><div class="mt-5 overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b text-left text-muted-foreground"><th class="p-2">#</th><th v-for="field in ledger.fields" :key="field.field_key" class="p-2">{{ field.name }}</th><th class="p-2" /></tr></thead><tbody><tr v-for="row in records?.items" :key="row.record_id" class="border-b"><td class="p-2 font-mono text-xs">{{ row.record_id.slice(0, 8) }}</td><td v-for="field in ledger.fields" :key="field.field_key" class="p-2">{{ row.values[field.field_key] ?? '—' }}</td><td class="p-2"><div class="flex gap-1"><Button size="sm" variant="ghost" @click="editRecord(row)"><Pencil :size="14" /></Button><Button size="sm" variant="ghost" @click="deleteRecord(row.record_id)"><Trash2 :size="14" /></Button></div></td></tr></tbody></table></div></CardContent></Card>
  </template>
  <Dialog v-model:open="showDefinition"><DialogContent class="max-w-2xl"><DialogHeader><DialogTitle>{{ definitionMode === 'create' ? '新建业务台账' : '编辑台账定义' }}</DialogTitle></DialogHeader><div class="grid gap-3"><label class="text-sm">标识<Input v-model="definition.ledger_key" :disabled="definitionMode === 'edit'" /></label><label class="text-sm">名称<Input v-model="definition.name" /></label><label class="text-sm">描述<Input v-model="definition.description" /></label><label class="text-sm">字段定义（JSON）<textarea v-model="definition.fields" class="mt-1 min-h-56 w-full rounded-md border border-input bg-background p-3 font-mono text-xs" /></label></div><DialogFooter><Button @click="saveDefinition">保存</Button></DialogFooter></DialogContent></Dialog>
  <Dialog v-model:open="showPlaneDialog"><DialogContent class="sm:max-w-[420px]"><DialogHeader><DialogTitle>{{ ledger?.name || '' }} — 归属能力平面</DialogTitle></DialogHeader><div class="space-y-2"><p class="text-xs text-muted-foreground">只有选中的能力平面可以发现并查询此业务台账。</p><div v-if="!allProfiles.length" class="py-6 text-center text-sm text-muted-foreground">暂无能力平面</div><div v-else class="max-h-[320px] space-y-1 overflow-y-auto rounded-lg border border-border p-1"><label v-for="profile in allProfiles" :key="profile.profile_key" class="list-row-interactive flex cursor-pointer items-center gap-3 rounded-md px-3 py-2"><input type="checkbox" :checked="pendingProfileKeys.includes(profile.profile_key)" class="size-4 rounded" @change="togglePlaneProfile(profile.profile_key)" /><div class="min-w-0 flex-1"><div class="truncate text-sm font-medium">{{ profile.name || profile.profile_key }}</div><div class="text-xs text-muted-foreground">{{ profile.profile_key }}</div></div></label></div></div><DialogFooter><Button variant="outline" @click="showPlaneDialog = false">取消</Button><Button :disabled="planeSaving" @click="savePlaneProfiles">{{ planeSaving ? '保存中...' : '确认' }}</Button></DialogFooter></DialogContent></Dialog>
</template>
