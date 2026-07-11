# 轻量工作流编辑器与执行器设计

日期：2026-07-11
分支：`codex/lightweight-workflow-editor-design`

术语约定：正文使用中文描述；`workflow_key`、`definition_json`、状态枚举值、类名和接口字段等代码标识保留英文，以便与实现直接对应。`operation` 表示操作型工作流，`summary` 表示总结型工作流。

## 1. 目标

用 Agent Bridge 自身的轻量可视化编辑器和 Python DAG 执行器替换当前强依赖 Claude Code 动态工作流的 `workflow.js` 方案。

第一版只解决当前明确需要的后台工作流：

- 在 Vue Flow 画布中编排少量领域节点。
- 继续使用现有全局后台调度器，并支持手动运行和测试。
- 通过现有 `AgentService` 调用 Claude、OpenCode 或 Codex。
- 通过现有 `ScriptService` 调用脚本管理中的托管 Python 脚本。
- 支持 DAG、并行和结构化条件分支。
- 任务获取和任务收尾由执行器负责，不再依赖 Agent 主动调用工作流 MCP 工具。
- 总结工作流固定生成 Markdown 主产物和 HTML 派生产物。

本设计不接入 Archon、Node-RED 或其他完整工作流平台，也不引入通用工作流引擎。

## 2. 设计原则

1. **结构化定义是唯一事实来源**：画布直接保存 JSON 图，不生成隐藏的 JS 或 Python。
2. **复用现有能力**：调度、任务租约、Agent 运行时、托管脚本、产物、日志和 SQLite 继续使用现有实现。
3. **领域节点而非通用节点市场**：第一版只有获取任务、Agent、托管脚本、输出结果四类节点。
4. **显式数据流**：节点通过 JSON 输出和路径引用传值，不通过共享临时文件或隐式全局变量传值。
5. **后端一致性优先**：技能统一拼接到用户提示词前，不依赖不同 Coding Agent 的原生 Skills 语义。
6. **失败语义简单**：普通节点采用快速失败；不做自动重试、失败继续或补偿。
7. **必要约束直接固化**：总结工作流的 Markdown 和 HTML 输出结构在编辑器中锁定，而不是保存时才让用户修复。

## 3. 方案选择

采用：**JSON 图定义 + Vue Flow 编辑器 + 自研轻量 Python DAG 执行器**。

不采用：

- 画布编译成 Python：会重新形成图与代码两套事实来源。
- 完整 Python 工作流引擎：会与现有调度器、任务状态、运行记录和 SQLite 生命周期重叠。
- 继续解析 `workflow.js`：只能展示有限静态结构，不能成为可靠编辑和执行协议。

前端继续复用仓库中已经安装的：

- `@vue-flow/core`
- `@vue-flow/background`
- `@vue-flow/controls`
- `@vue-flow/minimap`
- `@dagrejs/dagre`

后端使用现有 Python、Pydantic、SQLite 和 `asyncio`，不增加工作流引擎依赖。

## 4. 工作流定义

工作流元数据继续保留：

- `workflow_key`
- `name`
- `description`
- `profile_key`
- `workflow_type`: `operation | summary`，分别表示操作型和总结型
- `status`: `active | disabled`，分别表示启用和停用

原 `workflow_js` 被 `definition_json` 取代。定义结构如下：

```json
{
  "nodes": [
    {
      "id": "get-task",
      "type": "get_task",
      "name": "获取任务",
      "position": { "x": 80, "y": 180 },
      "config": {}
    }
  ],
  "edges": [
    {
      "id": "edge-1",
      "source": "get-task",
      "target": "analyze",
      "condition": null
    }
  ]
}
```

后端为工作流、节点配置、连线和条件定义 Pydantic 模型。前端 TypeScript 类型与后端模型保持同构，但后端校验是最终边界。

### 4.1 获取任务节点

类型：`get_task`

- 无业务配置。
- 一个工作流最多存在一个。
- 存在时必须是唯一无入边节点。
- 不存在时，工作流是手动输入型工作流，可以有一个或多个无入边节点。
- 直接调用 `WorkflowService.get_task_for_agent()` 对应的服务能力，不通过 Agent/MCP 领取任务。
- 没有可领取任务时，运行结束为 `no_task`，不执行后续节点。

### 4.2 Agent 节点

类型：`agent`

配置：

```json
{
  "prompt": "分析任务：{{ task.payload }}",
  "backend_key": "codex",
  "mcp_enabled": true,
  "skill_names": ["code-review", "report-style"],
  "result_mode": "text",
  "output_schema": null
}
```

约束：

- `backend_key` 必须是已注册且启用的 Coding Agent 后端。
- 第一版不提供节点级模型选择；当前每个后端只有唯一模型配置。
- `mcp_enabled=true` 时使用工作流绑定的 Profile 配置 Agent Bridge MCP。
- 不允许节点编辑完整 MCP JSON，也不允许节点选择其他 Profile。
- `skill_names` 引用系统技能管理中的技能，并按数组顺序注入。
- 技能正文在用户提示词之前拼接，不调用后端原生 Skills 机制。
- `result_mode=text` 时输出规范化为 `{ "text": "..." }`。
- `result_mode=json` 时必须提供 JSON Schema，AgentService 负责结构化输出和校验。

统一提示词结构：

```text
[技能：skill-a]
<skill-a 正文>

[技能：skill-b]
<skill-b 正文>

[任务指令]
<渲染后的节点提示词>
```

### 4.3 托管脚本节点

类型：`script`

配置：

```json
{
  "script_key": "workflow.collect_pages",
  "params": {
    "repo": "{{ task.payload.repo }}",
    "analysis": "{{ nodes.analyze.output }}"
  },
  "timeout_seconds": 60
}
```

约束：

- 节点中不能编辑代码。
- `script_key` 必须引用脚本管理中启用的托管 Python 脚本。
- 参数采用显式映射，不自动注入所有上游结果。
- 调用现有 `ScriptService.run_script()`。
- 脚本返回的 JSON 对象直接成为节点输出。
- 脚本运行仍使用现有隔离目录、超时、stdout、stderr 和运行记录。

### 4.4 输出结果节点

类型：`output`

输出节点是具有产物持久化语义的编码智能体节点，不是固定格式转换器。

配置包含 Agent 节点的提示词、后端、MCP 开关和技能列表，并增加：

```json
{
  "format": "markdown",
  "title": "项目分析报告",
  "path": "reports/{{ task.task_key }}/index.md",
  "tags": ["summary"],
  "prompt": "根据上游结果生成 Markdown 报告……",
  "backend_key": "claude",
  "mcp_enabled": false,
  "skill_names": ["report-style"]
}
```

输出 Agent 必须返回固定结构：

```json
{
  "title": "报告标题",
  "summary": "一句话摘要",
  "content": "完整 Markdown 或 HTML"
}
```

输出节点除了渲染用户可修改的提示词，还由执行器自动附加直接及间接上游节点的结构化输出。HTML 输出节点只自动附加 Markdown 主产物正文和元数据，不重复注入全部原始分析上下文。

执行器校验结构和格式后，通过现有 Workflow Artifact 服务保存产物。

## 5. 引用与条件

### 5.1 路径引用

第一版只支持：

```text
{{ input.topic }}
{{ task.payload.repo }}
{{ nodes.analyze.output.category }}
```

规则：

- 不支持函数、计算、过滤器或表达式。
- 节点只能引用运行输入、当前任务和拓扑上的祖先节点。
- 脚本参数值如果完整内容只有一个引用，保留原始 JSON 类型。
- 引用嵌入普通字符串时，将值转换为文本。
- 提示词或脚本参数引用在运行时缺失，节点失败，不替换为空字符串。

### 5.2 条件边

每条边最多配置一个条件：

```json
{
  "field": "nodes.classify.output.category",
  "operator": "equals",
  "value": "bug"
}
```

仅支持：

- `equals`
- `not_equals`
- `exists`
- `not_exists`
- `contains`

不支持逻辑与、逻辑或、条件组或自由表达式。复杂判断应由 Agent 结构化输出或托管脚本完成。

字段缺失时：

- `exists` 为 `false`
- `not_exists` 为 `true`
- `equals`、`not_equals`、`contains` 均为 `false`

每条条件边记录字段实际值和判断结果，供运行详情解释分支选择。

## 6. 总结工作流约束

创建 `summary` 工作流时自动创建并连接：

```text
Markdown 输出 -> HTML 输出
```

两个输出节点默认使用系统配置中的默认 Coding Agent 后端；创建完成后可以分别修改为其他已启用后端。Markdown 默认提示词要求根据全部上游节点输出生成结构清晰的 Markdown 主报告；HTML 默认提示词要求只根据 Markdown 主产物生成完整、内联 CSS、无外链脚本的 HTML 文档。

编辑器和后端共同强制：

- 恰好一个 Markdown 输出节点和一个 HTML 输出节点。
- 两个节点不可删除。
- 节点 `format` 不可修改。
- Markdown 到 HTML 的直接连线不可删除。
- HTML 节点不能连接后续节点。
- 提示词、后端、MCP、技能、标题、路径和标签允许修改。
- 其他处理节点可以连接到 Markdown 输出节点。
- HTML 节点以 Markdown 产物为主要输入。

失败语义：

- Markdown 输出失败：工作流和任务失败。
- HTML 输出失败：节点状态为 `warning`（警告），保留 Markdown，工作流和任务仍成功。

HTML 是派生展示，不因排版失败重新执行整条后台任务。

## 7. 执行架构

```text
WorkflowScheduler
      |
      v
WorkflowDagExecutor
      |
      +-- GetTaskHandler（获取任务处理器）
      +-- AgentHandler（Agent 执行处理器）
      +-- ScriptHandler（托管脚本处理器）
      +-- OutputHandler（输出结果处理器）
```

`WorkflowDagExecutor` 只负责：

- 校验并冻结工作流定义。
- 创建运行上下文和节点运行记录。
- 判断节点是否就绪。
- 求值条件边。
- 并行调度就绪节点。
- 持久化状态和输出。
- 快速失败和取消。
- 工作流及任务收尾。

每个节点处理器只负责执行一种节点并返回 JSON 对象。

### 7.1 运行上下文

```json
{
  "input": {},
  "task": null,
  "nodes": {
    "analyze": {
      "status": "completed",
      "output": {}
    }
  }
}
```

- 手动运行参数进入 `input`。
- 获取任务节点的结果进入 `task`。
- 节点只能写入自己的状态和输出。
- Agent 和脚本继续使用各自独立运行目录。
- 节点间不共享临时文件，数据通过 JSON 输出传递。

### 7.2 调度规则

1. 保存和运行前都验证 DAG 无环。
2. 无入边节点立即成为候选节点。
3. 节点等待所有直接上游进入终态。
4. 至少一条入边被激活时，节点运行。
5. 所有入边条件均不成立或来源节点被跳过时，节点标记 `skipped`。
6. 同一轮就绪节点通过 `asyncio` 并行运行。
7. 任一普通节点失败后，不再启动新节点，并尽力取消正在运行的 Agent。
8. HTML 输出失败是唯一的警告例外。

第一版不增加工作流级并发配置，沿用现有 Agent 并发控制和全局调度器运行限制。

### 7.3 任务生命周期

- 无获取任务节点：只结束工作流运行。
- 获取任务节点没有返回任务：运行结束为 `no_task`。
- 所有必需节点成功：任务标记 `completed`。
- 普通节点失败：任务标记 `failed`，不自动重新入队。
- 管理员可以在现有任务页手动重置失败任务。
- Markdown 成功而 HTML 失败：任务仍标记 `completed`（已完成）。

## 8. 持久化

### 8.1 工作流定义

`workflow_definitions` 使用可空的 `definition_json` 作为新执行器的定义来源。新建或经新编辑器保存的工作流必须写入该字段；升级前仅有 `workflow_js` 的历史工作流保持 `NULL`，不得被当成空图执行。

第一版不实现草稿、发布或版本列表。保存会覆盖当前定义。

### 8.2 工作流运行

`workflow_runs` 增加：

- `definition_snapshot_json`
- `input_json`
- `output_json`

运行启动时保存定义快照，确保编辑工作流不会改变已经启动的运行。

### 8.3 节点运行

新增 `workflow_node_runs`：

- `run_id`
- `node_id`
- `node_type`
- `status`: `pending | running | completed | skipped | failed | cancelled | warning`，分别表示等待、运行中、已完成、已跳过、失败、已取消和警告
- `condition_results_json`：该节点每条入边的条件字段、实际值和判断结果
- `output_json`
- `error`
- `agent_run_key`
- `script_run_id`
- `started_at`
- `finished_at`

节点表是运行详情页的直接数据源，不从日志反推图状态。Agent 和脚本的详细事件仍由现有运行记录承载。

## 9. 编辑器设计

现有工作流模块保留列表、详情、任务、产物和运行页面。编辑页面改为三栏：

- 左侧：获取任务、Agent、托管脚本、输出结果四类节点。
- 中间：Vue Flow DAG 画布。
- 右侧：当前节点或连线的配置。
- 顶部：返回、保存、测试运行。

交互：

- 从左侧拖入节点。
- 选中节点后编辑配置。
- 选中连线后编辑结构化条件。
- 删除节点时删除关联边。
- 总结型工作流的强制节点和连线不可删除。
- 不显示内联代码编辑器。
- 画布显示保存校验错误和运行状态。

第一版只显式保存，不自动保存。存在未保存修改时，测试运行先要求保存；不提供执行临时草稿的第二套入口。

## 10. API 调整

尽量沿用现有工作流路由：

- 保存工作流：接收 `definition_json`，不再接收 `workflow_js`。
- 获取工作流：返回结构化定义。
- 手动运行：增加可选 `input` JSON。
- 运行详情：返回定义快照、最终输出和节点运行列表。
- 工作流日志、任务、产物和 Agent Run 接口继续复用。

保存错误返回可定位信息：

```json
{
  "errors": [
    {
      "scope": "node",
      "id": "analyze",
      "field": "config.skill_names",
      "message": "skill not found: report-style"
    }
  ]
}
```

前端根据 `scope + id` 在画布中标记对应节点或边。

## 11. 错误处理

普通节点失败时记录：

- 节点 ID 和类型。
- 简短错误信息。
- Agent Run 或 Script Run 关联 ID。
- 已完成节点输出。
- 尚未运行节点状态。
- 取消中的并行节点。

运行输出：

- 普通工作流的 `output_json` 包含实际完成的末端节点输出。
- 总结工作流额外包含 Markdown 和 HTML 产物 ID。
- HTML 失败时 HTML 产物 ID 为空，并包含警告信息。

可以在保存时发现的引用、图结构和资源引用错误必须拒绝保存；不把明显配置错误延迟到运行阶段。

## 12. 迁移策略

- 不开发 `workflow.js -> definition_json` 自动转换器。
- 旧 `workflow_js` 字段保留一段兼容期，用于读取历史定义和历史运行展示。
- `definition_json IS NULL` 的历史工作流在列表中显示“需要迁移”，全局调度器跳过它，手动运行时返回明确校验错误。
- 新编辑器保存后，该工作流使用 `definition_json` 和新执行器。
- 现有工作流通过新编辑器手动重建。
- 新执行器完成并验证后，移除 Claude-only `ClaudeWorkflowRunner` 主路径。
- 当前 HTML 报告生成器行为迁移进 HTML 输出处理器后，移除调度器中的额外报告后处理。

不自动转换的理由是旧 JS 可包含任意动态控制流，转换结果无法可靠保证语义一致；维护一次性转换器不符合第一版范围。

## 13. 测试策略

后端：

1. 定义校验：环、非法边、节点 ID、任务节点和总结输出约束。
2. 引用渲染：类型保持、字符串插值、祖先限制和缺失字段。
3. 条件求值：五个操作符和字段缺失语义。
4. DAG 调度：串行、并行、条件分支、汇合和跳过传播。
5. 快速失败：停止新调度并取消运行中的 Agent。
6. 四种节点处理器：使用模拟 Agent 和模拟脚本进行隔离测试。
7. 任务生命周期：无任务、成功、失败和 HTML 警告。
8. API：保存、手动输入运行、定义快照和节点进度。
9. 调度器回归：全局窗口、并发上限和手动测试保持现状。

前端：

- 图定义和 Vue Flow 数据双向转换。
- 节点及连线编辑、删除和条件配置。
- 总结型工作流强制节点保护。
- 保存错误定位。
- 节点运行状态映射。
- Vue 类型检查和生产构建。
- 桌面宽度下验证画布、配置面板、长提示词和节点状态不重叠。

## 14. 第一版明确不做

- 循环和人工审批。
- 自动重试、失败继续和补偿。
- 工作流版本、草稿和发布流程。
- 单工作流 cron 或运行窗口。
- 内联 Python、JavaScript 或 Shell。
- 自由表达式和条件组。
- 多任务源。
- 动态节点插件。
- Archon、Node-RED 或旧 JS 工作流导入。
- 工作流模板市场。
- 单步调试和断点。
- Agent 会话在节点之间延续。
- 节点级模型或 Profile 选择。

## 15. 验收标准

1. 管理员可以创建操作型或总结型工作流，并通过 Vue Flow 保存合法 DAG。
2. 操作型工作流可以包含可选获取任务节点、Agent 节点、托管脚本节点和输出节点。
3. 总结型工作流始终包含受保护的 Markdown 和 HTML 输出节点及固定直接连线。
4. Agent 节点可选择 Claude、OpenCode 或 Codex，并配置提示词、Profile MCP 开关、技能顺序和可选 JSON Schema。
5. 脚本节点只能选择启用的托管 Python 脚本，并能用显式参数映射接收上游数据。
6. 条件边能根据结构化字段选择分支；并行节点可同时执行并正确汇合。
7. 任一普通节点失败会终止运行并标记任务失败；不会自动重新入队。
8. Markdown 成功而 HTML 失败时保留 Markdown，运行和任务成功，HTML 节点显示警告。
9. 全局后台调度和手动测试都使用同一新执行器。
10. 运行详情能在工作流图上显示节点状态、输出摘要以及 Agent/脚本详情入口。
