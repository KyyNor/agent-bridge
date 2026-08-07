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

`SQLiteStore` 仍提供兼容门面，具体持久化按 `storage/repositories/` 分域。主业务库为 `agent-bridge.db`，工具调用与 Agent 运行审计位于独立的 `agent-bridge-logs.db`；运行日志 repository 必须使用日志连接，不能重新写回主库。新增存储逻辑优先进入对应 repository；schema 变化使用幂等、可测试的迁移步骤。

业务台账使用独立的 `agent-bridge-ledgers.db`，定义与记录均以 SQLite 持久化；加载、筛选、排序和模糊匹配只针对 pandas 内存快照执行。所有字段默认精确匹配和可排序，文本字段仅额外配置是否允许字面包含检索，数字、日期与日期时间默认支持完整范围运算；多字段排序按传入顺序生效。每个台账上限为 100 字段、50,000 行；写入必须完整重建并原子替换快照，查询不得回退至 SQLite。

业务台账 Excel 导入模板仅导出当前字段标识表头，绝不复用数据导出接口或泄露已有记录。

业务台账定义可通过 `design_business_ledger` 设计 Agent 生成或修改。该 Agent 的输出必须经过 Draft 07 Schema 约束，只能形成待管理员采纳的完整定义；不得直接写入台账记录或注册为 Agent MCP 管理能力。

业务台账是 `business_ledger` 内置能力来源，Agent 只获得顶级 `query_business_ledger` 读取工具；管理定义和数据的 API 不得注册到 Agent MCP。没有 Profile 或未显式绑定资源时必须 fail closed；拒绝访问时只能提示当前 Profile 已获授权的台账。

`workflow_artifacts` 的标题、摘要、路径和正文通过 jieba 预分词与 SQLite FTS5 索引检索；中文查询词按 `AND` 组合，长度至少 3 的 ASCII 标识符使用 FTS5 前缀匹配，短 token 和带分隔符的路径/标识符使用字面匹配。Profile、current/history、标签、格式和路径前缀仍由普通表条件过滤。原始产物正文不被改写，分词副本单独维护并随 artifact 生命周期同步。
`artifacts_search` 结果通过公共 `DiskCacheStore` 做磁盘缓存，TTL 默认 8 小时，由系统配置中的 `artifact_search_cache_ttl_hours` 控制；检索请求每次读取当前配置，修改后立即生效。当前版本暂不因新产物或 current 状态变化主动清理检索缓存。

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

`profile use` 写入的 Agent Bridge HTTP MCP server 必须携带 300 秒工具调用超时（`timeout: 300000`，单位为毫秒），避免 Claude Code 远程 MCP 的默认短请求超时截断长耗时知识库问答。

`profile use` 同时安装 `SessionEnd` 配置同步 Hook。Hook 对实际生成的 Agent Bridge MCP、托管 Hook 和 `CLAUDE.md` 说明块计算 hash，仅在 hash 变化时写入；用户自有配置不参与托管投影，也不使用额外的 schema/version 文件。`profile sync` 是同一同步逻辑的手动入口。

`profile unuse` 交互列出当前项目和用户级已接入的 Profile，选择后只删除对应范围的 Agent Bridge MCP、托管 Hook 和说明块，保留用户自有配置；脚本调用使用 `--scope project|user --yes`。

顶层 MetaMCP 工具（除固定的 `search`、`execute` 外）由 `gateway/top_level_tools.py` 维护单一目录，并可在系统管理中临时关闭。注册 tools/list、能力目录检索和对应内置能力执行必须共用该状态；不得只从 tools/list 隐藏而保留通用 `execute` 绕过路径。Profile pin 属于 Profile 配置，不纳入全局顶层工具开关。

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

`RetrievalProbeService` 为弱模型提供 Profile 范围的轻量路由探测。来源通过
`RetrievalProbeAdapter` 注册，领域服务只负责模型短句、并发 deadline、状态聚合和审计，
不按来源类型建立中心分发链。当前 full-probe 只装配 Artifact 来源，探测只返回资源级
命中计数和建议工具，不返回正文。供外部调用的 `POST /retrieval/probe` 与 Claude Code Hook 的
`POST /retrieval/hooks/claude-code/full-probe` 路由分离：CLI 只转发原始 Hook payload，
服务端使用系统配置的 OpenAI Chat 小模型提取 0–8 个短句，并按 Profile/session 结合最近 3 轮
历史去重（最多缓存 12 轮、30 天滑动 TTL），仅检索 current 工作流产出物；
抽取失败不得回退 Jieba 短词检索。服务端负责生成标准 `additionalContext` 输出并写入通用
Hook 审计（审计保留原始 prompt）。`profile use` 会自动、幂等安装该 Hook，并保留用户已有 Hook；
全量探测的 `UserPromptSubmit` Hook 必须保持同步（`async: false`），以便将
`additionalContext` 注入当前轮，不能改为后台异步执行。
`CLAUDE.md` 托管块只补充
`<system-reminder>` 是系统补充信息的语义说明。

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

Agent run 的 `events.jsonl` 是可重放事实来源。`AgentService` 必须先将带单 run 递增
`event_id` 的事件 flush 到该文件，再交给进程内发布器分发给
`GET /agent-runs/{run_key}/events/stream`；不得让 SSE 路由轮询或自行写文件。连接以
`Last-Event-ID` 重放，慢订阅者发送 `resync_required` 交由客户端调用 REST 快照恢复。当前
server runtime 为单 uvicorn worker，进程内 hub 只适用于此模型；多 worker/多实例部署必须
在启用前更换为跨进程 broker 或共享日志实现。前端用 fetch 流携带
`X-Agent-Bridge-User`，不用无法附加该 Header 的原生 `EventSource`。

所有 Coding Agent 的结构化输出 Schema 统一按 JSON Schema Draft 07 传递和校验。历史
2020-12 Schema 中可无损转换的 `$defs` 和本地 `$ref` 会在 agent runtime 边界改写为
`definitions`；`unevaluatedProperties`、`prefixItems` 等无法无损转换的关键字必须明确拒绝，
不得静默降级为弱校验。

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

服务启动时必须回收上一进程遗留的工作流 `running` 记录：运行转为 `failed`，正在执行的节点结束，任务租约按 `lease_origin_status` 精确释放，关联的 `agent_runs` 也必须结束。调度器启动前从持久化的自动运行记录恢复当前窗口的计数，不能依赖进程内存；前端手动或批量运行进入终态后必须刷新工作流概览聚合状态。页面批量队列本身仍是前端内存队列，不提供服务重启后的续跑语义。

`agent` 与 `output` 节点的 `timeout_seconds` 是 1–86400 秒的运行控制参数，默认 600 秒；它不属于节点产物语义，单独调整超时不得使节点或下游失去复用资格。工作流名称、描述、节点显示名称和 Output 节点配置标题是展示字段，调整它们同样不得改变增量复用或触发重跑。版本判定走双口径：执行语义口径（`content_hash`，剥离 name/description/timeout/title 等字段）喂重跑与 stale 判定，单独调整这些字段时必须稳定不变；版本历史口径（`version_hash`，含这些字段）喂版本号递增与 diff，会随之变更并产生新 `revision_no`——这是版本记录而非增量版本，版本号递增本身不触发重跑。

保存工作流支持 `task_refresh_policy=auto|defer`。`auto` 保持现有行为，将受影响的最新完成任务标记为 `stale`；`defer` 只保存 revision，不改变任务队列。延期后的完成任务通过派生字段 `needs_refresh` 标识，后续由显式任务刷新操作转入 `stale`。不要通过修改 `content_hash` 隐藏执行语义变化；运行中的任务仍受运行快照与版本不一致护栏保护。

工作流编辑使用与版本号分离的 `edit_version` 乐观锁。前端进入编辑路由时必须重新读取详情，保存时传回 `expected_edit_version`；版本不一致返回 `409`，不得以旧标签页内容覆盖当前定义。不要用 `revision_no` 替代该并发令牌：展示字段修改会通过 `version_hash` 递增 `revision_no`，但那是版本记录口径，`edit_version` 才是并发保护令牌，两者职责不同。

除工作流外，可编辑管理资源统一通过 `agent_bridge.core.editing` 生成不透明 `edit_token`。读取接口返回令牌，写接口接受可选的 `expected_edit_token`；新建表单用空字符串表示“读取时资源不存在”，旧客户端未传令牌时保持兼容。令牌快照只包含该编辑域的可写字段，必须包含被脱敏的秘密原值以发现其他页面对秘密的修改，但不得把原值返回客户端。进入独立编辑页或打开列表编辑弹窗时应重新读取服务端详情；冲突统一抛出 `ConflictError`（HTTP `409`），不得静默覆盖。

增量运行只复用无副作用的节点结果；`get_task` 负责把队列任务租约绑定到当前 run，因此每次都必须重新执行，但只有任务业务输入相对基线发生变化时才使下游结果失效。带条件入边的节点及其下游在预览中只能作为“待条件结果”的候选；`WorkflowDagExecutor` 在条件实际命中、节点 ready 时再决定是否复用，未命中的分支不得使汇合后的节点失效。启用受管 Profile MCP 本身不使节点失去复用资格：节点配置与后端资源指纹稳定时复用既有结果；需要刷新外部读取时使用 `force_full`。按任务启动的 run 会先精确租赁已选任务，`get_task` 只返回该租约，不得改领其他队列任务。`workflow_set_task`、`workflow_run_log` 等 Agent 运行期间的辅助调用不因增量复用规则被强制重跑。

`task_key` 是任务的唯一身份，`task_version` 是版本演进线。同 `task_key` 出现新 `task_version` 时，尚未运行或无需继续重试的旧版本（`pending`/`stale`/`failed`/`abandoned`）由导入入口统一标为 `superseded`，调度器永不领取；`running` 的旧版本让它跑完，`completed` 的旧版本保留为历史产物。跨版本禁止增量复用：新版本首次执行因无同 `task_version` 基线而全量运行，`select_baseline` 的 `task_version` 硬等值匹配不得放宽。`workflow_set_task`（MCP 单发/批量）与 Excel 导入确认共用 `_apply_workflow_tasks`，取代行为一致；存量“同 task_key 多 version 排队”的数据由启动迁移 `backfill_workflow_tasks_superseded` 自动回填。

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
