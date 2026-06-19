<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { python } from '@codemirror/lang-python'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { autocompletion } from '@codemirror/autocomplete'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const container = ref<HTMLDivElement>()
let editor: EditorView | null = null

onMounted(() => {
  if (!container.value) return

  const updateListener = EditorView.updateListener.of((update) => {
    if (update.docChanged) {
      emit('update:modelValue', update.state.doc.toString())
    }
  })

  editor = new EditorView({
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        python(),
        history(),
        syntaxHighlighting(defaultHighlightStyle),
        autocompletion(),
        keymap.of(defaultKeymap),
        keymap.of(historyKeymap),
        EditorView.lineWrapping,
        updateListener,
      ],
    }),
    parent: container.value,
  })
})

watch(
  () => props.modelValue,
  (val) => {
    if (editor && val !== editor.state.doc.toString()) {
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: val },
      })
    }
  },
)

onUnmounted(() => {
  editor?.destroy()
})
</script>

<template>
  <div
    ref="container"
    class="min-h-[58vh] rounded-md border bg-background [&_.cm-editor]:outline-none [&_.cm-editor]:min-h-[58vh] [&_.cm-gutters]:border-r [&_.cm-gutters]:bg-muted/50 [&_.cm-activeLineGutter]:bg-muted"
  />
</template>
