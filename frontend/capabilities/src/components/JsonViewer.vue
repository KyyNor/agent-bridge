<script setup lang="ts">
import { computed } from 'vue'
import { formatJsonValue, tokenizeJson } from '../lib/jsonDisplay'

const props = withDefaults(defineProps<{
  value: unknown
  maxHeight?: string
}>(), {
  maxHeight: '240px',
})

const formatted = computed(() => formatJsonValue(props.value))
const tokens = computed(() => tokenizeJson(formatted.value))
</script>

<template>
  <pre
    class="json-viewer"
    :style="{ maxHeight }"
  ><span
    v-for="(token, index) in tokens"
    :key="index"
    :class="'json-token json-' + token.type"
  >{{ token.text }}</span></pre>
</template>

<style scoped>
.json-viewer {
  overflow-x: hidden;
  overflow-y: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  border-radius: 0.5rem;
  background: var(--secondary);
  padding: 0.75rem 1rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.65;
  color: var(--foreground);
}

.json-key { color: oklch(0.48 0.16 255); }
.json-string { color: oklch(0.43 0.13 155); }
.json-number { color: oklch(0.49 0.15 28); }
.json-boolean { color: oklch(0.48 0.17 300); }
.json-null { color: var(--muted-foreground); }
.json-punctuation { color: oklch(0.52 0.12 54); font-weight: 600; }
</style>
