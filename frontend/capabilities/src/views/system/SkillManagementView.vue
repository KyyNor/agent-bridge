<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { marked, type Tokens } from 'marked'
import { Check, Copy, Eye, Pencil, RotateCcw, Save } from '@lucide/vue'
import { api } from '../../api/client'
import type { SkillPrompt } from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import EditorActionBar from '../../components/EditorActionBar.vue'
import { confirm } from '../../composables/useConfirm'
import { useToast } from '../../composables/useToast'
import PaginationBar from '../../components/PaginationBar.vue'
import RevisionHistoryPanel from '../../components/version/RevisionHistoryPanel.vue'
import { DEFAULT_PAGE_SIZE_OPTIONS, paginate } from '../../lib/pagination'

const skills = ref<SkillPrompt[]>([])
const selectedName = ref('')
const selected = ref<SkillPrompt | null>(null)
const prompt = ref('')
const loading = ref(true)
const detailLoading = ref(false)
const saving = ref(false)
const error = ref('')
const message = ref('')
const previewTab = ref<'edit' | 'preview'>('preview')
const showHistory = ref(false)
const copied = ref(false)
const page = ref(1)
const pageSize = ref(10)
const { toast } = useToast()

const hasChanges = computed(() => selected.value ? prompt.value !== (selected.value.prompt || '') : false)
const sourceLabel = computed(() => selected.value?.source === 'database' ? '已自定义' : '默认提示词')
const previewRenderer = new marked.Renderer()
previewRenderer.html = ({ text }: Tokens.HTML) => escapeHtml(text)
previewRenderer.link = function ({ href, title, tokens }: Tokens.Link) {
  const label = this.parser.parseInline(tokens)
  const safeHref = safeUrl(href)
  if (!safeHref) return label
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
  return `<a href="${escapeHtml(safeHref)}"${titleAttr}>${label}</a>`
}
previewRenderer.image = function ({ href, title, text }: Tokens.Image) {
  const safeHref = safeUrl(href)
  if (!safeHref) return escapeHtml(text)
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
  return `<img src="${escapeHtml(safeHref)}" alt="${escapeHtml(text)}"${titleAttr}>`
}
const previewHtml = computed(() => marked.parse(prompt.value, {
  async: false,
  renderer: previewRenderer,
}) as string)
const pagedSkills = computed(() => paginate(skills.value, page.value, pageSize.value))

onMounted(async () => {
  await loadSkills()
})

async function loadSkills() {
  loading.value = true
  error.value = ''
  try {
    skills.value = await api.listSkills()
    selectedName.value = selectedName.value || skills.value[0]?.skill_name || ''
    if (selectedName.value) await selectSkill(selectedName.value)
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function selectSkill(skillName: string) {
  selectedName.value = skillName
  detailLoading.value = true
  message.value = ''
  error.value = ''
  try {
    selected.value = await api.getSkill(skillName)
    prompt.value = selected.value.prompt || ''
  } catch (e: unknown) {
    selected.value = null
    error.value = errorMessage(e)
  } finally {
    detailLoading.value = false
  }
}

async function saveSkill() {
  if (!selected.value || saving.value) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    selected.value = await api.saveSkill(selected.value.skill_name, prompt.value)
    prompt.value = selected.value.prompt || ''
    skills.value = await api.listSkills()
    message.value = '已保存到数据库'
    toast({ title: 'Skill 已保存', description: `“${selected.value.name}” 已更新。`, variant: 'success' })
  } catch (e: unknown) {
    error.value = errorMessage(e)
    toast({ title: '保存 Skill 失败', description: error.value, variant: 'error' })
  } finally {
    saving.value = false
  }
}

async function resetSkill() {
  if (!selected.value || saving.value) return
  if (!await confirm({ title: '恢复默认提示词', description: `恢复「${selected.value.name}」的默认提示词？当前自定义内容会被移除。`, confirmText: '恢复' })) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    selected.value = await api.resetSkill(selected.value.skill_name)
    prompt.value = selected.value.prompt || ''
    skills.value = await api.listSkills()
    message.value = '已恢复默认提示词'
    toast({ title: '已恢复默认提示词', description: `“${selected.value.name}” 已恢复。`, variant: 'success' })
  } catch (e: unknown) {
    error.value = errorMessage(e)
    toast({ title: '恢复默认提示词失败', description: error.value, variant: 'error' })
  } finally {
    saving.value = false
  }
}

async function copyRunPrompt() {
  if (!selected.value) return
  const cmd = `请执行 execute(service="built-in",tool_name="load_skill",param={"skill_name":"${selected.value.skill_name}"}) 来加载 skill`
  try {
    await navigator.clipboard.writeText(cmd)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = cmd
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[character] || character))
}

function safeUrl(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (/^[a-z][a-z\d+.-]*:/i.test(trimmed) && !/^(?:https?|mailto|tel):/i.test(trimmed)) {
    return null
  }
  return trimmed
}
</script>

<template>
  <div class="space-y-5">
    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
      {{ error }}
    </div>
    <div v-if="message" class="rounded-md border border-success/30 bg-success-soft px-3 py-2 text-sm text-success-soft-fg">
      {{ message }}
    </div>

    <div class="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card>
        <CardContent class="p-0">
          <div class="border-b px-4 py-3">
            <div class="text-sm font-medium text-foreground">内置 Skill</div>
            <div class="text-xs text-muted-foreground">{{ skills.length }} 个可配置项</div>
          </div>
          <div v-if="loading" class="px-4 py-8 text-sm text-muted-foreground">加载中</div>
          <div v-else-if="!skills.length" class="px-4 py-8 text-sm text-muted-foreground">暂无 Skill</div>
          <div v-else class="divide-y">
            <button
              v-for="item in pagedSkills"
              :key="item.skill_name"
              class="list-row-interactive w-full px-4 py-3 text-left"
              :class="selectedName === item.skill_name ? 'bg-muted/60' : ''"
              @click="selectSkill(item.skill_name)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate text-sm font-medium text-foreground">{{ item.name }}</span>
                <Badge variant="outline">{{ item.source === 'database' ? '自定义' : '默认' }}</Badge>
              </div>
              <div class="mt-1 font-mono text-xs text-muted-foreground">{{ item.skill_name }}</div>
              <p class="mt-1 line-clamp-2 text-xs text-muted-foreground">{{ item.description }}</p>
            </button>
          </div>
          <div v-if="skills.length" class="border-t px-4 py-3">
            <PaginationBar
              v-model:page="page"
              v-model:page-size="pageSize"
              :total="skills.length"
              :page-size-options="DEFAULT_PAGE_SIZE_OPTIONS"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent class="space-y-4 p-4">
          <div v-if="detailLoading" class="py-12 text-center text-sm text-muted-foreground">加载中</div>
          <template v-else-if="selected">
            <EditorActionBar>
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="text-base font-semibold text-foreground">{{ selected.name }}</h3>
                    <Badge variant="outline">{{ sourceLabel }}</Badge>
                  </div>
                  <div class="mt-1 font-mono text-xs text-muted-foreground">{{ selected.skill_name }}</div>
                  <p class="mt-2 text-sm text-muted-foreground">{{ selected.description }}</p>
                </div>
                <div>
                  <div class="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" @click="copyRunPrompt">
                      <Check v-if="copied" class="mr-1.5 h-4 w-4" />
                      <Copy v-else class="mr-1.5 h-4 w-4" />
                      {{ copied ? '已复制' : '复制运行提示' }}
                    </Button>
                    <Button variant="outline" size="sm" :disabled="saving || selected.source === 'default'" @click="resetSkill">
                      <RotateCcw class="mr-1.5 h-4 w-4" />
                      恢复默认
                    </Button>
                    <Button size="sm" :disabled="saving || !hasChanges" @click="saveSkill">
                      <Save class="mr-1.5 h-4 w-4" />
                      {{ saving ? '保存中' : '保存' }}
                    </Button>
                  </div>
                  <div class="mt-2 flex items-center justify-end gap-1">
                    <button
                      class="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition"
                      :class="previewTab === 'edit' ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'"
                      @click="previewTab = 'edit'"
                    >
                      <Pencil class="h-3.5 w-3.5" />
                      编辑
                    </button>
                    <button
                      class="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition"
                      :class="previewTab === 'preview' ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'"
                      @click="previewTab = 'preview'"
                    >
                      <Eye class="h-3.5 w-3.5" />
                      预览
                    </button>
                  </div>
                </div>
              </div>
              </div>
            </EditorActionBar>

            <textarea
              v-if="previewTab === 'edit'"
              v-model="prompt"
              class="min-h-[62vh] w-full rounded-md border bg-background p-3 font-mono text-xs leading-5"
              spellcheck="false"
            />
            <div
              v-else
              class="min-h-[62vh] w-full rounded-md border bg-background p-4 prose prose-sm max-w-none text-sm"
              v-html="previewHtml"
            />
          </template>
          <div v-else class="py-12 text-center text-sm text-muted-foreground">请选择 Skill</div>

          <!-- Version history -->
          <template v-if="selected">
            <div class="mt-4 border-t border-border pt-3">
              <button
                type="button"
                class="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground"
                @click="showHistory = !showHistory"
              >
                {{ showHistory ? '▾ 收起版本历史' : '▸ 查看版本历史' }}
                <span v-if="selected.revision_no" class="rounded bg-secondary px-1.5 py-0.5 font-mono">v{{ selected.revision_no }}</span>
              </button>
              <div v-if="showHistory" class="mt-3">
                <RevisionHistoryPanel
                  :key="`skill-${selected.skill_name}`"
                  entity-type="skill"
                  :entity-key="selected.skill_name"
                />
              </div>
            </div>
          </template>
        </CardContent>
      </Card>
    </div>
  </div>
</template>
