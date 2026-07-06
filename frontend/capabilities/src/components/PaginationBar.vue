<script setup lang="ts">
import { computed } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { Button } from './ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { clampPage, pageCount } from '../lib/pagination'

const props = defineProps<{
  total: number
  page: number
  pageSize: number
  pageSizeOptions: readonly number[]
}>()

const emit = defineEmits<{
  (e: 'update:page', value: number): void
  (e: 'update:pageSize', value: number): void
}>()

const count = computed(() => pageCount(props.total, props.pageSize))
const safePage = computed(() => clampPage(props.page, props.total, props.pageSize))
const first = computed(() => props.total === 0 ? 0 : (safePage.value - 1) * props.pageSize + 1)
const last = computed(() => Math.min(props.total, safePage.value * props.pageSize))
const pageSizeValue = computed({
  get: () => String(props.pageSize),
  set: value => {
    emit('update:pageSize', Number(value))
    emit('update:page', 1)
  },
})

function go(page: number) {
  emit('update:page', clampPage(page, props.total, props.pageSize))
}
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
    <span>共 {{ total }} 条<span v-if="total">，显示 {{ first }}-{{ last }}</span></span>
    <div class="flex items-center gap-2">
      <span class="text-xs">每页</span>
      <Select v-model="pageSizeValue">
        <SelectTrigger class="h-8 w-[86px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem v-for="size in pageSizeOptions" :key="size" :value="String(size)">{{ size }}</SelectItem>
        </SelectContent>
      </Select>
      <Button variant="outline" size="sm" class="h-8 px-2" :disabled="safePage <= 1" @click="go(safePage - 1)">
        <ChevronLeft :size="14" />
      </Button>
      <span class="min-w-[72px] text-center text-xs tabular-nums">{{ safePage }} / {{ count }}</span>
      <Button variant="outline" size="sm" class="h-8 px-2" :disabled="safePage >= count" @click="go(safePage + 1)">
        <ChevronRight :size="14" />
      </Button>
    </div>
  </div>
</template>
