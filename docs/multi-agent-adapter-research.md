# 多 Coding Agent 适配 & 统一 Agent 查询能力 — 调研与需求

本分支承载两个相关需求：
1. **多 Coding Agent 适配抽象** — 把 Claude Agent SDK 的耦合抽象成可替换的 `CodingAgent` 层，方便后续接入 Codex / Gemini CLI 等其他 coding agent。
2. **统一 Agent 查询接口与展示** — 提取公共的「Agent 运行结果查看」能力，不再被 workflow 独占。

本文档是调研结论 + 需求清单，**未含实现**。工程量较大，留待手头工作完成后处理。

---

## 需求一：多 Coding Agent 适配抽象

### 背景

当前项目与 Claude Agent SDK（Python `claude-agent-sdk` 包）的耦合**小而集中**，但**完全没有为多 coding agent 预留抽象**：

- 真正接触 SDK 的源文件只有 **2 个**：`agent_runtime/service.py`（唯一的 SDK 调用点 + `ClaudeAgentOptions` 构建）和 `agent_runtime/events.py`（SDK 消息 → 统一事件，已用类名字符串解耦，无 import）。
- 其余 `WorkflowRunner`、`CodeGraphService`、`agent_runs.py`、`app/service.py` 等上游消费者**零 SDK 依赖**，全部通过 `AgentService.run()` 委托——单点接缝，这是好消息。
- 但现有的 3 个 `Protocol`（`WorkflowRunner` / `BuiltinCapabilityProvider` / `BackendAdapter`）没有一个抽象「coding agent」；配置层（`server.toml`）也没有 agent 类型开关。

### 目标

引入一个 `CodingAgent` 抽象层，把 `AgentService` 拆成两层：

- **通用编排层**（保留）：工作目录管理、文件 staging、profile/CLAUDE.md 安装、MCP 配置、事件持久化、`AgentRunResult` 信封——这些与后端无关。
- **后端执行器**（新 `CodingAgent` Protocol）：只负责把统一请求转成各家 SDK / CLI 调用，把各家消息流翻译成统一事件（沿用 `events.py` 的事件 schema 作为中立表示）。

```
AgentService (通用编排, 保留)
   └─ CodingAgent Protocol (新)
        ├─ ClaudeCodingAgent     (现有 service.py 的 SDK 部分)
        ├─ CodexCodingAgent      (未来)
        └─ ...
```

### 设计要点（参考 4 个同类项目得出）

调研了 4 个能适配多 coding agent 的项目（见下文「调研对比」），核心设计教训：

| 决策点 | 建议 | 出处 |
|---|---|---|
| 接口返回值 | 返回**自创中立事件**（沿用 `events.py` 的 schema），**不要**绑死具体 agent 的消息类型 | Proma 写死 `AsyncIterable<SDKMessage>` 是反面教材 |
| runtime 纳入契约 | `start()/abort()` 必须在接口里，不能游离在外 | CloudCLI 的 runtime 没进契约是缺陷 |
| 能力降级 | capability flags + optional 方法，而非抛 NotImplemented | Paseo |
| 工具调用归一化 | 做成共享的 schema 驱动解析原语，每个 adapter 不重写 | Paseo `tool-call-detail-primitives` |
| 单终态契约 | 每个 run 恰好一个终态事件，失败也补发 | CloudCLI `createCompleteMessage` |
| 是否走 ACP | **需先调研**目标 agent 对 ACP 协议的支持度，可能省掉大部分胶水代码 | AionUi |

### Claude 特有概念的语义鸿沟（迁移难点）

- 🟢 **易通用**：prompt / cwd / model / timeout / MCP servers / 流式的 assistant-text + tool-call + tool-result / 最终结果文本
- 🟡 **可降级映射**：`tools={preset}` / `output_format=json_schema` / cost / num_turns / session_id
- 🔴 **Claude 独有、无对应物（最大难点）**：`Task*Message` 子代理生命周期 + `parent_tool_use_id` 归因、`skills`、`permission_mode`、`CLAUDE.md`。其他 agent 多数没有内建子代理，这些事件可自然缺失或退化。

### 验收标准

- [ ] 定义 `CodingAgent` Protocol（中立事件输出 + capability flags）
- [ ] `AgentService` 重构为通用编排层 + 可注入的后端执行器
- [ ] 现有 Claude SDK 调用收敛为 `ClaudeCodingAgent` 一个实现，行为不变
- [ ] 配置层（`server.toml`）支持选择 agent 后端
- [ ] 现有测试通过；为抽象层补测试

---

## 需求二：统一 Agent 查询接口与展示

### 现状问题

现在有 **workflow** 和 **agent-runs** 两个页面都能看 agent 执行过程，它们是**单独写的、有重复代码、功能也不一样**：

- **前端**：`WorkflowView.vue`（2456 行，progress/tasks 两种模式各自渲染一套时间线）和 `AgentRunsView.vue`（455 行，详情用 Dialog 内嵌）。两者的事件渲染逻辑（`eventKindLabel` / `eventMessage` / `eventKindClass` / 子 Agent 折叠卡片 / 主 Agent 事件行）**几乎逐字重复**；子 Agent 折叠状态管理（`Record<string, Set<string>>` toggle）重复了三处。巧合的是 `AgentRunsView` 已经通过 `as WorkflowRunEvent[]` 断言复用了 `lib/workflowEvents.ts`，证明事件结构本质同构。
- **后端**：workflow 执行本质上就是一次 `AgentService.run()`，会产生两份事件记录——`ClaudeWorkflowRunner` 的回调写 `{temp_dir}/events.jsonl`（文件），`AgentService._persist_run` 写 `agent_runs.events_json`（数据库），**用的是完全相同的 `event_record()` 格式**。`/workflow-runs/{run_id}/events` 和 `/agent-runs?workflow_run_id={run_id}` 返回内容本质相同，仅存储介质不同。
- **缺口**：当前 `ClaudeWorkflowRunner.run()` 调 `AgentService.run()` 时**没透传 `workflow_key`/`run_id`**，导致 workflow 执行生成的 agent_runs 记录里这两个字段是空的，无法通过 `workflow_run_id` 反查关联。

### 目标

提取一个**公共组件和公共接口**：agent 查询接口 / 运行结果页面**不再放在 workflow 里**，而是作为**通用的「Agent 运行结果查看」能力**，workflow 只是它的一个调用方，而不是独占者。

### 具体工作

**后端：统一查询接口**
- [x] 修复 `ClaudeWorkflowRunner` 透传 `workflow_key`/`run_id` 到 `AgentService.run()`，让 agent_runs 记录能关联到 workflow run（补上 `workflow_run_id` 反查能力）。
- [x] 以 `agent_runs` 表为统一基准，收口「执行过程」查询（事件、工具调用、结果、cost、num_turns、session_id）；workflow 侧只保留调度元数据（status / exit_code / temp_dir / task_key）和 artifacts。
- [x] 评估 `/workflow-runs/{run_id}/events`（读文件）是否可由 `/agent-runs` 维度统一提供（文件版基本冗余）。（结论：保留文件版供 workflow 实时轮询，事件流查询统一走 `/agent-runs`，已在路由注释标注两者关系。）

**前端：提取公共组件**
- [x] 抽取 `<RunEventTimeline>` 公共组件：整合 `buildTimeline` + `.tl-*` 模板 + `eventKindLabel` / `eventMessage` / `eventKindClass`，接收 `events` + 可选的 `subagentDetailLoader`。workflow 的 progress/tasks 和 agent-runs 详情都复用它。
- [x] 统一事件类型：`AgentRunEvent` 是 `WorkflowRunEvent` 的严格子集，可统一为后者（子 Agent 字段对纯 agent 运行留空）。
- [x] 抽取 `useSubagentCollapse` composable，消除三处重复的子 Agent 折叠状态管理。
- [x] 抽取 `renderMarkdown` 到 `lib/markdown.ts`（消除两份相同 `marked.parse`）。
- [x] AgentRunsView 详情改为子路由（如 `#agent-runs/{runKey}`）以支持深链分享，对齐 workflow 的交互模式。

### 各自独有、不纳入统一的部分

- workflow 独有：artifacts 产物树、任务进度（多步骤）、DAG 调用图、`workflow_run_logs` 结构化 stage 日志、实时轮询、子 Agent Claude transcript 深度面板。
- agent-runs 独有：prompt 展示、结构化 result、cost / num_turns / model / cwd / session 元信息网格。

### 验收标准

- [x] 存在一个通用的「Agent 运行结果查看」页面/组件，不依赖 workflow 概念即可展示任意一次 agent run 的执行过程。
- [x] workflow 的执行过程展示改为复用该公共能力。
- [x] 前端重复代码消除（事件渲染、子 Agent 折叠、markdown 渲染统一）。
- [x] 后端可通过 `workflow_run_id` 反查关联的 agent_runs 记录。

---

## 调研对比：4 个多 Coding Agent 适配项目

调研对象位于 `/Users/kyynor/Code/tech-demo/agent_adapter/`。

### CloudCLI（`claudecodeui`）

明确的 5-provider 适配（Claude / Codex / Cursor / Gemini / OpenCode），并配有一份「如何新增 provider」的详尽 README。核心是 `IProvider` 接口 + 6 个 facet（models / mcp / auth / skills / sessions / sessionSynchronizer），每个 provider 一个目录。**最大缺陷**：真正「启动 agent 进程 / SDK」这件事（5 个 runtime 文件，SDK 与 spawn CLI 混用）完全游离在 `IProvider` 契约之外，靠 `spawnFns` map 拼接——新增 provider 要改 12+ 处。可借鉴：双 session-id 模型（app id 稳定 / native id 隔离）、`NormalizedMessage` 统一信封 + 单终态契约、capabilities 矩阵驱动 UI。

### Paseo

最完整的 per-agent adapter 实现，支持 9 类 agent。两层契约：`AgentClient`（工厂）+ `AgentSession`（会话），大量可选方法 + capability flags 做降级。最值得抄的是 ACP 基类（3361 行）一举覆盖 copilot/cursor/kiro/任意 ACP agent，以及 `ToolCallDetail` discriminated union + 共享 zod 容错解析原语（把各家 path/file_path/filePath、content/text/output 等命名变体归一成 11 种语义）。缺点：`AgentManager` 是 3885 行上帝类；契约定义在 server 包内部而非独立 protocol 包。

### AionUi

走「协议优先」路线，不写 per-agent adapter：所有 agent 当 ACP agent，靠 `@agentclientprotocol/sdk` spawn + JSON-RPC 握手，agent 自己上报模型 / 命令 / MCP 能力，前端零代码接入新 agent（号称 18+）。真正的适配逻辑藏在闭源 Rust 后端（aioncore）。**启示**：如果目标 agent 集都支持 ACP，直接用 ACP 协议能省掉 80% 胶水代码——这值得优先调研 ACP 覆盖度。代价：不实现 ACP 的 agent（如 Cursor）接不进来；AionUi 代码里没有「非 ACP → ACP 翻译器」的范例。

### Proma

**反面教材**：声称通用 Agent 工作台，实际 Agent 模式只绑定 `claude-agent-sdk`，是「单 coding agent + 多 LLM 模型」。虽然定义了 `AgentProviderAdapter` 接口，但返回类型写死 `AsyncIterable<SDKMessage>`（Claude 的消息形状），编排层直接 import Claude 专用类型 / 错误函数，抽象名存实亡。教训：① 接口返回值不能绑死具体 agent 的消息格式；② 编排层不能 import 具体 adapter 的类型。讽刺的是它 Chat 侧的 `ProviderAdapter`（纯逻辑 request-build + response-parse，执行靠注入）反而是最干净的抽象范本。

### 路线总结

- **写 adapter**（CloudCLI / Paseo）：每个 agent 一个实现，灵活但工作量大；Paseo 的 ACP 基类是最佳折中。
- **走标准协议**（AionUi）：agent 都支持 ACP 时最省事，否则有盲区。
- **先调研 ACP 对目标 agent 的覆盖度**，再决定路线。
