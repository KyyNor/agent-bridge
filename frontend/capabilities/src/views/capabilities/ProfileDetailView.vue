<script setup lang="ts">
import { ArrowLeft } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import type {
  ProjectProfile,
  MemoryBlock,
  ProfileMemoryBinding,
  ProfileDocRender,
  ProfilePinPreview,
  ProfilePinRule,
  ProfileSourceRule,
  ProfileResourceRule,
  CatalogSource,
  KnowledgeBaseSummary,
  CodeRepository,
  BusinessLedger,
} from '../../api/types'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import StatusBadge from '../../components/StatusBadge.vue'
import SearchableMultiSelect, { type SearchableMultiSelectOption } from '../../components/SearchableMultiSelect.vue'
import { profileConfigDraftKey, type ProfileConfigDraft } from './profileConfigSnapshot'
import { profileConfigFirstUseTour } from '../../lib/onboardingTours'
import { useOnboardingTour } from '../../composables/useOnboardingTour'

const props = defineProps<{
  profileKey: string
  profile: ProjectProfile | null
}>()
const { maybeStartTour } = useOnboardingTour()

const emit = defineEmits<{
  saved: []
  back: []
  cancel: []
}>()

const configProfile = ref<ProjectProfile | null>(null)
const configLoading = ref(false)
const configRules = ref<ProfileSourceRule[]>([])
const configResources = ref<ProfileResourceRule[]>([])
const rulesEditToken = ref<string | undefined>()
const resourcesEditToken = ref<string | undefined>()
const pendingRules = ref<ProfileSourceRule[]>([])
const pendingResources = ref<ProfileResourceRule[]>([])
const allServices = ref<CatalogSource[]>([])
const allKbs = ref<KnowledgeBaseSummary[]>([])
const allRepos = ref<CodeRepository[]>([])
const allBusinessLedgers = ref<BusinessLedger[]>([])
const allMemoryBlocks = ref<MemoryBlock[]>([])
const profileMemory = ref<ProfileMemoryBinding | null>(null)
const pendingMemoryBlock = ref('')
const memoryLoaded = ref(false)
const memoryError = ref('')
const configSaving = ref(false)
const configError = ref('')
const saveError = ref('')
const pinPreview = ref<ProfilePinPreview | null>(null)
const pinEditToken = ref<string | undefined>()
const pendingPins = ref<ProfilePinRule[]>([])
const pinMode = ref<'disabled' | 'ratio' | 'count'>('disabled')
const pinRatio = ref(10)
const pinCount = ref(3)
const pinSaving = ref(false)
const pinsLoaded = ref(false)
const pinError = ref('')
const profileMarkdown = ref('')
const manualNotes = ref('')
const notesEditToken = ref<string | undefined>()
const docSaving = ref(false)
const docError = ref('')
const initialDraft = ref<ProfileConfigDraft | null>(null)

const copied = ref('')
const pinToolTypes = ['overview', 'search', 'detail']

const allowedServices = computed(() =>
  allServices.value.filter(svc => isServiceAllowed(svc.source_key))
)

const serviceOptions = computed<SearchableMultiSelectOption[]>(() => allServices.value.map(svc => ({
  value: svc.source_key,
  label: svc.name || svc.source_key,
  description: `${svc.source_key} · ${svc.source_type === 'openapi_service' ? 'OpenAPI' : 'MCP'}`,
})))
const knowledgeOptions = computed<SearchableMultiSelectOption[]>(() => allKbs.value.map(kb => ({
  value: kb.slug,
  label: kb.name,
  description: `${kb.slug} · ${kb.document_count} 文档`,
})))
const repositoryOptions = computed<SearchableMultiSelectOption[]>(() => allRepos.value.map(repo => ({
  value: repo.repo_key,
  label: repo.name,
  description: repo.repo_key,
})))
const businessLedgerOptions = computed<SearchableMultiSelectOption[]>(() => allBusinessLedgers.value.map(ledger => ({
  value: ledger.ledger_key,
  label: ledger.name,
  description: ledger.description || ledger.ledger_key,
})))
const allowedServiceKeys = computed(() => allServices.value
  .filter(svc => isServiceAllowed(svc.source_key))
  .map(svc => svc.source_key))

const activeMemoryBlocks = computed(() =>
  allMemoryBlocks.value.filter(block => block.status === 'active')
)

const autoPinGroups = computed(() =>
  (pinPreview.value?.groups || []).filter(g => g.source === 'auto')
)

const manualPinGroups = computed(() =>
  pendingPins.value.map(pin => ({
    ...pin,
    service_name: serviceName(pin.service_key),
  }))
)

const currentDraft = computed<ProfileConfigDraft>(() => ({
  sourceRules: pendingRules.value,
  resourceRules: pendingResources.value,
  memoryBlockKey: pendingMemoryBlock.value,
  pins: pendingPins.value,
  pinMode: pinMode.value,
  pinRatio: pinRatio.value,
  pinCount: pinCount.value,
  manualNotes: manualNotes.value,
}))

const hasUnsavedChanges = computed(() => {
  if (!initialDraft.value) return false
  if (!pinsLoaded.value) {
    const current = { ...currentDraft.value, pins: [], pinMode: 'disabled' as const, pinRatio: 10, pinCount: 3 }
    const initial = { ...initialDraft.value, pins: [], pinMode: 'disabled' as const, pinRatio: 10, pinCount: 3 }
    return profileConfigDraftKey(current) !== profileConfigDraftKey(initial)
  }
  return profileConfigDraftKey(currentDraft.value) !== profileConfigDraftKey(initialDraft.value)
})

const canSaveConfig = computed(() =>
  !!configProfile.value
  && !configLoading.value
  && !configSaving.value
  && !pinSaving.value
  && !docSaving.value
  && !configError.value
)

function cloneDraft(draft: ProfileConfigDraft): ProfileConfigDraft {
  return {
    ...draft,
    sourceRules: draft.sourceRules.map(rule => ({ ...rule })),
    resourceRules: draft.resourceRules.map(rule => ({ ...rule })),
    pins: draft.pins.map(pin => ({ ...pin })),
  }
}

function captureInitialDraft() {
  initialDraft.value = cloneDraft(currentDraft.value)
}

function syncInitialPinDraft() {
  if (!initialDraft.value) return
  initialDraft.value = {
    ...initialDraft.value,
    pins: pendingPins.value.map(pin => ({ ...pin })),
    pinMode: pinMode.value,
    pinRatio: pinRatio.value,
    pinCount: pinCount.value,
  }
}

function syncInitialNotesDraft() {
  if (!initialDraft.value) return
  initialDraft.value = { ...initialDraft.value, manualNotes: manualNotes.value }
}

defineExpose({
  hasUnsavedChanges,
  isBusy: computed(() => configLoading.value || configSaving.value || pinSaving.value || docSaving.value),
})

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : '未知错误'
}

function resetConfigState() {
  configError.value = ''
  saveError.value = ''
  pinError.value = ''
  docError.value = ''
  configRules.value = []
  configResources.value = []
  rulesEditToken.value = undefined
  resourcesEditToken.value = undefined
  pendingRules.value = []
  pendingResources.value = []
  allServices.value = []
  allKbs.value = []
  allRepos.value = []
  allBusinessLedgers.value = []
  allMemoryBlocks.value = []
  profileMemory.value = null
  pendingMemoryBlock.value = ''
  memoryLoaded.value = false
  memoryError.value = ''
  pinPreview.value = null
  pinEditToken.value = undefined
  pendingPins.value = []
  pinMode.value = 'disabled'
  pinRatio.value = 10
  pinCount.value = 3
  pinsLoaded.value = false
  profileMarkdown.value = ''
  manualNotes.value = ''
  notesEditToken.value = undefined
  initialDraft.value = null
}

async function loadProfile(profileKey: string) {
  resetConfigState()
  configProfile.value = props.profile?.profile_key === profileKey ? props.profile : null
  if (!profileKey) return
  configLoading.value = true
  if (!configProfile.value) {
    configError.value = '无法加载该能力平面（可能已被删除或不存在）'
    configLoading.value = false
    return
  }

  try {
    const [catalog, kbs, repos, businessLedgers, full] = await Promise.all([
      api.catalog(),
      api.listWikiKbs(),
      api.listCodeRepos(),
      api.listBusinessLedgers(),
      api.getProfile(profileKey),
    ])
    if (configProfile.value?.profile_key !== profileKey) return
    allServices.value = catalog.sources
    allKbs.value = kbs
    allRepos.value = repos
    allBusinessLedgers.value = businessLedgers
    configProfile.value = full
    rulesEditToken.value = full.rules_edit_token
    resourcesEditToken.value = full.resources_edit_token
    configRules.value = full.rules || []
    configResources.value = full.resource_rules || []
    pendingRules.value = [...configRules.value]
    pendingResources.value = [...configResources.value]
  } catch (e: unknown) {
    configError.value = `加载 Profile 核心配置失败：${errorMessage(e)}`
    configLoading.value = false
    return
  }
  await loadProfileMemory(profileKey)
  captureInitialDraft()
  configLoading.value = false
  if (!configError.value) await maybeStartTour(profileConfigFirstUseTour)
}

watch(() => props.profileKey, profileKey => { void loadProfile(profileKey) }, { immediate: true })

async function loadProfileMemory(profileKey: string) {
  memoryError.value = ''
  memoryLoaded.value = false
  try {
    const [memoryBlocks, memoryBinding] = await Promise.all([
      api.listMemoryBlocks(),
      api.getProfileMemory(profileKey),
    ])
    if (configProfile.value?.profile_key !== profileKey) return
    allMemoryBlocks.value = memoryBlocks
    profileMemory.value = memoryBinding
    pendingMemoryBlock.value = memoryBinding.block_key || ''
    memoryLoaded.value = true
  } catch (e: unknown) {
    if (configProfile.value?.profile_key === profileKey) {
      allMemoryBlocks.value = []
      profileMemory.value = null
      pendingMemoryBlock.value = ''
      memoryError.value = `加载记忆绑定失败：${errorMessage(e)}`
    }
  }
}

async function loadProfilePins(profileKey: string) {
  pinSaving.value = true
  pinError.value = ''
  try {
    const pins = await api.getProfilePins(profileKey)
    if (configProfile.value?.profile_key !== profileKey) return
    applyPinPreview(pins)
    pinsLoaded.value = true
    syncInitialPinDraft()
  } catch (e: unknown) {
    if (configProfile.value?.profile_key === profileKey) {
      pinError.value = `加载 Pin 预览失败：${errorMessage(e)}`
    }
  } finally {
    if (configProfile.value?.profile_key === profileKey) pinSaving.value = false
  }
}

async function loadProfileDoc(profileKey: string) {
  docSaving.value = true
  docError.value = ''
  try {
    const doc = await api.renderProfileDoc(profileKey)
    if (configProfile.value?.profile_key !== profileKey) return
    applyProfileDoc(doc)
    // The initial draft was captured before the async doc load pre-filled the
    // textarea; re-sync so pre-filling saved notes isn't reported as unsaved.
    syncInitialNotesDraft()
  } catch (e: unknown) {
    if (configProfile.value?.profile_key === profileKey) {
      docError.value = `加载 Profile 文档失败：${errorMessage(e)}`
    }
  } finally {
    if (configProfile.value?.profile_key === profileKey) docSaving.value = false
  }
}

function onAdvancedToggle(event: Event) {
  const details = event.currentTarget as HTMLDetailsElement | null
  const profileKey = configProfile.value?.profile_key
  if (!details?.open || !profileKey) return
  if (!pinsLoaded.value && !pinSaving.value) void loadProfilePins(profileKey)
  if (!profileMarkdown.value && !docSaving.value) void loadProfileDoc(profileKey)
}

function applyPinPreview(pins: ProfilePinPreview) {
  pinPreview.value = pins
  pinEditToken.value = pins.edit_token
  pendingPins.value = pins.groups
    .filter(g => g.source === 'manual')
    .map(({ service_key, tool_type }) => ({ service_key, tool_type }))
  pinMode.value = pins.settings.mode
  pinRatio.value = pins.settings.ratio_percent ?? 10
  pinCount.value = pins.settings.count ?? 3
}

function applyProfileDoc(doc: ProfileDocRender) {
  profileMarkdown.value = doc.markdown
  notesEditToken.value = doc.edit_token
  // Echoed manual_notes lets the edit textarea show what's already saved, so
  // the user can tweak it instead of retyping the whole note. Only pre-fill
  // when the user hasn't started typing (avoid clobbering in-flight edits).
  if (typeof doc.manual_notes === 'string' && manualNotes.value === '') {
    manualNotes.value = doc.manual_notes
  }
}

function getProfileCommand(profile: ProjectProfile) {
  return `agent-bridge profile use ${profile.profile_key}`
}

async function copyCommand(profile: ProjectProfile) {
  const cmd = getProfileCommand(profile)
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
  copied.value = profile.profile_key
  setTimeout(() => { copied.value = '' }, 2000)
}

function isServiceAllowed(key: string) {
  return pendingRules.value.some(r => r.source_key === key && r.effect === 'allow')
}

function updateAllowedServices(keys: string[]) {
  const selectedKeys = new Set(keys)
  const knownKeys = new Set(allServices.value.map(item => item.source_key))
  pendingRules.value = [
    ...pendingRules.value.filter(rule => rule.effect !== 'allow' || !knownKeys.has(rule.source_key)),
    ...allServices.value
      .filter(source => selectedKeys.has(source.source_key))
      .map(source => ({ source_type: source.source_type, source_key: source.source_key, effect: 'allow' as const })),
  ]
  pendingPins.value = pendingPins.value.filter(pin => !knownKeys.has(pin.service_key) || selectedKeys.has(pin.service_key))
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    overview: '概览',
    search: '搜索',
    detail: '详情',
    action: '操作',
  }
  return labels[type] || type
}

function serviceName(key: string) {
  return allServices.value.find(svc => svc.source_key === key)?.name || key
}

function manualPinExists(serviceKey: string, toolType: string) {
  return pendingPins.value.some(pin => pin.service_key === serviceKey && pin.tool_type === toolType)
}

function toggleManualPin(serviceKey: string, toolType: string) {
  if (!pinToolTypes.includes(toolType)) return
  if (manualPinExists(serviceKey, toolType)) {
    pendingPins.value = pendingPins.value.filter(pin => !(pin.service_key === serviceKey && pin.tool_type === toolType))
  } else {
    pendingPins.value = [...pendingPins.value, { service_key: serviceKey, tool_type: toolType }]
  }
}

function allowedResourceKeys(type: string, knownKeys: string[]) {
  const known = new Set(knownKeys)
  return pendingResources.value
    .filter(rule => rule.resource_type === type && known.has(rule.resource_key))
    .map(rule => rule.resource_key)
}

function updateAllowedResources(type: string, keys: string[], knownKeys: string[]) {
  const known = new Set(knownKeys)
  const selected = new Set(keys)
  pendingResources.value = [
    ...pendingResources.value.filter(rule => rule.resource_type !== type || !known.has(rule.resource_key)),
    ...knownKeys.filter(key => selected.has(key)).map(resource_key => ({ resource_type: type, resource_key })),
  ]
}

async function saveConfig() {
  if (!configProfile.value || configLoading.value || configError.value) return
  configSaving.value = true
  saveError.value = ''
  try {
    const rulesResult = await api.replaceProfileRules(
      configProfile.value.profile_key,
      pendingRules.value,
      rulesEditToken.value,
    )
    rulesEditToken.value = rulesResult.rules_edit_token
    const resourcesResult = await api.replaceProfileResources(
      configProfile.value.profile_key,
      pendingResources.value,
      resourcesEditToken.value,
    )
    resourcesEditToken.value = resourcesResult.resources_edit_token
    if (pinsLoaded.value) await savePins(true)
    if (memoryLoaded.value) {
      profileMemory.value = await api.setProfileMemory(
        configProfile.value.profile_key,
        pendingMemoryBlock.value || null,
        true,
        profileMemory.value?.edit_token,
      )
    }
    if (manualNotes.value.trim()) await saveManualNotes(true)
    await refreshProfileDoc(true)
    configRules.value = [...pendingRules.value]
    configResources.value = [...pendingResources.value]
    captureInitialDraft()
    emit('saved')
  } catch (e: unknown) {
    saveError.value = `保存配置失败：${errorMessage(e)}`
  }
  configSaving.value = false
}

async function savePins(raiseError = false) {
  if (!configProfile.value || !pinsLoaded.value) return
  pinSaving.value = true
  pinError.value = ''
  try {
    const replaced = await api.replaceProfilePins(
      configProfile.value.profile_key,
      pendingPins.value,
      pinEditToken.value,
    )
    applyPinPreview(replaced)
    const pins = await api.updateProfilePinSettings(configProfile.value.profile_key, {
      mode: pinMode.value,
      ratio_percent: pinMode.value === 'ratio' ? pinRatio.value : null,
      count: pinMode.value === 'count' ? pinCount.value : null,
    }, pinEditToken.value)
    applyPinPreview(pins)
    pinsLoaded.value = true
    syncInitialPinDraft()
  } catch (e: unknown) {
    pinError.value = `保存 Pin 配置失败：${errorMessage(e)}`
    if (raiseError) throw e
  } finally {
    pinSaving.value = false
  }
}

async function refreshPins() {
  if (!configProfile.value) return
  pinSaving.value = true
  pinError.value = ''
  try {
    const pins = await api.refreshProfilePins(configProfile.value.profile_key)
    applyPinPreview(pins)
    pinsLoaded.value = true
    syncInitialPinDraft()
  } catch (e: unknown) {
    pinError.value = `重新计算自动 Pin 失败：${errorMessage(e)}`
  }
  pinSaving.value = false
}

async function saveManualNotes(raiseError = false) {
  if (!configProfile.value) return
  docSaving.value = true
  docError.value = ''
  try {
    const doc = await api.updateProfileManualNotes(
      configProfile.value.profile_key,
      manualNotes.value,
      notesEditToken.value,
    )
    // Re-render carries the saved manual_notes; reflect it so the textarea
    // stays in sync with what was persisted (and is no longer "dirty").
    applyProfileDoc(doc)
    if (typeof doc.manual_notes === 'string') manualNotes.value = doc.manual_notes
    syncInitialNotesDraft()
  } catch (e: unknown) {
    docError.value = `保存手动补充失败：${errorMessage(e)}`
    if (raiseError) throw e
  }
  docSaving.value = false
}

async function refreshProfileDoc(raiseError = false) {
  if (!configProfile.value) return
  docSaving.value = true
  docError.value = ''
  try {
    const doc = await api.renderProfileDoc(configProfile.value.profile_key)
    applyProfileDoc(doc)
  } catch (e: unknown) {
    docError.value = `生成 Profile 文档失败：${errorMessage(e)}`
    if (raiseError) throw e
  } finally {
    docSaving.value = false
  }
}
</script>

<template>
  <div class="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col gap-4">
    <div class="flex shrink-0 flex-wrap items-start justify-between gap-3">
      <div class="flex min-w-0 items-start gap-2">
        <Button variant="ghost" size="sm" class="h-8 shrink-0 px-2" @click="emit('back')">
          <ArrowLeft :size="14" class="mr-1.5" />
          返回
        </Button>
        <div v-if="configProfile" class="min-w-0">
          <h2 class="text-lg font-semibold text-foreground">{{ configProfile.name }} — 配置</h2>
          <div class="mt-0.5 flex flex-wrap items-center gap-2">
            <p class="font-mono text-xs text-muted-foreground">{{ configProfile.profile_key }}</p>
            <StatusBadge v-if="configProfile.status === 'active'" status="enabled" />
            <StatusBadge v-else status="disabled" />
          </div>
          <p v-if="configProfile.description" class="mt-1 text-xs text-muted-foreground">{{ configProfile.description }}</p>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto pr-1">
      <div v-if="configLoading" class="py-12 text-center text-sm text-muted-foreground">加载中...</div>
      <div v-else-if="configError" class="rounded-lg border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive">
        {{ configError }}
      </div>
      <div v-else class="space-y-5 pb-4">
        <!-- Copy Command -->
        <div data-tour="profile-command">
          <div class="mb-2 text-sm font-medium">接入命令</div>
          <div class="flex min-w-0 items-center gap-2 rounded-lg bg-secondary px-4 py-2.5">
            <code class="min-w-0 flex-1 break-all font-mono text-sm text-foreground">{{ configProfile ? getProfileCommand(configProfile) : '' }}</code>
            <Button variant="ghost" size="sm" @click="configProfile && copyCommand(configProfile)">
              {{ copied === configProfile?.profile_key ? '已复制' : '复制' }}
            </Button>
          </div>
        </div>

        <!-- Memory Binding -->
        <section class="space-y-3 rounded-lg border border-border p-4">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 class="text-sm font-medium text-foreground">记忆</h3>
              <p class="mt-1 text-xs text-muted-foreground">为此能力平面绑定一个 active memory block。</p>
            </div>
            <Badge v-if="profileMemory?.block_key" variant="outline" class="max-w-full break-all">
              {{ profileMemory.block_key }}
            </Badge>
            <Badge v-else variant="secondary" class="text-muted-foreground">未绑定</Badge>
          </div>
          <div v-if="memoryError" class="rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">
            {{ memoryError }}
          </div>
          <select
            v-model="pendingMemoryBlock"
            :disabled="!!memoryError"
            class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            <option value="">未绑定</option>
            <option v-for="block in activeMemoryBlocks" :key="block.block_key" :value="block.block_key">
              {{ block.name }} ({{ block.block_key }})
            </option>
          </select>
          <p class="break-all text-xs text-muted-foreground">
            运行 agent-bridge profile use {{ configProfile?.profile_key }} --scope project --url http://127.0.0.1:8765/mcp 安装或刷新 Claude Code hooks。
          </p>
        </section>

        <!-- Allow List -->
        <div data-tour="profile-services">
          <div class="mb-2 text-sm font-medium">允许访问的服务</div>
          <div v-if="allServices.length === 0" class="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            暂无已注册的服务，请先在能力接入中添加服务
          </div>
          <SearchableMultiSelect
            v-else
            :model-value="allowedServiceKeys"
            :options="serviceOptions"
            placeholder="选择允许访问的服务"
            search-placeholder="搜索服务"
            @update:model-value="updateAllowedServices"
          />
        </div>

        <!-- KB Resources -->
        <div data-tour="profile-resources">
          <div class="mb-2 text-sm font-medium">允许访问的文档知识</div>
          <div v-if="allKbs.length === 0" class="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
            暂无文档知识，请先在文档知识中添加
          </div>
          <SearchableMultiSelect
            v-else
            :model-value="allowedResourceKeys('wiki_kb', allKbs.map(item => item.slug))"
            :options="knowledgeOptions"
            placeholder="选择允许访问的文档知识"
            search-placeholder="搜索文档知识"
            @update:model-value="updateAllowedResources('wiki_kb', $event, allKbs.map(item => item.slug))"
          />
        </div>

        <!-- Code Repo Resources -->
        <div>
          <div class="mb-2 text-sm font-medium">允许访问的代码仓库</div>
          <div v-if="allRepos.length === 0" class="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
            暂无代码仓库，请先在代码知识中添加
          </div>
          <SearchableMultiSelect
            v-else
            :model-value="allowedResourceKeys('code_repo', allRepos.map(item => item.repo_key))"
            :options="repositoryOptions"
            placeholder="选择允许访问的代码仓库"
            search-placeholder="搜索代码仓库"
            @update:model-value="updateAllowedResources('code_repo', $event, allRepos.map(item => item.repo_key))"
          />
        </div>

        <!-- Business Ledger Resources -->
        <div>
          <div class="mb-2 text-sm font-medium">允许访问的业务台账</div>
          <div v-if="allBusinessLedgers.length === 0" class="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
            暂无业务台账，请先在业务台账中添加
          </div>
          <SearchableMultiSelect
            v-else
            :model-value="allowedResourceKeys('business_ledger', allBusinessLedgers.map(item => item.ledger_key))"
            :options="businessLedgerOptions"
            placeholder="选择允许访问的业务台账"
            search-placeholder="搜索业务台账"
            @update:model-value="updateAllowedResources('business_ledger', $event, allBusinessLedgers.map(item => item.ledger_key))"
          />
        </div>

        <!-- Advanced Options -->
        <details class="rounded-lg border border-border" @toggle="onAdvancedToggle">
          <summary class="cursor-pointer px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">高级选项</summary>
          <div class="space-y-4 border-t border-border p-4">
            <!-- Pin Configuration -->
            <div class="space-y-3 rounded-lg border border-border p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div class="text-sm font-medium">工作置顶</div>
                  <div class="mt-1 text-xs text-muted-foreground">
                    当前会暴露 {{ pinPreview?.tools.length || 0 }} 个 pin_* 工具
                    <span class="ml-1">未保存的手动 Pin 需保存后刷新预览。</span>
                  </div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  <Button variant="outline" size="sm" :disabled="pinSaving" @click="refreshPins">
                    {{ pinSaving ? '处理中...' : '重新计算自动 Pin' }}
                  </Button>
                  <Button size="sm" :disabled="pinSaving || !pinsLoaded" @click="savePins()">
                    {{ pinSaving ? '保存中...' : '保存 Pin' }}
                  </Button>
                </div>
              </div>
              <div v-if="pinError" class="rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ pinError }}</div>

              <div class="grid gap-3 sm:grid-cols-[160px_1fr_1fr]">
                <label class="space-y-1">
                  <span class="text-xs font-medium text-muted-foreground">自动 Pin 模式</span>
                  <select v-model="pinMode" :disabled="!pinsLoaded" class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
                    <option value="disabled">关闭</option>
                    <option value="ratio">按比例</option>
                    <option value="count">按数量</option>
                  </select>
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-medium text-muted-foreground">比例百分比</span>
                  <Input v-model.number="pinRatio" type="number" min="1" max="100" :disabled="!pinsLoaded || pinMode !== 'ratio'" />
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-medium text-muted-foreground">数量</span>
                  <Input v-model.number="pinCount" type="number" min="1" :disabled="!pinsLoaded || pinMode !== 'count'" />
                </label>
              </div>

              <div>
                <div class="mb-2 text-xs font-medium text-muted-foreground">手动 Pin</div>
                <div v-if="manualPinGroups.length === 0" class="rounded-md border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">
                  暂无手动 pin；可在下方按服务选择概览、搜索或详情。
                </div>
                <div v-else class="flex flex-wrap gap-2">
                  <span v-for="pin in manualPinGroups" :key="`${pin.service_key}:${pin.tool_type}`" class="inline-flex min-w-0 max-w-full items-center gap-2 rounded-md bg-secondary px-2.5 py-1 text-xs">
                    <span class="min-w-0 break-all font-medium text-foreground">{{ pin.service_name }}</span>
                    <span class="min-w-0 break-all text-muted-foreground">{{ pin.service_key }} / {{ typeLabel(pin.tool_type) }}</span>
                    <button class="text-muted-foreground hover:text-destructive" type="button" @click="toggleManualPin(pin.service_key, pin.tool_type)">移除</button>
                  </span>
                </div>
              </div>

              <div>
                <div class="mb-2 text-xs font-medium text-muted-foreground">按已允许服务选择 Pin Level</div>
                <div v-if="allowedServices.length === 0" class="rounded-md border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">请先在上方允许至少一个服务。</div>
                <div v-else class="max-h-[220px] space-y-2 overflow-y-auto">
                  <div v-for="svc in allowedServices" :key="`pin-${svc.source_type}:${svc.source_key}`" class="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 px-3 py-2">
                    <div class="min-w-[180px] flex-1 break-all">
                      <div class="text-sm font-medium">{{ svc.name || svc.source_key }}</div>
                      <div class="text-xs text-muted-foreground">{{ svc.source_key }} · {{ svc.source_type === 'openapi_service' ? 'OpenAPI' : 'MCP' }}</div>
                    </div>
                    <label v-for="toolType in pinToolTypes" :key="`${svc.source_key}-${toolType}`" :class="['inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors', pinsLoaded ? 'cursor-pointer hover:bg-background' : 'cursor-not-allowed opacity-60']">
                      <input type="checkbox" :checked="manualPinExists(svc.source_key, toolType)" :disabled="!pinsLoaded" @change="toggleManualPin(svc.source_key, toolType)" class="h-3.5 w-3.5 rounded border-input text-primary focus:ring-primary" />
                      {{ typeLabel(toolType) }}
                    </label>
                  </div>
                </div>
              </div>

              <div v-if="autoPinGroups.length > 0" class="rounded-md bg-secondary px-3 py-2">
                <div class="mb-1 text-xs font-medium text-muted-foreground">自动 Pin 预览</div>
                <div class="flex flex-wrap gap-2">
                  <span v-for="pin in autoPinGroups.slice(0, 12)" :key="`auto-${pin.service_key}-${pin.tool_type}`" class="max-w-full break-all rounded bg-background px-2 py-1 text-xs text-muted-foreground">
                    {{ serviceName(pin.service_key) }} / {{ typeLabel(pin.tool_type) }}{{ pin.calls ? ` · ${pin.calls} 次` : '' }}
                  </span>
                  <span v-if="autoPinGroups.length > 12" class="px-2 py-1 text-xs text-muted-foreground">另 {{ autoPinGroups.length - 12 }} 项</span>
                </div>
              </div>
            </div>

            <!-- Profile Prompt -->
            <div class="space-y-3 rounded-lg border border-border p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div class="text-sm font-medium">能力平面提示词</div>
                  <div class="mt-1 text-xs text-muted-foreground">手动补充保存后会写入 profile 文档。</div>
                </div>
                <Button variant="outline" size="sm" :disabled="docSaving" @click="refreshProfileDoc">{{ docSaving ? '生成中...' : '重新生成/预览' }}</Button>
              </div>
              <div v-if="docError" class="rounded-md bg-destructive-soft px-3 py-2 text-xs text-destructive">{{ docError }}</div>
              <div class="space-y-2">
                <label class="text-xs font-medium text-muted-foreground">手动补充 Notes</label>
                <textarea v-model="manualNotes" class="min-h-[96px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" placeholder="补充该 profile 的使用边界、注意事项或协作约定..." />
                <Button size="sm" :disabled="docSaving || manualNotes.trim().length === 0" @click="saveManualNotes">{{ docSaving ? '保存中...' : '保存手动补充' }}</Button>
              </div>
              <div>
                <div class="mb-2 text-xs font-medium text-muted-foreground">Markdown 预览</div>
                <pre class="max-h-[260px] overflow-y-auto whitespace-pre-wrap rounded-md bg-secondary p-3 text-xs leading-relaxed text-foreground">{{ profileMarkdown || '暂无 profile 文档预览' }}</pre>
              </div>
            </div>
          </div>
        </details>
      </div>
    </div>

    <div class="shrink-0 border-t border-border bg-card/95 px-1 py-3 backdrop-blur" aria-live="polite">
      <div v-if="saveError" class="mb-3 rounded-lg bg-destructive-soft p-3 text-sm text-destructive">{{ saveError }}</div>
      <div class="flex flex-wrap justify-end gap-2">
        <Button variant="outline" type="button" @click="emit('cancel')">取消</Button>
        <Button data-tour="profile-save" @click="saveConfig" :disabled="!canSaveConfig">{{ configSaving ? '保存中...' : '确认' }}</Button>
      </div>
    </div>
  </div>
</template>
