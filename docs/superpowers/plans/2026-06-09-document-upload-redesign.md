# 文档知识上传交互重设计 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将文档上传从原生 file input 替换为拖拽上传对话框，支持多文件/文件夹选择，移除「稍后同步」选项，新增知识同步定时器。

**Architecture:** 前端仅在 Vue 组件内修改，不引入新依赖。拖拽上传使用浏览器原生 Drag & Drop API + `<input webkitdirectory>` 实现文件夹选择。上传对话框和 KB 列表上传按钮均内联在 KnowledgeView.vue 中。

**Tech Stack:** Vue 3 + TypeScript + shadcn-vue UI 组件 + 浏览器原生 API

---

### Task 1: 更新类型定义

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts:273-277` (KnowledgeSyncConfig)
- Modify: `frontend/capabilities/src/api/types.ts:279-292` (SchedulerStatus)

- [ ] **Step 1: 在 KnowledgeSyncConfig 中新增 doc_sync_cron**

```typescript
export interface KnowledgeSyncConfig {
  code_sync_cron: string
  ua_git_url: string
  understand_cron: string
  doc_sync_cron: string
}
```

- [ ] **Step 2: 在 SchedulerStatus 中新增 doc_sync**

```typescript
export interface SchedulerStatus {
  code_sync: SingleSchedulerStatus
  understand: SingleSchedulerStatus
  doc_sync: SingleSchedulerStatus
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/capabilities/src/api/types.ts
git commit -m "feat: add doc_sync_cron and doc_sync to KnowledgeSyncConfig and SchedulerStatus types

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 重写 KnowledgeView.vue 上传区域

**Files:**
- Modify: `frontend/capabilities/src/views/KnowledgeView.vue`

这个任务将完成以下变更：
- 移除旧的 `uploadFile`、`uploadLater`、`uploading` 单文件上传状态
- 新增上传对话框状态：`showUploadDialog`、`uploadKb`、`uploadFiles`、`uploading`
- 替换 `<script>` 中的 `onFileSelected` 和 `uploadDocument` 函数
- 在 KB 表格每行「详情」按钮前加「上传」按钮
- 新增上传对话框（拖拽区 + 文件列表 + 文件夹选择）

- [ ] **Step 1: 替换 script 中的上传相关状态和函数**

删除第 27-30 行：
```typescript
const uploadFile = ref<File | null>(null)
const uploadLater = ref(false)
const uploading = ref(false)
```

在 `const showPlaneDialog = ref(false)` 之后（约第 52 行后）新增：
```typescript
// 上传对话框
const showUploadDialog = ref(false)
const uploadKb = ref<KnowledgeBaseSummary | null>(null)
const uploadFiles = ref<File[]>([])
const uploading = ref(false)
const uploadDragOver = ref(false)
```

删除第 114-124 行的 `uploadDocument` 函数，替换为：
```typescript
function onUploadFilesSelected(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    for (let i = 0; i < target.files.length; i++) {
      uploadFiles.value.push(target.files[i])
    }
  }
  target.value = ''
}

function handleUploadDragOver(e: DragEvent) {
  e.preventDefault()
  uploadDragOver.value = true
}

function handleUploadDragLeave() {
  uploadDragOver.value = false
}

function handleUploadDrop(e: DragEvent) {
  e.preventDefault()
  uploadDragOver.value = false
  if (!e.dataTransfer) return
  addFilesFromDataTransfer(e.dataTransfer)
}

function addFilesFromDataTransfer(dt: DataTransfer) {
  const allowed = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md']
  const entries: FileSystemEntry[] = []
  for (let i = 0; i < dt.items.length; i++) {
    const entry = dt.items[i].webkitGetAsEntry()
    if (entry) entries.push(entry)
  }
  if (entries.length === 0) {
    for (let i = 0; i < dt.files.length; i++) {
      const f = dt.files[i]
      const ext = '.' + f.name.split('.').pop()?.toLowerCase()
      if (allowed.includes(ext)) uploadFiles.value.push(f)
    }
    return
  }
  entries.forEach(entry => traverseEntry(entry, allowed))
}

function traverseEntry(entry: FileSystemEntry, allowed: string[]) {
  if (entry.isFile) {
    const ext = '.' + entry.name.split('.').pop()?.toLowerCase()
    if (!allowed.includes(ext)) return
    ;(entry as FileSystemFileEntry).file(f => uploadFiles.value.push(f))
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    reader.readEntries(entries => entries.forEach(e => traverseEntry(e, allowed)))
  }
}

function removeUploadFile(index: number) {
  uploadFiles.value.splice(index, 1)
}

function openUploadDialog(kb: KnowledgeBaseSummary) {
  uploadKb.value = kb
  uploadFiles.value = []
  showUploadDialog.value = true
}

async function uploadDocuments() {
  if (!uploadKb.value || uploadFiles.value.length === 0) return
  uploading.value = true
  try {
    for (const file of uploadFiles.value) {
      await api.addDocument(file, [uploadKb.value.slug], true)
    }
    showUploadDialog.value = false
    uploadFiles.value = []
    await loadKbs()
  } catch { /* ignore */ }
  uploading.value = false
}

function getFileSizeLabel(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
```

删除第 183-186 行的 `onFileSelected` 函数。

- [ ] **Step 2: 在 KB 表格每行操作区加「上传」按钮**

将 template 中第 282-286 行的操作区替换为：
```html
<td class="px-4 py-3">
  <div class="flex gap-2">
    <Button size="sm" @click="openUploadDialog(k)" class="h-8 text-xs">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      上传
    </Button>
    <Button variant="outline" size="sm" @click="openDetail(k)" class="h-8 text-xs">详情</Button>
    <Button variant="outline" size="sm" @click="openPlaneDialog(k)" class="h-8 text-xs">能力平面</Button>
  </div>
</td>
```

- [ ] **Step 3: 移除详情弹窗中旧的文档上传区域**

在详情弹窗的「文档」Tab 中（第 372-378 行），删除整个旧的：
```html
<div class="flex items-center gap-3">
  <input type="file" @change="onFileSelected" class="text-sm" accept="..." />
  <label class="flex items-center gap-1.5 text-sm text-muted-foreground">
    <input type="checkbox" v-model="uploadLater" /> 稍后同步
  </label>
  <Button size="sm" @click="uploadDocument" :disabled="!uploadFile || uploading">...</Button>
</div>
```

替换为一个简单的提示文本或什么也不放（因为上传入口现在在行按钮上）：
```html
<div class="text-xs text-muted-foreground">点击知识库列表中的「上传」按钮添加文档，上传后由定时任务自动同步</div>
```

- [ ] **Step 4: 在模板末尾（`</template>` 前）添加上传对话框**

在详情弹窗的 `</Dialog>` 之后、评分平面弹窗之前，添加：
```html
<!-- 上传文档对话框 -->
<Dialog :open="showUploadDialog" @update:open="showUploadDialog = $event">
  <DialogContent class="sm:max-w-[520px]">
    <DialogHeader>
      <DialogTitle>上传文档 — {{ uploadKb?.name || '' }}</DialogTitle>
    </DialogHeader>
    <div class="space-y-4">
      <div class="text-xs text-muted-foreground">
        目标知识库：<span class="font-medium text-foreground">{{ uploadKb?.name }}</span>
        <span class="font-mono ml-1">({{ uploadKb?.slug }})</span>
      </div>

      <!-- 拖拽区域 / 文件列表 -->
      <div v-if="uploadFiles.length === 0"
        :class="['rounded-lg border-2 border-dashed p-10 text-center transition-colors cursor-pointer',
          uploadDragOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/20']"
        @dragover="handleUploadDragOver"
        @dragleave="handleUploadDragLeave"
        @drop="handleUploadDrop"
      >
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="1.5" class="mx-auto mb-3"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <div class="text-sm font-medium mb-1">拖拽文件或文件夹到此处</div>
        <div class="text-xs text-muted-foreground mb-4">支持 PDF、Word、Excel、PPT、TXT、Markdown — 上传后将由定时任务自动同步</div>
        <div class="flex items-center justify-center gap-3">
          <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm bg-primary text-primary-foreground text-sm font-medium cursor-pointer hover:bg-primary/80">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            选择文件
            <input type="file" multiple class="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md" @change="onUploadFilesSelected" />
          </label>
          <label class="inline-flex items-center gap-1.5 h-8 px-3 rounded-sm border border-border bg-background text-sm font-medium cursor-pointer hover:bg-muted">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            选择文件夹
            <input type="file" multiple webkitdirectory class="hidden" @change="onUploadFilesSelected" />
          </label>
        </div>
      </div>

      <!-- 文件列表 -->
      <div v-else class="rounded-lg border-2 border-green-200 bg-muted/20 p-4">
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm font-medium">已选择 <span class="text-green-700">{{ uploadFiles.length }}</span> 个文件</span>
          <Button variant="ghost" size="xs" class="h-7 text-xs text-muted-foreground" @click="uploadFiles = []">清除</Button>
        </div>
        <div class="space-y-1.5 max-h-[240px] overflow-y-auto">
          <div v-for="(f, i) in uploadFiles" :key="i"
            class="flex items-center gap-2.5 px-3 py-2 rounded border border-border bg-background text-sm"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="flex-1 truncate">{{ f.name }}</span>
            <span class="text-xs text-muted-foreground shrink-0">{{ getFileSizeLabel(f.size) }}</span>
          </div>
        </div>
        <label class="block mt-3 py-2 border border-dashed border-border rounded text-center text-xs text-muted-foreground cursor-pointer hover:bg-muted/50 transition-colors">
          + 继续添加文件
          <input type="file" multiple class="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md" @change="onUploadFilesSelected" />
        </label>
      </div>
    </div>
    <DialogFooter>
      <DialogClose as-child><Button variant="outline">取消</Button></DialogClose>
      <Button @click="uploadDocuments" :disabled="uploadFiles.length === 0 || uploading">
        {{ uploading ? '上传中...' : `上传 (${uploadFiles.length})` }}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

- [ ] **Step 5: 验证 TypeScript 没有类型错误**

```bash
cd frontend/capabilities && npx vue-tsc --noEmit 2>&1 | head -30
```

期望：没有新增的类型错误。

- [ ] **Step 6: 提交**

```bash
git add frontend/capabilities/src/views/KnowledgeView.vue
git commit -m "feat: replace native file input with drag-and-drop upload dialog, remove sync-later toggle

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 在知识处理配置中新增知识同步定时器

**Files:**
- Modify: `frontend/capabilities/src/views/KnowledgeProcessingConfigView.vue`

- [ ] **Step 1: 新增 computed 变量 for doc_sync cron**

在 `const understandNextRuns = computed(...)` 之后（约第 90 行）添加：
```typescript
const docSyncNextRuns = computed(() => formatNextRuns(syncConfig.value.doc_sync_cron || '*/30 * * * *'))
```

- [ ] **Step 2: 更新 cronValid 计算属性**

将第 91 行：
```typescript
const cronValid = computed(() => codeSyncNextRuns.value !== null && understandNextRuns.value !== null)
```
替换为：
```typescript
const cronValid = computed(() => codeSyncNextRuns.value !== null && understandNextRuns.value !== null && docSyncNextRuns.value !== null)
```

- [ ] **Step 3: 在 template 定时任务管理卡片中新增知识同步行**

在「代码理解」行之后（第 221 行 `</div>` 之后、保存按钮之前，约第 222 行）插入：
```html
<div class="flex items-center gap-6">
  <div class="text-sm shrink-0 whitespace-nowrap">知识同步 <span class="text-xs text-muted-foreground">(文档知识同步)</span></div>
  <Input v-model="syncConfig.doc_sync_cron" placeholder="*/30 * * * *" class="w-40 font-mono text-xs" />
  <span v-if="docSyncNextRuns" class="text-xs text-muted-foreground font-mono">{{ docSyncNextRuns }}</span>
  <span v-else class="text-xs text-destructive">表达式无效</span>
</div>
```

- [ ] **Step 4: 在调度状态区域新增知识同步状态**

在「代码理解」状态块之后（约第 290 行 `</div>` 之后，`</div>` 闭合之前），添加：
```html
<div>
  <div class="mb-2 flex items-center gap-3">
    <span class="text-xs text-muted-foreground">知识同步</span>
    <Badge :variant="schedulerStatus.doc_sync?.running ? 'secondary' : 'outline'" :class="schedulerStatus.doc_sync?.running ? 'bg-green-50 text-green-700' : ''">
      {{ schedulerStatus.doc_sync?.running ? '运行中' : '已暂停' }}
    </Badge>
    <span v-if="schedulerStatus.doc_sync?.cron" class="font-mono text-xs text-muted-foreground">{{ schedulerStatus.doc_sync.cron }}</span>
  </div>
  <div class="py-2 text-xs text-muted-foreground">处理所有待处理和失败的文档同步任务</div>
</div>
```

- [ ] **Step 5: 验证 TypeScript**

```bash
cd frontend/capabilities && npx vue-tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 6: 提交**

```bash
git add frontend/capabilities/src/views/KnowledgeProcessingConfigView.vue
git commit -m "feat: add doc sync cron timer to knowledge processing config

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 最终验证

- [ ] **Step 1: 运行前端类型检查**

```bash
cd frontend/capabilities && npx vue-tsc --noEmit
```

确认零错误。

- [ ] **Step 2: 检查 build 是否通过**

```bash
cd frontend/capabilities && npm run build 2>&1 | tail -10
```

- [ ] **Step 3: 检查无遗漏的旧引用**

```bash
grep -rn "uploadLater\|onFileSelected\|uploadFile\b" frontend/capabilities/src/views/KnowledgeView.vue
```
期望：无输出（旧状态全部移除）。

```bash
grep -rn "uploadDocument\b" frontend/capabilities/src/views/KnowledgeView.vue
```
期望：仅出现 `openUploadDialog`、`uploadDocuments`（新函数），无 `uploadDocument`（单数）。
