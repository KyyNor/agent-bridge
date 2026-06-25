# 设计：logging → loguru 重构 + 全子系统中文日志

日期：2026-06-25 ｜ 分支：`worktree-logging-refactor`

## 目标

1. 把日志库从 stdlib `logging` 换成 `loguru`，支持**分卷、最大容量、自动打包**。
2. 在所有关键节点用**中文**打印日志（全子系统覆盖，不限于 ua / claude-mem 示例）。
3. 顺带给本次改动到的文件补**中文注释 / docstring**。
4. 不影响现有代码逻辑（不改控制流 / 返回值 / 异常类型），充分验证。

## 核心策略：InterceptHandler，零改动现有调用点

- 现有 85 处 `logger = logging.getLogger(__name__)` **全部保持不动**。
- 新增 `InterceptHandler`（stdlib `logging.Handler`）装到 root logger，把所有 stdlib
  日志转发进 loguru（保留原始 模块/函数/行号）。这是 loguru 官方对「已有 stdlib 代码库」
  的推荐姿势，最低风险、零行为变更。
- 把 uvicorn 的 `uvicorn` / `uvicorn.error` / `uvicorn.access` 三个 logger 的 handler
  替换为 InterceptHandler，使 access/error 日志并入同一份 `agent-bridge.log`。
- root logger 采用**非破坏式**装配（仅在不存在 InterceptHandler 时追加），避免破坏
  pytest `caplog` 等既有 handler。

## 新模块：`src/agent_bridge/core/logging.py`

```python
setup_logging(paths: AgentBridgePaths, *, level="INFO", console=True)
```

- `logger.remove()` 清空默认 sink 后重装（幂等，可被多次调用——测试每次 `create_app` 用不同 tmp 路径）。
- **文件 sink** → `paths.logs_dir / "agent-bridge.log"`：
  - `rotation="100 MB"`
  - `retention=` 自定义 callable：先删超过 **90 天**的分卷，再从最旧的开始删直到累计回到 **5 GiB** 以内。
  - `compression="zip"`
  - `enqueue=True`（多线程/多请求安全写盘），`encoding="utf-8"`，`diagnose=False`（不泄漏变量值）。
- **控制台 sink**（stderr）INFO 彩色：服务子进程下 stderr 仍被重定向到 `server.log`，
  兼作早启动 / 崩溃捕获。
- 格式：`时间 | 级别 | 模块:函数:行号 - 中文消息`。
- 接管 stdlib logging：root 追加 InterceptHandler（非破坏式）；uvicorn.* 替换为 InterceptHandler。

## 文件职责

- `server.log`：保留为「原始 stderr / 早启动崩溃」捕获（`server_process.py` 既有的 stdout/stderr 重定向不动）。
- `agent-bridge.log`：结构化、轮转、压缩的应用主日志（新增）。

## 接入点

`setup_logging(paths)` 作为 `create_app()`（`api/app.py`）**第一行**（`resolved_paths` 计算之后、
`load_server_config` 之前）调用——早于 `AgentBridgeService` 装配，构造期日志也被捕获。
CLI 父进程保持 `typer.echo`（薄壳，非关键节点）。

## 全子系统中文日志（6 组不相交文件）

| 组 | 文件 | 新增日志 |
|---|---|---|
| A 能力与网关 | `capability_hub/service.py`、`gateway/metamcp.py`、`governance.py`、`sources/mcp/*`、`sources/openapi/*` | execute/search 入口+拒绝+完成+失败、MCP/OpenAPI 调用边界、profile 解析 |
| B 代码知识 | `code_knowledge/ua_client.py`、`service.py`、`scheduler.py`、`understand_scheduler.py` | UA 开始解析 / 各类解析错误 / 解析完成、镜像与索引（CLI/SQLite 双模式）、调度 tick |
| C 记忆 | `memory_management/claude_mem/worker.py`、`hooks.py` | claude-mem worker 进程启停 / 全停 / 开始同步、插件 ensure |
| D 工作流+Agent | `automation/workflows/*`、`agent_runtime/service.py` | workflow run 启动/产物/完成、agent run 启动/结束、归一化 service.py 两行英文日志为中文 |
| E 文档调度 | `scheduler_base.py`、`docs_knowledge/*`、`plugin_update_scheduler.py` | 调度器启停/tick、doc sync 任务扇出与后端成败、插件更新 |
| F 启动装配 | `api/app.py`、`app/service.py`、`runtime/server_process.py`、`plugin_runtime.py` | 服务启动/停止、Service 装配、save_sync_config refresh、插件运行时启停 |

**铁律：只加日志 + 注释，不改控制流 / 返回值 / 异常类型。**

## 执行顺序（hybrid：内联打底 + Workflow 并行）

1. **Phase 1（内联）**：加 `loguru` 依赖、建 `core/logging.py`、接入 `create_app`、
   写 `tests/test_logging_setup.py`、起服务验证 `agent-bridge.log` 落盘且参数生效。
2. **Phase 2（Workflow 并行 6 agent）**：按上表 6 组不相交文件并行加日志 + 注释，
   共用同一份日志风格指南保证一致性。
3. **Phase 3（Workflow 验证）**：跑全量 `pytest -m "not ragflow and not weknora"`
   （应 ≥577 全绿 + 新增测试）、一致性审查 agent、修问题、服务冒烟。

## 日志风格指南（Phase 2 各 agent 共用）

- 全中文消息；关键上下文用 `%s` 占位（profile、service、tool、repo、run_id、pid、耗时 ms、状态）。
- INFO = 正常生命周期边界（开始 / 完成）；WARNING = 可恢复异常 / 被拒绝；ERROR = 失败 + `exc_info`。
- 消息以「主语 + 动作」开头，例如「UA 开始解析 repo=%s」「claude-mem worker 进程启动 block=%s pid=%s」。
- 不在热路径循环里打 INFO（避免噪音）；循环内用 DEBUG 或聚合后打一条。

## 验证

- 新测试：文件 sink 创建、InterceptHandler 路由 stdlib→loguru、retention callable（年龄+容量双限）、
  压缩触发、uvicorn logger 被接管。
- 现有 577 保持全绿。
- 起服务确认 `agent-bridge.log` 写入、轮转/压缩/保留参数生效。
