# CLAUDE.md

本文件记录 Agent Bridge 当前架构与修改不变量。使用方式见 `README.md`，跨 Agent 通用开发规则见 `AGENTS.md`。三份文档必须随代码同步更新，不引用易漂移的源码行号。

## 常用命令

```bash
uv sync
./scripts/test.sh fast -q
./scripts/test.sh full -q

cd frontend/capabilities
npm ci
npm run dev
npm run check
```

服务管理：

```bash
uv run agent-bridge server start
uv run agent-bridge server init
uv run agent-bridge server status
uv run agent-bridge server stop
```

CLI 根命令只有 `server`、`profile`、`memory`。不要在文档中添加未经 `agent-bridge --help` 验证的命令。

## 部署与信任模型

- 默认数据根目录为 `/root/agent-bridge`，环境变量 `AGENT_BRIDGE_ROOT` 可覆盖。
- 当前按内部可信 VM 设计。身份来自 `X-Agent-Bridge-User`，`server.toml` 的 `admins` 决定管理员。
- 本阶段不增加互联网级认证；部署方负责监听地址、内网访问和反向代理边界。
- `server_runtime/` 只负责 uvicorn 服务进程；`agent_runtime/` 负责 Coding Agent 执行，不要混用。

## 应用与存储

`AgentBridgeService` 是装配根和兼容门面。FastAPI 路由调用应用/领域 service，领域 service 使用 repositories 或 adapter。不要继续把具体知识后端、工作流或脚本业务堆入门面。

`SQLiteStore` 仍提供兼容门面，具体持久化按 `storage/repositories/` 分域。新增存储逻辑优先进入对应 repository；schema 变化使用幂等、可测试的迁移步骤。

领域失败抛 `AgentBridgeError` 子类，由 API 全局异常处理器转换为 HTTP 响应。重新分类错误时使用明确类型并 `raise ... from exc`，不要修改任意异常对象。

## 能力中心

能力来源通过 `CapabilitySourceAdapter` 和 `CapabilitySourceRegistry` 分发。当前实现包括 Builtin、MCP、OpenAPI：

- adapter 负责来源匹配、根目录投影、工具检索、执行和工具名枚举；
- `CapabilityService` 只编排统一审计、查询过滤和响应信封；
- 新增来源应新增 adapter 并注册，不在 `CapabilityService` 增加中心 `if/elif`；
- 外部来源执行前由 adapter 完成 Profile 来源级校验；Builtin provider 负责自身资源级校验。

能力失败使用 `capability_hub/errors.py` 的不可变 `CapabilityFailure` 与类型化领域错误。审计字段包括 status、stage、owner、error_type、resource 和 log_id。禁止恢复 `_tool_call_*` 动态属性或原地改写异常 `message/args`。

`log_tool_call` 是统一审计出口。MetaMCP 的 search、execute、pinned tools、工作流辅助工具和脚本调用都必须可关联 log_id。

MetaMCP `/mcp` 每个请求按 profile/workflow 上下文创建 request-scoped 工具集，底层 transport 使用 stateless 模式。不要缓存包含动态工具注册的 server 实例，避免跨请求上下文泄漏。

## 文档知识后端

所有文档知识后端实现 `BackendAdapter`。应用层通过 `DocsKnowledgeService` 与 adapter 交互，不感知 Weknora、RagFlow、PageIndex 等具体类型。

可选能力使用独立、可运行时检查的 Protocol：

- `AgentManagementBackend`：列出/创建后端 Agent 和类型预设；
- `ManagedResourcesBackend`：初始化或修复后端托管资源；
- 基础 `BackendCapabilities` 只声明能力，不替代 Protocol 的真实实现检查。

后端专属 HTTP、鉴权、错误解释和自愈逻辑放在对应 adapter。未知或缺失 adapter 必须明确报错；Mock 只在显式 `type="mock"` 时使用，不能兜底配置错误。

## 代码知识与记忆

`CodeGraphService` 管理仓库镜像、代码图查询和 Understand-Anything。正式索引后端统一为 `CodeGraphBackend`：CLI 负责 init/index/query/callers/callees/impact，MCP stdio 负责 Explore；两者使用同一个 CodeGraph 引擎。CLI 缺失、索引未就绪或调用失败时抛 `BackendUnavailable`，不得返回空数组冒充“无结果”。

旧 `codegraph_index_items` SQLite 文本索引已移除。启动迁移会删除这类可重建派生数据，并要求受影响仓库重新同步。仓库文件列表和文件内容直接读取 Git 镜像，不依赖 CodeGraph。

Memory 与文档/代码知识平级，但由 Claude Code hooks 实时写入。claude-mem worker 是按需进程池；插件更新由独立多 job 的 `PluginUpdateScheduler` 管理。它与单 cron 的 `BaseCronScheduler` 语义不同，不要为了继承而继承。

## Coding Agent

`CodingAgent` / `CodingAgentRun` 是统一契约，Claude、Codex、OpenCode、Pi 在 `agent_runtime/adapters/` 实现。`AgentService` 负责工作目录、Profile/MCP 配置、运行记录和事件持久化。

Agent 运行观测也走统一规范化事件流：工具事件包含 `input`/`output`（短内容内联，超过阈值落到运行目录 `payloads/` 并返回安全相对引用），工具结果包含 `started_at`、`finished_at` 和 `duration_ms`；运行准备、后端执行、收尾和总耗时以 `stage` 事件记录。SQLite 保存可查询的摘要和完整事件列表，JSONL 负责运行中的实时追加和原始消息留档。

前端时间轴对长 payload 只展示预览，点击“查看”后在弹窗中按 Markdown 渲染，JSON 先格式化再展示，或使用只读 CodeMirror 对 JSON、HTML、Python、JavaScript 做语法高亮；工具输入、输出和模型详情即使是短内容也提供查看入口，完整内容加载后仍不直接塞回时间轴，避免大文本撑开页面。

Codex/Pi 的 JSONL 子进程生命周期统一由 `adapters/jsonl_cli.py` 管理，包括：

- 启动与 stdout JSONL 解码；
- stderr 收集和摘要；
- native message 可选转发；
- abort 的 terminate、等待、kill 升级。

各 adapter 只保留命令参数、协议事件映射和终态语义，不复制公共进程骨架。

OpenCode 使用 `opencode serve --port 0 --hostname 127.0.0.1` 的 server HTTP 模式。每次
Agent run 由 `adapters/opencode_server.py` 启动一个独立 server，等待监听地址和 HTTP 就绪后，
通过 `POST /session?directory=...` 创建会话，订阅
`GET /event?directory=...` 的 SSE，再调用
`POST /session/{id}/prompt_async?directory=...`。server 在 run 完成、失败、停止或超时后统一
回收；SSE framing 与 V1 事件映射分开，文本、工具、阶段、token 和 provider 耗时逐事件进入
统一事件流；reasoning part 的 provider 文本进入阶段事件 `detail`，由运行目录 payload 规则
负责长内容外置。结构化输出使用 OpenCode 的 `format.type=json_schema`，从
`StructuredOutput` tool part 的 `state.input` 提取。

运行时间轴保留原始事件流的可追踪性，但展示层会将同一 `tool_use_id` 的工具调用与结果合并为
一个工具卡片，并将同一 `stream_id` 的文本增量拼接后展示，避免 SSE token 粒度造成大量碎片节点。

Agent runtime 配置暂时强制 `slug == type`。现阶段同 type 多 slug 没有实例级差异配置，不能提供真实价值；未来引入实例化配置后再扩展一对多模型。

## 工作流

工作流是受 Pydantic 校验的结构化 DAG，不再执行 `workflow.js`：

- 节点类型：`get_task`、`agent`、`script`、`output`；
- 边可携带基于祖先节点输出的条件；
- 总结型工作流末尾必须是 Markdown 主报告和 HTML 派生报告；
- `WorkflowDagExecutor` 负责拓扑执行、条件、增量缓存和节点输出；
- `WorkflowScheduler` 负责运行时间窗、并发和任务租约，不继承单 cron 基类。

增量运行只复用无副作用的节点结果；`get_task` 负责把队列任务租约绑定到当前 run，因此每次都必须重新执行，但只有任务业务输入相对基线发生变化时才使下游结果失效。带条件入边的节点及其下游在预览中只能作为“待条件结果”的候选；`WorkflowDagExecutor` 在条件实际命中、节点 ready 时再决定是否复用，未命中的分支不得使汇合后的节点失效。启用受管 Profile MCP 本身不使节点失去复用资格：节点配置与后端资源指纹稳定时复用既有结果；需要刷新外部读取时使用 `force_full`。按任务启动的 run 会先精确租赁已选任务，`get_task` 只返回该租约，不得改领其他队列任务。`workflow_set_task`、`workflow_run_log` 等 Agent 运行期间的辅助调用不因增量复用规则被强制重跑。

导入/导出格式为 `agent-bridge.workflow`、`format_version=1`。`examples/workflows/*/workflow.json` 必须通过示例契约测试。不要恢复 `manifest.json + workflow.js` 双运行时。

Agent 运行事件以 `agent_runs` 为统一查询基准。前端通过 `RunEventTimeline`、`SubagentDetailPanel` 和 `useSubagentDetails` 复用运行详情；不得在 Workflow 与 AgentRuns 页面分别维护另一套加载状态。

## 时间处理规范

- 禁止使用 `datetime.utcnow()` 和 naive UTC。
- 当前时间使用 `core/timeutil.py:utc_now()`。
- 持久化 ISO 时间使用 `utc_iso()`；解析数据库或历史 `Z/+00:00/naive` 值使用 `parse_utc()`。
- 不在调用点重复定义 `now_iso`，也不手写 `.replace("+00:00", "Z")`。
- 过期、租约、缓存使用 aware UTC；耗时统计使用 monotonic/perf_counter。

## 日志

- 使用标准库 `logging.getLogger(__name__)`，由 `core/logging.py` 统一转发到 loguru。
- 日志消息使用中文和 `%s` 惰性格式化。
- INFO 记录关键生命周期开始/完成；WARNING 记录可恢复失败、拒绝和降级；ERROR 记录最终失败并在活动异常块中使用 `exc_info=True`。
- 核心异常不得静默吞掉。容错继续执行时也必须记录实体 key、阶段、状态、原因和耗时。
- 高频循环使用 DEBUG 或聚合日志，不逐条输出 INFO。

## 管理后台

前端位于 `frontend/capabilities`，使用 Vue 3、TypeScript、Vite、Tailwind v3，并保持 Chrome 90 兼容。

- 语义颜色统一定义在 `src/styles/base.css`，页面不直接使用 Tailwind 调色盘色或 hex。
- 页面共享判断进入 `src/lib`；共享状态进入 `src/composables`；可视区域进入 `src/components`。
- `WorkflowView` 等大视图只负责页面编排，新增功能先提取组件，不继续增加内联业务区块。
- 工作流任务执行规则统一使用 `canRunNormally`、`canForceRun`、`canRunTask`。
- 工作流定义/节点视觉 helper 位于 `src/lib`，不放在 `views/`。
- 设计原型不得放入 `public/`；测试验证真实组件、源码或行为。

修改前端后执行 `npm run check`。生产构建输出到 `src/agent_bridge/static/capabilities/`，该目录被忽略；发布流程必须先构建前端再构建 wheel。

## 文档同步检查

以下变化必须在同一提交更新 README、CLAUDE、AGENTS 或示例：

- CLI 命令、配置副作用或部署步骤；
- 新增/删除能力来源、知识后端、Agent backend；
- 工作流格式、节点、导入信封；
- 目录重命名、公共时间/日志/error 规范；
- 测试命令和发布构建链。

注释、docstring 和用户可见错误优先中文；标识符、协议字段和外部产品名保留英文。
