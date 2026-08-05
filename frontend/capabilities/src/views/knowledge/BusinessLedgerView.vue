<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Database, Download, FileUp, Pencil, Plus, Settings2, Trash2 } from '@lucide/vue'
import { api } from '../../api/client'
import type { BusinessLedger, BusinessLedgerRecords, ProjectProfile } from '../../api/types'
import { businessLedgerRecordFormValues } from '../../lib/businessLedger'
import { navigateTo } from '../../lib/navigation'
import { confirm } from '../../composables/useConfirm'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import BusinessLedgerDefinitionView from './BusinessLedgerDefinitionView.vue'

const props = defineProps<{ routeKey: string }>()
const routeSegments = computed(() => props.routeKey.split('/').filter(Boolean))
const ledgerKey = computed(() => routeSegments.value[0] || '')
const isDefinitionRoute = computed(() => ledgerKey.value === 'new' || routeSegments.value[1] === 'edit')
const definitionLedgerKey = computed(() => ledgerKey.value === 'new' ? null : ledgerKey.value)
const isDetail = computed(() => Boolean(ledgerKey.value) && !isDefinitionRoute.value)
const ledgers = ref<BusinessLedger[]>([])
const ledger = ref<BusinessLedger | null>(null)
const records = ref<BusinessLedgerRecords | null>(null)
const loading = ref(true)
const error = ref('')
const recordValues = ref<Record<string, any>>({})
const editingRecordId = ref<string | null>(null)
const showRecordDialog = ref(false)
const recordSaving = ref(false)
const keyword = ref('')
const importFile = ref<File | null>(null)
const importPreview = ref<{ preview_id: string; rows: number; errors: Array<{ row: number; error: string }> } | null>(null)
const showImportDialog = ref(false)
const importing = ref(false)
const showPlaneDialog = ref(false)
const allProfiles = ref<ProjectProfile[]>([])
const pendingProfileKeys = ref<string[]>([])
const planeSaving = ref(false)

async function load() {
  loading.value = true; error.value = ''
  try {
    if (isDefinitionRoute.value) return
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
function resetRecordForm() { if (!ledger.value) return; editingRecordId.value = null; recordValues.value = Object.fromEntries(ledger.value.fields.map(field => [field.field_key, ''])) }
function openCreateRecord() { resetRecordForm(); showRecordDialog.value = true }
function editRecord(row: { record_id: string; values: Record<string, unknown> }) { if (!ledger.value) return; editingRecordId.value = row.record_id; recordValues.value = businessLedgerRecordFormValues(ledger.value.fields, row.values); showRecordDialog.value = true }
async function saveRecord() {
  if (!ledger.value) return
  recordSaving.value = true
  try {
    if (editingRecordId.value) await api.updateBusinessLedgerRecord(ledger.value.ledger_key, editingRecordId.value, recordValues.value)
    else await api.addBusinessLedgerRecord(ledger.value.ledger_key, recordValues.value)
    await loadRecords(); resetRecordForm(); showRecordDialog.value = false
  } catch (e: any) { error.value = e.message || '保存记录失败' }
  finally { recordSaving.value = false }
}
async function deleteRecord(recordId: string) { if (!ledger.value || !await confirm({ title: '删除数据', description: '确定删除该条台账数据？', destructive: true, confirmText: '删除' })) return; await api.deleteBusinessLedgerRecord(ledger.value.ledger_key, recordId); await loadRecords() }
async function deleteLedger() { if (!ledger.value || !await confirm({ title: '删除业务台账', description: `确定删除「${ledger.value.name}」及全部数据？`, destructive: true, confirmText: '删除' })) return; await api.deleteBusinessLedger(ledger.value.ledger_key); await navigateTo('business-ledgers') }
function openImportDialog() { importFile.value = null; importPreview.value = null; showImportDialog.value = true }
async function previewImport() { if (!ledger.value || !importFile.value) return; importing.value = true; try { importPreview.value = await api.previewBusinessLedgerImport(ledger.value.ledger_key, importFile.value) } catch (e: any) { error.value = e.message || '导入预览失败' } finally { importing.value = false } }
async function confirmImport() { if (!ledger.value || !importPreview.value) return; importing.value = true; try { await api.confirmBusinessLedgerImport(ledger.value.ledger_key, importPreview.value.preview_id); importPreview.value = null; importFile.value = null; showImportDialog.value = false; await loadRecords() } catch (e: any) { error.value = e.message || '导入失败' } finally { importing.value = false } }
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
async function downloadImportTemplate() {
  if (!ledger.value) return
  try {
    const blob = await api.downloadBusinessLedgerTemplate(ledger.value.ledger_key)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `${ledger.value.ledger_key}-template.xlsx`; link.click()
    URL.revokeObjectURL(url)
  } catch (e: any) { error.value = e.message || '下载模板失败' }
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
function chooseFile(event: Event) { const input = event.target as HTMLInputElement; importFile.value = input.files?.[0] || null; input.value = '' }
onMounted(load)
watch(() => props.routeKey, load)
</script>

<template>
  <BusinessLedgerDefinitionView v-if="isDefinitionRoute" :ledger-key="definitionLedgerKey" />
  <template v-else>
    <div v-if="loading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
    <template v-else-if="!isDetail">
      <Teleport defer to="#ph-actions"><Button @click="navigateTo('business-ledgers/new')"><Plus :size="15" />新建台账</Button></Teleport>
      <div v-if="error" class="mb-4 rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
      <div v-if="!ledgers.length" class="py-20 text-center text-sm text-muted-foreground">暂无业务台账，创建后可维护 Excel 数据并按能力平面授权查询。</div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"><Card v-for="item in ledgers" :key="item.ledger_key" class="cursor-pointer transition-shadow hover:shadow-sm" @click="navigateTo(`business-ledgers/${item.ledger_key}`)"><CardContent class="p-5"><div class="flex items-start justify-between gap-3"><div><h2 class="font-medium">{{ item.name }}</h2><p class="mt-1 font-mono text-xs text-muted-foreground">{{ item.ledger_key }}</p></div><Database :size="18" class="text-primary" /></div><p class="mt-4 line-clamp-2 text-sm text-muted-foreground">{{ item.description || '暂无描述' }}</p><div class="mt-4 flex gap-2"><Badge variant="secondary">{{ item.record_count }} 行</Badge><Badge variant="outline">{{ item.fields.length }} 字段</Badge></div></CardContent></Card></div>
    </template>
    <main v-else-if="ledger" class="flex min-h-[calc(100vh-8rem)] min-w-0 flex-col space-y-4">
      <div class="flex shrink-0 flex-wrap items-start gap-3"><Button variant="ghost" size="sm" class="-ml-2 mt-0.5" @click="navigateTo('business-ledgers')"><ArrowLeft :size="16" />返回业务台账</Button><div class="min-w-0 flex-1 border-l border-border pl-3"><h1 class="truncate text-lg font-semibold">{{ ledger.name }}</h1><p class="truncate text-xs text-muted-foreground">{{ ledger.ledger_key }}</p></div><div class="flex flex-wrap gap-2"><Button variant="outline" size="sm" @click="openPlaneDialog"><Settings2 :size="15" />能力平面</Button><Button variant="outline" size="sm" @click="navigateTo(`business-ledgers/${ledger.ledger_key}/edit`)"><Pencil :size="15" />编辑定义</Button><Button variant="destructive" size="sm" @click="deleteLedger"><Trash2 :size="15" />删除</Button></div></div>
      <div v-if="error" class="rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ error }}</div>
      <Card><CardContent class="p-5"><p class="text-sm text-muted-foreground">{{ ledger.description || '暂无描述' }}</p><div class="mt-4 flex flex-wrap gap-2"><Badge v-for="field in ledger.fields" :key="field.field_key" variant="outline">{{ field.name }} · {{ field.field_type }}</Badge></div></CardContent></Card>
      <Card class="min-h-0 flex-1"><CardContent class="p-0"><div class="flex flex-wrap items-end justify-between gap-3 border-b border-border p-4"><label class="w-full max-w-sm text-sm">关键词<Input v-model="keyword" class="mt-1" @keyup.enter="loadRecords" /></label><div class="flex flex-wrap gap-2"><Button variant="outline" size="sm" @click="loadRecords">查询</Button><Button variant="outline" size="sm" @click="openImportDialog"><FileUp :size="15" />导入 Excel</Button><Button variant="outline" size="sm" @click="exportLedger"><Download :size="15" />导出 Excel</Button><Button size="sm" @click="openCreateRecord"><Plus :size="15" />新增数据</Button></div></div><div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b text-left text-muted-foreground"><th class="p-3">#</th><th v-for="field in ledger.fields" :key="field.field_key" class="p-3">{{ field.name }}</th><th class="p-3" /></tr></thead><tbody><tr v-if="!records?.items.length"><td :colspan="ledger.fields.length + 2" class="p-8 text-center text-sm text-muted-foreground">暂无数据</td></tr><tr v-for="row in records?.items" :key="row.record_id" class="border-b border-border/60"><td class="p-3 font-mono text-xs">{{ row.record_id.slice(0, 8) }}</td><td v-for="field in ledger.fields" :key="field.field_key" class="p-3">{{ row.values[field.field_key] ?? '—' }}</td><td class="p-3"><div class="flex gap-1"><Button size="sm" variant="ghost" title="编辑数据" @click="editRecord(row)"><Pencil :size="14" /></Button><Button size="sm" variant="ghost" title="删除数据" @click="deleteRecord(row.record_id)"><Trash2 :size="14" /></Button></div></td></tr></tbody></table></div></CardContent></Card>
    </main>
    <Dialog v-model:open="showRecordDialog"><DialogContent class="max-h-[85vh] max-w-2xl overflow-y-auto"><DialogHeader><DialogTitle>{{ editingRecordId ? '编辑数据' : '新增数据' }}</DialogTitle></DialogHeader><div v-if="ledger" class="grid gap-3 md:grid-cols-2"><label v-for="field in ledger.fields" :key="field.field_key" class="text-sm"><span class="mb-1 block text-muted-foreground">{{ field.name }}<span v-if="field.required" class="text-destructive"> *</span></span><select v-if="field.field_type === 'enum'" v-model="recordValues[field.field_key]" class="h-9 w-full rounded-md border border-input bg-background px-2"><option value="">请选择</option><option v-for="value in field.enum_values" :key="value">{{ value }}</option></select><Input v-else v-model="recordValues[field.field_key]" :type="field.field_type === 'number' ? 'number' : field.field_type === 'date' ? 'date' : field.field_type === 'datetime' ? 'datetime-local' : 'text'" /></label></div><DialogFooter><Button variant="outline" @click="showRecordDialog = false">取消</Button><Button :disabled="recordSaving" @click="saveRecord">{{ recordSaving ? '保存中...' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog v-model:open="showImportDialog"><DialogContent class="sm:max-w-[560px]"><DialogHeader><DialogTitle>导入 Excel</DialogTitle></DialogHeader><div class="space-y-4"><p class="text-sm text-muted-foreground">列名会按字段标识或字段名称自动匹配。请先预览并确认导入结果。</p><div class="flex flex-col gap-3 rounded-md border border-border bg-muted/20 p-3 sm:flex-row sm:items-end sm:justify-between"><div class="min-w-0 flex-1"><label for="business-ledger-import-file" class="text-sm font-medium">选择 Excel 文件</label><input id="business-ledger-import-file" class="mt-2 h-9 w-full cursor-pointer rounded-sm border border-input bg-transparent px-2.5 py-1 text-sm file:mr-2 file:border-0 file:bg-transparent file:font-medium" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" :disabled="importing" @change="chooseFile" /><p class="mt-1.5 text-xs text-muted-foreground">仅支持 .xlsx 文件，建议先下载模板填写。</p></div><Button type="button" variant="outline" :disabled="importing" @click="downloadImportTemplate">下载模板</Button></div><div v-if="importFile" class="text-xs text-muted-foreground">已选择：<span class="font-medium text-foreground">{{ importFile.name }}</span></div><div v-if="importPreview" class="rounded-md bg-muted p-3 text-sm">将导入 {{ importPreview.rows }} 行；错误 {{ importPreview.errors.length }} 行。<ul v-if="importPreview.errors.length" class="mt-2 list-disc pl-5 text-xs text-muted-foreground"><li v-for="item in importPreview.errors.slice(0, 5)" :key="item.row">第 {{ item.row }} 行：{{ item.error }}</li></ul></div></div><DialogFooter><Button variant="outline" @click="showImportDialog = false">取消</Button><Button v-if="importPreview" :disabled="importing" @click="confirmImport">{{ importing ? '导入中...' : '确认导入' }}</Button><Button v-else :disabled="!importFile || importing" @click="previewImport">{{ importing ? '预览中...' : '预览导入' }}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog v-model:open="showPlaneDialog"><DialogContent class="sm:max-w-[420px]"><DialogHeader><DialogTitle>{{ ledger?.name || '' }} — 归属能力平面</DialogTitle></DialogHeader><div class="space-y-2"><p class="text-xs text-muted-foreground">只有选中的能力平面可以发现并查询此业务台账。</p><div v-if="!allProfiles.length" class="py-6 text-center text-sm text-muted-foreground">暂无能力平面</div><div v-else class="max-h-[320px] space-y-1 overflow-y-auto rounded-lg border border-border p-1"><label v-for="profile in allProfiles" :key="profile.profile_key" class="list-row-interactive flex cursor-pointer items-center gap-3 rounded-md px-3 py-2"><input type="checkbox" :checked="pendingProfileKeys.includes(profile.profile_key)" class="size-4 rounded" @change="togglePlaneProfile(profile.profile_key)" /><div class="min-w-0 flex-1"><div class="truncate text-sm font-medium">{{ profile.name || profile.profile_key }}</div><div class="text-xs text-muted-foreground">{{ profile.profile_key }}</div></div></label></div></div><DialogFooter><Button variant="outline" @click="showPlaneDialog = false">取消</Button><Button :disabled="planeSaving" @click="savePlaneProfiles">{{ planeSaving ? '保存中...' : '确认' }}</Button></DialogFooter></DialogContent></Dialog>
  </template>
</template>
