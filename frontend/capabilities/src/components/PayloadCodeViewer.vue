<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { html } from '@codemirror/lang-html'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'

const props = withDefaults(defineProps<{
  content: string
  language: 'json' | 'html' | 'python' | 'javascript' | 'text'
  maxHeight?: string
}>(), {
  maxHeight: 'min(68vh, 720px)',
})

const container = ref<HTMLDivElement>()
let editor: EditorView | null = null

// 代码 payload 保留 CodeMirror 的语言解析，但视觉上与 JsonViewer 对齐，
// 使工作流时间线、调用日志和完整查看不再出现两套割裂的阅读体验。
const payloadCodeTheme = EditorView.theme({
  '&': {
    backgroundColor: 'var(--secondary)',
    color: 'var(--foreground)',
    fontFamily: 'var(--font-mono)',
  },
  '.cm-scroller': {
    overflow: 'auto',
    fontFamily: 'var(--font-mono)',
    lineHeight: '1.65',
  },
  '.cm-content': {
    minHeight: '220px',
    padding: '0.75rem 1rem',
    fontSize: '0.75rem',
  },
  '.cm-line': { padding: '0' },
  '.cm-gutters': { display: 'none' },
  '.cm-activeLine': { backgroundColor: 'transparent' },
  '.cm-cursor': { display: 'none' },
}, { dark: false })

const payloadCodeHighlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.tagName, tags.propertyName, tags.attributeName], color: 'var(--json-key)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--json-string)' },
  { tag: [tags.number, tags.integer, tags.float], color: 'var(--json-number)' },
  { tag: [tags.bool, tags.null, tags.atom], color: 'var(--json-boolean)' },
  { tag: [tags.punctuation, tags.bracket, tags.separator], color: 'var(--json-punctuation)' },
  { tag: tags.comment, color: 'var(--muted-foreground)', fontStyle: 'italic' },
])

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
        languageExtension(),
        payloadCodeTheme,
        syntaxHighlighting(payloadCodeHighlight),
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
    class="payload-code-viewer overflow-auto rounded-md bg-secondary [&_.cm-editor]:outline-none [&_.cm-editor]:min-h-[220px] [&_.cm-editor]:bg-secondary [&_.cm-scroller]:overflow-auto"
    :style="{ maxHeight }"
  />
</template>
