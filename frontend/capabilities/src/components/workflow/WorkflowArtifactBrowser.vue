<script setup lang="ts">
import type { WorkflowArtifact } from '../../api/types'
import type { ArtifactTreeRow } from '../../lib/workflowArtifactTree'
import { artifactFormatBadgeClass, artifactFormatLabel, artifactFormatOptions } from '../../lib/workflowArtifactFormats'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import PaginationBar from '../PaginationBar.vue'
import { CodeXml, FileText } from '@lucide/vue'

defineProps<{
  query: string
  pathMatch: string
  format: 'all' | 'markdown' | 'html'
  loading: boolean
  error: string
  rows: ArtifactTreeRow[]
  collapsedPaths: Set<string>
  total: number
  page: number
  pageSize: number
  pageSizeOptions: readonly number[]
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  'update:pathMatch': [value: string]
  'update:format': [value: 'all' | 'markdown' | 'html']
  'update:page': [value: number]
  'update:pageSize': [value: number]
  search: []
  toggleFolder: [path: string]
  open: [artifact: WorkflowArtifact]
  history: [artifact: WorkflowArtifact]
}>()

function updateQuery(value: string | number) { emit('update:query', String(value)) }
function updatePathMatch(value: string | number) { emit('update:pathMatch', String(value)) }
</script>

<template>
  <section class="space-y-4 rounded-lg border border-border bg-card p-4 shadow-card">
    <div class="flex flex-wrap items-end gap-3">
      <div class="min-w-[220px] flex-1">
        <label class="mb-1 block text-xs text-muted-foreground">检索</label>
        <Input :model-value="query" placeholder="标题、摘要、路径" @update:model-value="updateQuery" @keyup.enter="emit('search')" />
      </div>
      <div class="min-w-[180px] flex-1">
        <label class="mb-1 block text-xs text-muted-foreground">路径匹配</label>
        <Input :model-value="pathMatch" placeholder="task key 或产物路径" @update:model-value="updatePathMatch" @keyup.enter="emit('search')" />
      </div>
      <div>
        <label class="mb-1 block text-xs text-muted-foreground">格式</label>
        <div class="flex rounded-md border bg-background p-0.5" role="group" aria-label="产物格式筛选">
          <Button
            v-for="option in artifactFormatOptions"
            :key="option.value"
            type="button"
            size="sm"
            :variant="format === option.value ? 'secondary' : 'ghost'"
            class="h-7 px-2 text-xs"
            @click="emit('update:format', option.value)"
          >{{ option.label }}</Button>
        </div>
      </div>
      <Button :disabled="loading" @click="emit('search')">{{ loading ? '检索中' : '检索产物' }}</Button>
    </div>
    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">{{ error }}</div>
    <div v-if="!rows.length" class="rounded-md border px-4 py-8 text-sm text-muted-foreground">暂无产物</div>
    <div class="space-y-1.5">
      <template v-for="row in rows" :key="row.type + ':' + row.path">
        <button
          v-if="row.type === 'folder'"
          class="list-row-interactive flex w-full items-center gap-1.5 rounded-md py-1 text-left text-xs font-semibold uppercase text-muted-foreground hover:text-foreground"
          :style="{ paddingLeft: `${row.depth * 16 + 8}px` }"
          @click="emit('toggleFolder', row.path)"
        >
          <span>{{ collapsedPaths.has(row.path) ? '▸' : '▾' }}</span>
          <span>{{ row.segment }}/</span>
          <span class="font-normal normal-case text-muted-foreground/70">({{ row.count }})</span>
        </button>
        <div v-else-if="row.artifact" class="rounded-md border p-3" :style="{ marginLeft: `${row.depth * 16}px` }">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div class="text-sm font-medium text-foreground">{{ row.artifact.title }}</div>
              <div class="mt-1 text-xs text-muted-foreground">{{ row.artifact.path }}</div>
              <div v-if="row.artifact.task_version" class="mt-1 text-xs text-muted-foreground">version: <span class="font-mono">{{ row.artifact.task_version }}</span></div>
            </div>
            <div class="flex flex-wrap items-center gap-1">
              <Badge v-if="row.artifact.is_current" variant="outline">current</Badge>
              <Badge
                class="text-xs"
                :class="artifactFormatBadgeClass(row.artifact.format === 'html' ? 'html' : 'markdown')"
              >
                <CodeXml v-if="row.artifact.format === 'html'" />
                <FileText v-else />
                {{ artifactFormatLabel(row.artifact.format === 'html' ? 'html' : 'markdown') }}
              </Badge>
              <Badge v-for="tag in row.artifact.tags || []" :key="tag" variant="outline">{{ tag }}</Badge>
              <Button v-if="row.artifact.task_key" variant="ghost" size="sm" class="h-7 text-xs" @click="emit('history', row.artifact)">历史</Button>
              <Button variant="ghost" size="sm" class="h-7 text-xs" @click="emit('open', row.artifact)">查看</Button>
            </div>
          </div>
          <p class="mt-2 text-sm text-muted-foreground">{{ row.artifact.summary || row.artifact.snippet }}</p>
        </div>
      </template>
    </div>
    <PaginationBar
      v-if="total"
      :page="page"
      :page-size="pageSize"
      :total="total"
      :page-size-options="pageSizeOptions"
      @update:page="emit('update:page', $event)"
      @update:page-size="emit('update:pageSize', $event)"
    />
  </section>
</template>
