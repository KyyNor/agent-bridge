<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { html } from '@codemirror/lang-html'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'

const props = withDefaults(defineProps<{
  content: string
  language: 'json' | 'html' | 'python' | 'javascript' | 'text'
  maxHeight?: string
}>(), {
  maxHeight: 'min(68vh, 720px)',
})

const container = ref<HTMLDivElement>()
let editor: EditorView | null = null

function languageExtension() {
  switch (props.language) {
    case 'json': return json()
    case 'html': return html()
    case 'python': return python()
    case 'javascript': return javascript({ jsx: true, typescript: true })
    default: return []
  }
}

onMounted(() => {
  if (!container.value) return
  editor = new EditorView({
    state: EditorState.create({
      doc: props.content,
      extensions: [
        lineNumbers(),
        languageExtension(),
        syntaxHighlighting(defaultHighlightStyle),
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
      ],
    }),
    parent: container.value,
  })
})

onUnmounted(() => {
  editor?.destroy()
})
</script>

<template>
  <div
    ref="container"
    class="payload-code-viewer overflow-auto rounded-md border bg-background [&_.cm-editor]:outline-none [&_.cm-editor]:min-h-[220px] [&_.cm-editor]:bg-background [&_.cm-scroller]:overflow-auto"
    :style="{ maxHeight }"
  />
</template>
