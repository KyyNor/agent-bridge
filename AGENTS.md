# Agent Bridge 开发约定

本文件适用于整个仓库。更完整的架构背景见 `CLAUDE.md`；命令与使用方式见 `README.md`。

## 开发原则

1. 应用层只编排领域服务和 adapter，不根据具体后端类型写 `isinstance` 或多段 `if/elif`。
2. 同一能力的不同实现必须通过 Protocol、adapter 和 registry 接入。新增实现时优先注册，不修改中心分发链。
3. 不通过 monkeypatch、动态 `setattr` 或修改任意异常对象传递业务状态。错误上下文使用明确类型，并通过 `raise ... from exc` 保留原因。
4. 重复出现的进程生命周期、网络传输、JSONL 解析、过滤和状态管理应提取为共享组件；保留各协议真实差异。
5. `AgentBridgeService` 是装配与兼容门面，不承载大段具体领域逻辑。新增业务进入对应领域 service。
6. Python 和 Vue 单文件应保持单一职责。文件持续增长到约 800 行时必须评估拆分；超过 1200 行原则上不再增加新职责。

## 后端与标识约束

- 文档知识后端实现 `BackendAdapter`；可选能力通过独立 runtime-checkable Protocol 表达。
- 能力来源实现 `CapabilitySourceAdapter` 并注册到来源 registry。
- Coding Agent 的配置暂时要求 `slug == type`。在支持实例级差异配置之前，不创建同一 type 的多个无差异 slug。
- Coding Agent 后端可选 `effort` 思考力度字段，留空表示不传参、保持各 CLI 默认。各实现通过类属性 `supported_efforts` 声明自己的取值集合（Claude：low/medium/high/xhigh/max；Codex：minimal~xhigh；Pi：off~xhigh），由 `create_coding_agent_registry` 统一校验；OpenCode 的 variant 名由 provider 决定，`supported_efforts` 为 `None` 时不做枚举校验。保存接口必须先构建 registry 校验再写 `server.toml`，禁止把非法取值落盘导致服务重启失败。
- OpenCode 使用由 Agent Bridge 按 run 管理的 server HTTP 模式；server 启动、就绪探测、SSE framing、请求和回收集中在 `opencode_server.py`，adapter 只负责 OpenCode V1 API 事件与统一事件模型的映射，便于未来替换 V2 client。
- DSH 作为 Coding Agent 后端走标准 Agent Client Protocol：每次 run 启动独立、短生命周期的 `dsh --profile acp --patch <模型路由>` 进程（JSON-RPC over stdio），不解析 TUI 文本、不与 DSH Web Runtime 共享进程。模型路由必须经 run 目录中的 `--patch` 覆盖 `dsh-acp` 行（acp profile shipped 行钉死 deepseek-official，用户 settings.yaml 的默认模型不会覆盖它）；组级模型接入与 Linux 身份由 `dsh/agent_runtime.py` 统一解析并幂等注入用户级 `settings.yaml`，后台 run 不依赖 Web Runtime。全新 DSH_HOME 必须预创建标准子目录（sessions/storages/change-ledger/task-board），否则 provider 注册失败；DSH 侧 LLM provider 注册可能晚于 ACP 应答开始，adapter 对 `no adapter registered` 做退避重试。`.mcp.json` 在 adapter 内转换为 ACP `session/new` 的 stdio/HTTP MCP 声明（Profile 能力平面照常经 MetaMCP 网关），不得把 Profile 规则复制进 DSH。权限请求按无人值守语义自动放行（优先 allow_* 选项，沙箱仍由 DSH 执行）；取消走 `session/cancel` + stdin EOF 优雅退出。effort 为空集合（托管供应商路由不暴露 reasoning effort，配置即拒绝）；原始 ACP update 全量进 `messages.jsonl` 供诊断。
- Mock 后端只能由显式 `type = "mock"` 使用，不得作为未知或缺失配置的静默回退。
- CodeGraph CLI/MCP 是同一正式后端的两种调用通道，统一通过 `CodeGraphBackend` 使用。
- 禁止为 CodeGraph 恢复 SQLite 隐式文本索引降级；后端缺失或索引未就绪必须明确失败。
- 仓库文件读取和文件列表基于 Git 镜像，不应依赖 CodeGraph 后端。
- 工作流产物的文本检索使用 jieba 预分词与 SQLite FTS5；结构化范围条件（Profile、current/history、标签、格式和路径前缀）必须继续在 `workflow_artifacts` 主表上过滤。中文查询按分词后的关键词组合匹配，长度至少 3 的 ASCII 标识符支持前缀匹配，短 token 和带分隔符的路径/标识符保持字面匹配。
- `artifacts_search` 的结果使用公共 `DiskCacheStore` 磁盘缓存，`artifact_search_cache_ttl_hours` 默认 8 小时并可由系统配置页面调整；当前不要求因新产物写入主动失效缓存。
- 平台概览通过 `DashboardOverviewService` 聚合，使用公共 `DiskCacheStore` 缓存 4 小时；缓存键必须隔离用户、所属小组、可见资源范围和日期区间，页面手动刷新必须重建当前缓存。
- 工作流 `agent` 与 `output` 节点的 `timeout_seconds` 仅为运行控制参数；工作流名称/描述、节点显示名称和 Output 配置标题均为展示字段。调整这些字段不能改变增量复用判定或触发下游重跑。版本判定走双口径：执行语义口径（`content_hash`，剥离 name/description/timeout/title）喂重跑与 stale 判定，必须稳定不变；版本历史口径（`version_hash`，含这些字段）喂版本号递增与 diff，会随之变更并产生新 `revision_no`，但版本号递增本身不触发重跑。不要用 `revision_no` 作为并发/复用令牌，并发控制用独立的 `edit_version`。
- 工作流保存支持 `task_refresh_policy=auto|defer`：`auto` 将受影响的最新完成任务标记为 `stale`，`defer` 只创建新 revision、不改变任务队列；延期结果保持 `completed` 并通过派生的 `needs_refresh` 标记，后续必须经显式刷新操作才进入增量队列。不得通过伪造 `content_hash` 隐藏执行语义变化，运行中的任务仍受版本快照护栏保护。
- `task_key` 是工作流任务的唯一身份，`task_version` 是版本演进线。同 `task_key` 出现新 `task_version` 时，尚未运行或无需继续重试的旧版本（`pending`/`stale`/`failed`/`abandoned`）必须由下发入口（`_apply_workflow_tasks`，`workflow_set_task` 单发/批量与 Excel 导入共用）统一标为 `superseded`，调度器永不领取；`running` 旧版本让它跑完、`completed` 旧版本保留为历史产物。跨版本禁止增量复用：`select_baseline` 的 `task_version` 硬等值不得放宽，新版本首次执行必须全量。存量“同 task_key 多 version 排队”数据由 `backfill_workflow_tasks_superseded` 启动迁移幂等回填。
- Coding Agent 的结构化输出 JSON Schema 统一按 Draft 07 传递和校验；历史 2020-12 的 `$defs` 与本地 `$ref` 仅可无损改写为 `definitions`，其余无法等价转换的专属关键字必须明确拒绝，不得静默弱化校验。
- `profile use` 写入的 Agent Bridge HTTP MCP 配置使用 `timeout: 300000`（毫秒），将 Claude Code 远程 MCP 工具调用上限固定为 300 秒；该值与 Weknora 后端 HTTP 超时分开管理。
- `profile use` 安装 Claude Code `SessionEnd` 配置同步 Hook；同步对实际生成的 Agent Bridge MCP、托管 Hook 和说明块计算 hash，仅在结果变化时更新，不引入 schema/version 文件，并保留用户自有配置。
- `profile unuse` 必须同时扫描当前项目和 user scope，交互选择卸载目标；卸载只删除对应范围的 Agent Bridge MCP、托管 Hook 和说明块，必须保留用户自有配置。
- 工作流服务启动必须回收上一进程遗留的 `running` 运行、节点和任务租约，并恢复当前调度窗口的持久化自动运行计数；手动/批量运行终态后必须刷新工作流概览聚合，前端批量队列不承诺服务重启后续跑。
- DSH Web Runtime 按 `agent_bridge.dsh` 领域包演进：进程生命周期沿用 claude-mem worker 的状态文件与 SIGTERM→SIGKILL 升级语义，uid/gid 切换只允许经 `dsh/launcher.py`（`Popen(user=, group=)`，禁止 `preexec_fn`）；一次性命令（如插件安装）同样经 launcher 的 `run_once` 降权执行。用户级 DSH 配置目录固定为 Linux 用户 home 下 `.config/dsh/<business-user>/`，不得迁入 `AGENT_BRIDGE_ROOT/data`；组级 `api_key` 按敏感配置模式保存与脱敏（只返回 `api_key_set`）。每个业务用户至多一个实例，动态端口不通过用户态接口暴露。
- DSH 插件首装：名单内置于包内 `src/agent_bridge/dsh/dsh-plugins.txt`（每行一个 spec、必须钉住精确版本，`#` 注释；随版本发布，护栏测试见 `test_packaged_plugin_list_is_pinned`），**在 web 进程启动前**以目标 Linux 用户身份同步执行 `dsh plugin --profile web add <spec>` 装完（运行中的 DSH 不热加载 profile 变更，先启动后安装即首访竞态），成功条目记入 `<DSH_HOME>/agent-bridge-plugins.txt`（0600）。实际只在首次初始化（及版本名单新增条目）执行；失败/超出总预算的条目不阻塞启动、不写 marker、下次启动自动重试；名单移除条目不卸载已装插件；安装环境不得携带组级 API Key。安装前必须在 profile `pnpm-workspace.yaml` 显式声明不构建原生依赖（`allowBuilds` map，`BLOCKED_BUILDS` 当前为 node-pty；pnpm 10.33 正式键，等价于 `ignoredBuiltDependencies`，并清理历史放行条目）：pnpm 10 默认拦截依赖构建并留待定告警，node-pty 无 Linux 预编译、构建需工具链与 Node headers，默认跳过保证整份名单装得上，依赖它的能力由插件自行降级；内网需要终端能力时再把该依赖移出名单并预置 python3/make/g++ 与 Node headers。
- DSH 的模型接入必须走其原生配置而非自定义环境变量：公共 Base URL（留空回落系统「公共模型配置」）与可用模型、组级默认模型合并写入用户 `DSH_HOME/settings.yaml`（`llm-pi-ai.providers.agent-bridge` + `agent-default-model`），API Key 只经环境变量传递、不落盘；写入必须保留用户其余 settings 且内容未变化时不触盘。启动命令模板只支持 `{port}` 与 `{patch}` 占位符，默认带 `--no-open`；就绪后必须从 `dsh web: …?token=…` 横幅捕获鉴权入口，供 Workspace 代理完成首次 token→Cookie 换取。`dsh_group_configs`/`dsh_runtime_config` 的分层迁移必须先回填再删列：旧组级 `base_url`/`available_models_json` 合并进全局行（只填全局为空的字段），不得静默丢弃用户已保存的接入配置。
- DSH Workspace 代理目标只能来自当前登录业务用户已登记的 runtime 状态，禁止接受 URL 参数指定端口；HTTP/WS 转发复用 `dashboard_proxy` 骨架，新增请求头/响应头/查询串改写走 hook 而不是复制转发实现。DSH 前端以 `<base href="/">` 用根绝对路径请求资源与 `/api/**`（含 WS `/api/remote.mux`），这些前缀外请求由 `workspace_escape_path` 按 Referer（HTTP）与同源 Origin（WebSocket）认领，`RESERVED_PATH_PREFIXES`（`/api/v1`、`/agent-bridge`、`/static/capabilities`、`/dashboard`、`/memory-dashboard`、`/health`）永不参与；工作台内嵌套文档（插件 studio 等页面本身也服务在根路径下）的子资源请求按同 authority 且非保留路径的 Referer 一并认领，外部站点 Referer 因 authority 不同必须排除。上游 Origin 必须改写为目标 origin，DSH 会话 Cookie 必须保持 `Path=/` 原样透传，不得收窄到前缀；部分 DSH 插件（如 task-board）的控制面路由要求请求携带浏览器信号（`Sec-Fetch-Site: same-origin` 或 `Origin` 之一，缺失即 403），代理作为已认证入口必须在两种信号都缺席时补齐 `Sec-Fetch-Site: same-origin`，浏览器已带值的（含显式 `cross-site`）一律原样透传、不得改写。首访鉴权在服务端完成：每次根导航都用 `?token=` 换取新鲜会话 Cookie 并吞掉 DSH 的 303，token 不得出现在浏览器地址栏，交换请求也不带任何浏览器 Cookie。DSH 的启动 token 绑定进程内 owner、随 Connection 重载静默轮换并重印 `dsh web:` 横幅（签名密钥持久化在 DSH_HOME，会话 Cookie 可跨进程重启存活），因此交换失败必须经 `refresh_workspace_auth` 重扫日志取最新横幅重试一次；仍失败则不带 token 直接代理原请求（既有会话有效则 200，否则如实 401），禁止把带 token 查询的 303 回放给浏览器——DSH 对“已认证 + token 查询”只回去掉查询串的 303 且不下发 Set-Cookie，回放即无限重定向。能力平面注入经 `dsh/workspace.py` 的短期 capability（用户唯一、TTL 受限）与 DSH loader patch 覆盖文件（`<DSH_HOME>/agent-bridge-mcp.patch.yml`，0600，经 `{patch}` → `--patch` 生效）；`/mcp` 校验后以业务用户身份沿用 Profile 权限体系；覆盖文件只写 DSH 配置目录，不进 `AGENT_BRIDGE_ROOT/data`。
- `workflow_runs` 的任务维度索引 `idx_workflow_runs_task` 与 `tool_call_logs` 的时间窗聚合覆盖索引 `idx_tool_call_logs_stats` 是任务队列和概览聚合的查询护栏：两表行内均内嵌大 JSON 字段，缺索引的任务维度关联或时间窗聚合会退化为逐行回表并随数据量线性放大。修改这些查询必须同步评估索引，不得随意删除；护栏测试见 `tests/test_storage_query_indexes.py`。

## 时间处理规范

- 禁止新增 `datetime.utcnow()`、naive UTC 或散落的 `datetime.now(...).isoformat()`。
- 统一使用 `agent_bridge.core.timeutil`：获取当前时间用 `utc_now()`，持久化用 `utc_iso()`，解析历史时间用 `parse_utc()`。
- 内部时间对象必须带 UTC 时区；对外序列化格式由公共 helper 固定，不在调用点手写 `replace("+00:00", "Z")`。
- 涉及过期、租约、缓存和耗时的逻辑必须有边界测试；持续时间使用 monotonic 时钟。

## 日志、文档与语言

- 使用 `logging.getLogger(__name__)` 和 `%s` 惰性参数；不要直接引入 loguru。
- 生命周期、降级、拒绝、失败等核心日志必须使用清晰中文，并包含可定位的实体 key、阶段、状态和耗时。
- 不得静默吞掉核心异常。允许容错的边界也要记录 warning 或持久化错误状态。
- 注释、docstring、README、CLAUDE、AGENTS 和用户可见错误优先中文；协议字段、标识符和外部产品名保留英文。
- 代码行为、CLI、配置或目录变化时，同一提交同步更新 README/CLAUDE/AGENTS 和示例。
- Agent 运行的工具输入/输出、模型推理详情与阶段耗时统一进入规范化事件流；短 payload 可内联，长 payload 只能通过运行目录的安全相对引用按需读取。时间轴可以把同一工具调用的开始/结果和同一文本流的增量合并展示，但不得丢失原始事件；payload 通过“查看”弹窗查看，Markdown 渲染，JSON 先格式化，JSON/HTML/Python/JavaScript 使用语法高亮。

## 前端约定

- 页面复用的判断、格式化、状态加载进入 `src/lib` 或 `src/composables`；页面不得再定义同名但语义相反的私有实现。
- 大视图按功能区域提取组件，不在单个 `.vue` 中继续累积列表、编辑、运行、详情等多种职责。
- 设计稿和原型不得放入 Vite `public/` 生产目录。测试应验证真实组件或行为。
- 保持 Chrome 90 兼容约束和现有语义颜色令牌体系。

## 验证与提交

常规修改至少运行相关领域测试。跨领域或发布相关修改运行：

```bash
./scripts/test.sh full -q
```

前端修改至少运行：

```bash
cd frontend/capabilities
npm run check
```

发布构建必须从干净依赖开始构建前端，再构建并安装 wheel 做 smoke test。提交只包含当前任务文件，不覆盖或清理其他人的未提交修改。
