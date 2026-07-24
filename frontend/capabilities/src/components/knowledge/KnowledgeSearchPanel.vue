<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import type { KnowledgeBaseSummary, SearchResultChunk } from '../../api/types'
import { Button } from '../ui/button'
import { Input } from '../ui/input'

const props = defineProps<{ kb: KnowledgeBaseSummary | null }>()

const searchQuery = ref('')
const searchResults = ref<SearchResultChunk[]>([])
const searching = ref(false)
const question = ref('')
const answer = ref('')
const answerChunks = ref<SearchResultChunk[]>([])
const sessionId = ref<string | null>(null)
const asking = ref(false)

function reset() {
  searchQuery.value = ''
  searchResults.value = []
  question.value = ''
  answer.value = ''
  answerChunks.value = []
  sessionId.value = null
}

async function search() {
  const kb = props.kb
  if (!kb || !searchQuery.value.trim()) return
  searching.value = true
  try {
    searchResults.value = (await api.search(kb.slug, searchQuery.value.trim())).results
  } catch {
    searchResults.value = []
  } finally {
    searching.value = false
  }
}

async function ask() {
  const kb = props.kb
  if (!kb || !question.value.trim()) return
  asking.value = true
  try {
    const result = await api.ask({ kb: kb.slug, question: question.value.trim(), session_id: sessionId.value || undefined })
    answer.value = result.answer
    answerChunks.value = result.chunks
    sessionId.value = result.session_id
  } catch {
    answer.value = '问答失败'
    answerChunks.value = []
  } finally {
    asking.value = false
  }
}

watch(() => props.kb?.slug, reset)
</script>

<template>
  <div class="space-y-4">
    <div class="space-y-2">
      <h4 class="text-sm font-medium">检索</h4>
      <div class="flex gap-2">
        <Input v-model="searchQuery" placeholder="输入检索关键词" class="flex-1" @keydown.enter="search" />
        <Button size="sm" @click="search" :disabled="searching || !searchQuery.trim()">{{ searching ? '搜索中...' : '搜索' }}</Button>
      </div>
      <div v-if="searchResults.length > 0" class="space-y-2">
        <div v-for="(chunk, index) in searchResults" :key="index" class="rounded-lg border border-border p-3">
          <div class="mb-1 text-xs text-muted-foreground">{{ chunk.document_name }} · 相似度 {{ (chunk.similarity * 100).toFixed(1) }}%</div>
          <div class="text-sm whitespace-pre-wrap">{{ chunk.content }}</div>
        </div>
      </div>
    </div>
    <hr class="border-border" />
    <div class="space-y-2">
      <h4 class="text-sm font-medium">问答</h4>
      <div class="flex gap-2">
        <Input v-model="question" placeholder="输入问题" class="flex-1" @keydown.enter="ask" />
        <Button size="sm" @click="ask" :disabled="asking || !question.trim()">{{ asking ? '思考中...' : '提问' }}</Button>
      </div>
      <div v-if="answer" class="rounded-lg border border-border bg-secondary/30 p-4"><div class="text-sm whitespace-pre-wrap">{{ answer }}</div></div>
      <div v-if="answerChunks.length > 0" class="space-y-1">
        <div class="text-xs text-muted-foreground">引用 ({{ answerChunks.length }})</div>
        <div v-for="(chunk, index) in answerChunks" :key="index" class="rounded border border-border/60 p-2 text-xs text-muted-foreground">{{ chunk.document_name }}: {{ chunk.content.slice(0, 100) }}...</div>
      </div>
    </div>
  </div>
</template>
