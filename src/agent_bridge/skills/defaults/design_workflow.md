# design_workflow

你正在为 Agent Bridge 编写工作流。请先理解用户目标，再同时维护两份内容：

1. `workflow.js`：Claude Code 实际执行的工作流脚本。
2. 工作流结构定义：页面展示和校验用的 JSON manifest。

## workflow.js 基础约定

工作流脚本在一次独立运行目录中执行。脚本应当：

- 使用平台提供的 MCP 工具完成任务编排。
- 通过 `workflow_get_task` 领取一个待处理任务。
- 通过 `workflow_set_task` 创建或刷新待处理任务。
- 通过 `workflow_run_log` 记录关键业务日志。
- 在运行目录写入 `out/result.json` 作为最终验收结果。
- 将产物文件写到运行目录内，推荐放在 `out/artifacts/`。

建议结构：

```js
import fs from "node:fs/promises";
import path from "node:path";

async function main() {
  await fs.mkdir("out/artifacts", { recursive: true });

  // 先领取任务。没有任务时可以先生成任务，也可以输出 no_executable_task。
  const taskResult = await workflow_get_task();
  const task = taskResult.task;

  if (!task) {
    await fs.writeFile(
      "out/result.json",
      JSON.stringify({ status: "no_executable_task", reason: "no pending task" }, null, 2),
    );
    return;
  }

  await workflow_run_log({
    level: "info",
    stage: "process",
    task_key: task.task_key,
    message: `processing ${task.task_key}`,
    payload: { type: task.type, task_version: task.task_version },
  });

  const artifactFile = `out/artifacts/${task.task_key.replace(/[^a-zA-Z0-9_-]/g, "_")}.md`;
  await fs.writeFile(artifactFile, `# ${task.task_key}\n\n任务类型：${task.type || "default"}\n`);

  await fs.writeFile(
    "out/result.json",
    JSON.stringify(
      {
        status: "completed",
        task_key: task.task_key,
        task_version: task.task_version || "",
        artifacts: [
          {
            title: task.task_key,
            path: `reports/${task.task_key}.md`,
            tags: [task.type || "workflow"],
            format: "markdown",
            file: artifactFile,
            summary: "workflow artifact",
            metadata: { type: task.type || "" },
          },
        ],
      },
      null,
      2,
    ),
  );
}

main().catch(async error => {
  await fs.mkdir("out", { recursive: true });
  await fs.writeFile(
    "out/result.json",
    JSON.stringify({ status: "failed", error: String(error?.message || error) }, null, 2),
  );
  throw error;
});
```

## 固定返回格式

成功完成一个任务时，`out/result.json` 必须是：

```json
{
  "status": "completed",
  "task_key": "page:a",
  "task_version": "sha256:optional",
  "artifacts": [
    {
      "title": "Page A Report",
      "path": "reports/page-a.md",
      "tags": ["page", "summary"],
      "format": "markdown",
      "file": "out/artifacts/page-a.md",
      "summary": "short summary",
      "metadata": {}
    }
  ]
}
```

没有可执行任务时：

```json
{
  "status": "no_executable_task",
  "reason": "no pending task"
}
```

要求：

- `task_key` 必须等于领取到的任务。
- 如果任务有 `task_version`，必须原样写回。
- 当前只接受 `format: "markdown"`。
- `file` 必须指向运行目录内真实存在的产物文件。
- `path` 是产物在工作流产物库中的逻辑路径，不能以 `/` 开头，不能包含 `..`。

## 任务工具

### workflow_get_task

领取当前工作流运行的一条任务：

```js
const { task } = await workflow_get_task();
```

返回：

```json
{
  "task": {
    "task_key": "page:a",
    "task_version": "v1",
    "type": "page_summary",
    "payload": {}
  }
}
```

`task` 为 `null` 表示当前没有待处理任务。

### workflow_set_task

创建或刷新任务：

```js
await workflow_set_task({
  tasks: [
    {
      "task_key": "page:a",
      "task_version": "v1",
      "type": "page_summary",
      "payload": { "page": "a" }
    }
  ]
});
```

字段约定：

- `task_key`：必填，稳定任务 ID。
- `task_version`：可选，用于同一任务的新版本再次执行。
- `type`：可选，任务类型。可在脚本中按类型分支，例如 `page_summary`、`index_build`、`validation`。
- `payload`：任务业务参数，必须是对象。

### workflow_run_log

记录业务日志：

```js
await workflow_run_log({
  level: "info",
  stage: "dispatch",
  message: "created page tasks",
  task_key: "page:a",
  payload: { count: 1 }
});
```

## 工作流结构定义 manifest

manifest 用于页面展示工作流结构。推荐结构：

```json
{
  "name": "Page Report",
  "nodes": [
    {
      "id": "discover",
      "label": "发现任务",
      "type": "task_producer",
      "description": "读取输入并调用 workflow_set_task 创建任务"
    },
    {
      "id": "process",
      "label": "处理任务",
      "type": "task_worker",
      "description": "调用 workflow_get_task 并按 task.type 分支处理"
    }
  ],
  "edges": [
    { "from": "discover", "to": "process", "label": "pending tasks" }
  ],
  "schemas": {
    "task": {
      "type": "object",
      "required": ["task_key", "payload"],
      "properties": {
        "task_key": { "type": "string" },
        "task_version": { "type": "string" },
        "type": { "type": "string" },
        "payload": { "type": "object" }
      }
    },
    "result": {
      "type": "object",
      "required": ["status"],
      "properties": {
        "status": { "enum": ["completed", "no_executable_task", "failed"] },
        "task_key": { "type": "string" },
        "task_version": { "type": "string" },
        "artifacts": { "type": "array" }
      }
    }
  }
}
```

## 智能体协作方式

如果用户要求智能体协助编写工作流，应提示智能体先读取本技能：

```text
请执行 execute service='built-in' tool='load_skill' arguments={"skill_name":"design_workflow"} 读取技能，
然后参照技能内容和我的需求，完成 workflow.js 与工作流结构定义 manifest。
```

智能体完成后应检查：

- 是否调用了 `workflow_get_task`、`workflow_set_task` 或说明为什么不需要。
- 是否为不同 `task.type` 设计了清晰分支。
- 是否写入 `out/result.json`。
- 是否保留并写回 `task_version`。
- manifest 的 `nodes`、`edges`、`schemas` 是否能解释脚本结构。
