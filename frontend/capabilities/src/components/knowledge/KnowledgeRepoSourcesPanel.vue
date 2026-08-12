<script setup lang="ts">
import { GitBranch } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { CodeRepository, KbRepoSource, KnowledgeBaseSummary } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { confirm } from '../../composables/useConfirm'
import { SHARED_RESOURCE_READ_ONLY_HINT } from '../../lib/resourceAccess'

const props = defineProps<{
  kb: KnowledgeBaseSummary | null
  readOnly?: boolean
}>()

const emit = defineEmits<{
  sourcesChange: [sources: KbRepoSource[]]
  refreshDetail: []
}>()

const sources = ref<KbRepoSource[]>([])
const codeRepos = ref<CodeRepository[]>([])
const repoSourceForm = ref({ repo_key: '', include_suffixes: '.md, .txt' })
const saving = ref(false)
const syncing = ref<Record<string, boolean>>({})
const deleting = ref<Record<string, boolean>>({})
const error = ref('')
const message = ref('')
const kbSlug = computed(() => props.kb?.slug || '')

function publishSources(next: KbRepoSource[]) {
  sources.value = next
  emit('sourcesChange', next)
}

function resetForm() {
  if (codeRepos.value.length === 0) {
    repoSourceForm.value = { repo_key: '', include_suffixes: '.md, .txt' }
    return
  }
  const repo = codeRepos.value.find(candidate => !sources.value.some(source => source.repo_key === candidate.repo_key)) || codeRepos.value[0]
  repoSourceForm.value.repo_key = repo.repo_key
  onRepoSourceSelect()
}

function normalizeSuffixInput(value: string): string[] {
  const suffixes: string[] = []
  for (const raw of value.split(/[\s,，]+/)) {
    const trimmed = raw.trim().toLowerCase()
    if (!trimmed) continue
    const suffix = trimmed.startsWith('.') ? trimmed : `.${trimmed}`
    if (!suffixes.includes(suffix)) suffixes.push(suffix)
  }
  return suffixes
}

function onRepoSourceSelect() {
  const existing = sources.value.find(source => source.repo_key === repoSourceForm.value.repo_key)
  repoSourceForm.value.include_suffixes = existing?.include_suffixes.join(', ') || '.md, .txt'
  error.value = ''
  message.value = ''
}

async function loadSources() {
  const slug = kbSlug.value
  if (!slug) {
    publishSources([])
    codeRepos.value = []
    return
  }
  error.value = ''
  message.value = ''
  const [sourceResult, repoResult] = await Promise.allSettled([
    api.listKbRepoSources(slug),
    api.listCodeRepos(),
  ])
  publishSources(sourceResult.status === 'fulfilled' ? sourceResult.value : [])
  codeRepos.value = repoResult.status === 'fulfilled' ? repoResult.value : []
  resetForm()
}

async function refreshSources() {
  if (!kbSlug.value) return
  publishSources(await api.listKbRepoSources(kbSlug.value))
}

async function saveRepoSource() {
  const slug = kbSlug.value
  error.value = ''
  message.value = ''
  const includeSuffixes = normalizeSuffixInput(repoSourceForm.value.include_suffixes)
  if (!repoSourceForm.value.repo_key) {
    error.value = '请选择代码仓库'
    return
  }
  if (includeSuffixes.length === 0) {
    error.value = '请至少填写一个文件后缀'
    return
  }
  saving.value = true
  try {
    await api.saveKbRepoSource(slug, { repo_key: repoSourceForm.value.repo_key, include_suffixes: includeSuffixes })
    await refreshSources()
    repoSourceForm.value.include_suffixes = includeSuffixes.join(', ')
    message.value = '已保存 Git 数据源'
  } catch (cause: any) {
    error.value = cause.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function syncRepoSource(source: KbRepoSource) {
  error.value = ''
  message.value = ''
  syncing.value = { ...syncing.value, [source.repo_key]: true }
  try {
    const result = await api.syncKbRepoSource(kbSlug.value, source.repo_key)
    await refreshSources()
    emit('refreshDetail')
    message.value = `已同步：新增 ${result.added}，删除 ${result.removed}，更新 ${result.updated}`
  } catch (cause: any) {
    error.value = cause.message || '同步失败'
  } finally {
    syncing.value = { ...syncing.value, [source.repo_key]: false }
  }
}

async function deleteRepoSource(source: KbRepoSource) {
  const ok = await confirm({
    title: '移除数据源',
    description: `确定移除数据源「${source.repo_name || source.repo_key}」？将从该知识库删除 ${source.doc_count} 个由它提供的文档，并在后端同步删除。此操作不会删除 git 仓库本身。`,
    destructive: true,
    confirmText: '移除',
  })
  if (!ok) return
  error.value = ''
  message.value = ''
  deleting.value = { ...deleting.value, [source.repo_key]: true }
  try {
    await api.deleteKbRepoSource(kbSlug.value, source.repo_key)
    await refreshSources()
    emit('refreshDetail')
    message.value = '已移除数据源'
  } catch (cause: any) {
    error.value = cause.message || '删除失败'
  } finally {
    deleting.value = { ...deleting.value, [source.repo_key]: false }
  }
}

watch(kbSlug, () => { void loadSources() }, { immediate: true })
</script>

<template>
  <div class="space-y-4">
    <div class="rounded-lg border border-border p-4">
      <div class="mb-3 flex items-center gap-2">
        <GitBranch :size="15" class="text-muted-foreground" />
        <h4 class="text-sm font-medium">Git 数据源</h4>
      </div>
      <div v-if="codeRepos.length === 0" class="py-4 text-sm text-muted-foreground">暂无已登记的代码仓库，请先在代码知识中添加仓库。</div>
      <div v-else class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">代码仓库</label>
          <select v-model="repoSourceForm.repo_key" @change="onRepoSourceSelect" class="h-9 w-full rounded-md border border-border bg-background px-2 text-sm">
            <option v-for="repo in codeRepos" :key="repo.repo_key" :value="repo.repo_key">{{ repo.name || repo.repo_key }}</option>
          </select>
        </div>
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">后缀过滤</label>
          <Input v-model="repoSourceForm.include_suffixes" placeholder=".md, .txt" />
        </div>
        <div class="flex items-end">
          <Button class="h-9" @click="saveRepoSource" :disabled="saving || !repoSourceForm.repo_key || readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">{{ saving ? '保存中...' : '保存' }}</Button>
        </div>
      </div>
      <div v-if="error" class="mt-3 rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ error }}</div>
      <div v-if="message" class="mt-3 rounded-md bg-success-soft px-3 py-2 text-xs text-success-soft-fg">{{ message }}</div>
    </div>

    <div v-if="sources.length === 0" class="py-6 text-center text-sm text-muted-foreground">暂无 Git 数据源</div>
    <table v-else class="w-full">
      <thead><tr class="border-b border-border">
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">仓库</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">include_suffixes</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">最近同步</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">错误</th>
        <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground"></th>
      </tr></thead>
      <tbody><tr v-for="source in sources" :key="source.repo_key" class="border-b border-border/60">
        <td class="px-3 py-2">
          <div class="text-sm font-medium">{{ source.repo_name || source.repo_key }}</div>
          <div class="font-mono text-xs text-muted-foreground">{{ source.repo_key }}</div>
        </td>
        <td class="px-3 py-2"><div class="flex flex-wrap gap-1"><Badge v-for="suffix in source.include_suffixes" :key="suffix" variant="secondary" class="font-mono text-[11px]">{{ suffix }}</Badge></div></td>
        <td class="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">{{ source.last_synced_at ? formatLocalDatetime(source.last_synced_at) : '未同步' }}</td>
        <td class="px-3 py-2 max-w-[180px] overflow-hidden text-ellipsis text-xs text-destructive" :title="source.last_error ?? ''">{{ source.last_error || '—' }}</td>
        <td class="px-3 py-2 text-right"><div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" class="h-7 text-xs" @click="syncRepoSource(source)" :disabled="syncing[source.repo_key] || readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">{{ syncing[source.repo_key] ? '同步中...' : '立即同步' }}</Button>
          <Button variant="outline" size="sm" class="h-7 text-xs text-destructive hover:text-destructive" @click="deleteRepoSource(source)" :disabled="deleting[source.repo_key] || readOnly" :title="readOnly ? SHARED_RESOURCE_READ_ONLY_HINT : undefined">{{ deleting[source.repo_key] ? '删除中...' : '删除' }}</Button>
        </div></td>
      </tr></tbody>
    </table>
  </div>
</template>
