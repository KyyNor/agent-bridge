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
  border-radius: var(--radius-control);
  background: var(--secondary);
  padding: 0.75rem 1rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.65;
  color: var(--foreground);
}

.json-key { color: var(--json-key); }
.json-string { color: var(--json-string); }
.json-number { color: var(--json-number); }
.json-boolean { color: var(--json-boolean); }
.json-null { color: var(--muted-foreground); }
.json-punctuation { color: var(--json-punctuation); font-weight: 600; }
</style>
