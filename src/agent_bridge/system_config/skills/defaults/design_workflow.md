# design_workflow

你正在为 Agent Bridge 设计一个工作流。Agent Bridge 的工作流是一份**结构化 JSON DAG 定义**，在 Vue Flow 画布上编排，由后端轻量 DAG 执行器运行。本技能描述这份定义的完整契约。

工作流定义不再是脚本（不写 JS / Python 控制流），而是 `definition_json`：一组节点和连线。画布直接保存这份 JSON，后端校验是最终边界。

## 工作流元信息

- `workflow_key`：稳定标识。
- `name` / `description`：展示用。
- `profile_key`：工作流绑定的能力平面（Agent 节点开 MCP 时用它配置 Agent Bridge MCP）。
- `workflow_type`：`operation`（普通）或 `summary`（总结类，强制 Markdown→HTML 输出对）。
- `status`：`active` / `disabled`。

## 四类节点

### 1. 获取任务 `get_task`

无业务配置。调用服务的租约能力，租约一条待处理任务（锁到本次 run，`attempt_count+1`，租期 7200s）。

约束：

- 一个工作流**最多一个** `get_task` 节点。
- 存在时必须是**唯一的无入边起点**（整个图只有它一个根节点）。
- 不存在时，工作流是手动输入型：可以有一个或多个无入边节点，靠运行时传入的 `input` 驱动。
- 没有可租约任务时，运行结束为 `no_task`，**不执行后续任何节点**。
- 节点输出形如 `{ "task": { "task_key", "task_version", "type", "payload" } | null }`，供下游引用。

> 注意：`get_task` **只负责租约**，不负责生产任务。如果需要在队列空时从外部数据源补任务，那是前置 `script` 节点的职责（脚本里调 `workflow_set_task` 幂等 upsert），不是 `get_task` 的行为。第一版 DAG 不支持「租约为空 → 补任务 → 再租约」的循环，补任务应作为每次 run 固定执行的前置节点。

### 2. Agent `agent`

配置：

```json
{
  "prompt": "分析任务：{{ task.payload.repo }}",
  "backend_key": "codex",
  "mcp_enabled": true,
  "skill_names": ["code-review"],
  "result_mode": "text",
  "output_schema": null
}
```

- `backend_key`：必须是已注册启用的 Coding Agent 后端（如 `claude` / `opencode` / `codex`）。第一版无节点级模型选择。
- `mcp_enabled=true` 时用工作流绑定的 profile 配置 Agent Bridge MCP；不允许节点改 profile 或编辑 MCP JSON。
- `skill_names`：引用系统技能管理的技能，按数组顺序拼接到用户提示词**之前**（不调后端原生 Skills 机制）。
- `result_mode="text"`：输出规范化为 `{ "text": "..." }`。
- `result_mode="json"`：**必须**提供 `output_schema`（JSON Schema），AgentService 负责结构化输出与校验；输出必须是 JSON 对象。

统一提示词结构（技能在前、任务指令在后）：

```text
[技能：skill-a]
<skill-a 正文>

[技能：skill-b]
<skill-b 正文>

[任务指令]
<渲染后的节点提示词>
```

### 3. 托管脚本 `script`

配置：

```json
{
  "script_key": "workflow.collect_pages",
  "params": { "repo": "{{ task.payload.repo }}", "analysis": "{{ nodes.analyze.output }}" },
  "timeout_seconds": 60
}
```

- `script_key` 必须引用脚本管理中**启用**的托管 Python 脚本。
- 参数采用**显式映射**，不自动注入所有上游结果。
- 调用现有 `ScriptService.run_script()`（隔离目录、超时、stdout/stderr、运行记录）。
- 脚本返回的 JSON 对象**直接成为节点输出**。
- 节点里不能编辑脚本代码；代码在「脚本管理」页维护，节点只做参数映射。

### 4. 输出结果 `output`

输出节点是带产物持久化语义的 Agent 节点。配置含 Agent 节点的全部字段，并增加：

```json
{
  "format": "markdown",
  "title": "项目分析报告",
  "path": "reports/{{ task.task_key }}/index.md",
  "tags": ["summary"],
  "prompt": "...",
  "backend_key": "claude",
  "mcp_enabled": false,
  "skill_names": []
}
```

- `format`：`markdown` 或 `html`。
- `path`：产物逻辑路径，不能以 `/` 开头、不能含 `..`。支持引用（如 `repos/{{ task.task_key }}.md`）。
- 输出 Agent 必须返回固定结构：`{ "title", "summary", "content" }`，`content` 是完整 Markdown 或 HTML。

执行器对两种格式取上游数据的方式不同：

- **Markdown 输出**：自动拼接**全部祖先节点**的输出到提示词（`[上游节点输出]` 段），便于汇总。
- **HTML 输出**：只取**直接上游**的 Markdown 产物（必须是 `format=markdown` 的 output 节点）作为主输入，据此派生 HTML；缺少 Markdown 主产物时节点失败。

产物由执行器经 `save_artifact` 持久化；路径/格式/内容均由后端校验，无需 agent 自己写文件。

## 引用（路径插值）

第一版只支持纯路径引用，不支持函数、计算、过滤器或表达式：

```text
{{ input.topic }}
{{ task.payload.repo }}
{{ nodes.analyze.output.category }}
```

规则：

- 只能引用运行输入 `input.*`、当前任务 `task.*`、拓扑上的**祖先节点** `nodes.<id>.output.*`。引用非祖先节点会被保存校验拒绝。
- 脚本参数值若**整体只有一个引用**，保留原始 JSON 类型（不转字符串）；引用嵌入普通字符串时转为文本。
- 运行时引用字段缺失，节点**失败**（不替换为空字符串）。

## 条件边（条件分支）

每条边**可选**配置一个条件，实现结构化分支：

```json
{
  "id": "classify-bug",
  "source": "classify",
  "target": "handle-bug",
  "condition": { "field": "nodes.classify.output.category", "operator": "equals", "value": "bug" }
}
```

操作符仅五种：`equals` / `not_equals` / `exists` / `not_exists` / `contains`。不支持 AND、OR、条件组或自由表达式——复杂判断应交给 Agent 的 JSON 输出或托管脚本完成。

字段缺失时的判定：`exists=false`、`not_exists=true`、`equals`/`not_equals`/`contains` 均 `false`。

调度语义：

- 节点等待**所有直接上游**进入终态后，检查每条入边的条件。
- **至少一条**入边的条件成立且来源节点 `completed`/`warning` 时，节点运行。
- 所有入边条件都不成立、或来源节点被跳过时，节点标记 `skipped`（跳过会向下游传播）。
- 无条件的边视为条件恒成立。
- 同一轮就绪节点通过 `asyncio` **并行**运行。

> 条件字段只能引用**来源节点或其祖先**的输出（保存时校验），不能引用与来源并行的兄弟节点——否则会读到未确定的值。

**前端如何配置条件分支**：在 Vue Flow 编辑器画布上**点选一条连线**，右侧配置面板会出现「连线条件」区：勾选「启用条件」后填写字段路径（如 `nodes.classify.output.category`）、选择操作符、填写期望值。总结型工作流的 Markdown→HTML 连线固定为无条件，不可配置。

## 总结型工作流约束

创建 `summary` 类型时，编辑器自动生成并锁定：

```text
（其他节点）──▶ Markdown 输出 ──▶ HTML 输出
```

编辑器与后端共同强制：

- 恰好一个 Markdown 输出节点和一个 HTML 输出节点，位于图末端（按 Markdown、HTML 顺序）。
- 两个节点不可删除，`format` 不可修改。
- Markdown→HTML 的直接连线不可删除、不可加条件。
- HTML 节点**只能**直接依赖 Markdown 主报告，且必须是末端（无后续节点）。
- 其他处理节点可以连到 Markdown 输出；提示词、后端、MCP、技能、标题、路径、标签允许修改。

失败语义：

- Markdown 输出失败 → 工作流和任务都失败。
- HTML 输出失败 → 节点状态 `warning`，保留 Markdown 产物，**工作流和任务仍成功**（HTML 是派生展示，不因排版失败重跑整条任务）。

## 执行语义（fail-fast）

- 保存和运行前都校验 DAG 无环。
- 任一**普通**节点失败后，不再启动新节点，并尽力取消正在运行的 Agent；已就绪/未运行节点标记失败或跳过。
- HTML 输出失败是唯一的 warning 例外。
- 第一版**不做**自动重试、失败继续、补偿、循环、人工审批。

### 任务生命周期收尾

- 无 `get_task` 节点：只结束工作流运行。
- `get_task` 没租到任务：运行结束 `no_task`。
- 所有必需节点成功：任务标记 `completed`。
- 普通节点失败：任务标记 `failed`，不自动重新入队；管理员可在任务页手动重置。
- Markdown 成功而 HTML 失败：任务仍 `completed`。

## 服务端硬约束（执行器强制，非 manifest 约定）

这些由后端保证，**不需要**在节点配置里声明，了解即可：

- 产物 `path` 不得为绝对路径或含 `..`；`format` 仅限 `markdown` / `html`。
- 输出节点强制返回 `{ title, summary, content }` 固定结构；content 非空；HTML 须含 `<html>` 或 `<body>` 标签且 ≤ 5 MiB。
- profile / workflow 运行上下文从请求头读取、经 ContextVar 传递；节点配置里不写 profile。

## 智能体协作方式

如果用户要求智能体协助设计工作流，应提示智能体先读取本技能：

```text
请执行 execute service='built-in' tool_name='load_skill' params={"skill_name":"design_workflow"} 读取技能，
然后参照技能内容与我的需求，完成工作流 DAG 定义（节点、连线、条件与配置）。
```

智能体产出后应检查：

- 图是否无环？节点 ID / 边 ID 是否唯一？边的 source/target 是否都指向存在的节点？
- 是否需要 `get_task`？若用了，它是否是唯一根节点？
- Agent / 输出节点的 `backend_key` 是否是已启用后端？`skill_names` 是否都存在？`result_mode=json` 时是否提供了 `output_schema`？
- 脚本节点的 `script_key` 是否引用了启用脚本？必填参数是否都映射了？
- 所有 `{{ ... }}` 引用是否只指向 `input` / `task` / 祖先节点？条件字段是否只引用来源或其祖先？
- 若是 `summary` 型：是否只有一对受保护的 Markdown / HTML 输出节点，且顺序、连线、依赖关系符合约束？
- 输出节点的 `path` 是否合法（非绝对、无 `..`）？

## 完整定义示例（总结型）

```json
{
  "nodes": [
    { "id": "get-task", "type": "get_task", "name": "获取任务", "position": { "x": 80, "y": 220 }, "config": {} },
    {
      "id": "enrich", "type": "agent", "name": "富化信息", "position": { "x": 400, "y": 220 },
      "config": {
        "prompt": "为仓库 {{ task.task_key }} 收集结构化素材……",
        "backend_key": "claude", "mcp_enabled": true, "skill_names": [],
        "result_mode": "json", "output_schema": { "type": "object", "required": ["summary"], "properties": { "summary": { "type": "string" } } }
      }
    },
    {
      "id": "markdown-output", "type": "output", "name": "Markdown 主报告", "position": { "x": 740, "y": 140 },
      "config": {
        "format": "markdown", "title": "{{ task.task_key }} 速览", "path": "repos/{{ task.task_key }}.md", "tags": ["summary"],
        "prompt": "根据上游输出生成速览 Markdown……", "backend_key": "claude", "mcp_enabled": false, "skill_names": []
      }
    },
    {
      "id": "html-output", "type": "output", "name": "HTML 派生报告", "position": { "x": 1080, "y": 140 },
      "config": {
        "format": "html", "title": "{{ task.task_key }} 速览", "path": "repos/{{ task.task_key }}.html", "tags": ["summary"],
        "prompt": "只根据 Markdown 主产物生成完整 HTML 文档……", "backend_key": "claude", "mcp_enabled": false, "skill_names": []
      }
    }
  ],
  "edges": [
    { "id": "task-enrich", "source": "get-task", "target": "enrich" },
    { "id": "enrich-markdown", "source": "enrich", "target": "markdown-output" },
    { "id": "markdown-to-html", "source": "markdown-output", "target": "html-output" }
  ]
}
```

## 第一版明确不做

循环、人工审批、自动重试、失败继续、补偿、工作流版本/草稿/发布、单工作流 cron、内联代码、自由表达式与条件组、多任务源、动态节点插件、Agent 会话跨节点延续、节点级模型或 profile 选择。
