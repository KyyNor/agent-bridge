<script setup lang="ts">
/**
 * Generic version-history + diff panel. Shared by scripts, workflows and skills.
 *
 * Left: scrollable list of revisions (newest first). Each row is selectable as
 * the "from" or "to" endpoint of a diff; the current version is flagged.
 * Right: the diff between the two selected revisions — structured view for
 * workflows, unified text for scripts/skills, with a toggle to swap to raw
 * unified text for workflows too.
 */
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { Revision, DiffResult, VersionedEntity } from '@/api/types'
import { timeAgo } from '@/lib/time'
import UnifiedDiff from '@/components/diff/UnifiedDiff.vue'
import WorkflowStructuredDiff from '@/components/diff/WorkflowStructuredDiff.vue'

const props = defineProps<{
  entityType: VersionedEntity
  entityKey: string
}>()

const revisions = ref<Revision[]>([])
const loading = ref(false)
const error = ref('')

// Selected revision numbers for the diff. Defaults: current vs. previous.
const fromNo = ref<number | null>(null)
const toNo = ref<number | null>(null)

const diff = ref<DiffResult | null>(null)
const diffLoading = ref(false)
const diffError = ref('')
// For workflows: toggle between structured and raw-unified rendering.
const workflowView = ref<'structured' | 'text'>('structured')

const isWorkflow = computed(() => props.entityType === 'workflow')

async function loadRevisions() {
  loading.value = true
  error.value = ''
  try {
    const list =
      props.entityType === 'script'
        ? await api.listScriptRevisions(props.entityKey)
        : props.entityType === 'workflow'
          ? await api.listWorkflowRevisions(props.entityKey)
          : await api.listSkillRevisions(props.entityKey)
    revisions.value = list
    if (list.length > 0) {
      const current = list.find((r) => r.is_current) || list[0]
      toNo.value = current.revision_no
      fromNo.value = list.length > 1 ? list[1].revision_no : current.revision_no
    } else {
      fromNo.value = null
      toNo.value = null
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    revisions.value = []
  } finally {
    loading.value = false
  }
}

async function loadDiff() {
  if (fromNo.value == null || toNo.value == null) {
    diff.value = null
    return
  }
  diffLoading.value = true
  diffError.value = ''
  try {
    diff.value =
      props.entityType === 'script'
        ? await api.diffScript(props.entityKey, fromNo.value, toNo.value)
        : props.entityType === 'workflow'
          ? await api.diffWorkflow(props.entityKey, fromNo.value, toNo.value)
          : await api.diffSkill(props.entityKey, fromNo.value, toNo.value)
  } catch (e) {
    diff.value = null
    diffError.value = e instanceof Error ? e.message : String(e)
  } finally {
    diffLoading.value = false
  }
}

function pickFrom(no: number) {
  if (toNo.value === no) return
  fromNo.value = no
}
function pickTo(no: number) {
  if (fromNo.value === no) return
  toNo.value = no
}
function swap() {
  const a = fromNo.value
  fromNo.value = toNo.value
  toNo.value = a
}

watch(() => [props.entityType, props.entityKey], loadRevisions, { immediate: true })
watch([fromNo, toNo], loadDiff)

const hasRevisions = computed(() => revisions.value.length > 0)
</script>

<template>
  <div class="space-y-3">
    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
      {{ error }}
    </div>

    <div v-if="loading" class="px-3 py-6 text-center text-sm text-muted-foreground">加载版本列表…</div>

    <div v-else-if="!hasRevisions" class="rounded-md border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
      尚无历史版本。保存一次变更后将自动产生版本。
    </div>

    <div v-else class="grid gap-4 lg:grid-cols-[280px_1fr]">
      <!-- Revision list -->
      <aside class="space-y-2">
        <div class="flex items-center justify-between">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            版本列表 ({{ revisions.length }})
          </h4>
          <button
            type="button"
            class="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-40"
            :disabled="fromNo == null || toNo == null"
            @click="swap"
          >
            ⇄ 交换
          </button>
        </div>
        <ul class="max-h-[60vh] space-y-1 overflow-y-auto rounded-md border border-border bg-card p-1">
          <li
            v-for="rev in revisions"
            :key="rev.revision_no"
            :class="[
              'cursor-pointer rounded-sm border px-2 py-1.5 text-sm transition-colors',
              toNo === rev.revision_no
                ? 'border-success/40 bg-success-soft'
                : fromNo === rev.revision_no
                  ? 'border-destructive/40 bg-destructive-soft'
                  : 'border-transparent hover:bg-secondary/60',
            ]"
            @click="pickTo(rev.revision_no)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="font-mono font-medium">v{{ rev.revision_no }}</span>
              <span
                v-if="rev.is_current"
                class="rounded bg-secondary px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
              >当前</span>
            </div>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ timeAgo(rev.created_at) }} · {{ rev.created_by }}
            </div>
            <div class="mt-1 flex gap-2">
              <button
                type="button"
                :class="[
                  'rounded px-1.5 py-0.5 text-[10px] font-medium',
                  fromNo === rev.revision_no
                    ? 'bg-destructive-soft text-destructive-soft-fg'
                    : 'bg-secondary text-muted-foreground hover:text-foreground',
                ]"
                @click.stop="pickFrom(rev.revision_no)"
              >旧版 (from)</button>
              <button
                type="button"
                :class="[
                  'rounded px-1.5 py-0.5 text-[10px] font-medium',
                  toNo === rev.revision_no
                    ? 'bg-success-soft text-success-soft-fg'
                    : 'bg-secondary text-muted-foreground hover:text-foreground',
                ]"
                @click.stop="pickTo(rev.revision_no)"
              >新版 (to)</button>
            </div>
          </li>
        </ul>
        <p class="px-1 text-[11px] leading-4 text-muted-foreground">
          点击行选中「新版」，点击「旧版」按钮设定对比起点。
        </p>
      </aside>

      <!-- Diff view -->
      <section class="min-w-0 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="text-sm text-muted-foreground">
            对比
            <span class="font-mono font-medium text-destructive-soft-fg">v{{ fromNo }}</span>
            <span class="mx-1">→</span>
            <span class="font-mono font-medium text-success-soft-fg">v{{ toNo }}</span>
          </div>
          <div v-if="isWorkflow && diff?.structured" class="inline-flex h-8 items-center gap-0.5 rounded-md bg-secondary p-1">
            <button
              type="button"
              :class="[
                'h-6 rounded-sm px-2 text-xs font-medium transition-colors',
                workflowView === 'structured' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="workflowView = 'structured'"
            >结构化</button>
            <button
              type="button"
              :class="[
                'h-6 rounded-sm px-2 text-xs font-medium transition-colors',
                workflowView === 'text' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="workflowView = 'text'"
            >文本 diff</button>
          </div>
        </div>

        <div v-if="diffError" class="rounded-md border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-soft-fg">
          {{ diffError }}
        </div>

        <div v-else-if="diffLoading" class="px-3 py-8 text-center text-sm text-muted-foreground">计算差异…</div>

        <template v-else-if="diff">
          <WorkflowStructuredDiff
            v-if="isWorkflow && workflowView === 'structured' && diff.structured"
            :diff="diff.structured"
          />
          <UnifiedDiff
            v-else
            :content="diff.text.content"
            :caption="`${diff.text.from_label} → ${diff.text.to_label}`"
          />
        </template>
      </section>
    </div>
  </div>
</template>
