<script setup lang="ts">
import { computed } from 'vue'
import { formatJsonValue, tokenizeJson } from '../lib/jsonDisplay'

const props = withDefaults(defineProps<{
  value: unknown
  maxHeight?: string
  /** 嵌入卡片时移除自身底色和外边距，由父容器提供表面。 */
  density?: 'default' | 'compact'
}>(), {
  maxHeight: '240px',
  density: 'default',
})

const formatted = computed(() => formatJsonValue(props.value))
const tokens = computed(() => tokenizeJson(formatted.value))
</script>

<template>
  <pre
    class="json-viewer"
    :class="{ 'json-viewer-compact': density === 'compact' }"
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

.json-viewer.json-viewer-compact {
  border-radius: 0;
  background: transparent;
  padding: 0.5rem 0.625rem;
  font-size: 0.6875rem;
  line-height: 1.55;
}

.json-key { color: var(--json-key); }
.json-string { color: var(--json-string); }
.json-number { color: var(--json-number); }
.json-boolean { color: var(--json-boolean); }
.json-null { color: var(--muted-foreground); }
.json-punctuation { color: var(--json-punctuation); font-weight: 600; }
</style>
