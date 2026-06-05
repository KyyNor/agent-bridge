# Agent Bridge 迁移实施计划

> **给 agentic worker 的要求：** 执行本计划时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`。步骤使用 checkbox（`- [ ]`）便于跟踪。

**目标：** 将 `wiki-manager` 全量改名为 `Agent Bridge`，按领域重组后端，将能力治理控制台迁移到 Vue 3 + Vite + TypeScript，并把过大的 SQLite storage 模块拆成聚焦的 repository。

**架构方向：** 先做命名迁移，确保后续所有代码都在最终包名、命令名和目录名下工作。然后建立稳定边界：FastAPI route 保持薄层，业务逻辑进入领域 service，SQLite 只保留一个 schema/migration 入口，查询逻辑按领域拆到 repository。前端先沿用现有 HTTP API 契约完成 Vue 迁移，确认构建产物能被 FastAPI 正常加载后，再移除旧静态 HTML/JS/CSS 壳。

**技术栈：** Python 3.11、Typer、FastAPI、SQLite、pytest、Vue 3、Vite、TypeScript。

---

## 已确认决策

- 产品名：`Agent Bridge`
- 发布包名：`agent-bridge`
- Python import 包名：`agent_bridge`
- CLI 命令：`agent-bridge` 和 `agb`
- 兼容策略：不保留旧 `wiki` 命令，不保留旧 `wiki_manager` import 包，不保留旧 `/root/wiki-manager` 默认目录兼容
- 前端框架：Vue 3 + Vite + TypeScript
- 后端拆分方式：领域模块 + 薄基础设施层
- storage 拆分方式：一个 SQLite schema/migration 入口 + 按领域拆分 repository

## 依赖关系

### 必须串行

1. Task 1 必须最先执行。它会改包名、CLI、环境变量、默认路径、import 路径和基础文档。
2. Task 2 必须在 Task 1 之后执行。它建立最终后端目录骨架，并把模块移动到新边界内。
3. Task 3 必须在 Task 2 之后执行。它先建立 `SQLiteStore` facade 和 repository 契约，避免 storage 大拆时影响面失控。
4. Task 4 必须在 Task 3 之后执行。它按领域逐块抽离 storage 行为。
5. Task 8 必须在 Task 4、Task 5、Task 6、Task 7 都完成之后执行。它负责旧痕迹清理、文档更新和最终验收。

### Task 2 后可以并行

- Task 5 API route 拆分：可以和 Task 4 并行，但前提是 Task 3 已经发布稳定的 storage/service import 边界。
- Task 6 CLI 拆分：可以和 Task 4、Task 5 并行，因为主要影响 Typer 命令和 client import。
- Task 7 前端 Vue 迁移：可以和 Task 4、Task 5、Task 6 并行，但要先确认 API path 不会在同阶段被大改。

### 不建议并行

- Task 1 不要和任何任务并行。全仓库包名迁移会和几乎所有文件冲突。
- `storage.py` 拆分不要多 worker 同时做。可以一个 worker 顺序拆 knowledge、capabilities、governance、codegraph。
- Vue 构建产物经 FastAPI 验证前，不要删除旧静态前端文件。

## 目标文件结构

```text
src/agent_bridge/
  __init__.py
  __main__.py
  api/
    __init__.py
    app.py
    dependencies.py
    schemas.py
    routes/
      __init__.py
      health.py
      knowledge.py
      capabilities.py
      governance.py
      builtins.py
      metamcp.py
  cli/
    __init__.py
    app.py
    server.py
    knowledge.py
    metamcp.py
  core/
    __init__.py
    config.py
    domain.py
    slug.py
  storage/
    __init__.py
    sqlite.py
    schema.py
    types.py
    repositories/
      __init__.py
      knowledge.py
      capabilities.py
      governance.py
      codegraph.py
  capabilities/
    __init__.py
    models.py
    service.py
    governance.py
    builtin.py
    builtin_wiki.py
    builtin_codegraph.py
    mcp_http_client.py
    mcp_server.py
  knowledge/
    __init__.py
    service.py
    archive.py
    backends/
      __init__.py
      registry.py
      mock.py
      ragflow.py
      weknora.py
  codegraph/
    __init__.py
    service.py
  runtime/
    __init__.py
    server_process.py
  web/
    __init__.py
    pages.py
    static/
      capabilities/
        generated build output
frontend/
  capabilities/
    package.json
    vite.config.ts
    tsconfig.json
    index.html
    src/
      main.ts
      App.vue
      api/
      components/
      views/
      stores/
      styles/
```

## Task 1：全量改名为 Agent Bridge

**执行方式：** 严格串行，必须第一个执行。

**涉及文件：**
- 修改：`pyproject.toml`
- 移动：`src/wiki_manager/` 到 `src/agent_bridge/`
- 修改：`src/agent_bridge/**/*.py` 中所有 import
- 修改：`tests/**/*.py` 中所有 import 和 monkeypatch 路径
- 修改：`README.md`
- 修改：`docs/` 下仍代表当前产品/runtime 的文档
- 修改：`uv.lock` 只允许通过 `uv lock` 或 `uv sync` 刷新

- [ ] 修改 `pyproject.toml` 项目信息：
  - `name = "agent-bridge"`
  - `description = "Agent capability and knowledge bridge for managed MCP access."`
  - scripts：
    - `agent-bridge = "agent_bridge.cli.app:main"`
    - `agb = "agent_bridge.cli.app:main"`
- [ ] 将 `src/wiki_manager` 移动为 `src/agent_bridge`。
- [ ] 将 `src/` 和 `tests/` 中的 `wiki_manager...` import 全部替换为 `agent_bridge...`。
- [ ] 修改 `src/agent_bridge/__main__.py`，让它导入 `agent_bridge.cli.app:main`。
- [ ] 修改 `src/agent_bridge/__init__.py`，让版本读取 `version("agent-bridge")`。
- [ ] 修改配置命名：
  - `DEFAULT_ROOT`：`/root/agent-bridge`
  - `ROOT_ENV_VAR`：`AGENT_BRIDGE_ROOT`
  - `USER_ENV_VAR`：`AGENT_BRIDGE_USER`
  - `WikiManagerPaths`：`AgentBridgePaths`
  - `WikiManagerClient`：`AgentBridgeClient`
  - 如果 `wiki` 表示内置 Wiki 知识源，例如 source key 为 `wiki`，可以保留。
- [ ] 修改 server process 启动目标，从 `wiki_manager.server:create_app` 改为新 API app 目标。
- [ ] 修改 FastAPI title，从 `wiki-manager` 改为 `Agent Bridge`。
- [ ] 修改 MCP server name，从 `wiki-manager` 改为 `agent-bridge`。
- [ ] 修改用户侧 HTTP header：
  - 新 header 使用 `X-Agent-Bridge-User` 和 `X-Agent-Bridge-MetaMCP-Profile`。
  - 不保留旧 `X-Wiki-*` header。
- [ ] 修改 README 命令示例：
  - `uv run wiki ...` 改成 `uv run agent-bridge ...`
  - 增加 `uv run agb ...` 短命令示例。
- [ ] 运行 `uv lock` 或 `uv sync` 刷新 lock 元数据。
- [ ] 验证：
  - `uv run pytest -v`
  - `uv run agent-bridge --version`
  - `uv run agb --version`
- [ ] 提交：`refactor: rename project to agent bridge`

## Task 2：建立后端模块边界

**执行方式：** Task 1 后严格串行执行。

**涉及文件：**
- 创建目标文件结构中的目录和 `__init__.py`
- 移动现有模块到目标领域
- 修改 `src/agent_bridge/` 和 `tests/` 中的 import

- [ ] 创建目标包目录和空 `__init__.py`。
- [ ] 移动 core 模块：
  - `config.py` 到 `core/config.py`
  - `domain.py` 到 `core/domain.py`
  - `slug.py` 到 `core/slug.py`
- [ ] 移动 runtime 模块：
  - `server_process.py` 到 `runtime/server_process.py`
- [ ] 移动 knowledge 模块：
  - `services.py` 到 `knowledge/service.py`
  - `archive.py` 到 `knowledge/archive.py`
  - `registry.py` 到 `knowledge/backends/registry.py`
  - `mock_backend.py` 到 `knowledge/backends/mock.py`
  - `ragflow_backend.py` 到 `knowledge/backends/ragflow.py`
  - `weknora_backend.py` 到 `knowledge/backends/weknora.py`
- [ ] 移动 capability 模块：
  - `capabilities.py` 到 `capabilities/models.py`
  - `capability_service.py` 到 `capabilities/service.py`
  - `capability_governance.py` 到 `capabilities/governance.py`
  - `builtin_capabilities.py` 到 `capabilities/builtin.py`
  - `builtin_wiki.py` 到 `capabilities/builtin_wiki.py`
  - `builtin_codegraph.py` 到 `capabilities/builtin_codegraph.py`
  - `mcp_http_client.py` 到 `capabilities/mcp_http_client.py`
  - `mcp_server.py` 到 `capabilities/mcp_server.py`
- [ ] 移动 CodeGraph service：
  - `codegraph_service.py` 到 `codegraph/service.py`
- [ ] 移动 CLI：
  - `cli.py` 到 `cli/app.py`
- [ ] 移动页面壳：
  - `web_pages.py` 到 `web/pages.py`
- [ ] 暂时让 `api/app.py` 承接原 `server.py` 内容，Task 5 再拆 route。
- [ ] 更新所有 import 和测试路径。
- [ ] 运行重点测试：
  - `uv run pytest tests/test_config_backends.py tests/test_services.py tests/test_capability_service.py tests/test_server.py tests/test_cli.py -v`
- [ ] 运行完整测试：
  - `uv run pytest -v`
- [ ] 提交：`refactor: organize backend by domain`

## Task 3：建立 Storage Facade 与 Repository 契约

**执行方式：** Task 2 后严格串行执行。

**涉及文件：**
- 创建：`src/agent_bridge/storage/sqlite.py`
- 创建：`src/agent_bridge/storage/schema.py`
- 创建：`src/agent_bridge/storage/types.py`
- 创建：`src/agent_bridge/storage/repositories/*.py`
- 修改：迁移后的旧 storage 模块，Task 4 完成后再删除
- 修改：storage 相关测试

- [ ] 将 row 转 dict、JSON helper 等移动到 `storage/types.py`。
- [ ] 将建表 SQL、schema migration helper 移动到 `storage/schema.py`。
- [ ] 创建 `storage/sqlite.py`，提供公开 `SQLiteStore` facade。
- [ ] 这个任务中先保持 `SQLiteStore` 现有方法名稳定，避免 service 同时大改。
- [ ] 给 `SQLiteStore` 增加 repository 属性：
  - `store.knowledge`
  - `store.capabilities`
  - `store.governance`
  - `store.codegraph`
- [ ] repository 接收数据库路径或 connection factory，不直接持有业务 service。
- [ ] Task 3 和 Task 4 期间，旧 `SQLiteStore` 方法先代理到 repository 方法。
- [ ] 增加 facade 代理测试：
  - 新建 `tests/test_storage_facade.py`
  - 验证 `SQLiteStore.init_schema()` 会初始化所有现有表。
  - 验证代表性方法既可以通过旧 facade 调用，也可以通过新 repository 属性调用。
- [ ] 运行：
  - `uv run pytest tests/test_storage.py tests/test_capability_storage.py tests/test_capability_governance_storage.py tests/test_storage_facade.py -v`
- [ ] 提交：`refactor: add sqlite storage repositories`

## Task 4：按领域拆分 Storage

**执行方式：** storage 内部串行；Task 3 合并后可和 Task 5/6/7 并行。

**涉及文件：**
- 修改：`src/agent_bridge/storage/repositories/knowledge.py`
- 修改：`src/agent_bridge/storage/repositories/capabilities.py`
- 修改：`src/agent_bridge/storage/repositories/governance.py`
- 修改：`src/agent_bridge/storage/repositories/codegraph.py`
- 修改：`src/agent_bridge/storage/sqlite.py`
- 删除：所有方法迁出后的旧单体 storage 模块

- [ ] 抽离 knowledge storage：
  - KB CRUD
  - KB membership
  - document records
  - sync state records
  - archive metadata references
  - 测试：`tests/test_storage.py`、`tests/test_services.py`、`tests/test_e2e.py`
- [ ] 抽离 capability registry storage：
  - MCP service CRUD
  - MCP tool CRUD
  - tool type updates
  - catalog/list queries
  - 测试：`tests/test_capability_storage.py`、`tests/test_capability_service.py`
- [ ] 抽离 governance storage：
  - Project Profile
  - source rule
  - resource rule
  - tool call log
  - stats aggregation
  - 测试：`tests/test_capability_governance_storage.py`、`tests/test_capability_log_analysis.py`、`tests/test_capability_stats.py`、`tests/test_profile_resources.py`
- [ ] 抽离 CodeGraph storage：
  - repository records
  - sync runs
  - indexed files
  - symbols
  - 测试：`tests/test_codegraph_service.py`、`tests/test_builtin_codegraph.py`
- [ ] 每抽完一个领域，先跑对应重点测试，再继续下一个领域。
- [ ] 所有领域测试通过后，把 service 代码迁到 repository 属性调用，只在确实提升可读性的位置迁。
- [ ] 只有当没有 service/test 依赖旧代理方法时，才删除旧 delegating methods。如果删除代理导致改动过大，保留 `SQLiteStore` facade 作为新包内公开兼容面。
- [ ] 运行：
  - `uv run pytest tests/test_storage.py tests/test_capability_storage.py tests/test_capability_governance_storage.py tests/test_codegraph_service.py -v`
  - `uv run pytest -v`
- [ ] 提交：`refactor: split sqlite storage by domain`

## Task 5：拆分 FastAPI App 和 Route

**执行方式：** Task 2 后可以执行；Task 3 后最安全。

**涉及文件：**
- 创建：`src/agent_bridge/api/app.py`
- 创建：`src/agent_bridge/api/dependencies.py`
- 创建：`src/agent_bridge/api/schemas.py`
- 创建：`src/agent_bridge/api/routes/*.py`
- 修改：导入 `create_app` 的测试

- [ ] 将当前 app module 里的 request model 移到 `api/schemas.py`。
- [ ] 将共享依赖构造移到 `api/dependencies.py`。
- [ ] 保持公开函数：
  - `create_app(paths: AgentBridgePaths | None = None, admins: set[str] | None = None) -> FastAPI`
- [ ] 拆分 route：
  - `routes/health.py`：`/health`
  - `routes/knowledge.py`：KB、docs、sync、ask/search endpoints
  - `routes/capabilities.py`：service registry、tool registry、catalog endpoints
  - `routes/governance.py`：profiles、rules、logs、stats endpoints
  - `routes/builtins.py`：Wiki 和 CodeGraph 内置资源管理 API
  - `routes/metamcp.py`：当前由 FastAPI app 承载的 HTTP MetaMCP gateway endpoints
- [ ] 在 `api/app.py` 注册 routers。
- [ ] 保持 route path 稳定。本阶段改产品名，不顺手改 REST path。
- [ ] 更新 server process 启动目标为 `agent_bridge.api.app:create_app`。
- [ ] 运行：
  - `uv run pytest tests/test_server.py tests/test_capability_api.py tests/test_metamcp_http_gateway.py tests/test_e2e.py -v`
- [ ] 提交：`refactor: split api routes`

## Task 6：按命令组拆分 Typer CLI

**执行方式：** Task 2 后可以执行；可和 Task 4/5/7 并行。

**涉及文件：**
- 创建：`src/agent_bridge/cli/app.py`
- 创建：`src/agent_bridge/cli/server.py`
- 创建：`src/agent_bridge/cli/knowledge.py`
- 创建：`src/agent_bridge/cli/metamcp.py`
- 修改：`tests/test_cli.py`

- [ ] `cli/app.py` 只保留 root Typer app 构造、全局 callback 和 `main()`。
- [ ] 将 server commands 移到 `cli/server.py`。
- [ ] 将 KB/document/sync/search/ask commands 移到 `cli/knowledge.py`。
- [ ] 将 MetaMCP profile/config commands 移到 `cli/metamcp.py`。
- [ ] 确认两个脚本都调用同一个 `main()`：
  - `agent-bridge`
  - `agb`
- [ ] 更新测试导入路径为 `agent_bridge.cli.app`。
- [ ] 运行：
  - `uv run pytest tests/test_cli.py -v`
  - `uv run agent-bridge --help`
  - `uv run agb --help`
- [ ] 提交：`refactor: split cli command groups`

## Task 7：能力治理控制台迁移到 Vue 3 + Vite + TypeScript

**执行方式：** Task 2 后可以执行；API 契约冻结后可并行。

**涉及文件：**
- 创建：`frontend/capabilities/package.json`
- 创建：`frontend/capabilities/vite.config.ts`
- 创建：`frontend/capabilities/tsconfig.json`
- 创建：`frontend/capabilities/index.html`
- 创建：`frontend/capabilities/src/**`
- 修改：`src/agent_bridge/web/pages.py`
- 修改：`pyproject.toml`，确保 Vue build artifacts 会进入 Python distribution
- 后续删除：旧 `src/agent_bridge/web/static/capabilities/app.js`
- 后续删除：旧 `src/agent_bridge/web/static/capabilities/app.css`

- [ ] 在 `frontend/capabilities` 下 scaffold Vue 3 + Vite + TypeScript。
- [ ] 添加 npm scripts：
  - `dev`
  - `build`
  - `preview`
  - `typecheck`
- [ ] 定义 TypeScript API models：
  - services
  - tools
  - catalog sources
  - profiles
  - profile rules
  - profile resource rules
  - logs and log detail
  - stats
  - built-in Wiki/CodeGraph resources
- [ ] 创建 API client module，每个现有 endpoint 对应一个 typed function。
- [ ] 拆分 views：
  - `CatalogView.vue`
  - `ServicesView.vue`
  - `ToolsView.vue`
  - `ProfilesView.vue`
  - `LogsView.vue`
  - `StatsView.vue`
  - `BuiltinsView.vue`
  - `ClaudeConfigView.vue`
- [ ] 拆分 common components：
  - `AppShell.vue`
  - `NavButton.vue`
  - `StatusBadge.vue`
  - `TagList.vue`
  - `DataTable.vue`
  - `ModalDialog.vue`
  - `JsonViewer.vue`
  - `ToastMessage.vue`
- [ ] 将 CSS 拆到 scoped component styles 和少量全局样式：
  - `src/styles/base.css`
  - `src/styles/tokens.css`
- [ ] 将旧 hash routing 逻辑替换为轻量 Vue state/router。除非视图状态明显复杂，否则不要引入 Vue Router。
- [ ] 修改 `web/pages.py`，只输出加载构建产物的最小 HTML shell。
- [ ] 构建输出路径建议使用：
  - `src/agent_bridge/web/static/capabilities/dist`
- [ ] 确认 FastAPI static mount 在本地开发和安装包模式都能正确加载 Vite assets。
- [ ] 运行前端验证：
  - `cd frontend/capabilities && npm install`
  - `npm run typecheck`
  - `npm run build`
- [ ] 运行后端页面验证：
  - `uv run pytest tests/test_capability_api.py::test_capability_admin_page -v`
  - `uv run pytest tests/test_capability_api.py -v`
- [ ] 在执行 session 中启动 server，并用 Browser/Playwright 检查页面：
  - `AGENT_BRIDGE_ROOT="$(mktemp -d)" uv run agent-bridge server start`
  - 打开 `/admin/capabilities`
  - 确认页面不是空白、没有 console error、导航可用、弹窗可打开、数据可加载。
- [ ] 提交：`refactor: migrate capability console to vue`

## Task 8：清理、文档更新和最终验收

**执行方式：** Task 4、Task 5、Task 6、Task 7 全部完成后严格串行执行。

**涉及文件：**
- 修改：`README.md`
- 修改：`docs/` 下当前 Agent Bridge 相关文档
- 修改：仍有旧路径残留的测试
- 删除：过时旧静态资源和无用 module shim

- [ ] 搜索旧名字：
  - `rg -n "wiki-manager|wiki_manager|WIKI_MANAGER|WikiManager|uv run wiki|\\bwiki server|X-Wiki" README.md pyproject.toml src tests docs`
- [ ] 对每个命中项分类：
  - 历史文档描述，可以保留
  - 当前产品/runtime 命名，必须改
  - 内置 `wiki` 能力源，可以保留
- [ ] 更新 README：
  - Agent Bridge 概述
  - 安装/setup
  - `agent-bridge` 示例
  - `agb` 示例
  - 默认 root `/root/agent-bridge`
  - MetaMCP 使用方式
  - 前端 dev/build 说明
- [ ] 如需长篇精致说明，新增 HTML 架构说明：
  - 建议路径：`docs/agent-bridge-architecture.html`
- [ ] 只有在测试和前端 build 都通过后，才删除旧文件。
- [ ] 运行最终检查：
  - `uv run pytest -v`
  - `uv run agent-bridge --help`
  - `uv run agb --help`
  - `cd frontend/capabilities && npm run typecheck && npm run build`
- [ ] 运行端到端 smoke check：
  - 启动 server
  - 初始化 storage
  - 打开 `/admin/capabilities`
  - 创建/查看一个 profile
  - 查看 built-in resources
  - 调用 MetaMCP search
- [ ] 提交：`docs: update agent bridge migration docs`

## 推荐并行分工

### Worker A：Rename Lead

- 负责 Task 1。
- Worker A 合并且完整测试通过前，其它 worker 不启动。

### Worker B：Backend Structure Lead

- 负责 Task 2 和 Task 5。
- Task 1 完成后启动。
- 需要和 Worker E 确认 API path 不变。

### Worker C：Storage Lead

- 负责 Task 3 和 Task 4。
- Task 2 完成后启动。
- storage 内部建议单人顺序推进，避免冲突。

### Worker D：CLI Lead

- 负责 Task 6。
- Task 2 完成后启动。
- 可以和 Worker C、Worker E 并行。

### Worker E：Frontend Lead

- 负责 Task 7。
- Task 2 完成且 Worker B 确认 API path 稳定后启动。
- 可以和 Worker C、Worker D 并行。

### Worker F：Integration Lead

- 负责 Task 8。
- Worker B、C、D、E 全部合并后启动。
- 负责最终旧名扫描、文档清理和完整验收。

## 风险控制

- 重构期间保持 route path 稳定。改产品名，不顺手改所有 API path。
- `wiki` 只在表示内置 Wiki 知识源时保留，不再作为产品名、命令名或包名。
- `SQLiteStore` facade 先保留，等 service 和测试稳定后再决定是否删除代理方法。
- 前端迁移阶段不引入大型状态管理库。Vue component state 和 composables 应该足够。
- 不把前端迁移和后端 route 重命名混在同一个任务里。
- 每个 task 完成后提交，方便定位回退。

## 完成标准

- `uv run pytest -v` 通过。
- `uv run agent-bridge --help` 可用。
- `uv run agb --help` 可用。
- runtime import 不再引用 `wiki_manager`。
- 当前产品/runtime 文档不再指导用户运行 `wiki`。
- 默认 runtime root 是 `/root/agent-bridge`。
- 能力治理控制台由 Vue 3 + Vite + TypeScript 构建。
- 旧单体前端 JS/CSS 文件已删除或不再被服务。
- `storage.py` 不再承载全部 storage 领域，storage 行为按 repository 分组。
- FastAPI 后端已拆成 route modules 和领域 services。
