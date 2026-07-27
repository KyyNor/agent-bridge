<script setup lang="ts">
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import PayloadCodeViewer from './PayloadCodeViewer.vue'
import { renderMarkdown } from '../lib/markdown'
import { payloadLanguageLabel, type PayloadLanguage } from '../lib/payloadPresentation'

defineProps<{
  open: boolean
  title: string
  label?: string
  content: string
  language: PayloadLanguage
}>()

defineEmits<{
  (event: 'update:open', open: boolean): void
}>()
</script>

<template>
  <Dialog :open="open" @update:open="$emit('update:open', $event)">
    <DialogContent class="w-[min(1280px,calc(100vw-2rem))] !max-w-[1280px] sm:!max-w-[1280px] max-h-[calc(100vh-2rem)] overflow-hidden">
      <DialogHeader>
        <DialogTitle>{{ title }} · {{ payloadLanguageLabel(language) }}</DialogTitle>
        <div v-if="label" class="text-xs text-muted-foreground">{{ label }}</div>
      </DialogHeader>
      <div class="min-h-0 overflow-auto">
        <div
          v-if="language === 'markdown'"
          class="payload-markdown rounded-md border bg-background p-4"
          v-html="renderMarkdown(content)"
        />
        <PayloadCodeViewer
          v-else
          :content="content"
          :language="language"
        />
      </div>
    </DialogContent>
  </Dialog>
</template>
