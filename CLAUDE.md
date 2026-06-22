# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Agent Bridge 是一个 Python 3.11 服务，作用是**给 Agent 提供受管控的能力与知识访问**。它把异构的能力来源（外部 MCP 服务、OpenAPI/REST 服务、平台内置能力）统一到一个 MetaMCP 网关后面，配合基于 Profile 的访问治理、全量工具调用审计，并内置文档知识库（Wiki）同步与代码仓库索引（CodeGraph / Understand-Anything）两套知识子系统，外加一个定时工作流（workflow）引擎。

最终面向 Agent 的入口只有两个工具：`search`（浏览能力）和 `execute`（在治理下执行）。

## 常用命令

```bash
uv sync                              # 安装依赖
uv run pytest -v                     # 跑全部测试
uv run pytest tests/test_capability_service.py::TestName   # 跑单个测试
uv run pytest -m "not ragflow and not weknora"  # 跳过需要真实后端的集成测试

# 服务（默认监听 127.0.0.1:8765）
uv run agent-bridge server start     # 等价短命令：uv run agb
uv run agent-bridge server stop
uv run agent-bridge server status
uv run agent-bridge server init      # 初始化 SQLite 表结构

# Profile 接入 Claude Code（写本地 .mcp.json + 注入 CLAUDE.md 指针）
uv run agent-bridge profile use <profile> --scope project --url http://127.0.0.1:8765/mcp
```

前端（Vue 3，构建产物会写到 `src/agent_bridge/static/capabilities/`，该目录被 gitignore）：

```bash
cd frontend/capabilities
npm install
npm run dev       # 开发服务器，带 API 代理
npm run build     # vue-tsc 类型检查 + 生产构建
npm run typecheck
```

注意事项：
- 项目**没有配置任何 linter / formatter / type checker**（pyproject 里无 ruff/mypy/black），Python 端唯一的校验是测试。
- 服务进程通过 `uvicorn agent_bridge.api.app:create_app --factory` 启动（见 `runtime/server_process.py`），用 PID 文件管理，5 秒健康检查。
- `ragflow` / `weknora` 标记的测试需要真实的 RagFlow / Weknora 服务，本地通常用 `-m` 过滤掉。

## 部署与信任模型（务必先理解）

- **数据根目录**默认 `/root/agent-bridge`，用环境变量 `AGENT_BRIDGE_ROOT` 覆盖；其下分 `config/ data/ logs/ run/ repos/`。`AgentBridgePaths`（`core/config.py`）是这些路径的单一来源。
- 当前阶段**信任内部可信 VM**：调用方的身份完全来自 HTTP 头 `X-Agent-Bridge-User`（即 Linux 用户名），不做强认证。`default_user()` 默认 `root`，可用 `AGENT_BRIDGE_USER` 覆盖。
- `server.toml`（`config/server.toml`）里的 `admins` 集合决定谁是管理员；几乎所有写操作都以 `require_admin_user(actor, self.admins)` 开头。未显式传 `admins` 时，API 层每次请求会重新加载该文件（支持热更新 admin 列表）。
- 启动服务的那一类部署账户必须能创建并写入根目录下的各子目录。

## 架构总览

整个系统是分层 + 中心化服务装配的结构。**所有子系统的根都在 `app/service.py` 的 `AgentBridgeService.__init__`**，它一次性装配出 governance、capabilities、agents、codegraph、四个 scheduler、workflows、skills、scripts，并注册三个内置能力提供者（platform / wiki / codegraph）。FastAPI app 在 `api/app.py:create_app` 里创建，持有这个 service。

请求处理链：FastAPI 路由（`api/routes/*`）→ `AgentBridgeService` / 各子 service → `SQLiteStore`（仓库模式）。`AgentBridgeError`（`AccessDenied`/`NotFound`/`ValidationError`，定义于 `core/domain.py`）被一个全局异常处理器统一转成 JSON 响应，所以业务层一律抛领域异常而非手写 HTTP 状态码。

### 存储层（`storage/`）

- `SQLiteStore`（`storage/sqlite.py`）是单一门面，按领域拆成 7 个 **repository**（`storage/repositories/*`：knowledge / capabilities / governance / codegraph / workflows / scripts / agent_runs）。每个 repo 持有 `db_path` 和一个共享的 `connect` 上下文管理器。
- 表结构在 `storage/schema.py`（约 35 张表，`CREATE TABLE IF NOT EXISTS`，`init_schema()` 幂等建表）。Wiki 后端曾用 TOML 配置，`migrate_toml_backends_to_db` 做过一次性迁移到 DB。

### 能力中心（`capability_hub/`）—— 核心子系统

这是最值得读多文件才能理解的部分。

**能力来源是三类，分发走 if/elif 探测，不是注册表**（已确认，且 `sources/*/​__init__.py` 全是空文件）。固定优先级：`builtin > openapi > mcp`。具体逻辑在 `CapabilityService.execute`（`capability_hub/service.py:571`）和 `_search_without_log`：先看 `service` key 是否在 `builtin_providers` 字典里，再看是否是已注册的 openapi 服务，否则按 MCP 处理。

- `sources/builtin/`：实现 `BuiltinCapabilityProvider` Protocol（`base.py`）。三个提供者都在 `app/service.py:86-88` **命令式注册**：`PlatformBuiltinProvider`（`load_skill` / `run_script`）、`WikiBuiltinProvider`（wiki 检索）、`CodeGraphBuiltinProvider`（`codegraph_explore`）。内置提供者**自己做资源级**（wiki KB / code repo）的策略校验。
- `sources/mcp/`：`McpHttpClient` 基于官方 MCP SDK 的 streamable HTTP，每次调用新开连接，无连接池。
- `sources/openapi/`：`parser.py` 把 OpenAPI 文档转成工具（推断 tool_type，GET/`{var}` → detail，其余 GET → search），`http_client.py` 执行时通过 `httpx` 发请求。注意执行走 `asyncio.to_thread` 包了同步 httpx。

**MetaMCP 网关（`gateway/metamcp.py`）**：在 `/mcp` 路由暴露一个 FastMCP server。**每个请求都新建一个临时的 FastMCP 实例**（无状态），用模块级 `ContextVar`（`_request_profile` / `_request_workflow_context`）携带 per-request 上下文。Profile 通过 `X-Agent-Bridge-MetaMCP-Profile` 头传入；工作流上下文通过 `X-Agent-Bridge-Workflow[-Key|-Run-Id]` 头传入，存在完整工作流上下文时才会额外注册 `workflow_get_task` / `workflow_set_task` / `workflow_run_log` 三个辅助工具。除 `search`/`execute` 外，还会把 wiki/codegraph 直连工具和 profile 的 pinned 工具提升为顶层 MCP 工具。

**治理（`governance.py`）**：`CapabilityGovernanceService` 管四件事——Profile CRUD、策略校验、工具调用日志、Pin 管理。关键规则：
- **来源级默认拒绝 + allow 后减 deny**：`filter_source_keys` 里若无 allow 规则则结果为空，deny 再从 allow 里扣。`profile_key is None` 时放行全部。
- **资源级是纯 allow-list**（无 deny），按 `ProfileResourceType`（`wiki_kb` / `code_repo`）。
- `log_tool_call` 是**唯一审计出口**：每次 `execute`/`search`（含被拒绝的）都写一行，带 `entrypoint`、`source_type`、`status`、`failure_stage`、`failure_owner`、`error_type`、`duration_ms`，失败时把 `log_id` 缝进异常信息便于关联。`invoke_logged_tool`（`service.py:144`）让工作流辅助工具/脚本运行也走同一套审计。

**Profile 文档与 Pin（`profiles/`）**：`docs.py` 把 profile 渲染成 Markdown 指导文档，并用 `@`-import 指针块幂等地注入到 `CLAUDE.md`/`AGENTS.md`（`install_profile_to_cwd`，与 `agb profile use` 的行为一致）。`pins.py` 里只有 `{overview, search, detail}` 类型工具可被 pin，pin 粒度是 `(service, tool_type)` 组而非单个工具，可按 ratio/count 自动选（基于 30 天用量，结果缓存 24h）。

### 知识管理（`knowledge_management/`）

**文档知识（`docs_knowledge/`，Wiki）**：上传 → `ArchiveStorage`（sha256 内容寻址归档）→ 建同步任务 → 由调度器扇出到一个或多个**检索后端**。所有后端实现 `BackendAdapter` Protocol（`core/domain.py`）：`mock` / `ragflow` / `weknora` / `pageindex`，由 `backends/registry.py` 按 DB 配置懒加载。**`PageIndexBackend` 比较特殊**——它自带 litellm 做检索增强问答、用 MarkItDown 把 office/csv 转 markdown 再索引，检索是 token 重叠打分而非向量搜索。

**代码知识（`code_knowledge/`，CodeGraph）**：镜像 git 仓库 → 索引成符号/文件图（`codegraph` CLI 可用时走 CLI，否则降级为内置文本索引器写进 SQLite）→ 可选地用 Understand-Anything 做知识图谱分析。**所有查询接口都有「优先 CLI/MCP、否则 SQLite 降级」的双模式**。`UnderstandAnythingClient.analyze` 不跑 CLI，而是把整个 agent 循环委托给 `AgentService.run(skills=["understand"])`，期望 agent 写出 `.understand-anything/knowledge-graph.json`。`DashboardPool` 是一个 LRU+空闲超时的 Vite dev-server 进程池，用来托管 UA dashboard。

### 工作流（`automation/workflows/`）

定时任务队列驱动：每个 workflow run 启动一个 Claude agent 执行一份 **JS manifest（`workflow.js`）**，agent 通过 MCP 辅助工具（`workflow_get_task` 等，全部经 `invoke_logged_tool` 审计）领任务、回写进度，最终产出受版本管理的 markdown 产物（artifact）。

- **`AgentService.run`（`agent_runtime/service.py:78`）是通用执行原语**——工作流 run 和 Understand-Anything 分析都走这同一个入口，靠 `agent_name`/`skills`/`prompt` 区分。它有两种模式：托管（自动建隔离工作目录、装 profile 指导和受控 `.mcp.json`）和就地（caller 自管目录，MCP 默认不接入）。
- **服务端强制工作流契约**（不在 manifest 里）：`result_parser.py` 校验 `result.json` 状态/task_key，`save_artifact` 强制路径不出 run 目录、格式仅限 markdown、profile 匹配。manifest 的头部注释即记录这些约束。
- **`WorkflowScheduler` 是自定义的**（不继承 `BaseCronScheduler`）：有每日执行时间窗（默认 22:00–07:00，可跨午夜）、并发上限、轮询 tick（60s）。最近的 `839ac44` 修复了「只在活动窗口内调度 tick」。`run_workflow_now` 可绕过窗口做即时测试运行，但仍共享内存中的 `_running` 防并发。

### 调度器（`knowledge_management/scheduler_base.py`）

`BaseCronScheduler` 是三个**cron 类**知识调度器（DocSync、CodeGraph、Understand）的公共骨架，约定子类设三个类属性（`_cron_config_key`/`_default_cron`/`_scheduler_name`），从同一个 `sync_config` 表读 cron，`refresh()` 在管理员改 cron 时被调用。注意工作流调度器**不用**这个基类。所有 cron 表达式都解析成 APScheduler 的 `CronTrigger`，解析失败即安全降级为无 job。

### 系统配置（`system_config/`）

- `scripts/`：受控的 Python 脚本（可被 `run_script` 内置工具或工作流调用）。执行时把代码 + 一个 envelope 写进隔离 run 目录，用 `subprocess` 跑 `script_runner.py`，agent-bridge 通过环境变量（`AGENT_BRIDGE_API_BASE` 等）和 envelope 把上下文喂给脚本，脚本 `main(envelope)` 返回 JSON。`runtime_support.py` 用模板渲染 runner 和 runtime helper。
- `skills/`：管理两个内置 skill prompt（`design_script` / `design_workflow`，默认值在 `system_config/skills/defaults/`），DB 可覆盖。`load_skill` 内置工具即读这里。

### API 与 CLI

- `api/routes/*` 按 domain 分模块（health / knowledge / capabilities / governance / agent_runs / builtins / workflows / script_runtime），均以工厂函数 `create_*_routes(service, actor, ...)` 形式注册到 `api/app.py`。`actor` 来自 `X-Agent-Bridge-User` 头。
- CLI（`cli/`，Typer）只是个 HTTP 客户端薄壳，通过 `AgentBridgeClient`（`client.py`）调服务端；`server` 子命令直接管进程。`profile use` 是少数会写**本地文件**的命令（`.mcp.json` + profile 文档 + CLAUDE.md 指针），它依赖服务端实时渲染的 profile 文档，这也是 TODO 里提到的「CLI 与服务端耦合」点。

## 系统级配置项

`save_sync_config`（`app/service.py`）集中管理一组同步/调度参数，写入 `knowledge_sync_config` 表，并触发所有 scheduler `refresh()`：`code_sync_cron`、`understand_cron`、`doc_sync_cron`、`workflow_start_time/stop_time`、`workflow_max_runs/max_runtime_minutes/task_rerun_days`、`mcp_timeout_seconds`（默认 150，见 `core/defaults.py`，最近 `a65223a` 才可配）、`understand_timeout_minutes`。改这些参数后必须让对应 scheduler 刷新。

## 给改动者的关键约定

1. **新增一类能力来源 ≠ 加个文件**：来源类型分发是 `service.py` 里的 if/elif，要新增来源类型得改 `execute`/`_search_without_log` 的探测分支（参考既有 memory 笔记：这是「if/elif 探测分发非注册表」）。但**新增内置提供者**很简单：实现 Protocol + 在 `app/service.py` 调 `register_builtin_provider`。
2. 写新的 service 方法时：开头 `require_admin_user`，业务失败抛 `AgentBridgeError` 子类，不要自己返回 HTTP 码。涉及工具执行就考虑是否要经 `log_tool_call` / `invoke_logged_tool` 审计。
3. Profile 策略：来源级记得「无 allow 即全拒」，资源级是纯 allow-list；改动治理逻辑后注意 `search` 的可见性过滤（root 列表 + path 探测两处）和 `execute` 的两个分支（openapi `service.py:598`、mcp `service.py:615`）都要覆盖。
4. 任何经 MCP gateway 暴露给 agent 的执行，profile / workflow 上下文都从请求头读，靠 `ContextVar` 传递——不要在 gateway 里假设有进程级单例状态。
