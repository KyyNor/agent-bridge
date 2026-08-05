<script setup lang="ts">
import { Check, ChevronsUpDown } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Button } from './ui/button'
import { Input } from './ui/input'

export interface SearchableMultiSelectOption {
  value: string
  label: string
  description?: string
}

const props = withDefaults(defineProps<{
  modelValue?: string[]
  options: SearchableMultiSelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  emptyText?: string
}>(), {
  modelValue: () => [],
  placeholder: '请选择',
  searchPlaceholder: '搜索选项',
  emptyText: '没有匹配的选项',
})

const emit = defineEmits<{
  'update:modelValue': [values: string[]]
}>()

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const keyword = ref('')
const selected = computed(() => new Set(props.modelValue))
const filteredOptions = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  if (!query) return props.options
  return props.options.filter(item => `${item.label} ${item.description || ''} ${item.value}`.toLocaleLowerCase().includes(query))
})
const triggerLabel = computed(() => {
  const selectedOptions = props.options.filter(item => selected.value.has(item.value))
  if (!selectedOptions.length) return props.placeholder
  if (selectedOptions.length <= 2) return selectedOptions.map(item => item.label).join('、')
  return `${selectedOptions.slice(0, 2).map(item => item.label).join('、')} 等 ${selectedOptions.length} 项`
})

function toggle(value: string) {
  const next = new Set(props.modelValue)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  emit('update:modelValue', [...next])
}

function toggleOpen() {
  open.value = !open.value
  if (!open.value) keyword.value = ''
}

function closeOnOutsidePointer(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) {
    open.value = false
    keyword.value = ''
  }
}

onMounted(() => document.addEventListener('pointerdown', closeOnOutsidePointer))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOnOutsidePointer))
</script>

<template>
  <div ref="root" class="relative">
    <Button
      type="button"
      variant="outline"
      class="w-full justify-between gap-2 font-normal"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggleOpen"
    >
      <span class="min-w-0 truncate text-left" :class="{ 'text-muted-foreground': !modelValue?.length }">{{ triggerLabel }}</span>
      <ChevronsUpDown :size="15" class="shrink-0 text-muted-foreground" />
    </Button>
    <div v-if="open" class="cn-menu-translucent absolute z-30 mt-1 w-full overflow-hidden rounded-md border border-border shadow-md">
      <div class="border-b border-border p-2">
        <Input v-model="keyword" :placeholder="searchPlaceholder" autofocus @keydown.esc="toggleOpen" />
      </div>
      <div role="listbox" aria-multiselectable="true" class="max-h-56 overflow-y-auto p-1">
        <button
          v-for="item in filteredOptions"
          :key="item.value"
          type="button"
          role="option"
          :aria-selected="selected.has(item.value)"
          class="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground"
          @click="toggle(item.value)"
        >
          <span class="flex size-4 shrink-0 items-center justify-center rounded-sm border border-input" :class="{ 'border-primary bg-primary text-primary-foreground': selected.has(item.value) }">
            <Check v-if="selected.has(item.value)" :size="12" />
          </span>
          <span class="min-w-0 flex-1">
            <span class="block truncate">{{ item.label }}</span>
            <span v-if="item.description" class="block truncate text-xs text-muted-foreground">{{ item.description }}</span>
          </span>
        </button>
        <p v-if="!filteredOptions.length" class="px-2 py-5 text-center text-sm text-muted-foreground">{{ emptyText }}</p>
      </div>
    </div>
  </div>
</template>
