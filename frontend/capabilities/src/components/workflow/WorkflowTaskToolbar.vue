<script setup lang="ts">
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select'

defineProps<{
  searchInput: string
  status: string
  type: string
  hasArtifacts: string
  sort: string
  statuses: string[]
  types: string[]
  statusCounts: Record<string, number>
  statusLabel: (status: string) => string
  showReset: boolean
  visibleTaskCount: number
  allVisibleSelected: boolean
  someVisibleSelected: boolean
  selectedCount: number
  batchBusy: boolean
  batchAction: 'reset' | 'run' | ''
  batchCurrent: number
  batchTotal: number
  stopRequested: boolean
  filteredCount: number
  totalCount: number
  hasWorkflow: boolean
  loading: boolean
}>()

const emit = defineEmits([
  'update:searchInput', 'update:status', 'update:type', 'update:hasArtifacts', 'update:sort',
  'search', 'resetFilters', 'selectVisible', 'resetSelected', 'runSelected',
  'stopBatch', 'downloadTemplate', 'import', 'refresh',
])
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <Input :model-value="searchInput" type="search" placeholder="搜索 task_key / 类型" class="h-8 w-56 text-xs" @update:model-value="emit('update:searchInput', $event)" @input="emit('search')" />
    <Select :model-value="status" @update:model-value="emit('update:status', $event)">
      <SelectTrigger class="h-8 w-[140px] text-xs"><SelectValue placeholder="全部状态" /></SelectTrigger>
      <SelectContent><SelectItem value="__all_status__">全部状态</SelectItem><SelectItem v-for="item in statuses" :key="item" :value="item">{{ statusLabel(item) }} {{ statusCounts[item] || 0 }}</SelectItem></SelectContent>
    </Select>
    <Select :model-value="type" @update:model-value="emit('update:type', $event)">
      <SelectTrigger class="h-8 w-[140px] text-xs"><SelectValue placeholder="全部类型" /></SelectTrigger>
      <SelectContent><SelectItem value="__all__">全部类型</SelectItem><SelectItem v-for="item in types" :key="item" :value="item">{{ item }}</SelectItem></SelectContent>
    </Select>
    <Select :model-value="hasArtifacts" @update:model-value="emit('update:hasArtifacts', $event)">
      <SelectTrigger class="h-8 w-[120px] text-xs"><SelectValue placeholder="产物" /></SelectTrigger>
      <SelectContent><SelectItem value="__all_artifacts__">全部产物</SelectItem><SelectItem value="with">有产物</SelectItem><SelectItem value="without">无产物</SelectItem></SelectContent>
    </Select>
    <Select :model-value="sort" @update:model-value="emit('update:sort', $event)">
      <SelectTrigger class="h-8 w-[150px] text-xs"><SelectValue placeholder="排序" /></SelectTrigger>
      <SelectContent><SelectItem value="default">默认（状态优先）</SelectItem><SelectItem value="task_key_asc">task_key ↑</SelectItem><SelectItem value="task_key_desc">task_key ↓</SelectItem><SelectItem value="set_at_asc">设置时间 ↑</SelectItem><SelectItem value="set_at_desc">设置时间 ↓</SelectItem><SelectItem value="updated_at_desc">最近更新</SelectItem></SelectContent>
    </Select>
    <Button v-if="showReset" variant="ghost" size="sm" class="h-8 text-xs" @click="emit('resetFilters')">重置筛选</Button>
    <div class="ml-auto flex items-center gap-3">
      <label v-if="visibleTaskCount" class="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" :checked="allVisibleSelected" :indeterminate.prop="someVisibleSelected && !allVisibleSelected" :disabled="batchBusy" @change="emit('selectVisible', $event)" />本页全选</label>
      <span v-if="selectedCount" class="text-xs text-primary">已选 {{ selectedCount }}</span>
      <Button v-if="selectedCount" variant="outline" size="sm" class="h-8 text-xs text-warning" :disabled="batchBusy" @click="emit('resetSelected')">{{ batchAction === 'reset' ? `重置中 ${batchCurrent}/${batchTotal}` : '批量重置' }}</Button>
      <Button v-if="selectedCount" variant="outline" size="sm" class="h-8 text-xs text-primary" :disabled="batchBusy" @click="emit('runSelected')">{{ batchAction === 'run' ? `运行中 ${batchCurrent}/${batchTotal}` : '批量运行' }}</Button>
      <Button v-if="batchAction === 'run'" variant="outline" size="sm" class="h-8 text-xs text-destructive" :disabled="stopRequested" @click="emit('stopBatch')">{{ stopRequested ? '停止中' : '停止批量' }}</Button>
      <span class="text-xs text-muted-foreground">{{ filteredCount }} / {{ totalCount }}</span>
      <Button variant="outline" size="sm" class="h-8 text-xs" :disabled="!hasWorkflow || batchBusy" @click="emit('downloadTemplate')">下载模板</Button>
      <Button size="sm" class="h-8 text-xs" :disabled="!hasWorkflow || batchBusy" @click="emit('import')">导入 Excel</Button>
      <Button variant="outline" size="sm" class="h-8 text-xs" :disabled="loading || !hasWorkflow" @click="emit('refresh')">{{ loading ? '刷新中' : '刷新' }}</Button>
    </div>
  </div>
</template>
