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
- 管理服务端 Python 脚本、Skill、同步调度和插件更新。
- 在 `/admin/capabilities` 提供管理后台，在 `/mcp` 提供 MetaMCP 入口。

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

短命令 `agb` 与 `agent-bridge` 等价。当前 CLI 根命令只有 `server`、`profile`、`memory`；知识库、工作流、Agent 和系统配置通过管理后台或 HTTP API 管理。

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
- 项目 `CLAUDE.md` 或用户 `~/.claude/CLAUDE.md` 中由 Agent Bridge 管理的 profile 指针块。

它不会把完整 profile 正文复制进 CLAUDE.md。动态 profile 与 memory 上下文由服务端维护，并在会话 hook 中刷新。

常用 Profile 命令：

```bash
uv run agent-bridge profile list
uv run agent-bridge profile show safe-readonly
uv run agent-bridge profile config --scope project
uv run agent-bridge profile pins refresh safe-readonly
```

需要帮助较弱模型选择 Wiki、CodeGraph、Memory 或工作流产出物时，可以手工配置
Claude Code `asyncRewake` 全量检索探测 Hook。它不会由 `profile use` 自动安装，
也不会改变现有 Profile 行为。配置方式和独立 API 契约见
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

服务配置位于 `config/server.toml`，数据库和运行数据位于 `data/`。日志默认写入 `logs/agent-bridge.log`。

Agent 运行记录采用 SQLite 与运行目录混合存储：`data/wiki.db` 保存 `agent_runs` 摘要、终态结果和规范化事件；每次运行的 `messages.jsonl`、实时 `events.jsonl` 和较大的工具输入/输出保存在 `run/agent-runs/<run-key>/` 下。事件时间轴展示短 payload，较大的 payload 通过 `/agent-runs/{run_key}/payload?ref=...` 按需读取；工具输入、输出和模型详情都提供“查看”入口，并在弹窗中按 Markdown 渲染，JSON 先格式化再展示，HTML、Python、JavaScript 使用语法高亮。

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

## 工作流自动调度并发

系统配置页面可分别设置自动调度的全局并发数（默认 4）和单个工作流并发数（默认 2）。调度器按工作流轮转分配运行槽位，既不会超过全局上限，也不会让单个工作流占用超过自身上限的槽位。`workflow_max_runs` 仍表示每个调度窗口内的自动运行次数上限，与并发配置独立。
