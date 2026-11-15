<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog'

const props = defineProps<{ open: boolean; kbSlug: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const keyStatus = ref<{ has_key: boolean; masked_key: string; updated_at: string | null } | null>(null)
const revealedKey = ref('')
const specification = ref<Record<string, unknown> | null>(null)
const error = ref('')
const busy = ref(false)
const copied = ref(false)
const confirmingReset = ref(false)
let generation = 0
const endpoint = computed(() => `${window.location.origin}/api/v1/knowledge-api/kbs/${encodeURIComponent(props.kbSlug)}/documents`)
const examples = computed(() => [
  { title: '列出文档', command: `curl '${endpoint.value}?limit=50&offset=0' -H "Authorization: Bearer $AGB_API_KEY"` },
  { title: '新增文档', command: `curl -X POST '${endpoint.value}' -H "Authorization: Bearer $AGB_API_KEY" -F 'file=@./document.pdf'` },
  { title: '删除当前知识库中的文档', command: `curl -X DELETE '${endpoint.value}/DOCUMENT_SLUG' -H "Authorization: Bearer $AGB_API_KEY"` },
])
watch(() => [props.open, props.kbSlug] as const, async ([open]) => {
  const request = ++generation
  revealedKey.value = ''
  confirmingReset.value = false
  copied.value = false
  keyStatus.value = null
  specification.value = null
  error.value = ''
  if (!open) return
  busy.value = true
  try {
    const [status, schema] = await Promise.all([api.getPersonalApiKey(), api.getKnowledgeOpenApi(props.kbSlug)])
    if (request !== generation) return
    keyStatus.value = status
    specification.value = schema
  } catch (e) {
    if (request === generation) error.value = e instanceof Error ? e.message : '加载失败'
  } finally { if (request === generation) busy.value = false }
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
    copied.value = false
  } catch (e) { if (request === generation) error.value = e instanceof Error ? e.message : '生成失败' }
  finally { if (request === generation) busy.value = false }
}
async function copyKey() {
  try {
    await navigator.clipboard.writeText(revealedKey.value)
    copied.value = true
  } catch { error.value = '自动复制不可用，请选中下方 Key 手动复制' }
}
function downloadSchema() {
  if (!specification.value) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(specification.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${props.kbSlug}.openapi.json`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>知识库 API 接入 · {{ kbSlug }}</DialogTitle></DialogHeader>
      <p class="text-sm text-muted-foreground">个人 API Key 在所有知识库通用，权限与当前用户一致。接口支持列出、新增和删除文档。</p>
      <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p>
      <section class="space-y-3 rounded-md border p-4">
        <h3 class="font-semibold">我的 API Key</h3>
        <p v-if="keyStatus" class="font-mono text-sm">{{ keyStatus.has_key ? keyStatus.masked_key : '尚未生成' }}</p>
        <template v-if="revealedKey">
          <p class="text-sm text-muted-foreground">完整 Key 仅本次显示，请妥善保存。</p>
          <input :value="revealedKey" readonly aria-label="新生成的 API Key" class="w-full rounded border bg-background p-2 font-mono text-sm" />
          <Button size="sm" variant="outline" @click="copyKey">{{ copied ? '已复制' : '复制 Key' }}</Button>
        </template>
        <div v-if="confirmingReset" class="space-y-2">
          <p class="text-sm text-destructive">重置后，其他程序正在使用的旧 Key 将立即失效。</p>
          <Button size="sm" :disabled="busy" @click="resetKey">确认重置</Button>
          <Button size="sm" variant="ghost" :disabled="busy" @click="confirmingReset = false">取消</Button>
        </div>
        <Button v-else :disabled="busy || !keyStatus" size="sm" @click="keyStatus?.has_key ? confirmingReset = true : resetKey()">{{ keyStatus?.has_key ? '重置 Key' : '生成 Key' }}</Button>
      </section>
      <p class="text-sm text-muted-foreground">示例中的 AGB_API_KEY 环境变量填写你的个人 Key。上传单文件上限 32 MiB，接收成功后按知识库同步策略处理；文档列表包含 status 和 sync_status。删除只影响当前知识库。</p>
      <section v-for="example in examples" :key="example.title" class="space-y-2">
        <h3 class="text-sm font-semibold">{{ example.title }}</h3>
        <pre class="overflow-x-auto rounded-md bg-muted p-3 text-xs">{{ example.command }}</pre>
      </section>
      <p class="text-xs text-muted-foreground">常见响应：200 成功；202 已接收；400 输入无效；401 Key 无效；403 无操作权限；404 资源不存在；413 文件过大；422 参数格式错误。</p>
      <Button variant="outline" :disabled="!specification" @click="downloadSchema">下载 OpenAPI JSON</Button>
      <details v-if="specification"><summary class="cursor-pointer text-sm">查看 OpenAPI 定义</summary><pre class="mt-2 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">{{ JSON.stringify(specification, null, 2) }}</pre></details>
    </DialogContent>
  </Dialog>
</template>
