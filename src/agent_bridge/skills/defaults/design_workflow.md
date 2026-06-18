# design_workflow

你正在为 Agent Bridge 编写工作流。先理解用户目标，再同时维护两份内容：

1. `workflow.js`：Claude Code 动态工作流脚本（harness 运行时执行的**控制流脚本**）。
2. 工作流结构定义：平台注册/渲染用的 JSON `manifest`（`name` / `nodes` / `edges` / `schemas`）。

## workflow.js 是什么（务必先理解）

`workflow.js` **不是 Node.js 脚本**，而是 Claude Code 动态工作流规范的可执行脚本。harness 的 JS 运行时只跑**控制流**（`if` / `await` / `for` / `parallel`），每个 `agent()` 调用**派生一个子 agent** 去真正调工具、写文件、上网。

运行方式：claude 以 `Workflow({ scriptPath: "./workflow.js" })` 执行本脚本。同目录的 `manifest.json` 是给平台注册/渲染用的【结构定义】，与本脚本分属两套运行时、互不解析——所以**这里不要再写 `export const manifest`**。

### 铁律（违反会导致 run 失败）

- ❌ 不要 `import` Node 模块（`fs` / `path` / `process` …），不要写 `async function main()`，不要顶层 `main().catch(...)`。
- ❌ 不要在脚本里直接 `await workflow_get_task()` 或 `fs.writeFile(...)`——这些工具与能力只能由**子 agent** 调用。
- ❌ `export const meta` 必须是脚本的**第一条语句**——前面不要写 `'use strict'` 或任何可执行语句（注释可以），否则 harness 编译直接失败（报错 `must be the FIRST statement`）。
- ❌ 不要用 `new Date()` / `Date.now()` / `Math.random()`——harness 运行时禁用，调用即抛错。需要时间戳/随机数时：通过 `args` 传入，或交给子 agent（子 agent 是普通 agent，可用 Bash `date` 等，不受此限制）。
- ✅ 一切工具调用（`workflow_get_task` / `workflow_set_task` / `workflow_run_log`，以及 `Write` / `Bash` / `WebFetch` / `WebSearch` …）都包在 `agent('指示子 agent 做什么', { schema })` 里。
- ✅ 一次 run 只完成一个 task；`out/result.json` 的 `task_key` 必须等于本次 `workflow_get_task` 的租约值。
- ✅ 产物 `file` 必须在 `./out/` 下、不以 `/` 开头、不含 `..`；`format` 只能 `"markdown"`。
- ✅ `result.status` 只能 `"completed"`（需 `task_key` + 非空 `artifacts`）或 `"no_executable_task"`（带 `reason`）。

## 可用 API（控制流原语）

| API | 作用 |
|---|---|
| `export const meta = { name, description, phases }` | 顶部元信息，**必须纯字面量**；`phases` 供进度展示 |
| `phase('Lease')` | 声明进入某阶段（进度分组） |
| `agent(prompt, { label, phase, schema })` | 派生子 agent 执行；返回其结构化结果 |
| `parallel([() => agent(...), () => agent(...)])` | 并行 fan-out，等全部完成 |
| `log('msg')` | 输出一行进度 |

- `schema` 是 JSON Schema，强制子 agent 用结构化输出返回；脚本据此做控制流分支。优先 `additionalProperties: false` + 明确 `required`。
- `parallel` 的每个元素必须是 thunk `() => agent(...)`，不是直接 `agent(...)`。

## 骨架（正确范式，通用版）

```js
export const meta = {
  name: 'example-summary',
  description: '一句话说明这个工作流做什么：领任务 → 处理 → 产出 markdown。',
  phases: [
    { title: 'Lease', detail: 'workflow_get_task 租约任务；无则建任务再租约，或输出 no_executable_task' },
    { title: 'Process', detail: '按 task.type 分支处理，必要时 parallel 并行' },
    { title: 'Emit', detail: '写产物 markdown 与 out/result.json' },
  ],
}

// ---- schemas：约束每个 agent() 的结构化返回 ----
const TASK_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task'],
  properties: {
    task: {
      oneOf: [
        { type: 'null' },
        {
          type: 'object', additionalProperties: false,
          required: ['task_key'],
          properties: {
            task_key: { type: 'string' },
            task_version: { type: 'string' },
            type: { type: 'string' },
            payload: { type: 'object' },
          },
        },
      ],
    },
  },
}

// 任务键 → 安全文件名（artifact.file 不得含 / .. 等）
function fileBase(key) {
  return String(key || '').replace(/[^A-Za-z0-9._-]+/g, '-')
}

// ============================================================================
// Phase 1 — 取任务
// ============================================================================
phase('Lease')
let leased = await agent(
  '调用 MCP 工具 workflow_get_task()（无入参）。注意：调用即「租约」——会立即把任务锁到本次 run 并 attempt_count+1，租期 7200s。\n' +
  '只返回 workflow_get_task 的结果（{ task:{ task_key, task_version, type, payload } } 或 { task:null }），不要做其它事。',
  { label: 'get_task', phase: 'Lease', schema: TASK_SCHEMA },
)

if (!leased.task) {
  // 无可租约任务 → 合法的非失败终态（也可先 workflow_set_task 建任务，再 get_task）
  await agent(
    '用 Write 工具写 ./out/result.json，内容严格为：\n' +
    '{"status":"no_executable_task","reason":"no pending task"}\n只做这一件事。',
    { label: 'emit_no_task', phase: 'Lease' },
  )
  return { status: 'no_executable_task' }
}

const task = leased.task
const base = fileBase(task.task_key)

// ============================================================================
// Phase 2 — 处理（按 task.type 分支；示例：取数据 + 并行调研）
// ============================================================================
phase('Process')
const [dataOut, researchOut] = await parallel([
  () => agent(
    `按任务 ${task.task_key} 取所需数据（用 Bash / WebFetch 等工具）。返回结构化结果。软失败：抓不到就如实标注，不要中断。`,
    { label: 'fetch_data', phase: 'Process' },
  ),
  () => agent(
    `WebSearch 调研与 ${task.task_key} 相关的背景。软失败：无有价值结果就如实写「调研不足」，不要中断。`,
    { label: 'research', phase: 'Process' },
  ),
])

// ============================================================================
// Phase 3 — 产出（写 markdown + result.json）
// ============================================================================
phase('Emit')
const resultSkeleton = JSON.stringify({
  status: 'completed',
  task_key: task.task_key,
  task_version: task.task_version || '',
  artifacts: [{
    title: task.task_key,
    path: 'reports/' + task.task_key + '.md',
    file: 'out/artifacts/' + base + '.md',
    tags: [task.type || 'workflow'],
    format: 'markdown',
    summary: '<=80字一句话概述，需你填写>',
  }],
}, null, 2)

await agent(
  '用 Write 工具写两个文件：\n' +
  `1) ./out/artifacts/${base}.md：按任务 ${task.task_key} 的内容生成 markdown。\n` +
  '2) ./out/result.json：内容严格为下面这段 JSON（只把 summary 占位替换成真实的一句话概述，其余字节不变，保持合法 JSON）：\n' +
  resultSkeleton + '\n\n' +
  `硬约束（违反会被服务端拒绝、run 判失败）：result.json 的 task_key 必须等于本次租约值 "${task.task_key}"；file 不得以 / 开头、不得含 ..；format 只能 "markdown"；status 只能 "completed"。`,
  { label: 'write_artifact', phase: 'Emit' },
)

return { status: 'completed' }
```

## 任务工具（都是 MCP 工具，通过 agent 调用）

### workflow_get_task

领取/租约当前工作流运行的一条任务。**调用即租约**：把任务锁到本次 run，`attempt_count+1`，租期 7200s。

```js
const leased = await agent(
  '调用 MCP 工具 workflow_get_task()（无入参）。只返回结果，不做其它事。',
  { label: 'get_task', schema: TASK_SCHEMA },
)
// leased.task: { task_key, task_version, type, payload } 或 null
```

`task` 为 `null` 表示当前没有待处理任务（可输出 `no_executable_task`，或先建任务再租约）。

### workflow_set_task

幂等地创建或刷新任务：`completed` 跳过、`running` 未过期保护、`pending`/过期/`abandoned` 重置为 `pending`。用于在没有可租约任务时自行生产任务。

```js
await agent(
  '调用 MCP 工具 workflow_set_task({ tasks:[ { task_key, type, payload }, ... ] })。' +
  '把返回的 { created, updated, skipped_completed, skipped_running } 直接返回。',
  { label: 'seed_tasks', schema: SEED_SCHEMA },
)
```

任务字段约定：

- `task_key`：必填，稳定任务 ID。
- `task_version`：可选，同一任务的新版本再次执行时使用。
- `type`：可选，任务类型。可在脚本中按类型分支，例如 `page_summary`、`index_build`、`validation`。
- `payload`：任务业务参数，必须是对象。

### workflow_run_log

记录运行过程中的业务日志（供运行记录面板展示）：

```js
await agent(
  '调用 MCP 工具 workflow_run_log({ level:"info", stage:"lease", task_key:"<key>", message:"leased task", payload:{} })。',
  { label: 'log_lease' },
)
```

## 固定返回格式（out/result.json）

成功完成一个任务时：

```json
{
  "status": "completed",
  "task_key": "page:a",
  "task_version": "v1",
  "artifacts": [
    {
      "title": "Page A Report",
      "path": "reports/page-a.md",
      "tags": ["page", "summary"],
      "format": "markdown",
      "file": "out/artifacts/page-a.md",
      "summary": "short summary"
    }
  ]
}
```

没有可执行任务时：

```json
{ "status": "no_executable_task", "reason": "no pending task" }
```

要求：

- `task_key` 必须等于领取到的任务（租约值）。
- 如果任务有 `task_version`，必须原样写回。
- 当前只接受 `format: "markdown"`。
- `file` 必须指向运行目录内真实存在的产物文件（在 `./out/` 下，安全文件名）。
- `path` 是产物在工作流产物库中的逻辑路径，不能以 `/` 开头，不能包含 `..`。

> 提示：result.json 里 `task_key` / `path` / `file` 通常都是确定的，只有 `summary` 需要子 agent 生成——可在脚本里先拼好 JSON 骨架（`JSON.stringify`），再让写文件的 agent 只替换 `summary` 占位，避免子 agent 改动其它字段破坏硬约束。

## 工作流结构定义 manifest

`manifest.json` 供平台注册/渲染，包含 `name` / `nodes` / `edges` / `schemas`，**不被脚本运行时解析**。参考结构：

```json
{
  "name": "示例速览",
  "description": "一句话说明工作流目的。",
  "nodes": [
    { "id": "get_task", "kind": "io", "description": "workflow_get_task 租约一个待处理任务" },
    { "id": "seed_tasks", "kind": "source", "description": "无任务时建任务再租约" },
    { "id": "process", "kind": "fetch", "description": "按 task.type 分支处理" },
    { "id": "write_artifact", "kind": "output", "description": "写 out/artifacts/<key>.md" },
    { "id": "emit_result", "kind": "io", "description": "写 out/result.json" }
  ],
  "edges": [
    { "from": "get_task", "to": "seed_tasks", "when": "task == null" },
    { "from": "seed_tasks", "to": "get_task" },
    { "from": "get_task", "to": "process", "when": "task != null" },
    { "from": "process", "to": "write_artifact" },
    { "from": "write_artifact", "to": "emit_result" }
  ],
  "schemas": {
    "task": { "task_key": "string", "task_version": "string", "type": "string", "payload": {} },
    "artifact": { "path": "reports/<key>.md", "file": "out/artifacts/<key>.md", "format": "markdown" }
  }
}
```

- `nodes[].kind`：建议用 `io` / `source` / `fetch` / `research` / `output` 等语义标签。
- `edges[].when`：可选，标注分支条件，便于渲染条件流转。
- `schemas`：字段示例/说明（非严格 JSON Schema），帮助读者理解 task 与 artifact 的形状。

## 智能体协作方式

如果用户要求智能体协助编写工作流，应提示智能体先读取本技能：

```text
请执行 execute service='built-in' tool='load_skill' arguments={"skill_name":"design_workflow"} 读取技能，
然后参照技能内容与我的需求，完成 workflow.js 与 manifest.json。
```

智能体完成后应检查：

- 是否用 `agent()` 派生子 agent 调用工具/写文件（**而非**直接 `await workflow_get_task()` 或 `fs.writeFile`）？有没有误用 `import fs` / `async function main()`？
- 顶部是否干净（`export const meta` 是第一条语句，没有 `'use strict'`）？脚本里有没有 `new Date()` / `Date.now()` / `Math.random()`（都必须剔除；时间戳交给子 agent 用 Bash `date` 填）？
- 一次 run 是否只处理一个 task？`out/result.json` 的 `task_key` 是否用了 `workflow_get_task` 的租约值？
- 是否为不同 `task.type` 设计了清晰分支？独立的子任务是否用 `parallel([...])` 并行？
- `artifact.file` 是否在 `./out/` 下、文件名安全（无 `/` / `..`）？`format` 是否为 `"markdown"`？
- 是否原样写回了 `task_version`？
- `manifest` 的 `nodes` / `edges` / `schemas` 是否能解释脚本结构？
