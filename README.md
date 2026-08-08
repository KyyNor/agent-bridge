# Agent Bridge

Agent Bridge 是面向内部可信环境的 Agent 能力与知识管理平台。它把 MCP、OpenAPI、内置知识能力、Coding Agent、工作流、脚本和记忆统一到一个 FastAPI 服务，并提供 Vue 3 管理后台。

## 主要能力

- 注册 MCP 与 OpenAPI 服务，同步工具定义并统一检索、执行和审计。
- 通过 Profile 管理来源级与资源级访问策略，并将常用工具提升为 pinned tools。
- 管理文档知识库、CodeGraph 代码知识和 claude-mem 记忆。
- 支持 Claude、Codex、OpenCode、Pi 等 Coding Agent 后端。
- OpenCode 由 Agent Bridge 按 run 启动并回收本机 server，通过 HTTP API 执行会话；当前使用
  `prompt_async` + `/event` SSE 实时接收文本、阶段和工具事件，OpenCode V1 事件映射与 server
  生命周期分开，便于未来替换 V2 client；运行时间轴会合并工具调用/结果和文本增量，同时保留
  原始事件用于诊断。
- 通过结构化 DAG 编排 Agent、脚本、任务领取和 Markdown/HTML 产物。
- 工作流产物的标题、摘要、路径和正文使用 jieba 预分词与 SQLite FTS5 检索；长度至少 3 的 ASCII 标识符支持前缀匹配，结构化权限与版本过滤仍由 SQLite 普通条件处理。
- `artifacts_search` 使用 DiskCache 缓存检索结果，默认保留 8 小时；缓存时长可在「系统管理」页面修改，保存后立即按新配置生效。当前版本不主动因新产物写入而清理缓存。
- 管理服务端 Python 脚本、Skill、同步调度和插件更新。
- 在 `/admin/capabilities` 提供管理后台，在 `/mcp` 提供 MetaMCP 入口。

「系统管理 → 顶层 MCP 工具」可查看所有可配置的 `/mcp` 顶层工具（`search` 和 `execute` 两个固定入口除外），并临时关闭其中任意工具。关闭会立即使该工具不再出现在 MCP tools/list 和能力目录中，且通过通用 `execute` 调用其对应内置能力同样会被拒绝；重新启用即可恢复。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm（构建管理后台时需要）
- CodeGraph CLI（启用代码图索引、查询和 Explore 时必须安装）

安装后端与前端依赖：

```bash
uv sync
cd frontend/capabilities
npm ci
```

## 启动

```bash
uv run agent-bridge server start
uv run agent-bridge server init
uv run agent-bridge server status
```

然后访问：

- 管理后台：<http://127.0.0.1:8765/admin/capabilities>
- 健康检查：<http://127.0.0.1:8765/health>
- MetaMCP：<http://127.0.0.1:8765/mcp>

停止服务：

```bash
uv run agent-bridge server stop
```

短命令 `agb` 与 `agent-bridge` 等价。当前 CLI 根命令只有 `server`、`profile`、`memory`；知识库、工作流、Agent 和系统管理通过管理后台或 HTTP API 管理。

## 模型评估运行时

「系统管理 → 模型评估」只支持本地 Docker，不会向 Agent Bridge 主 Python 环境安装 OpenCompass。评估按五个能力维度组织：通用知识（C-Eval、MMLU-Pro）、数学（GSM8K）、指令遵循（IFEval）、代码（HumanEval、MBPP）和 Agent（SWE-bench Lite）。页面可设置“每个数据集最多题数”（默认 64、范围 1–1000），并选择固定前 N 条或带 seed 的随机抽样；所有勾选数据集按相同上限执行，SWE-bench 中对应最多任务数。

部署机需要预先构建或导入两份镜像。OpenCompass、HumanEval 与 MBPP 数据在构建时打入镜像，运行时不下载、不挂载 OpenCompass cache：

```bash
docker build -t agent-bridge-opencompass-runner:latest docker/model-evaluation/opencompass
docker build -t agent-bridge-agent-worker:latest docker/model-evaluation/agent-worker

export AGENT_BRIDGE_EVAL_OPENCOMPASS_IMAGE=agent-bridge-opencompass-runner:latest
export AGENT_BRIDGE_EVAL_AGENT_WORKER_IMAGE=agent-bridge-agent-worker:latest
```

镜像构建前需按 [docker/model-evaluation/README.md](docker/model-evaluation/README.md) 放入固定版本的 OpenCompass、HumanEval 与 MBPP 数据。SWE-bench manifest 默认从 `AGENT_BRIDGE_ROOT/data/model-evaluation/swebench-manifest.json` 以只读方式挂入 `agent-worker`；也可通过 `AGENT_BRIDGE_EVAL_SWEBENCH_MANIFEST` 指定宿主机绝对路径。修改 manifest 无需重编 worker 镜像。各 task 对应的 testbed 镜像仍须提前导入本机 Docker。Docker daemon 或任一指定镜像缺失时，模型评估功能会直接显示为不可用，不创建任务。

普通题集在一次性 `opencompass-runner` 容器中执行；HumanEval/MBPP 的每个 case 会启动无网络、无 API Key 的独立代码沙箱；SWE-bench 按任务启动独立 testbed。API Key 只作为容器运行时环境变量传递，不保存到 SQLite、运行请求文件或日志。

若公共模型配置的 Base URL 指向宿主机的 `localhost`，容器默认会改用 `host.docker.internal`；Linux 部署可通过 `AGENT_BRIDGE_EVAL_DOCKER_HOST` 指向可从容器访问的宿主机地址或网关。没有可访问的 Docker 环境或指定镜像时，不存在 venv/CLI 后备，评估功能直接不可用。

## Profile 接入 Claude Code

先在管理后台或 API 创建 Profile，再执行：

```bash
uv run agent-bridge profile use safe-readonly \
  --scope project \
  --url http://127.0.0.1:8765/mcp
```

`profile use` 会写入或更新：

- 项目级 `.mcp.json`（`scope=project`）或用户级 `~/.mcp.json`；
- Claude Code hooks 配置；
- 项目 `CLAUDE.md` 或用户 `~/.claude/CLAUDE.md` 中由 Agent Bridge 管理的
  `<system-reminder>` 语义说明块。

写入的 `agent-bridge` MCP server 默认设置 300 秒工具调用超时（`timeout: 300000`，单位为毫秒），用于覆盖 Claude Code 远程 HTTP MCP 的短请求超时。AgentService 为受管 Agent 生成的临时 MCP 配置也使用同一设置。

`profile use` 还会安装一个 Claude Code `SessionEnd` Hook。每次会话结束时，它会对 Agent Bridge 实际管理的 MCP、Hook 和 `CLAUDE.md` 说明块计算配置 hash；只有生成结果变化时才更新，用户自己的 MCP server、Hook 和文档内容会保留。代码升级后无需手动修改版本号；首次接入某个 project 或 user scope 仍需执行一次 `profile use` 安装该 Hook。

执行 `profile unuse` 会列出当前项目和用户级已接入的 Profile，交互选择一个后卸载对应范围的 Agent Bridge MCP、托管 Hook 和说明块；用户自己的 MCP server、Hook 与文档内容会保留。非交互场景可使用 `--scope project|user --yes`。

它不会把完整 profile 正文或绝对文件路径复制进 CLAUDE.md。动态 profile 与
memory 上下文由服务端维护，并通过 `SessionStart` Hook 注入。

`profile use` 安装的全量检索探测 Hook 会在 `UserPromptSubmit` 同步执行：它在本轮等待至多 20 秒，将命中的资源提示作为当前轮 `additionalContext` 返回；没有命中或失败时不影响正常对话。

常用 Profile 命令：

```bash
uv run agent-bridge profile list
uv run agent-bridge profile show safe-readonly
uv run agent-bridge profile config --scope project
uv run agent-bridge profile sync safe-readonly --scope project
uv run agent-bridge profile unuse
uv run agent-bridge profile pins refresh safe-readonly
```

`profile use` 会自动安装 Claude Code 普通 `async` 全量检索探测 Hook。CLI 只转发 Claude
Code 的原始 Hook payload；服务端通过标准 `full-probe` Hook 使用系统管理的模型生成 0–8
个业务检索短句，并按 Profile/session 结合最近 3 轮历史去重（最多缓存 12 轮、30 天滑动
TTL），仅探测当前 Profile 的工作流产出物。模型未配置或调用失败时 Hook 保持
静默，并将原始 prompt 与完整 Hook 请求/响应写入通用审计日志。监控页仅对
`codegraph_explore`、`session-start` 和 `full-probe` 的限定 Markdown 字段提供预览，其余
日志仍通过 JSON 查看完整载荷。工作方式和独立 API 契约见
[Claude Code 全量检索探测 Hook](docs/integrations/retrieval-probe-hook/README.md)。

## 测试与质量检查

推荐从仓库根目录执行：

```bash
./scripts/test.sh fast -q
./scripts/test.sh full -q
# 需要真实外部服务/CLI/本地进程时再运行：
./scripts/test.sh all -q
```

- `fast`：不运行 e2e、真实 CLI、进程和外部知识后端测试；前端运行单元测试。
- `full`：运行完整的自包含后端测试；前端运行测试、类型检查和生产构建。
- `all`：清除默认 marker 排除，运行包括真实 CLI、进程和外部后端在内的全部测试。
- `integration`：运行需要真实 RagFlow/Weknora 的集成测试。

`full` 和 `all` 都会先运行 Ruff 基础静态检查。CI 在全新 checkout 中执行 `full`，随后构建 wheel、安装到隔离环境并验证管理后台静态文件已经打包。

前端单独开发：

```bash
cd frontend/capabilities
npm run dev
npm test
npm run typecheck
npm run build
npm run check
```

Vite 产物写入 `src/agent_bridge/static/capabilities/`。该目录不提交到 Git；发布 wheel 前必须先执行 `npm ci && npm run build`。

CodeGraph 不提供 SQLite 文本索引降级。CLI 缺失、索引未建立或查询失败时，API 会明确返回后端不可用；安装 CLI 后需要重新同步受影响仓库。

## 数据与配置

默认数据根目录是 `/root/agent-bridge`，可通过 `AGENT_BRIDGE_ROOT` 覆盖：

```text
/root/agent-bridge/
├── config/
├── data/
├── logs/
├── repos/
└── run/
```

服务配置位于 `config/server.toml`，数据库和运行数据位于 `data/`。主业务库为 `data/agent-bridge.db`，高频工具调用与 Agent 运行审计独立保存到 `data/agent-bridge-logs.db`；升级时会安全复制历史 `wiki.db` 到新的主库文件名。日志默认写入 `logs/agent-bridge.log`。

业务台账保存于 `data/agent-bridge-ledgers.db`。每个台账最多 100 个字段、200,000 行记录；服务启动后异步构建内存快照，管理读写和后续的受控查询均使用同一份完整快照。

业务台账的 Excel 导入窗口可下载当前字段定义生成的空白 `.xlsx` 模板；模板只包含字段标识表头，不会携带台账中的已有数据。

新建或编辑业务台账定义时可使用“AI 设计”：Agent 先返回可视化字段草案，管理员确认“采纳并保存”后才会写入定义；该能力不会由 Agent 直接新增、修改或删除记录。

能力平面通过 `business_ledger` 资源规则显式授权业务台账。获授权的 Agent 使用顶级 MCP 工具 `query_business_ledger` 查询；所有字段默认支持精确匹配和排序，文本字段可额外开启字面包含检索，数字、日期与日期时间字段默认支持大于、小于、大于等于、小于等于和范围筛选。排序可按多个字段依次传入；台账、字段和查询方式自动注入 Profile，上下文外的台账不可发现也不可查询。

Agent 运行记录采用 SQLite 与运行目录混合存储：`data/agent-bridge-logs.db` 保存 `agent_runs` 摘要、终态结果和规范化事件；每次运行的 `messages.jsonl`、实时 `events.jsonl` 和较大的工具输入/输出保存在 `run/agent-runs/<run-key>/` 下。运行中的时间轴通过 `GET /agent-runs/{run_key}/events/stream` SSE 接收已落盘的新事件，以 `Last-Event-ID` 断线重放；`GET /agent-runs/{run_key}/events` 仍用于初始快照和重同步。浏览器客户端使用 fetch 流以保留 `X-Agent-Bridge-User` Header，不直接使用原生 `EventSource`。若经反向代理部署，必须关闭 SSE 响应缓冲并将读取超时设置为大于最长 Agent run。事件时间轴展示短 payload，较大的 payload 通过 `/agent-runs/{run_key}/payload?ref=...` 按需读取；Agent 运行详情、工作流批量执行详情、任务展开日志和运行进度复用同一组输入提示词和执行结果卡片，每张卡片均可打开详情。Markdown 在详情中正常渲染，JSON 先格式化再展示，HTML、Python、JavaScript 使用语法高亮；工具输入、输出和模型详情同样提供“查看”入口。

工作流 Agent 的 JSON 输出 Schema 按 JSON Schema Draft 07 校验和传递给 Coding Agent。已有
Draft 2020-12 Schema 中的 `$defs` 及其本地 `$ref` 会自动转换为 Draft 07 的 `definitions`；
无法无损转换的 2020-12 专属关键字会在工作流校验阶段被拒绝。

工作流的 `agent` 与 `output` 节点可分别设置 `timeout_seconds`（默认 600 秒，范围 1–86400 秒）。它是运行控制参数，不改变节点处理语义；单独调整该值不会使增量运行失去既有节点结果复用资格。工作流名称、描述、节点显示名称和 Output 节点的展示标题同样不参与增量复用或重跑判定。调整这些展示/运行控制字段会产生新的版本号并在 diff 中可见（版本历史口径），但不会触发任务重跑（执行语义口径不变）。

工作流编辑页每次进入时都会重新读取最新定义。保存请求携带独立的 `edit_version` 乐观锁；如果定义已被其他页面更新，服务端返回 `409` 并保留当前页面草稿，避免旧标签页覆盖新内容。`edit_version` 只用于并发编辑保护，与增量运行使用的 `revision_no` 无关。

编辑工作流时可以选择任务刷新策略：默认“保存并安排增量刷新”（`task_refresh_policy=auto`）会将受影响的最新完成任务标记为 `stale`；“仅保存，暂不刷新”（`task_refresh_policy=defer`）只创建新 revision，不改变任务队列、不创建运行。此时任务仍显示为 `completed`，但任务列表会通过 `needs_refresh` 标记其结果来自旧执行语义；之后可以在任务页按任务或批量调用刷新操作，再安排增量运行。`content_hash` 仍按当前定义计算，不通过伪造 hash 规避版本一致性检查。

其他管理页采用统一的 `edit_token` 乐观并发协议。代码仓库、分类、知识后端、知识库默认检索配置、能力服务、Profile 配置、脚本和 Skill 在进入编辑时会读取最新详情；保存时传回 `expected_edit_token`。系统级配置页也会携带加载时取得的令牌。若另一个标签页已先保存，服务端返回 `409`，当前页面保留草稿并提示刷新，不会用历史数据覆盖新配置。令牌是服务端根据可编辑字段生成的不透明摘要，不包含或暴露 API Key、认证头等秘密。

当前部署模型是内部可信 VM：请求身份来自 `X-Agent-Bridge-User`，不提供互联网级认证。部署方必须限制监听地址、网络访问和反向代理边界。

## 目录概览

```text
src/agent_bridge/
├── api/                       # FastAPI 路由与页面入口
├── app/                       # 应用装配与兼容门面
├── capability_hub/            # 能力来源、治理、MetaMCP
├── knowledge_management/      # 文档、代码与记忆知识
├── agent_runtime/             # Coding Agent 抽象与执行
├── automation/workflows/      # 结构化 DAG 工作流
├── system_config/             # 脚本、Skill、插件调度
├── server_runtime/            # uvicorn 服务进程管理
└── storage/                   # SQLite schema 与 repositories

frontend/capabilities/         # Vue 3 管理后台
examples/workflows/            # 当前格式的工作流导入示例
tests/                         # 后端测试
```

开发约定与架构不变量见 [AGENTS.md](AGENTS.md) 和 [CLAUDE.md](CLAUDE.md)。

## 工作流示例

- `examples/workflows/fine-report-analysis/workflow.json`
- `examples/workflows/hellogithub-summary/workflow.json`

示例均使用 `agent-bridge.workflow` / `format_version=1` 导入信封，并由测试校验结构化 DAG。

## 工作流增量复用

增量执行会复用配置、运行资源和历史产物均一致的已完成节点。条件分支的实际路径依赖节点输出，预览会将该分支及其下游标为“待条件结果”；运行时仅在条件命中后决定复用或重新执行，未命中的分支不会使汇合节点失效。

## 工作流任务版本演进

`task_key` 是任务的唯一身份，`task_version` 是它的版本演进线。当同一 `task_key` 出现新的 `task_version` 时：

- 尚未运行的旧版本（`pending`/`stale`）以及无需继续重试的旧版本（`failed`/`abandoned`）被标记为 `superseded`，调度器永不领取它们；
- 正在运行的旧版本（`running`）让它跑完，不被取代；
- 已成功完成（`completed`）的旧版本保留为历史产物，其产物仍按 `task_key` 聚合、按版本归档。

跨版本禁止增量复用：新版本首次执行因无同 `task_version` 的基线而全量运行（不同版本的报表内容与解析结构不同，不能复用）。`workflow_set_task`（MCP 单发/批量）与 Excel 导入确认共用同一下发入口，取代行为一致。存量库中“同 `task_key` 多个 version 都在排队”的数据会在启动迁移中自动回填为 `superseded`，每个 `task_key` 仅保留最新版本参与执行。

## 工作流自动调度并发

系统管理页面可分别设置自动调度的全局并发数（默认 4）和单个工作流并发数（默认 2）。调度器按工作流轮转分配运行槽位，既不会超过全局上限，也不会让单个工作流占用超过自身上限的槽位。`workflow_max_runs` 仍表示每个调度窗口内的自动运行次数上限，与并发配置独立。

服务重启时会将上一进程遗留的工作流运行标记为失败，关闭未完成的节点并释放精确任务租约；启动调度器前会恢复当前窗口的自动运行计数，避免概览永久显示“执行中”或在同一窗口重复调度。页面上的批量任务队列仍是前端队列，重启页面或服务后不会续跑未提交的队列项。
