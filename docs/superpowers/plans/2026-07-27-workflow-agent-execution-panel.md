# 工作流 Agent 输入与结果面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agent 运行详情与所有工作流运行入口一致展示当前 Agent 的输入提示词、执行结果及可查看完整内容的详情弹窗。

**Architecture:** 把 payload 内容类型识别与详情弹窗从 `RunEventTimeline` 抽为共享 helper 和组件；新增只负责 Agent 输入/结果摘要卡片的展示组件。工作流运行进度维护当前选中 Agent 的完整详情，任务展开日志缓存其主 Agent 的完整详情，两者都把记录交给同一展示组件。

**Tech Stack:** Vue 3 `<script setup>`、TypeScript、Tailwind CSS、Reka Dialog、CodeMirror、Node `tsx --test`。

## Global Constraints

- Markdown 继续使用现有 `renderMarkdown` 正常渲染；已知实体编码 URL 问题不在本需求修复范围内。
- JSON 必须先经 `formatJsonValue` 格式化；HTML、Python、JavaScript 和纯文本继续使用只读 `PayloadCodeViewer`。
- 保持 Chrome 90 兼容，不新增浏览器新 API。
- 只读取 `GET /agent-runs/{run_key}` 补足 `prompt/result`，不改变 CLI、后端接口、事件流或子 Agent 详情协议。
- 页面共用判断和格式化放入 `src/lib` 或独立组件；不得在 `WorkflowView.vue` 与 `AgentRunsView.vue` 重复实现。

---

## File Structure

- Create `frontend/capabilities/src/lib/payloadPresentation.ts`：统一 payload 的字符串化、内容类型识别、语言标签和 JSON 格式化。
- Create `frontend/capabilities/src/components/PayloadDetailDialog.vue`：统一的 Markdown/代码详情弹窗。
- Modify `frontend/capabilities/src/components/RunEventTimeline.vue`：保留 payload 懒加载和时间线状态，改用共享 helper 与详情弹窗。
- Create `frontend/capabilities/src/components/AgentRunExecutionPanel.vue`：输入提示词与执行结果的摘要卡片和详情入口。
- Modify `frontend/capabilities/src/components/WorkflowRunDetailPanel.vue`：在事件流前装配 Agent 输入/结果面板。
- Modify `frontend/capabilities/src/views/monitoring/AgentRunsView.vue`：用共享面板替换私有 Prompt/结果展示。
- Modify `frontend/capabilities/src/composables/useWorkflowRunProgress.ts`：维护当前 Agent 标签对应的完整 `AgentRun`，并防止迟到请求覆盖新选择。
- Modify `frontend/capabilities/src/composables/useWorkflowTasks.ts`：按任务 `lease_run_id` 缓存主 Agent 完整记录。
- Modify `frontend/capabilities/src/views/workflow/WorkflowView.vue`：向批量、进度和任务展开入口传入完整详情。
- Modify `README.md`：补充 Agent 输入/结果也在工作流上下文复用展示及详情能力。
- Create `frontend/capabilities/tests/payloadPresentation.test.ts`：覆盖 payload 类型识别与 JSON 格式化。
- Create `frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts`：验证公共面板、详情弹窗和三个工作流入口的装配关系。
- Modify `frontend/capabilities/tests/payloadViewerLayout.test.ts`：验证时间线改用公共详情弹窗，且保留 Markdown 与代码查看能力。

## Tasks

### Task 1: 提取 payload 呈现 helper 与详情弹窗

**Files:**
- Create: `frontend/capabilities/src/lib/payloadPresentation.ts`
- Create: `frontend/capabilities/src/components/PayloadDetailDialog.vue`
- Modify: `frontend/capabilities/src/components/RunEventTimeline.vue:20-205,340-377`
- Create: `frontend/capabilities/tests/payloadPresentation.test.ts`
- Modify: `frontend/capabilities/tests/payloadViewerLayout.test.ts`

**Interfaces:**
- Consumes: `formatJsonValue(value: unknown): string`, `renderMarkdown(markdown: string): string`, `PayloadCodeViewer`。
- Produces: `PayloadLanguage`, `payloadLanguageLabel(language)`, `payloadText(value)`, `detectPayloadLanguage(content, hints)`, `preparePayloadPresentation(value, hints)`，以及 `PayloadDetailDialog` 的 `open/title/label/content/language` props 和 `update:open` event。

- [ ] **Step 1: Write the failing helper and layout tests**

```ts
import { detectPayloadLanguage, preparePayloadPresentation } from '../src/lib/payloadPresentation.ts'

test('detectPayloadLanguage honors content metadata before content inference', () => {
  assert.equal(detectPayloadLanguage('# heading', { contentType: 'text/markdown' }), 'markdown')
  assert.equal(detectPayloadLanguage('<html></html>', { ref: 'report.html' }), 'html')
  assert.equal(detectPayloadLanguage('{"ok":true}'), 'json')
  assert.equal(detectPayloadLanguage('def run(): pass'), 'python')
  assert.equal(detectPayloadLanguage('const run = () => {}'), 'javascript')
  assert.equal(detectPayloadLanguage('plain output'), 'text')
})

test('preparePayloadPresentation formats structured JSON before opening it', () => {
  assert.deepEqual(preparePayloadPresentation({ ok: true }), {
    content: '{\n  "ok": true\n}', language: 'json',
  })
})
```

Extend `payloadViewerLayout.test.ts` so it reads `PayloadDetailDialog.vue`, asserts the dialog imports `renderMarkdown`, `PayloadCodeViewer` and the Dialog primitives, and asserts `RunEventTimeline.vue` imports and renders `PayloadDetailDialog` instead of owning `<Dialog :open="payloadModal !== null">`.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `cd frontend/capabilities && npx tsx --test tests/payloadPresentation.test.ts tests/payloadViewerLayout.test.ts`

Expected: FAIL because the helper and dialog files do not exist and `RunEventTimeline` still contains its private dialog.

- [ ] **Step 3: Implement the helper and reusable dialog**

Create `payloadPresentation.ts` with these exported types and signatures:

```ts
export type PayloadLanguage = 'markdown' | 'json' | 'html' | 'python' | 'javascript' | 'text'
export interface PayloadHints { contentType?: string; ref?: string }
export interface PayloadPresentation { content: string; language: PayloadLanguage }

export function payloadText(value: unknown): string
export function detectPayloadLanguage(content: string, hints?: PayloadHints): PayloadLanguage
export function payloadLanguageLabel(language: PayloadLanguage): string
export function preparePayloadPresentation(value: unknown, hints?: PayloadHints): PayloadPresentation
```

Move the current `payloadLanguage` inference order into `detectPayloadLanguage`: explicit content type/reference extension, JSON parse, Markdown markers, Python markers, JavaScript markers, then text. `preparePayloadPresentation` must call `payloadText`, detect the language, and call `formatJsonValue` only when the resolved language is `json`.

Create `PayloadDetailDialog.vue` with:

```ts
defineProps<{ open: boolean; title: string; label?: string; content: string; language: PayloadLanguage }>()
defineEmits<{ (event: 'update:open', open: boolean): void }>()
```

Render `renderMarkdown(content)` with `v-html` for Markdown; render `<PayloadCodeViewer :content="content" :language="language" />` otherwise. Keep the current full-width dialog classes and show `title · payloadLanguageLabel(language)` plus the optional label under the heading.

In `RunEventTimeline.vue`, retain `payloadRef`, preview, lazy blob loading and `payloadModal` state. Replace local language types/functions and the private Dialog template with `preparePayloadPresentation(content, { contentType, ref })` and `<PayloadDetailDialog>`. Keep the input/output/detail labels and byte display exactly as they are.

- [ ] **Step 4: Run the focused tests to verify pass**

Run: `cd frontend/capabilities && npx tsx --test tests/payloadPresentation.test.ts tests/payloadViewerLayout.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit the shared rendering extraction**

```bash
git add frontend/capabilities/src/lib/payloadPresentation.ts \
  frontend/capabilities/src/components/PayloadDetailDialog.vue \
  frontend/capabilities/src/components/RunEventTimeline.vue \
  frontend/capabilities/tests/payloadPresentation.test.ts \
  frontend/capabilities/tests/payloadViewerLayout.test.ts
git commit -m "refactor: share payload detail rendering"
```

### Task 2: 新建 Agent 输入与结果公共展示面板

**Files:**
- Create: `frontend/capabilities/src/components/AgentRunExecutionPanel.vue`
- Modify: `frontend/capabilities/src/components/WorkflowRunDetailPanel.vue:1-105`
- Modify: `frontend/capabilities/src/views/monitoring/AgentRunsView.vue:1-20,356-365`
- Create: `frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts`

**Interfaces:**
- Consumes: `AgentRun`, `preparePayloadPresentation`, `PayloadDetailDialog`, `JsonViewer`.
- Produces: `<AgentRunExecutionPanel :run="AgentRun | null" :loading="boolean" :error="string" />`; `WorkflowRunDetailPanel` gains `agentRunDetail`, `agentRunDetailLoading`, and `agentRunDetailError` props.

- [ ] **Step 1: Write the failing component composition test**

```ts
test('Agent input/result panel is shared by standalone and workflow details', () => {
  assert.match(panel, /<PayloadDetailDialog/)
  assert.match(panel, /输入提示词/)
  assert.match(panel, /执行结果/)
  assert.match(panel, /border-border/)
  assert.match(panel, />详情</)
  assert.match(workflowDetail, /<AgentRunExecutionPanel/)
  assert.match(agentRuns, /<AgentRunExecutionPanel :run="detailRun"/)
})
```

The same test must assert that `WorkflowRunDetailPanel` declares all three `agentRunDetail*` props and places the panel before `<RunEventTimeline`.

- [ ] **Step 2: Run the focused test to verify failure**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts`

Expected: FAIL because the component and new props do not exist.

- [ ] **Step 3: Implement the panel and replace duplicated standalone markup**

`AgentRunExecutionPanel.vue` accepts a full or partial `AgentRun` record plus loading/error state. It must:

```ts
defineProps<{ run: Pick<AgentRun, 'prompt' | 'result' | 'run_key'> | null; loading?: boolean; error?: string }>()
```

- render a compact loading row only while no matching `run` is available;
- render an error callout without hiding the event timeline when `error` is non-empty;
- show no card for `prompt === undefined || prompt === ''` or `result === undefined`;
- render two separate cards with `rounded-lg border border-border bg-card shadow-sm`, a distinct header, constrained wrapped preview, and a `详情` button;
- use `JsonViewer` for the result preview and a `<pre>` for the prompt preview;
- open `PayloadDetailDialog` with `preparePayloadPresentation` for each card. The prompt card title is `输入提示词`; result card title is `执行结果`.

Add the three optional props to `WorkflowRunDetailPanel` and render this panel after `AgentRunTabs` and before loading/empty/event branches. In `AgentRunsView`, remove its local Prompt `<pre>` and result `<JsonViewer>` blocks, import `AgentRunExecutionPanel`, and render `<AgentRunExecutionPanel :run="detailRun" />` before its event timeline.

- [ ] **Step 4: Run the focused test to verify pass**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit the shared Agent execution presentation**

```bash
git add frontend/capabilities/src/components/AgentRunExecutionPanel.vue \
  frontend/capabilities/src/components/WorkflowRunDetailPanel.vue \
  frontend/capabilities/src/views/monitoring/AgentRunsView.vue \
  frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts
git commit -m "feat: share agent input and result panel"
```

### Task 3: 为批量和运行进度加载当前标签的完整 Agent 详情

**Files:**
- Modify: `frontend/capabilities/src/composables/useWorkflowRunProgress.ts:80-330,390-530,542-570`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue:245-285,1490-1510,1790-1808`
- Modify: `frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts`

**Interfaces:**
- Consumes: `api.getAgentRun(runKey): Promise<AgentRun>`, `progressAgentRunKey`.
- Produces: `progressAgentRunDetail`, `progressAgentRunDetailLoading`, `progressAgentRunDetailError`, `loadProgressAgentRunDetail(options?)`; all `WorkflowRunDetailPanel` callers receive the corresponding props.

- [ ] **Step 1: Extend the failing composition test for selected-run data flow**

Add assertions that `useWorkflowRunProgress.ts` declares `progressAgentRunDetail`, calls `api.getAgentRun(agentRunKey)`, compares the request's run key with `progressAgentRunKey.value` before committing data, and returns the new refs. Assert `WorkflowView.vue` destructures them and passes `:agent-run-detail`, `:agent-run-detail-loading`, and `:agent-run-detail-error` to both `WorkflowRunDetailPanel` instances.

- [ ] **Step 2: Run the focused test to verify failure**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts`

Expected: FAIL because workflow progress only loads event summaries and events.

- [ ] **Step 3: Implement selected Agent detail state with stale-response protection**

In `useWorkflowRunProgress.ts`, add:

```ts
const progressAgentRunDetail = ref<AgentRun | null>(null)
const progressAgentRunDetailLoading = ref(false)
const progressAgentRunDetailError = ref('')
let progressAgentRunDetailRequest = 0

async function loadProgressAgentRunDetail(options: { quiet?: boolean } = {}) {
  const requestId = ++progressAgentRunDetailRequest
  const agentRunKey = progressAgentRunKey.value
  if (!agentRunKey) {
    progressAgentRunDetail.value = null
    progressAgentRunDetailLoading.value = false
    progressAgentRunDetailError.value = ''
    return
  }
  progressAgentRunDetailLoading.value = true
  try {
    const detail = await api.getAgentRun(agentRunKey)
    if (requestId !== progressAgentRunDetailRequest || agentRunKey !== progressAgentRunKey.value) return
    progressAgentRunDetail.value = detail
    progressAgentRunDetailError.value = ''
  } catch (error: unknown) {
    if (requestId !== progressAgentRunDetailRequest || agentRunKey !== progressAgentRunKey.value) return
    progressAgentRunDetailError.value = errorMessage(error)
  } finally {
    if (requestId === progressAgentRunDetailRequest && agentRunKey === progressAgentRunKey.value) {
      progressAgentRunDetailLoading.value = false
    }
  }
}
```

The loader captures both an incremented request id and `agentRunKey`. It clears detail/loading for an empty key; otherwise calls `api.getAgentRun(agentRunKey)`. It updates detail/error/loading only if both the request id and current `progressAgentRunKey.value` still match, so switching A → B cannot repaint B with A's response. On a non-quiet initial failure, record `errorMessage(error)` without clearing `runEvents`.

When `loadProgressAgentRuns` selects a default or preserves a selected key, load the selected detail. When `selectProgressAgentRun` changes the key, await both `loadProgressAgentEvents()` and `loadProgressAgentRunDetail()`. In `pollTestRun` and `refreshProgress`, refresh the selected detail alongside events. At every route/run reset that clears `progressAgentRunKey`, also invalidate pending detail requests and clear the three detail states.

Expose these refs and loader in the composable return. In `WorkflowView.vue`, destructure the refs and pass them to both `WorkflowRunDetailPanel` instances (batch and progress); do not add a third `AgentRunTabs`.

- [ ] **Step 4: Run the focused test and typecheck**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts && npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit workflow selected-Agent loading**

```bash
git add frontend/capabilities/src/composables/useWorkflowRunProgress.ts \
  frontend/capabilities/src/views/workflow/WorkflowView.vue \
  frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts
git commit -m "feat: show selected agent input in workflow runs"
```

### Task 4: 将任务展开日志接入同一完整详情面板

**Files:**
- Modify: `frontend/capabilities/src/composables/useWorkflowTasks.ts:1-50,85-105,650-755,785-900`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue:330-445,1675-1710`
- Modify: `frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts`

**Interfaces:**
- Consumes: `api.getAgentRunForWorkflowRun(leaseRunId)`, `api.getAgentRun(runKey)`, `BatchRunDetailHook.runIdToAgentRunKey`.
- Produces: `taskAgentRun(task): AgentRun | null`; task state caches details as `taskRunDetails: Record<string, AgentRun>` keyed by `lease_run_id`.

- [ ] **Step 1: Extend the failing layout/data-flow test for task expansion**

Add assertions that `useWorkflowTasks.ts` owns `taskRunDetails`, stores an `agentRun` during `toggleTaskLogs`, returns `taskAgentRun`, and that `WorkflowView.vue` imports `AgentRunExecutionPanel` or passes the result through a local panel inside the expanded task block before `<RunEventTimeline`.

- [ ] **Step 2: Run the focused test to verify failure**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts`

Expected: FAIL because expanded task logs only render events.

- [ ] **Step 3: Cache and render the task's main Agent full record**

Add `taskRunDetails = ref<Record<string, AgentRun>>({})` and `taskAgentRun(task)` to `useWorkflowTasks.ts`. In `toggleTaskLogs`, when expanding a task for the first time:

1. Use the existing `runIdToAgentRunKey` mapping if present; otherwise call `api.getAgentRunForWorkflowRun(task.lease_run_id)` and store its `run_key` mapping.
2. Reuse the returned full `AgentRun`; if only a mapped key is available, call `api.getAgentRun(runKey)`.
3. Store the full record under the `lease_run_id` before assigning events.

Keep current event and payload behavior, and do not issue duplicate detail requests if `taskRunDetails[lease_run_id]` already exists. Clear `taskRunDetails` wherever `taskRunLogs`/execution-scoped task state is cleared.

Return `taskAgentRun`, destructure it in `WorkflowView.vue`, and render `<AgentRunExecutionPanel :run="taskAgentRun(task)" :loading="isTaskLogLoading(task)" />` after the `Agent 输出` heading and before the event-empty/event-timeline branch. This single main-Agent card is intentional: task expansion has no multi-Agent tabs, while batch/progress tabs continue to determine the displayed Agent.

- [ ] **Step 4: Run the focused test and typecheck**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts && npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit task-log integration**

```bash
git add frontend/capabilities/src/composables/useWorkflowTasks.ts \
  frontend/capabilities/src/views/workflow/WorkflowView.vue \
  frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts
git commit -m "feat: show agent details in task logs"
```

### Task 5: 更新运行观测说明并完成前端验证

**Files:**
- Modify: `README.md:143`
- Modify: `frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts`

**Interfaces:**
- Consumes: 已完成的公共详情弹窗、公共 Agent 输入/结果面板、工作流完整详情状态。
- Produces: 与实际 UI 行为一致的运行观测文档和回归覆盖。

- [ ] **Step 1: Write the failing documentation assertion**

Append to `agentRunExecutionPanelLayout.test.ts`:

```ts
test('README documents that workflow reuses the agent input and result detail view', () => {
  const readme = readFileSync(resolve(root, '../../README.md'), 'utf8')
  assert.match(readme, /输入提示词和执行结果/)
  assert.match(readme, /工作流/)
})
```

- [ ] **Step 2: Run the focused test to verify failure**

Run: `cd frontend/capabilities && npx tsx --test tests/agentRunExecutionPanelLayout.test.ts`

Expected: FAIL because the README does not yet document the shared input/result view.

- [ ] **Step 3: Document the completed behavior**

Extend the Agent 运行记录 paragraph in `README.md` to say that Agent 运行详情、工作流批量执行详情、任务展开日志和运行进度 reuse the same input-prompt/result cards. State that each card has a detail button; Markdown is rendered and JSON is formatted before viewing, while HTML/Python/JavaScript use syntax highlighting. Do not claim a security change.

- [ ] **Step 4: Run focused and complete frontend verification**

Run: `cd frontend/capabilities && npx tsx --test tests/payloadPresentation.test.ts tests/payloadViewerLayout.test.ts tests/agentRunExecutionPanelLayout.test.ts && npm run check`

Expected: focused tests PASS; `npm run check` passes, or fails only on the pre-existing `workflowIncrementalRun.test.ts` assertion expecting `复用节点` while unrelated source says `复用候选`. Record that existing failure without changing its wording.

- [ ] **Step 5: Review and commit the documentation/verification changes**

Run:

```bash
git diff --check
git diff -- frontend/capabilities/src frontend/capabilities/tests README.md
git status --short
git add README.md frontend/capabilities/tests/agentRunExecutionPanelLayout.test.ts
git commit -m "docs: describe shared agent execution details"
```

Expected: no whitespace errors; commit contains only the documentation and final regression-test update.
