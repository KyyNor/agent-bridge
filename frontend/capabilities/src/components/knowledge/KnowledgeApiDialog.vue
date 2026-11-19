<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import { saveBlob } from '../../lib/download'
import SegmentedTabs from '../SegmentedTabs.vue'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog'

const props = defineProps<{ open: boolean; kbSlug: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const keyStatus = ref<{ has_key: boolean; masked_key: string; updated_at: string | null } | null>(null)
const revealedKey = ref('')
const specification = ref<Record<string, unknown> | null>(null)
const error = ref('')
const schemaError = ref('')
const copyError = ref('')
const busy = ref(false)
const copied = ref<'key' | 'command' | null>(null)
const confirmingReset = ref(false)
const selectedExample = ref('list')
let generation = 0
const endpoint = computed(() => `${window.location.origin}/api/v1/knowledge-api/kbs/${encodeURIComponent(props.kbSlug)}/documents`)
const examples = computed(() => [
  { key: 'list', label: '列出', command: `curl '${endpoint.value}?limit=50&offset=0' -H "Authorization: Bearer $AGB_API_KEY"`, hint: '返回当前知识库的文档及同步状态。' },
  { key: 'upload', label: '上传', command: `curl -X POST '${endpoint.value}' -H "Authorization: Bearer $AGB_API_KEY" -F 'file=@./document.pdf'`, hint: '上传单文件最多 32 MiB，接收后按知识库同步策略处理。' },
  { key: 'delete', label: '删除', command: `curl -X DELETE '${endpoint.value}/DOCUMENT_SLUG' -H "Authorization: Bearer $AGB_API_KEY"`, hint: '将 DOCUMENT_SLUG 替换为文档标识。删除只影响当前知识库。' },
])
const example = computed(() => examples.value.find(item => item.key === selectedExample.value))
watch(selectedExample, () => {
  if (copied.value === 'command') copied.value = null
  copyError.value = ''
})
watch(() => [props.open, props.kbSlug] as const, async ([open]) => {
  const request = ++generation
  revealedKey.value = ''
  confirmingReset.value = false
  copied.value = null
  selectedExample.value = 'list'
  keyStatus.value = null
  specification.value = null
  error.value = ''
  schemaError.value = ''
  copyError.value = ''
  busy.value = open
  if (!open) return
  const [status, schema] = await Promise.allSettled([api.getPersonalApiKey(), api.getKnowledgeOpenApi(props.kbSlug)])
  if (request !== generation) return
  if (status.status === 'fulfilled') keyStatus.value = status.value
  else error.value = status.reason instanceof Error ? status.reason.message : '个人 Key 加载失败'
  if (schema.status === 'fulfilled') specification.value = schema.value
  else schemaError.value = schema.reason instanceof Error ? schema.reason.message : 'OpenAPI 定义加载失败'
  busy.value = false
})
async function resetKey() {
  const request = generation
  busy.value = true
  error.value = ''
  try {
    const result = await api.resetPersonalApiKey()
    if (request !== generation) return
    revealedKey.value = result.api_key
    keyStatus.value = result
    confirmingReset.value = false
    copied.value = null
  } catch (e) { if (request === generation) error.value = e instanceof Error ? e.message : '生成失败' }
  finally { if (request === generation) busy.value = false }
}
async function copy(text: string, target: 'key' | 'command') {
  const request = generation
  const tab = selectedExample.value
  copyError.value = ''
  try {
    await navigator.clipboard.writeText(text)
    if (request === generation && (target === 'key' || tab === selectedExample.value)) copied.value = target
  } catch {
    if (request === generation) copyError.value = '自动复制不可用，请选中文本手动复制。'
  }
}
function downloadSchema() {
  if (!specification.value) return
  saveBlob(new Blob([JSON.stringify(specification.value, null, 2)], { type: 'application/json' }), `${props.kbSlug}.openapi.json`)
}
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="max-h-[90vh] w-[calc(100%-2rem)] grid-cols-[minmax(0,1fr)] overflow-y-auto sm:max-w-3xl">
      <DialogHeader class="pr-7">
        <DialogTitle>知识库 API 接入</DialogTitle>
        <DialogDescription class="[overflow-wrap:anywhere]">{{ kbSlug }}</DialogDescription>
      </DialogHeader>
      <section class="min-w-0 space-y-3 rounded-md border p-3 sm:p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="min-w-0">
            <h3 class="font-semibold">个人 API Key</h3>
            <p v-if="keyStatus" class="text-sm text-muted-foreground [overflow-wrap:anywhere]">
              {{ keyStatus.has_key ? keyStatus.masked_key : '尚未生成' }}
            </p>
          </div>
          <Button v-if="!confirmingReset" :disabled="busy || !keyStatus" size="sm" @click="keyStatus?.has_key ? confirmingReset = true : resetKey()">{{ keyStatus?.has_key ? '重置 Key' : '生成 Key' }}</Button>
        </div>
        <p v-if="error" role="alert" class="text-sm text-destructive [overflow-wrap:anywhere]">{{ error }}</p>
        <template v-if="revealedKey">
          <p class="text-sm text-muted-foreground">完整 Key 仅本次显示，请妥善保存。</p>
          <pre aria-label="新生成的 API Key" class="whitespace-pre-wrap rounded border bg-background p-2 font-mono text-sm [overflow-wrap:anywhere]">{{ revealedKey }}</pre>
          <Button size="sm" variant="outline" @click="copy(revealedKey, 'key')">{{ copied === 'key' ? '已复制' : '复制 Key' }}</Button>
        </template>
        <div v-if="confirmingReset" class="space-y-2">
          <p class="text-sm text-destructive">重置后，其他程序正在使用的旧 Key 将立即失效。</p>
          <Button size="sm" :disabled="busy" @click="resetKey">确认重置</Button>
          <Button size="sm" variant="ghost" :disabled="busy" @click="confirmingReset = false">取消</Button>
        </div>
      </section>
      <section class="min-w-0 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="font-semibold">调用示例</h3>
          <Button variant="outline" size="sm" :disabled="!specification" @click="downloadSchema">下载 OpenAPI JSON</Button>
        </div>
        <p class="text-sm text-muted-foreground">将个人 Key 写入环境变量 <code>AGB_API_KEY</code>。各知识库按你的当前权限处理请求。</p>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <SegmentedTabs v-model="selectedExample" :tabs="examples" />
          <Button v-if="example" variant="ghost" size="sm" @click="copy(example.command, 'command')">{{ copied === 'command' ? '已复制' : '复制命令' }}</Button>
        </div>
        <template v-if="example">
          <pre class="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs [overflow-wrap:anywhere]">{{ example.command }}</pre>
          <p class="text-xs text-muted-foreground">{{ example.hint }}</p>
        </template>
        <p v-if="copyError" role="status" class="text-sm text-destructive">{{ copyError }}</p>
        <p v-if="schemaError" role="alert" class="text-sm text-destructive [overflow-wrap:anywhere]">{{ schemaError }}</p>
        <details v-if="specification">
          <summary class="cursor-pointer text-sm">查看 OpenAPI 定义</summary>
          <pre class="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs [overflow-wrap:anywhere]">{{ JSON.stringify(specification, null, 2) }}</pre>
        </details>
      </section>
    </DialogContent>
  </Dialog>
</template>
