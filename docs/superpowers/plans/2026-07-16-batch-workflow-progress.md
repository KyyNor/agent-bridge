# Batch Workflow Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox ( - [ ] ) syntax for tracking.

**Goal:** 在工作流任务页持续显示批量运行进度，并复用单次运行的 Agent 事件详情展示当前任务。

**Architecture:** 保持现有页面级串行队列和 workflow run API，不新增后端 batch 状态。为队列补充任务/Run 生命周期回调；把任务页和单次进度页共用的 Agent 输出区抽成一个展示组件；WorkflowView.vue 负责把批量回调接到现有 run 轮询和事件加载逻辑。

**Tech Stack:** Vue 3 script setup、TypeScript、现有 RunEventTimeline / SubagentDetailPanel、Node test runner + tsx、vue-tsc。

---

## 文件边界

- Modify: frontend/capabilities/src/lib/workflowTasks.ts — 增加批量队列生命周期回调，不改变串行、取消和停止语义。
- Modify: frontend/capabilities/src/views/workflow/WorkflowView.vue — 接收回调、维护批次统计、刷新当前 Run、渲染任务页进度区，并改用详情组件。
- Create: frontend/capabilities/src/components/WorkflowRunDetailPanel.vue — 只负责 Agent run 切换、事件时间线和子 Agent 详情展示。
- Modify: frontend/capabilities/tests/workflowBatchRunner.test.ts — 覆盖新增回调的触发顺序和终态计数时机。

## Task 1: 为批量队列增加可观察的 Run 生命周期回调

Files:

- Modify: frontend/capabilities/src/lib/workflowTasks.ts:128-181
- Test: frontend/capabilities/tests/workflowBatchRunner.test.ts

- [ ] Step 1: 先写回调顺序测试

在现有队列测试文件增加一个用例：

~~~ts
const events: string[] = []
const result = await runWorkflowTaskQueue([task('first')], {
  canExecute: () => true,
  execute: async () => ({ run_id: 'run-first' }),
  waitForRun: async (runId, onUpdate) => {
    events.push('wait:' + runId)
    await onUpdate?.(run('run-first', 'running'))
    return run('run-first', 'completed')
  },
  onTaskStart: current => events.push('task-start:' + current.task_key),
  onRunStart: (current, runId) => events.push('run-start:' + current.task_key + ':' + runId),
  onRunUpdate: (_current, currentRun) => events.push('run-update:' + currentRun.status),
  onTaskFinish: outcome => events.push('task-finish:' + outcome.status),
})

assert.deepEqual(events, [
  'task-start:first',
  'run-start:first:run-first',
  'wait:run-first',
  'run-update:running',
  'task-finish:success',
])
assert.equal(result.outcomes[0].status, 'success')
~~~

waitForRun 的第二个参数必须是可选更新回调，保证现有测试中只接收 runId 的实现仍兼容。

- [ ] Step 2: 运行聚焦测试，确认新测试先失败

~~~bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts
~~~

Expected: 新增用例因生命周期回调尚未触发而失败；现有队列用例保持通过。

- [ ] Step 3: 实现最小回调接口

在 WorkflowTaskQueueOptions 中加入：

~~~ts
onRunStart?: (task: WorkflowTask, runId: string) => void | Promise<void>
onRunUpdate?: (task: WorkflowTask, run: WorkflowRun) => void | Promise<void>
onTaskFinish?: (outcome: WorkflowTaskQueueOutcome) => void | Promise<void>
~~~

把 waitForRun 改成：

~~~ts
waitForRun: (
  runId: string,
  onUpdate?: (run: WorkflowRun) => void | Promise<void>,
) => Promise<WorkflowRun>
~~~

队列按以下位置触发：

1. execute 返回有效 run_id 后触发 onRunStart。
2. 调用 waitForRun(run_id, run => onRunUpdate?.(task, run))。
3. 每个 outcome push 后，包括 skipped、无 run_id 和异常路径，都触发一次 onTaskFinish。

回调使用 await，保证页面状态更新不会与下一个任务启动交错；不要修改 shouldStopOnError 和 remaining 逻辑。

- [ ] Step 4: 重新运行聚焦测试

~~~bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts
~~~

Expected: 全部队列测试通过。

## Task 2: 抽取可复用的当前 Run 详情展示组件

Files:

- Create: frontend/capabilities/src/components/WorkflowRunDetailPanel.vue
- Modify: frontend/capabilities/src/views/workflow/WorkflowView.vue:15,2438-2483

- [ ] Step 1: 创建纯展示组件

组件接收以下 props，并只通过 emits 把用户操作交回父页面：

~~~ts
type DetailFn<T> = (taskId: string) => T

const props = defineProps<{
  events: WorkflowRunEvent[]
  agentRuns: AgentRun[]
  selectedAgentRunKey: string
  eventsLoading: boolean
  agentRunsLoading: boolean
  contextKey: string
  subagentDetail: DetailFn<WorkflowSubagentDetail | null>
  subagentDetailLoading: DetailFn<boolean>
  subagentDetailError: DetailFn<string>
}>()

const emit = defineEmits<{
  (event: 'select-agent-run', runKey: string): void
  (event: 'refresh'): void
  (event: 'expand-subagent', taskId: string): void
}>()
~~~

将现有进度页中 Agent run Badge、刷新按钮、空事件提示和 RunEventTimeline 模板移入该组件；组件内继续使用 SubagentDetailPanel，不移动数据请求。

- [ ] Step 2: 让单次进度页改用新组件

在 WorkflowView.vue 中引入组件，替换 progress section 中原有的 Agent 输出模板，保持以下绑定等价：

~~~vue
<WorkflowRunDetailPanel
  :events="runEvents"
  :agent-runs="progressAgentRuns"
  :selected-agent-run-key="progressAgentRunKey"
  :events-loading="logsLoading"
  :agent-runs-loading="progressAgentRunsLoading"
  :context-key="'run:' + progressAgentRunKey"
  :subagent-detail="progressSubagentDetail"
  :subagent-detail-loading="progressSubagentDetailLoading"
  :subagent-detail-error="progressSubagentDetailError"
  @select-agent-run="selectProgressAgentRun"
  @refresh="refreshProgress"
  @expand-subagent="ensureProgressSubagentDetail"
/>
~~~

详情组件只做展示抽取；单次运行页的路由、轮询、产物按钮和状态头不改。

- [ ] Step 3: 运行类型检查

~~~bash
npm --prefix frontend/capabilities run typecheck
~~~

Expected: 新组件的 props、emits 和现有 WorkflowView.vue 绑定无 TypeScript 错误。

## Task 3: 接入任务页的批次状态与当前 Run 详情

Files:

- Modify: frontend/capabilities/src/views/workflow/WorkflowView.vue:120-135,1137-1217,2063-2390

- [ ] Step 1: 增加页面级批次状态

保留现有 batchProgress.current / total 字段，扩展为：

~~~ts
const batchProgress = ref({
  current: 0,
  total: 0,
  completed: 0,
  success: 0,
  failed: 0,
  skipped: 0,
})
const batchCurrentTask = ref<WorkflowTask | null>(null)
const batchCurrentTaskId = ref('')
const batchCurrentRunId = ref('')
const batchRunDetailError = ref('')
~~~

prepareTasks、路由变化和组件卸载时清理当前任务/Run；批量结束时不清理最后一个 Run ID，以便保留最后详情。

- [ ] Step 2: 让批量轮询同步刷新当前 Run 详情

扩展 waitForBatchRun 为接收可选更新回调：

~~~ts
async function waitForBatchRun(
  runId: string,
  token: number,
  onUpdate?: (run: WorkflowRun) => void | Promise<void>,
): Promise<WorkflowRun> {
  while (true) {
    if (token !== batchToken) throw new Error('页面队列已停止')
    const run = await api.getWorkflowRun(runId)
    mergeWorkflowRun(run)
    await onUpdate?.(run)
    if (['completed', 'no_task', 'failed', 'stopped'].includes(run.status)) return run
    await sleep(1500)
  }
}
~~~

新增 loadBatchRunDetail(task, runId, quiet)：设置 batchCurrentTaskId / batchCurrentRunId，复用 progressWorkflowKey / progressRunId，调用已有 loadProgressAgentRuns 和 loadProgressAgentEvents；quiet 刷新失败只设置 batchRunDetailError，不阻塞队列。

- [ ] Step 3: 接上队列回调并实时更新统计

在 runSelectedTasks 的 queue options 中接入：

~~~ts
onTaskStart: (task, index, total) => {
  batchCurrentTask.value = task
  batchCurrentTaskId.value = taskId(task)
  batchProgress.value = { ...batchProgress.value, current: index + 1, total }
},
onRunStart: (task, runId) => loadBatchRunDetail(task, runId, false),
onTaskFinish: outcome => {
  batchProgress.value = {
    ...batchProgress.value,
    completed: batchProgress.value.completed + 1,
    success: batchProgress.value.success + (outcome.status === 'success' ? 1 : 0),
    failed: batchProgress.value.failed + (outcome.status === 'failed' ? 1 : 0),
    skipped: batchProgress.value.skipped + (outcome.status === 'skipped' ? 1 : 0),
  }
},
~~~

waitForRun 把队列传入的 onUpdate 转成 quiet 的当前 Run 详情刷新；使用 batchCurrentTask 保存的队列对象作为当前任务来源，不从刷新后的任务列表反查对象；不启动额外定时器。

- [ ] Step 4: 在任务页加入顶部进度条和详情区

在 tasks section 的筛选/工具栏下方增加：

- 批量运行状态、completed / total 进度条、成功/失败/跳过/待执行统计。
- 当前任务 key、当前 Run ID 和状态。
- WorkflowRunDetailPanel，绑定共享的 runEvents、progressAgentRuns、progressAgentRunKey 和现有 progress 子 Agent 方法。

仅当 batchAction === 'run' 或存在批量结束汇总时显示；批量重置不显示 Run 详情。当前任务卡用 batchCurrentTaskId 增加运行中样式，其他任务操作保持现有禁用规则。

- [ ] Step 5: 运行前端类型检查

~~~bash
npm --prefix frontend/capabilities run typecheck
~~~

Expected: 通过；若出现函数类型不匹配，优先修正 WorkflowTaskQueueOptions.waitForRun 的可选回调签名，不改变 API 返回类型。

## Task 4: 聚焦验证与提交

Files:

- Verify: frontend/capabilities/src/lib/workflowTasks.ts
- Verify: frontend/capabilities/src/components/WorkflowRunDetailPanel.vue
- Verify: frontend/capabilities/src/views/workflow/WorkflowView.vue
- Verify: frontend/capabilities/tests/workflowBatchRunner.test.ts

- [ ] Step 1: 运行批量队列测试

~~~bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts
~~~

Expected: 所有队列测试通过。

- [ ] Step 2: 运行前端完整 typecheck 和构建

~~~bash
npm --prefix frontend/capabilities run typecheck
npm --prefix frontend/capabilities run build
~~~

Expected: vue-tsc 和 Vite 构建均成功；构建产物位于被 gitignore 的 src/agent_bridge/static/capabilities/，不加入提交。

- [ ] Step 3: 做一次人工验收

在任务页选择至少 3 个任务，确认：顶部显示当前第 N 项和完成统计；当前 Run 的 Agent 事件持续更新；任务切换后详情切换；单项失败按既有策略继续或停止；批量结束后最后 Run 详情仍可查看；单任务执行仍跳转原进度页。

- [ ] Step 4: 检查 diff 并提交

~~~bash
git diff --check
git status --short
git add frontend/capabilities/src/lib/workflowTasks.ts frontend/capabilities/src/components/WorkflowRunDetailPanel.vue frontend/capabilities/src/views/workflow/WorkflowView.vue frontend/capabilities/tests/workflowBatchRunner.test.ts
git commit -m "feat: show batch workflow run progress"
~~~

Expected: 只提交本功能涉及的前端文件；不提交构建产物或 .superpowers/ 临时内容。
