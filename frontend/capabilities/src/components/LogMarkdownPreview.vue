<script setup lang="ts">
import { renderMarkdown } from '../lib/markdown'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'

defineProps<{
  open: boolean
  title: string
  markdown: string
}>()

defineEmits<{
  'update:open': [open: boolean]
}>()
</script>

<template>
  <Dialog :open="open" @update:open="$emit('update:open', $event)">
    <DialogContent class="w-[min(1180px,calc(100vw-2rem))] sm:max-w-[1180px]">
      <DialogHeader><DialogTitle>{{ title }}</DialogTitle></DialogHeader>
      <div class="prose prose-sm max-h-[70vh] max-w-none overflow-y-auto" v-html="renderMarkdown(markdown)" />
    </DialogContent>
  </Dialog>
</template>
