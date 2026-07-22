import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

function readSrc(rel: string): string {
  return readFileSync(resolve(root, rel), 'utf-8')
}

// --- types + client wiring -------------------------------------------------

test('types.ts declares version / diff / syntax-check contracts', () => {
  const file = readSrc('src/api/types.ts')
  assert.match(file, /export type VersionedEntity = 'script' \| 'workflow' \| 'skill'/)
  assert.match(file, /export interface Revision \{[\s\S]*?revision_no: number[\s\S]*?is_current\?: boolean/)
  assert.match(file, /export interface SyntaxCheckResult \{[\s\S]*?ok: boolean[\s\S]*?errors:/)
  assert.match(file, /export interface DiffResult \{[\s\S]*?entity_type: VersionedEntity[\s\S]*?text: DiffText[\s\S]*?structured\?: WorkflowStructuredDiff/)
  // ManagedScript carries the new optional fields.
  assert.match(file, /revision_no\?: number[\s\S]*?syntax_check\?: SyntaxCheckResult/)
})

test('client.ts exposes revision / diff / validate methods for all three entities', () => {
  const file = readSrc('src/api/client.ts')
  assert.match(file, /validateScriptCode:/)
  assert.match(file, /listScriptRevisions:/)
  assert.match(file, /getScriptRevision:/)
  assert.match(file, /diffScript:/)
  assert.match(file, /listSkillRevisions:/)
  assert.match(file, /getSkillRevision:/)
  assert.match(file, /diffSkill:/)
  assert.match(file, /listWorkflowRevisions:/)
  assert.match(file, /getWorkflowRevision:/)
  assert.match(file, /diffWorkflow:/)
})

// --- shared components ------------------------------------------------------

test('UnifiedDiff parses the backend unified text without a third-party diff dep', () => {
  const file = readSrc('src/components/diff/UnifiedDiff.vue')
  assert.match(file, /import \{ parseUnifiedDiff, diffStats/)
  // No external diff package import.
  assert.doesNotMatch(file, /from ['"]diff['"]|from ['"]fast-diff['"]|from ['"]deep-diff['"]/)
})

test('WorkflowStructuredDiff renders node/edge/metadata changes', () => {
  const file = readSrc('src/components/diff/WorkflowStructuredDiff.vue')
  assert.match(file, /diff\.nodes\.added/)
  assert.match(file, /diff\.nodes\.removed/)
  assert.match(file, /diff\.nodes\.changed/)
  assert.match(file, /diff\.edges\.added/)
  assert.match(file, /diff\.edges\.changed/)
  assert.match(file, /diff\.metadata/)
  assert.doesNotMatch(file, /(?:bg|text|border)-(?:emerald|rose|amber)-\d+/)
})

test('RevisionHistoryPanel switches between structured and text views for workflows', () => {
  const file = readSrc('src/components/version/RevisionHistoryPanel.vue')
  assert.match(file, /entityType: VersionedEntity/)
  assert.match(file, /workflowView = ref<'structured' \| 'text'>\('structured'\)/)
  assert.match(file, /WorkflowStructuredDiff/)
  assert.match(file, /UnifiedDiff/)
  // Dispatches to the correct API per entity type.
  assert.match(file, /api\.listScriptRevisions/)
  assert.match(file, /api\.listWorkflowRevisions/)
  assert.match(file, /api\.listSkillRevisions/)
  assert.doesNotMatch(file, /(?:bg|text|border)-(?:emerald|rose|amber)-\d+/)
})

test('workflow version history exposes source labels and restore action', () => {
  const file = readSrc('src/components/version/RevisionHistoryPanel.vue')
  assert.match(file, /sourceLabel|来源/)
  assert.match(file, /restoreWorkflowRevision|恢复此版本/)
})

test('workflow API client exposes export, restore, and import calls', () => {
  const file = readSrc('src/api/client.ts')
  const types = readSrc('src/api/types.ts')
  const view = readSrc('src/views/workflow/WorkflowView.vue')
  assert.match(file, /restoreWorkflowRevision/)
  assert.match(file, /exportWorkflow/)
  assert.match(file, /previewWorkflowImport/)
  assert.match(file, /confirmWorkflowImport/)
  assert.match(types, /revision_source: WorkflowRevisionSource/)
  assert.match(file, /formatHttpError/)
  assert.doesNotMatch(file, /return `\$\{status\}:/)
  assert.match(view, /const workflowDetailError = ref\(''\)/)
  assert.match(view, /v-if="workflowDetailError"/)
})

test('unifiedDiff parser classifies add / del / hunk rows', () => {
  const file = readSrc('src/lib/unifiedDiff.ts')
  assert.match(file, /export type DiffLineType = 'hunk' \| 'add' \| 'del' \| 'ctx'/)
  assert.match(file, /export function parseUnifiedDiff/)
  assert.match(file, /export function diffStats/)
  assert.match(file, /headerLines/)
})

// --- entry integration ------------------------------------------------------

test('script editor surfaces syntax warnings and a version-history panel', () => {
  const file = readSrc('src/views/system/ScriptsView.vue')
  // Live validation against /scripts/validate.
  assert.match(file, /api\.validateScriptCode/)
  // Syntax banner uses the warning-soft token pair (warn-but-allow policy).
  assert.match(file, /bg-warning-soft/)
  assert.match(file, /text-warning-soft-fg/)
  // Version history button + panel.
  assert.match(file, /版本历史/)
  assert.match(file, /RevisionHistoryPanel/)
  assert.match(file, /entity-type="script"/)
  assert.match(file, /<Dialog v-if="editingKey" v-model:open="showHistory">/)
  assert.match(file, /脚本版本历史/)
  // Debounced validation must ignore stale responses after a newer edit or route change.
  assert.match(file, /syntaxRequestId/)
  assert.match(file, /requestId === syntaxRequestId/)
})

test('skill editor exposes a collapsible version-history section', () => {
  const file = readSrc('src/views/system/SkillManagementView.vue')
  assert.match(file, /RevisionHistoryPanel/)
  assert.match(file, /entity-type="skill"/)
  assert.match(file, /版本历史/)
  // Skill prompts are editable/admin-controlled input and must not inject raw HTML into v-html.
  assert.match(file, /function escapeHtml/)
  assert.match(file, /previewRenderer\.html/)
  assert.match(file, /renderer: previewRenderer/)
})

test('workflow detail adds a versions tab wired into the segmented control', () => {
  const file = readSrc('src/views/workflow/WorkflowView.vue')
  assert.match(file, /import RevisionHistoryPanel from ['"]\.\.\/\.\.\/components\/version\/RevisionHistoryPanel\.vue['"]/)
  assert.match(file, /\{ key: 'versions', label: '版本历史' \}/)
  assert.match(file, /detailTab === 'versions'/)
  assert.match(file, /entity-type="workflow"/)
  // Guard accepts the new value.
  assert.match(file, /value !== 'versions'/)
})

test('workflow import UI supports new, overwrite, diff, and confirmation', () => {
  const dialog = readSrc('src/components/workflow/WorkflowImportDialog.vue')
  const view = readSrc('src/views/workflow/WorkflowView.vue')
  assert.match(dialog, /新工作流/)
  assert.match(dialog, /覆盖现有工作流/)
  assert.match(dialog, /bg-destructive-soft/)
  assert.match(dialog, /text-destructive-soft-fg/)
  assert.match(dialog, /border-destructive\/30/)
  assert.match(dialog, /WorkflowStructuredDiff/)
  assert.match(dialog, /确认导入/)
  assert.match(dialog, /whitespace-pre-line/)
  assert.match(view, /导出工作流/)
  assert.match(view, /导入工作流/)
})

test('workflow canvas distinguishes node types with base category colors', () => {
  const canvas = readSrc('src/views/workflow/WorkflowEditorCanvas.vue')
  const visuals = readSrc('src/lib/workflowNodeVisuals.ts')

  assert.match(canvas, /workflowNodeToneClass/)
  assert.match(canvas, /workflowNodeTypeText/)
  assert.match(visuals, /get_task: 'blue'/)
  assert.match(visuals, /agent: 'violet'/)
  assert.match(visuals, /script: 'teal'/)
  assert.match(visuals, /output: 'amber'/)
  assert.match(visuals, /bg-cat-blue-fg/)
  assert.match(visuals, /bg-cat-violet-fg/)
  assert.match(visuals, /bg-cat-teal-fg/)
  assert.match(visuals, /bg-cat-amber-fg/)
  assert.doesNotMatch(visuals, /(?:bg|text|border)-(?:blue|purple|violet|teal|amber|emerald|rose)-\d+/)
})

test('workflow import dialog keeps confirmation footer fixed while preview content scrolls', () => {
  const dialog = readSrc('src/components/workflow/WorkflowImportDialog.vue')

  assert.match(dialog, /grid-rows-\[auto_minmax\(0,1fr\)_auto\]/)
  assert.match(dialog, /<div class="min-h-0 flex-1 space-y-4 overflow-y-auto/)
  assert.match(dialog, /<DialogFooter class="shrink-0[^"]*">/)
})
