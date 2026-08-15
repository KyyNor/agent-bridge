import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { api } from '../api/client'
import type { DocumentDetail, DocumentUploadSummary, KnowledgeBaseSummary, KnowledgeFolder } from '../api/types'
import { alert } from './useConfirm'

export type UploadItemStatus = 'pending' | 'uploading' | 'processing' | 'success' | 'error'

export interface KnowledgeUploadItem {
  file: File
  relativePath: string
  status: UploadItemStatus
  progress: number
  stage: string
  error: string
}

interface UseKnowledgeUploadQueueOptions {
  detailKb: Ref<KnowledgeBaseSummary | null>
  selectedFolderId: Ref<number | null>
  rootFolder: ComputedRef<KnowledgeFolder | null>
  showingAllDocuments: Ref<boolean>
  onUploaded: (kb: KnowledgeBaseSummary) => Promise<void>
}

const ALLOWED_DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.zip']

export function useKnowledgeUploadQueue({
  detailKb,
  selectedFolderId,
  rootFolder,
  showingAllDocuments,
  onUploaded,
}: UseKnowledgeUploadQueueOptions) {
  const showUploadDialog = ref(false)
  const uploadKb = ref<KnowledgeBaseSummary | null>(null)
  const uploadFiles = ref<KnowledgeUploadItem[]>([])
  const uploadFolderId = ref<number | null>(null)
  const uploading = ref(false)
  const uploadDragOver = ref(false)
  const uploadError = ref('')
  const failedUploadCount = computed(() => uploadFiles.value.filter(item => item.status === 'error').length)
  const retryableUploadCount = computed(() => uploadFiles.value.filter(item => item.status !== 'success').length)
  let nextUploadIndex = 0

  function normalizeRelativePath(path: string) {
    return path.replace(/\\/g, '/').replace(/^\/+/, '') || path
  }

  function addUploadItem(file: File, relativePath?: string) {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_DOC_EXTENSIONS.includes(ext)) return
    uploadFiles.value.push({
      file,
      relativePath: normalizeRelativePath(relativePath || file.name),
      status: 'pending',
      progress: 0,
      stage: '等待上传',
      error: '',
    })
  }

  function onUploadFilesSelected(event: Event) {
    const target = event.target as HTMLInputElement
    if (target.files && target.files.length > 0) {
      for (let index = 0; index < target.files.length; index++) {
        const file = target.files[index]
        addUploadItem(file, file.webkitRelativePath || file.name)
      }
    }
    target.value = ''
  }

  function handleUploadDragOver(event: DragEvent) {
    event.preventDefault()
    uploadDragOver.value = true
  }

  function handleUploadDragLeave(event: DragEvent) {
    const element = event.currentTarget as HTMLElement | null
    if (event.relatedTarget && element?.contains(event.relatedTarget as Node)) return
    uploadDragOver.value = false
  }

  function handleUploadDrop(event: DragEvent) {
    event.preventDefault()
    uploadDragOver.value = false
    if (event.dataTransfer) addFilesFromDataTransfer(event.dataTransfer)
  }

  function addFilesFromDataTransfer(dataTransfer: DataTransfer) {
    const entries: FileSystemEntry[] = []
    for (let index = 0; index < dataTransfer.items.length; index++) {
      const entry = dataTransfer.items[index].webkitGetAsEntry()
      if (entry) entries.push(entry)
    }
    if (entries.length === 0) {
      for (let index = 0; index < dataTransfer.files.length; index++) {
        const file = dataTransfer.files[index]
        addUploadItem(file, file.webkitRelativePath || file.name)
      }
      return
    }
    entries.forEach(entry => traverseEntry(entry))
  }

  function traverseEntry(entry: FileSystemEntry, parentPath = '') {
    const relativePath = parentPath ? `${parentPath}/${entry.name}` : entry.name
    if (entry.isFile) {
      const ext = '.' + entry.name.split('.').pop()?.toLowerCase()
      if (!ALLOWED_DOC_EXTENSIONS.includes(ext)) return
      ;(entry as FileSystemFileEntry).file(file => addUploadItem(file, relativePath))
      return
    }
    if (!entry.isDirectory) return
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    const readAll = () => {
      reader.readEntries(entries => {
        if (entries.length === 0) return
        entries.forEach(child => traverseEntry(child, relativePath))
        readAll()
      })
    }
    readAll()
  }

  function openUploadDialog(kb: KnowledgeBaseSummary) {
    uploadKb.value = kb
    uploadFiles.value = []
    uploadFolderId.value = kb.slug === detailKb.value?.slug
      ? (showingAllDocuments.value ? rootFolder.value?.id ?? null : selectedFolderId.value)
      : null
    uploadError.value = ''
    showUploadDialog.value = true
  }

  function updateUploadDialogOpen(open: boolean) {
    if (!open && uploading.value) return
    showUploadDialog.value = open
  }

  function clearUploadFiles() {
    if (!uploading.value) uploadFiles.value = []
  }

  function isUploadSummary(result: DocumentDetail | DocumentUploadSummary): result is DocumentUploadSummary {
    return 'uploaded_count' in result && 'skipped_count' in result
  }

  function updateUploadItem(index: number, patch: Partial<KnowledgeUploadItem>) {
    const item = uploadFiles.value[index]
    if (item) uploadFiles.value[index] = { ...item, ...patch }
  }

  function uploadProcessingStage(file: File) {
    return file.name.toLowerCase().endsWith('.zip')
      ? '正在解析 / 正在解压 / 正在入库 / 排队同步'
      : '正在解析 / 正在入库 / 排队同步'
  }

  function uploadErrorMessage(error: unknown) {
    return error instanceof Error ? error.message : String(error)
  }

  async function uploadOne(index: number, kbSlug: string, folderId: number | null) {
    const item = uploadFiles.value[index]
    if (!item) return null
    updateUploadItem(index, { status: 'uploading', progress: 0, stage: '正在上传', error: '' })
    try {
      const result = await api.addDocument(item.file, [kbSlug], true, folderId, item.relativePath, (loaded, total) => {
        const progress = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
        const uploadFinished = total > 0 && loaded >= total
        updateUploadItem(index, {
          status: uploadFinished ? 'processing' : 'uploading',
          progress,
          stage: uploadFinished ? uploadProcessingStage(item.file) : `正在上传 ${progress}%`,
        })
      })
      updateUploadItem(index, { status: 'success', progress: 100, stage: '上传成功', error: '' })
      return result
    } catch (error: unknown) {
      updateUploadItem(index, { status: 'error', stage: '上传失败', error: uploadErrorMessage(error) })
      return null
    }
  }

  async function uploadWorker(
    uploadIndexes: number[],
    results: Array<DocumentDetail | DocumentUploadSummary | null>,
    kbSlug: string,
    folderId: number | null,
  ) {
    while (true) {
      const queueIndex = nextUploadIndex++
      const itemIndex = uploadIndexes[queueIndex]
      if (itemIndex == null) return
      results[itemIndex] = await uploadOne(itemIndex, kbSlug, folderId)
    }
  }

  async function uploadDocuments() {
    const kb = uploadKb.value
    if (!kb || uploadFiles.value.length === 0 || uploading.value) return
    const uploadIndexes = uploadFiles.value
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => item.status !== 'success')
      .map(({ index }) => index)
    if (uploadIndexes.length === 0) return
    uploading.value = true
    uploadError.value = ''
    try {
      uploadIndexes.forEach(index => updateUploadItem(index, { status: 'pending', progress: 0, stage: '等待上传', error: '' }))
      const results: Array<DocumentDetail | DocumentUploadSummary | null> = []
      nextUploadIndex = 0
      await Promise.all(Array.from(
        { length: Math.min(3, uploadIndexes.length) },
        () => uploadWorker(uploadIndexes, results, kb.slug, uploadFolderId.value),
      ))
      const completedResults = results.filter(
        (result): result is DocumentDetail | DocumentUploadSummary => result !== null,
      )
      const failedCount = uploadIndexes.filter(index => uploadFiles.value[index]?.status === 'error').length
      const uploadedCount = completedResults.reduce((count, result) => count + (isUploadSummary(result) ? result.uploaded_count : 1), 0)
      const skippedCount = completedResults.reduce((count, result) => count + (isUploadSummary(result) ? result.skipped_count : 0), 0)
      if (failedCount > 0) {
        uploadError.value = `${failedCount} 个文件上传失败，请查看下方具体错误并重试。`
        return
      }
      let syncStartError = ''
      const immediateTargets = kb.backend_targets.filter(target => target.status === 'active' && target.sync_on_upload)
      if (uploadedCount > 0 && immediateTargets.length > 0) {
        uploadIndexes.forEach(index => {
          if (uploadFiles.value[index]?.status === 'success') updateUploadItem(index, { stage: `正在同步：${immediateTargets.map(target => target.slug).join('、')}` })
        })
        try {
          const results = await Promise.allSettled(immediateTargets.map(target => api.triggerKbSync(kb.slug, target.slug)))
          if (results.some(result => result.status === 'rejected')) throw new Error('部分后端同步未启动')
        } catch {
          syncStartError = '文件已入库，但部分后端同步未启动，请稍后点击“立即同步”。'
        }
      } else if (uploadedCount > 0) {
        uploadIndexes.forEach(index => {
          if (uploadFiles.value[index]?.status === 'success') updateUploadItem(index, { stage: '已入库，等待定时同步' })
        })
      }
      if (skippedCount > 0) {
        await alert({ title: '上传完成', description: `成功入库 ${uploadedCount} 个文件，跳过 ${skippedCount} 个重复文件。` })
      }
      showUploadDialog.value = false
      uploadFiles.value = []
      await onUploaded(kb)
      if (syncStartError) await alert({ title: '同步未启动', description: syncStartError, destructive: true })
    } catch (error: unknown) {
      uploadError.value = uploadErrorMessage(error) || '上传失败'
    } finally {
      uploading.value = false
    }
  }

  return {
    showUploadDialog,
    uploadKb,
    uploadFiles,
    uploadFolderId,
    uploading,
    uploadDragOver,
    uploadError,
    failedUploadCount,
    retryableUploadCount,
    openUploadDialog,
    updateUploadDialogOpen,
    clearUploadFiles,
    onUploadFilesSelected,
    handleUploadDragOver,
    handleUploadDragLeave,
    handleUploadDrop,
    uploadDocuments,
  }
}
