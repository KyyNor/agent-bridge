<script setup lang="ts">
/**
 * RunEventTimeline — a reusable timeline for agent / workflow run event streams.
 *
 * Renders the interleaved main-agent + sub-agent event timeline that was
 * previously duplicated across WorkflowView.vue (progress + tasks modes) and
 * AgentRunsView.vue (detail dialog). All three now compose this component.
 *
 * Responsibilities kept here:
 *  - event grouping & interleaving (buildTimeline) + the `.tl-*` styles
 *  - sub-agent collapse state (useSubagentCollapse), defaulting to collapsed
 *
 * Responsibilities left to the parent via the optional `#subagent-body` slot:
 *  - the rich sub-agent detail (e.g. WorkflowSubagentDetailPanel transcript).
 *    When the slot is omitted, a compact fallback (the sub-agent's own events)
 *    is rendered — this is what AgentRunsView uses.
 *
 * Emits `expand(taskId)` when a sub-agent is expanded, so the parent can lazily
 * load detail (workflow loads the Claude transcript on first expand).
 */
import { computed, ref, watch } from 'vue'
import { Bot, ChevronRight } from '@lucide/vue'
import { Badge } from './ui/badge'
import JsonViewer from './JsonViewer.vue'
import PayloadDetailDialog from './PayloadDetailDialog.vue'
import { useSubagentCollapse } from '../composables/useSubagentCollapse'
import { groupEventsByActor, subagentStatus, subagentStatusLabel, subagentUsage } from '../lib/workflowEvents'
import {
  buildTimeline,
  eventKindLabel,
  eventMessage as renderEventMessage,
  subagentStatusBadgeClass,
  timelineKind,
} from '../lib/runEventRender'
import { renderMarkdown as renderMd } from '../lib/markdown'
import { detectPayloadLanguage, preparePayloadPresentation, type PayloadLanguage } from '../lib/payloadPresentation'
import { formatLocalDatetime } from '../lib/time'
import type { WorkflowRunEvent } from '../api/types'

const props = withDefaults(
  defineProps<{
    events: WorkflowRunEvent[]
    /** Show the agent_name chip on main-agent rows (workflow progress mode). */
    showAgentName?: boolean
    /** Stable key namespacing the internal collapse state. Defaults to "tl". */
    contextKey?: string
    /** Loaded large payloads, keyed by the relative payload ref. */
    payloads?: Record<string, string>
    /** Payload loading errors, keyed by the relative payload ref. */
    payloadErrors?: Record<string, string>
  }>(),
  { showAgentName: false, contextKey: 'tl' },
)

const emit = defineEmits<{
  (e: 'expand', taskId: string): void
  (e: 'load-payload', ref: string): void
}>()

const { isCollapsed, toggle, initCollapsed } = useSubagentCollapse()

type PayloadSide = 'input' | 'output' | 'detail'
type PayloadTarget = { event: WorkflowRunEvent; side: PayloadSide; ref: string }
type PayloadModal = PayloadTarget & { content: string; language: PayloadLanguage }

const LONG_PAYLOAD_MARKER = '…（内容较长，点击查看完整内容）'

const payloadModal = ref<PayloadModal | null>(null)
const pendingPayloadModal = ref<PayloadTarget | null>(null)

// (Re)initialise collapse state whenever the event stream changes so freshly
// loaded runs start with all sub-agents collapsed.
watch(
  () => props.events,
  (events) => initCollapsed(props.contextKey, events),
  { immediate: true },
)

const groups = computed(() => groupEventsByActor(props.events))
const timeline = computed(() => buildTimeline(groups.value))

function onToggle(taskId: string) {
  const wasCollapsed = isCollapsed(props.contextKey, taskId)
  toggle(props.contextKey, taskId)
  if (wasCollapsed) emit('expand', taskId)
}

function payloadRef(event: WorkflowRunEvent, side: PayloadSide): string {
  const value = event[`${side}_payload_ref`]
  return typeof value === 'string' ? value : ''
}

function payloadPreview(event: WorkflowRunEvent, side: PayloadSide): string {
  const preview = event[`${side}_preview`]
  if (typeof preview === 'string') return preview
  const value = event[side]
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function payloadValueText(event: WorkflowRunEvent, side: PayloadSide): string {
  const value = event[side]
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function payloadIsJson(event: WorkflowRunEvent, side: PayloadSide): boolean {
  const value = event[side]
  if (value && typeof value === 'object') return true
  const content = payloadValueText(event, side) || payloadPreview(event, side)
  return detectPayloadLanguage(content, {
    contentType: String(event[`${side}_content_type`] || ''),
    ref: payloadRef(event, side),
  }) === 'json'
}

/** Support old events that retained the full value but only stored a preview marker. */
function inlinePayload(event: WorkflowRunEvent, side: PayloadSide): string {
  const full = payloadValueText(event, side)
  if (!full) return ''
  const preview = event[`${side}_preview`]
  if (typeof preview === 'string' && preview.includes(LONG_PAYLOAD_MARKER)) {
    return preview === full ? '' : full
  }
  return full
}

function canOpenPayload(event: WorkflowRunEvent, side: PayloadSide): boolean {
  return Boolean(payloadRef(event, side) || inlinePayload(event, side))
}

function payloadSize(event: WorkflowRunEvent, side: PayloadSide): string {
  const value = event[`${side}_bytes`]
  if (typeof value !== 'number') return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function loadedPayload(ref: string): string {
  return props.payloads?.[ref] || ''
}

function payloadError(ref: string): string {
  return props.payloadErrors?.[ref] || ''
}

function buildPayloadModal(target: PayloadTarget, content: string): PayloadModal {
  const presentation = preparePayloadPresentation(content, {
    contentType: String(target.event[`${target.side}_content_type`] || ''),
    ref: target.ref,
  })
  return {
    ...target,
    ...presentation,
  }
}

function openPayload(event: WorkflowRunEvent, side: PayloadSide) {
  const ref = payloadRef(event, side)
  const target = { event, side, ref }
  const content = ref ? loadedPayload(ref) : inlinePayload(event, side)
  if (!content) {
    if (!ref) return
    pendingPayloadModal.value = target
    emit('load-payload', ref)
    return
  }
  payloadModal.value = buildPayloadModal(target, content)
}

watch(
  () => props.payloads,
  () => {
    const pending = pendingPayloadModal.value
    if (!pending) return
    const content = loadedPayload(pending.ref)
    if (!content) return
    pendingPayloadModal.value = null
    payloadModal.value = buildPayloadModal(pending, content)
  },
)

function closePayload() {
  payloadModal.value = null
  pendingPayloadModal.value = null
}
</script>

<template>
  <div class="tl-timeline">
    <template v-for="(entry, idx) in timeline" :key="contextKey + ':tl:' + idx">
      <!-- Main agent event row -->
      <div v-if="entry.actor.role === 'main'" class="tl-event" :class="'k-' + timelineKind(entry.event)">
        <div class="tl-avatar" />
        <div class="tl-body">
          <div class="tl-head">
            <span class="tl-kind">{{ eventKindLabel(entry.event) }}</span>
            <span v-if="showAgentName && entry.event.agent_name" class="tl-target">{{ entry.event.agent_name }}</span>
            <span v-if="entry.event.tool_name" class="tl-target"><b>{{ entry.event.tool_name }}</b></span>
            <span v-if="entry.event.duration_ms != null" class="tl-duration">{{ entry.event.duration_ms }}ms</span>
            <span v-if="entry.event.created_at" class="tl-time">{{ formatLocalDatetime(entry.event.created_at) }}</span>
          </div>
          <div v-if="renderEventMessage(entry.event)" class="tl-content">
            <div v-if="entry.event.message" class="tl-md" v-html="renderMd(entry.event.message)" />
            <p v-else>{{ renderEventMessage(entry.event) }}</p>
          </div>
          <div
            v-if="entry.event.kind === 'tool_call' || entry.event.kind === 'tool_result' || entry.event.kind === 'structured_output'"
            class="tl-tool-details"
          >
            <div v-if="entry.event.kind === 'tool_call' && payloadPreview(entry.event, 'input')" class="tl-tool-payload">
              <div class="tl-tool-payload-head">
                <span>输入<span v-if="payloadSize(entry.event, 'input')"> · {{ payloadSize(entry.event, 'input') }}</span></span>
                <button
                  v-if="canOpenPayload(entry.event, 'input')"
                  type="button"
                  class="tl-payload-button"
                  @click.stop="openPayload(entry.event, 'input')"
                >
                  查看
                </button>
              </div>
              <JsonViewer
                v-if="payloadIsJson(entry.event, 'input')"
                :value="payloadPreview(entry.event, 'input')"
                max-height="180px"
                density="compact"
              />
              <pre v-else>{{ payloadPreview(entry.event, 'input') }}</pre>
              <div v-if="payloadError(payloadRef(entry.event, 'input'))" class="tl-payload-error">{{ payloadError(payloadRef(entry.event, 'input')) }}</div>
            </div>
            <div v-if="(entry.event.kind === 'tool_result' || entry.event.kind === 'tool_call' || entry.event.kind === 'structured_output') && payloadPreview(entry.event, 'output')" class="tl-tool-payload">
              <div class="tl-tool-payload-head">
                <span>输出<span v-if="payloadSize(entry.event, 'output')"> · {{ payloadSize(entry.event, 'output') }}</span></span>
                <button
                  v-if="canOpenPayload(entry.event, 'output')"
                  type="button"
                  class="tl-payload-button"
                  @click.stop="openPayload(entry.event, 'output')"
                >
                  查看
                </button>
              </div>
              <JsonViewer
                v-if="payloadIsJson(entry.event, 'output')"
                :value="payloadPreview(entry.event, 'output')"
                max-height="180px"
                density="compact"
              />
              <pre v-else>{{ payloadPreview(entry.event, 'output') }}</pre>
              <div v-if="payloadError(payloadRef(entry.event, 'output'))" class="tl-payload-error">{{ payloadError(payloadRef(entry.event, 'output')) }}</div>
            </div>
          </div>
          <div v-if="payloadPreview(entry.event, 'detail')" class="tl-event-detail">
            <div class="tl-tool-payload-head">
              <span>详细信息<span v-if="payloadSize(entry.event, 'detail')"> · {{ payloadSize(entry.event, 'detail') }}</span></span>
              <button
                v-if="canOpenPayload(entry.event, 'detail')"
                type="button"
                class="tl-payload-button"
                @click.stop="openPayload(entry.event, 'detail')"
              >
                查看
              </button>
            </div>
            <JsonViewer
              v-if="payloadIsJson(entry.event, 'detail')"
              :value="payloadPreview(entry.event, 'detail')"
              max-height="260px"
              density="compact"
            />
            <pre v-else>{{ payloadPreview(entry.event, 'detail') }}</pre>
            <div v-if="payloadError(payloadRef(entry.event, 'detail'))" class="tl-payload-error">{{ payloadError(payloadRef(entry.event, 'detail')) }}</div>
          </div>
        </div>
      </div>

      <!-- Sub-agent thread card (collapsed by default) -->
      <div v-else class="tl-event">
        <div class="tl-avatar tl-avatar-subagent" />
        <div
          class="tl-sub"
          :class="{
            open: !isCollapsed(contextKey, entry.actor.id),
            'is-failed': subagentStatus(events, entry.actor.id) === 'failed' || subagentStatus(events, entry.actor.id) === 'error',
          }"
        >
          <button type="button" class="tl-sub-head" @click="onToggle(entry.actor.id)">
            <span class="tl-bot"><Bot :size="14" /></span>
            <span>
              <span class="tl-sub-id block leading-tight">{{ entry.actor.label }}</span>
              <span class="tl-sub-desc block">task {{ entry.actor.id.slice(0, 8) }}</span>
            </span>
            <span class="tl-sub-stats">
              <span v-if="subagentUsage(events, entry.actor.id)">
                <b>{{ subagentUsage(events, entry.actor.id)?.total_tokens ?? 0 }}</b> tokens ·
                <b>{{ subagentUsage(events, entry.actor.id)?.tool_uses ?? 0 }}</b> 工具
              </span>
              <Badge variant="outline" :class="subagentStatusBadgeClass(subagentStatus(events, entry.actor.id))" class="text-[10px]">
                {{ subagentStatusLabel(events, entry.actor.id) }}
              </Badge>
              <ChevronRight :size="14" class="tl-chevron" />
            </span>
          </button>
          <div v-if="!isCollapsed(contextKey, entry.actor.id)" class="tl-sub-body">
            <!-- Parent injects rich sub-agent detail (e.g. transcript panel). -->
            <slot name="subagent-body" :task-id="entry.actor.id" :actor="entry.actor">
              <!-- Default fallback: render this sub-agent's events as plain rows. -->
              <div class="space-y-1.5">
                <div
                  v-for="(ev, i) in groups.find(g => g.actor.id === entry.actor.id)?.events || []"
                  :key="i"
                  class="flex items-start gap-2 rounded-md border border-border/60 bg-background px-3 py-2 text-xs"
                >
                  <Badge variant="secondary" :class="subagentStatusBadgeClass(ev.status)">{{ eventKindLabel(ev) }}</Badge>
                  <div class="flex-1">
                    <div v-if="renderEventMessage(ev)" class="break-all">{{ renderEventMessage(ev) }}</div>
                    <div class="mt-0.5 flex flex-wrap gap-x-3 text-muted-foreground">
                      <span v-if="ev.tool_name" class="font-mono">{{ ev.tool_name }}</span>
                      <span v-if="ev.created_at">{{ formatLocalDatetime(ev.created_at) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </slot>
          </div>
        </div>
      </div>
    </template>
  </div>

  <PayloadDetailDialog
    v-if="payloadModal"
    :open="payloadModal !== null"
    title="查看"
    :label="`${payloadModal.side === 'input' ? '输入' : payloadModal.side === 'output' ? '输出' : '详细信息'}${payloadSize(payloadModal.event, payloadModal.side) ? ` · ${payloadSize(payloadModal.event, payloadModal.side)}` : ''}`"
    :content="payloadModal.content"
    :language="payloadModal.language"
    @update:open="(open: boolean) => { if (!open) closePayload() }"
  />
</template>

<style>
/* ============================================================
   Agent 输出 时间轴 (timeline)
   - 用主题 token 变量 (var(--*)) 兼容明暗模式
   - 命名空间 tl- 避免与其它组件冲突
   - 一个强调色 (primary 蓝), 子 agent 紫色仅作"角色"语义区分
   NOTE: intentionally non-scoped — WorkflowSubagentDetailPanel (rendered via
   the subagent-body slot) reuses the .tl-mini-* / .tl-result classes below.
   ============================================================ */
.tl-timeline{position:relative;padding:2px 0 0;max-width:100%;overflow-x:hidden}
.tl-timeline::before{content:"";position:absolute;left:18px;top:6px;bottom:6px;width:2px;background:var(--border);border-radius:var(--radius-compact)}
.tl-event{position:relative;padding:0 0 12px 46px}
.tl-event:last-child{padding-bottom:0}
.tl-avatar{position:absolute;left:10px;top:2px;width:18px;height:18px;border-radius:50%;background:var(--card);border:2px solid var(--primary);display:flex;align-items:center;justify-content:center;z-index:2}
.tl-avatar::after{content:"";width:8px;height:8px;border-radius:50%;background:var(--primary)}
.tl-event.k-think .tl-avatar{border-color:var(--warning)}
.tl-event.k-think .tl-avatar::after{background:var(--warning)}
.tl-event.k-tool .tl-avatar{border-color:var(--info)}
.tl-event.k-tool .tl-avatar::after{background:var(--info)}
.tl-event.k-result .tl-avatar{border-color:var(--success)}
.tl-event.k-result .tl-avatar::after{background:var(--success)}
.tl-event.k-error .tl-avatar{border-color:var(--destructive)}
.tl-event.k-error .tl-avatar::after{background:var(--destructive)}
.tl-body{min-width:0;max-width:100%;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-card);overflow:hidden;transition:border-color .12s ease}
.tl-body:hover{border-color:var(--input)}
.tl-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 14px;border-bottom:1px solid var(--border)}
.tl-kind{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:var(--radius-compact)}
.k-message .tl-kind{background:var(--accent);color:var(--accent-foreground)}
.k-think .tl-kind{background:rgb(var(--warning-rgb) / .16);color:var(--warning)}
.k-tool .tl-kind{background:rgb(var(--info-rgb) / .16);color:var(--info)}
.k-result .tl-kind{background:rgb(var(--success-rgb) / .16);color:var(--success)}
.k-error .tl-kind{background:rgb(var(--destructive-rgb) / .16);color:var(--destructive)}
.tl-target{font-family:var(--font-mono);font-size:12px;color:var(--muted-foreground)}
.tl-target b{color:var(--foreground);font-weight:600}
.tl-time{margin-left:auto;font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);flex-shrink:0;opacity:.85}
.tl-duration{font-family:var(--font-mono);font-size:11px;color:var(--foreground);font-weight:600}
.tl-content{min-width:0;max-width:100%;padding:11px 14px;font-size:13.5px;color:var(--foreground);line-height:1.6;overflow-wrap:anywhere;word-break:break-word}
.tl-content p{margin:0 0 6px}
.tl-content p:last-child{margin-bottom:0}
.k-message .tl-content{font-size:14px;line-height:1.65}
.tl-content pre{margin:8px 0 0;background:var(--muted);border-radius:var(--radius-control);padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;line-height:1.6;color:var(--foreground);overflow-x:hidden;overflow-y:auto;max-height:220px;white-space:pre-wrap;overflow-wrap:anywhere}

/* ===== Markdown rendered inside timeline (.tl-md) ===== */
.tl-md{max-width:100%;font-size:inherit;line-height:inherit;color:inherit;word-break:break-word;overflow-wrap:anywhere}
.tl-md>:first-child{margin-top:0}
.tl-md>:last-child{margin-bottom:0}
.tl-md p{margin:0 0 6px}
.tl-md p:last-child{margin-bottom:0}
.tl-md h1,.tl-md h2,.tl-md h3,.tl-md h4{font-weight:600;line-height:1.3;margin:14px 0 6px;color:var(--foreground)}
.tl-md h1{font-size:1.18em}
.tl-md h2{font-size:1.1em}
.tl-md h3{font-size:1.02em}
.tl-md h4{font-size:.96em}
.tl-md ul,.tl-md ol{margin:4px 0 6px;padding-left:20px}
.tl-md li{margin:2px 0}
.tl-md li::marker{color:var(--muted-foreground)}
.tl-md a{color:var(--primary);text-decoration:underline;text-underline-offset:2px}
.tl-md a:hover{opacity:.8}
.tl-md blockquote{margin:6px 0;padding:4px 12px;border-left:3px solid var(--border);color:var(--muted-foreground)}
.tl-md blockquote p{margin:2px 0}
.tl-md hr{border:none;border-top:1px solid var(--border);margin:10px 0}
.tl-md code{font-family:var(--font-mono);font-size:.88em;background:var(--muted);padding:1px 5px;border-radius:var(--radius-compact);color:var(--foreground)}
.tl-md pre{margin:8px 0;background:var(--muted);border-radius:var(--radius-control);padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;line-height:1.6;color:var(--foreground);overflow-x:hidden;overflow-y:auto;max-height:260px;white-space:pre-wrap;overflow-wrap:anywhere}
.tl-md pre code{background:transparent;padding:0;font-size:inherit;border-radius:0}
.tl-md table{width:100%;border-collapse:collapse;margin:8px 0;font-size:.95em}
.tl-md th,.tl-md td{border:1px solid var(--border);padding:5px 8px;text-align:left}
.tl-md th{background:var(--muted);font-weight:600}

/* ===== Tool payload details ===== */
.tl-tool-details{display:flex;flex-direction:column;gap:8px;padding:0 14px 11px}
.tl-tool-payload{min-width:0;border:1px solid var(--border);border-radius:var(--radius-control);background:var(--muted);overflow:hidden}
.tl-tool-payload-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 9px;color:var(--muted-foreground);font-size:11px;font-weight:600}
.tl-tool-payload pre{margin:0;padding:7px 9px;max-height:180px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--font-mono);font-size:11px;line-height:1.5;color:var(--foreground)}
.tl-event-detail{padding:0 14px 11px}
.tl-event-detail pre{margin:0;padding:9px 10px;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--muted);border-radius:var(--radius-control);font-family:var(--font-mono);font-size:11px;line-height:1.5;color:var(--foreground)}
.tl-payload-button{border:1px solid var(--border);border-radius:var(--radius-compact);padding:2px 7px;color:var(--primary);background:var(--card);font-size:10px;cursor:pointer}
.tl-payload-button:hover{background:var(--accent)}
.tl-payload-error{padding:4px 9px;color:var(--destructive);font-size:11px}
/* ===== Subagent thread card ===== */
.tl-sub{position:relative;min-width:0;max-width:100%;background:var(--subagent-surface);border:1px solid var(--subagent-border);border-radius:var(--radius-card);overflow:hidden;transition:border-color .12s ease}
.tl-sub-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;cursor:pointer;width:100%;text-align:left;background:transparent;border:none}
.tl-sub-head:hover{background:rgb(var(--cat-violet-fg-rgb) / .06)}
.tl-bot{width:26px;height:26px;border-radius:var(--radius-control);flex-shrink:0;background:linear-gradient(135deg,var(--cat-violet-fg),var(--subagent-bot-end));display:flex;align-items:center;justify-content:center;color:var(--primary-foreground)}
.tl-sub.is-failed .tl-bot{background:linear-gradient(135deg,var(--destructive),var(--subagent-failed-bot-end))}
.tl-sub-id{font-weight:600;color:var(--cat-violet-fg);font-size:13.5px}
:root.dark .tl-sub-id{color:var(--cat-violet-fg)}
.tl-sub-desc{font-size:12px;color:var(--muted-foreground)}
.tl-sub-head > span:not(.tl-bot):not(.tl-sub-stats){min-width:0}
.tl-sub-stats{min-width:0;margin-left:auto;display:flex;align-items:center;gap:10px;font-size:11.5px;color:var(--muted-foreground);font-family:var(--font-mono);flex-wrap:wrap}
.tl-sub-stats b{color:var(--foreground);font-weight:600}
.tl-chevron{color:var(--cat-violet-fg);transition:transform .15s ease;flex-shrink:0}
:root.dark .tl-chevron{color:var(--cat-violet-fg)}
.tl-sub.open .tl-chevron{transform:rotate(90deg)}
.tl-sub-body{min-width:0;max-width:100%;padding:8px 14px 14px;background:var(--card);border-top:1px solid var(--subagent-body-border);overflow-x:hidden}
.tl-result{margin:12px 0 4px;padding:10px 12px;background:var(--subagent-result-surface);border:1px solid var(--subagent-result-border);border-left:3px solid var(--cat-violet-fg);border-radius:var(--radius-control)}
.tl-sub.is-failed .tl-result{background:var(--subagent-failed-result-surface);border-color:var(--subagent-failed-result-border);border-left-color:var(--destructive)}
.tl-result-label{font-size:11px;font-weight:600;color:var(--cat-violet-fg);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px;display:flex;align-items:center;gap:5px}
:root.dark .tl-result-label{color:var(--cat-violet-fg)}
.tl-sub.is-failed .tl-result-label{color:var(--destructive)}
.tl-result-text{min-width:0;font-size:13px;color:var(--foreground);line-height:1.6;overflow-wrap:anywhere}
.tl-result-text pre{margin:6px 0 0;background:var(--muted);border-radius:var(--radius-control);padding:8px 10px;font-family:var(--font-mono);font-size:11.5px;color:var(--foreground);overflow-x:hidden;overflow-y:auto;max-height:200px;white-space:pre-wrap;overflow-wrap:anywhere}

/* ===== Mini timeline inside subagent (used by WorkflowSubagentDetailPanel) ===== */
.tl-mini{position:relative;padding:6px 0 0 4px;margin-top:10px}
.tl-mini::before{content:"";position:absolute;left:9px;top:10px;bottom:10px;width:2px;background:var(--subagent-timeline-line);border-radius:var(--radius-compact)}
.tl-mini-event{position:relative;padding:0 0 10px 22px}
.tl-mini-event:last-child{padding-bottom:0}
.tl-avatar-subagent{border-color:var(--cat-violet-fg)}
.tl-mavatar{position:absolute;left:0;top:1px;width:12px;height:12px;border-radius:50%;background:var(--card);border:2px solid var(--cat-violet-fg);z-index:2}
.tl-mini-event.k-think .tl-mavatar{border-color:var(--warning)}
.tl-mini-event.k-tool .tl-mavatar{border-color:var(--info)}
.tl-mini-event.k-result .tl-mavatar{border-color:var(--success)}
.tl-mini-event.k-error .tl-mavatar{border-color:var(--destructive)}
.tl-mini-head{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:3px}
.tl-mini-kind{font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:var(--radius-compact)}
.tl-mini-event.k-think .tl-mini-kind{background:rgb(var(--warning-rgb) / .16);color:var(--warning)}
.tl-mini-event.k-tool .tl-mini-kind{background:rgb(var(--info-rgb) / .16);color:var(--info)}
.tl-mini-event.k-result .tl-mini-kind{background:rgb(var(--success-rgb) / .16);color:var(--success)}
.tl-mini-event.k-error .tl-mini-kind{background:rgb(var(--destructive-rgb) / .16);color:var(--destructive)}
.tl-mini-event.k-message .tl-mini-kind{background:rgb(var(--cat-violet-fg-rgb) / .14);color:var(--cat-violet-fg)}
:root.dark .tl-mini-event.k-message .tl-mini-kind{color:var(--cat-violet-fg)}
.tl-mini-target{font-family:var(--font-mono);font-size:11.5px;color:var(--muted-foreground)}
.tl-mini-target b{color:var(--foreground);font-weight:600}
.tl-mini-time{margin-left:auto;font-family:var(--font-mono);font-size:10.5px;color:var(--muted-foreground);opacity:.8}
.tl-mini-content{min-width:0;max-width:100%;font-size:12.5px;color:var(--muted-foreground);line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
.tl-mini-content.tl-dump{font-family:var(--font-mono);font-size:11.5px;color:var(--foreground);background:var(--muted);padding:6px 9px;border-radius:var(--radius-compact);margin-top:4px;white-space:pre-wrap;overflow-x:hidden;overflow-y:auto;max-height:160px;overflow-wrap:anywhere}
.tl-mini-content > .tl-md{color:var(--foreground);white-space:normal;word-break:break-word}
.tl-mini-action{border:0;background:transparent;padding:0;color:var(--primary);font-size:10.5px;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.tl-mini-action:hover{opacity:.8}
.tl-mini-payload-action{margin-top:4px}

@media (prefers-reduced-motion: reduce){
  .tl-body,.tl-sub,.tl-chevron{transition:none}
}
</style>
