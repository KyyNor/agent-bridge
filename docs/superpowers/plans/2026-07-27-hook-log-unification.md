# Hook 日志统一与 Markdown 预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 full-probe 通过服务端生成标准 Claude Hook 输出并使用与 session-start 相同的审计结构，同时在调用日志中提供中文工具名与指定 Markdown 输出的预览。

**Architecture:** 将 memory Hook 中与领域无关的审计逻辑提取到项目级 Claude Hook 审计器；memory 与 retrieval probe 都传入标准 Hook 请求和执行结果。RetrievalProbeService 保留检索编排职责，并在服务端生成 additionalContext；CLI 只转发 stdout。前端用独立的日志显示、Markdown 提取和预览组件维护展示规则。

**Tech Stack:** Python 3.11、FastAPI/Pydantic、Typer、pytest；Vue 3、TypeScript、marked、现有 Dialog 与 node:test。

## Global Constraints

- 保持 Chrome 90 兼容；不引入新的浏览器 API 或前端依赖。
- 所有 Python 日志使用中文和 `logging.getLogger(__name__)`；耗时使用 monotonic 时钟。
- Hook 请求完整保存，包含用户 prompt；不在 full-probe 路径脱敏。
- `profile hook claude-code retrieval-probe` 命令名保持不变；新的审计 action/tool name 为 `full-probe`。
- `/retrieval/probe` 保持非 Hook 探测 API；新的 Claude Hook 路由为 `/retrieval/hooks/claude-code/full-probe`。
- 仅对 `codegraph_explore`、`session-start`、`full-probe`（以及历史 `full_probe`）提供 Markdown 预览；未知 JSON 不推断为 Markdown。

---

### Task 1: 抽取项目级 Claude Hook 审计器并迁移 memory Hook

**Files:**
- Create: `src/agent_bridge/hooks/__init__.py`
- Create: `src/agent_bridge/hooks/claude_code.py`
- Modify: `src/agent_bridge/knowledge_management/memory/hooks.py:7-214`
- Test: `tests/test_hook_audit_logs.py`

**Interfaces:**
- Consumes: `GovernanceService.log_tool_call(...)` 与 `CallLogStatus`、`SourceType`。
- Produces: `audit_claude_code_hook_call(...) -> None`，由任意 Claude Code Hook 入口调用。
- Preserves: memory Hook 的 `entrypoint="memory_hook_claude_code"`、现有 action 名称和错误状态映射。

- [ ] **Step 1: 写出共享审计器的失败测试**

在 `tests/test_hook_audit_logs.py` 为一个轻量 FakeGovernance 增加测试，调用项目级 helper 后断言它传给 `log_tool_call` 的请求和响应完全为标准 Hook 外层：

```python
assert captured["request"] == {
    "action": "session-start",
    "event_name": "SessionStart",
    "matcher": "startup|resume|clear|compact",
    "payload": {"source": "startup"},
    "timeout_seconds": 60,
    "source": "claude-code",
}
assert captured["response"] == {
    "stdout": '{"hookSpecificOutput":{}}',
    "stderr": "",
    "exit_code": 0,
    "status": "ok",
}
assert captured["status"] == "success"
```

再断言非零退出码或 `worker_error` 写入 `status="error"`，并优先使用 `stderr` 作为 `error_message`。

- [ ] **Step 2: 运行失败测试，确认缺少共享模块**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_hook_audit_logs.py`

Expected: FAIL，提示 `agent_bridge.hooks.claude_code` 或 `audit_claude_code_hook_call` 尚不存在；既有 memory 测试仍可通过。

- [ ] **Step 3: 实现项目级审计器并替换 memory 私有审计方法**

在 `src/agent_bridge/hooks/claude_code.py` 实现一个只负责审计的函数。其调用边界固定为：

```python
def audit_claude_code_hook_call(
    governance: Any | None,
    *,
    actor: str,
    profile_key: str,
    entrypoint: str,
    action: str,
    event_name: str | None,
    matcher: str | None,
    payload: dict[str, Any],
    timeout_seconds: int,
    result: dict[str, Any],
    duration_ms: int,
    exception: Exception | None = None,
) -> None:
    ...
```

函数内部构造请求 envelope，成功/失败均调用 `governance.log_tool_call`；`exception` 存在时
响应为 `{"exception_type": ..., "message": ...}`、错误类型为 `hook_exception`。审计写入自身失败仅用中文 warning 记录，不覆盖原 Hook 结果。将 `MemoryHookService` 的 `_audit_hook_call`、`_audit_status`、`_audit_error_message`、`_audit_error_type` 删除，改在成功和异常路径调用新函数。

- [ ] **Step 4: 运行 Hook 审计回归测试**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_hook_audit_logs.py tests/test_memory_hooks.py`

Expected: PASS；memory 现有日志字段与错误行保持不变。

- [ ] **Step 5: 提交共享审计器**

```bash
git add src/agent_bridge/hooks/__init__.py src/agent_bridge/hooks/claude_code.py \
  src/agent_bridge/knowledge_management/memory/hooks.py tests/test_hook_audit_logs.py
git commit -m "refactor: share Claude hook audit logging"
```

### Task 2: 服务端 full-probe Hook 输出、路由与 CLI 透传

**Files:**
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/reminder.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/service.py:1-450`
- Modify: `src/agent_bridge/api/schemas.py:1-40`
- Modify: `src/agent_bridge/api/routes/memory.py:1-140`
- Modify: `src/agent_bridge/api/routes/retrieval_probe.py:1-35`
- Modify: `src/agent_bridge/client.py:119-130`
- Modify: `src/agent_bridge/cli/profile_hooks.py:1-180`
- Modify: `tests/test_retrieval_probe_service.py`
- Modify: `tests/test_retrieval_probe_api.py`
- Modify: `tests/test_retrieval_probe_hook.py`
- Modify: `tests/test_hook_audit_logs.py`
- Create: `tests/test_retrieval_probe_reminder.py`

**Interfaces:**
- Consumes: `audit_claude_code_hook_call` from Task 1 and `NOOP_HOOK_STDOUT` from memory models.
- Produces: `await RetrievalProbeService.handle_claude_code_hook(...) -> dict[str, Any]` with `stdout`/`stderr`/`exit_code`/`status`.
- Produces: `AgentBridgeClient.post_retrieval_probe_hook(payload, timeout=...) -> dict[str, Any]` for the new `/retrieval/hooks/claude-code/full-probe` route.
- Preserves: `RetrievalProbeService.probe(...) -> ProbeResponse` and `POST /retrieval/probe` for non-Hook users.

- [ ] **Step 1: 写出 full-probe 服务端 Hook 的失败测试**

在 `tests/test_retrieval_probe_service.py` 添加异步测试，提供完整原始 Hook payload：

```python
raw_payload = {
    "hook_event_name": "UserPromptSubmit",
    "session_id": "session-1",
    "cwd": "/repo",
    "prompt": "订单同步失败",
}
result = await service.handle_claude_code_hook(
    actor="root", profile_key="dev", event_name="UserPromptSubmit",
    matcher=None, payload=raw_payload, timeout_seconds=12,
)
stdout = json.loads(result["stdout"])
assert stdout["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
assert "delivery_id:" in stdout["hookSpecificOutput"]["additionalContext"]
```

在 `tests/test_hook_audit_logs.py` 断言 full-probe 行与 memory Hook 使用相同请求/响应外层、
`tool_name == "full-probe"`，并且 `request_json.payload.prompt` 保留原文。为无命中情况断言
`stdout == NOOP_HOOK_STDOUT`、仍是 `status="ok"`。

- [ ] **Step 2: 运行服务失败测试，确认当前没有 Hook 方法**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py tests/test_hook_audit_logs.py`

Expected: FAIL，提示 `RetrievalProbeService.handle_claude_code_hook` 不存在；当前日志仍是脱敏的 `full_probe` 摘要结构。

- [ ] **Step 3: 将提醒渲染迁移至 retrieval-probe 包并实现标准 Hook 处理**

将 `render_probe_reminder` 及其 `_safe_line`、`_fit_lines`、命中/建议 helper 从
`cli/profile_hooks.py` 移至 `retrieval_probe/reminder.py`。它接收 `ProbeResponse` 或其 payload，
不依赖 Typer、stdin 或 HTTP。

在 `RetrievalProbeService` 新增：

```python
async def handle_claude_code_hook(
    self, *, actor: str, profile_key: str, event_name: str | None,
    matcher: str | None, payload: dict[str, Any], timeout_seconds: int,
) -> dict[str, Any]:
    """执行 full-probe 并返回 Claude Code 的标准 Hook 结果。"""
```

从 `payload["prompt"]` 和 `payload["session_id"]` 派生探测参数；调用 `probe` 时禁止其写入
Hook 审计，随后由本方法使用 Task 1 helper 写入 action `full-probe`。命中时将服务端生成的
`additionalContext` 序列化到 `stdout`；无命中使用 `NOOP_HOOK_STDOUT`。异常路径也必须通过
共享审计器记录后重新抛出。

将原 `probe` 的审计改为非 Hook 调用专用：`entrypoint="retrieval_probe_api"`、
`tool_name="retrieval-probe"`、不再使用 `SourceType.hook` 或 `full_probe`。

- [ ] **Step 4: 定义通用请求 schema、注册 Hook 路由并扩展客户端**

在 `api/schemas.py` 将可复用请求模型命名为 `ClaudeCodeHookRequest`：

```python
class ClaudeCodeHookRequest(BaseModel):
    profile_key: str
    event_name: str | None = None
    matcher: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    hook_timeout_seconds: int = Field(default=60, ge=1, le=300)
```

memory 路由改用该模型。`create_retrieval_probe_routes` 添加：

```python
@router.post("/retrieval/hooks/claude-code/full-probe")
async def handle_full_probe_hook(
    payload: ClaudeCodeHookRequest,
    current_actor: str = Depends(actor),
) -> dict[str, Any]:
    return await service.retrieval_probe.handle_claude_code_hook(...)
```

`AgentBridgeClient.post_retrieval_probe_hook` POST 该 URL。保留 `probe_retrieval` 指向原
`/retrieval/probe`，以维持独立 API 的兼容性。

- [ ] **Step 5: 简化 CLI 为原样 stdout 转发**

`profile_hooks.retrieval_probe_hook` 仍只读取/校验 stdin 的对象、限定
`hook_event_name == "UserPromptSubmit"` 且 prompt 非空；随后发送：

```python
{
    "profile_key": profile,
    "event_name": "UserPromptSubmit",
    "matcher": None,
    "payload": raw_payload,
    "hook_timeout_seconds": timeout,
}
```

成功时只执行：

```python
stdout = str(result.get("stdout") or NOOP_HOOK_STDOUT)
typer.echo(stdout)
raise typer.Exit(int(result.get("exit_code") or 0))
```

删除 CLI 内的提醒渲染和 JSON 输出拼装；服务不可用时仍静默返回，输出
`NOOP_HOOK_STDOUT`，与其他非 SessionStart Hook 一致。

- [ ] **Step 6: 更新并运行 API、CLI 与领域测试**

更新 API 测试以覆盖新路径、schema 边界和客户端 URL；更新 CLI 测试的 FakeClient 返回标准
Hook 结果并断言 stdin 原 payload 原样送往服务端、stdout 未被再加工。将渲染函数单元测试
移动到 `tests/test_retrieval_probe_reminder.py`。

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_hook.py tests/test_hook_audit_logs.py tests/test_memory_api.py tests/test_memory_cli.py`

Expected: PASS；`/retrieval/probe` 仍返回 `ProbeResponse`，新 Hook 路由返回标准四字段结果。

- [ ] **Step 7: 提交服务端 Hook 统一**

```bash
git add src/agent_bridge/knowledge_management/retrieval_probe src/agent_bridge/api \
  src/agent_bridge/client.py src/agent_bridge/cli/profile_hooks.py \
  tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py \
  tests/test_retrieval_probe_hook.py tests/test_retrieval_probe_reminder.py \
  tests/test_hook_audit_logs.py
git commit -m "feat: unify full probe hook logging"
```

### Task 3: 实现日志工具中文展示、Markdown 提取和预览组件

**Files:**
- Create: `frontend/capabilities/src/lib/toolCallDisplay.ts`
- Create: `frontend/capabilities/src/lib/logMarkdownPreview.ts`
- Create: `frontend/capabilities/src/components/LogMarkdownPreview.vue`
- Modify: `frontend/capabilities/src/views/monitoring/LogsView.vue:1-320`
- Test: `frontend/capabilities/tests/toolCallDisplay.test.ts`
- Test: `frontend/capabilities/tests/logMarkdownPreview.test.ts`
- Test: `frontend/capabilities/tests/logsViewLayout.test.ts`

**Interfaces:**
- Produces: `toolCallDisplayName(log: Pick<ToolCallLog, 'source_type' | 'source_key' | 'tool_name'>): string`.
- Produces: `extractLogMarkdownPreview(log: Pick<ToolCallLog, 'tool_name' | 'response_json'>): { title: string; markdown: string } | null`.
- Produces: `LogMarkdownPreview` with `open`, `title`, `markdown` props and `update:open` event.

- [ ] **Step 1: 编写工具中文名与 Markdown 字段提取的失败测试**

在 `toolCallDisplay.test.ts` 测试 source-aware 映射：

```ts
assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name: 'session-start' }), '会话启动')
assert.equal(toolCallDisplayName({ source_type: 'hook', source_key: 'claude_code', tool_name: 'full-probe' }), '全量检索探测')
assert.equal(toolCallDisplayName({ source_type: 'builtin', source_key: 'codegraph', tool_name: 'codegraph_explore' }), 'CodeGraph 代码探索')
assert.equal(toolCallDisplayName({ source_type: 'builtin', source_key: 'memory', tool_name: 'search' }), '记忆检索')
assert.equal(toolCallDisplayName({ source_type: 'mcp_service', source_key: 'custom', tool_name: 'lookup' }), 'lookup')
```

在 `logMarkdownPreview.test.ts` 分别传入：`stdout` 中的 session-start/full-probe
`additionalContext`、CodeGraph `mcp_result.content: [{ type: 'text', text: '# Result' }]`，断言返回标题和
Markdown；传入无效 JSON、空 additionalContext、未知工具则断言 `null`。`full_probe` 必须和
`full-probe` 相同处理。

- [ ] **Step 2: 运行前端失败测试**

Run: `cd frontend/capabilities && npm test -- tests/toolCallDisplay.test.ts tests/logMarkdownPreview.test.ts`

Expected: FAIL，提示两个 helper 模块不存在。

- [ ] **Step 3: 实现可测试的前端 helper**

`toolCallDisplay.ts` 用以 `source_type:source_key:tool_name` 为键的常量映射覆盖所有当前
builtin providers 与 Claude memory/full-probe actions；例如：

```ts
const TOOL_LABELS: Record<string, string> = {
  'hook:claude_code:session-start': '会话启动',
  'hook:claude_code:session-init': '会话初始化',
  'hook:claude_code:full-probe': '全量检索探测',
  'builtin:codegraph:codegraph_explore': 'CodeGraph 代码探索',
  'builtin:memory:search': '记忆检索',
}
```

添加 `full_probe` 历史别名，未命中时返回原 `tool_name` 或 `—`。`logMarkdownPreview.ts` 使用
`JSON.parse` 的安全 helper，仅走设计中三条精确路径，绝不递归扫描任意对象。

- [ ] **Step 4: 创建可复用预览 Dialog 并接入日志详情**

`LogMarkdownPreview.vue` 使用已有 `Dialog`、`DialogContent`、`DialogHeader`、`DialogTitle` 与
`renderMarkdown`：

```vue
<Dialog :open="open" @update:open="$emit('update:open', $event)">
  <DialogContent class="w-[min(1180px,calc(100vw-2rem))] sm:max-w-[1180px]">
    <DialogHeader><DialogTitle>{{ title }}</DialogTitle></DialogHeader>
    <div class="prose prose-sm max-h-[70vh] max-w-none overflow-y-auto" v-html="renderMarkdown(markdown)" />
  </DialogContent>
</Dialog>
```

`LogsView` 使用 `toolCallDisplayName` 同时替换表格和详情中的工具名；在显示元素上设置
`:title="detailLog.tool_name || ''"` 与相同行的表格 title。详情根据
`extractLogMarkdownPreview(detailLog)` 显示“预览”按钮，并将结果传给 `LogMarkdownPreview`；
JSON 查看器不变。

- [ ] **Step 5: 运行前端单元测试与布局保护测试**

添加 `logsViewLayout.test.ts`，读取 `LogsView.vue` 和 `LogMarkdownPreview.vue` 源码，断言中文展示
helper、原名 title、条件“预览”按钮、`LogMarkdownPreview` 组件以及 `renderMarkdown` 的使用均存在。

Run: `cd frontend/capabilities && npm test -- tests/toolCallDisplay.test.ts tests/logMarkdownPreview.test.ts tests/logsViewLayout.test.ts`

Expected: PASS。

- [ ] **Step 6: 提交日志 UI 改动**

```bash
git add frontend/capabilities/src/lib/toolCallDisplay.ts \
  frontend/capabilities/src/lib/logMarkdownPreview.ts \
  frontend/capabilities/src/components/LogMarkdownPreview.vue \
  frontend/capabilities/src/views/monitoring/LogsView.vue \
  frontend/capabilities/tests/toolCallDisplay.test.ts \
  frontend/capabilities/tests/logMarkdownPreview.test.ts \
  frontend/capabilities/tests/logsViewLayout.test.ts
git commit -m "feat: preview markdown hook log output"
```

### Task 4: 同步文档并执行交叉回归

**Files:**
- Modify: `CLAUDE.md:80-90`
- Modify: `README.md:60-80`
- Modify: `docs/integrations/retrieval-probe-hook/README.md`

**Interfaces:**
- Documents: 新的 Hook 端点、服务端生成 additionalContext、`full-probe` 审计名，以及日志预览范围。
- Verifies: 后端 Hook 与前端监控页面均符合设计。

- [ ] **Step 1: 更新用户文档与架构说明**

在 `CLAUDE.md` 说明 retrieval probe 的非 Hook API 与 Claude Hook 路由分离，Hook 输出在服务端
生成并写入通用审计。README 和集成指南改为说明 CLI 只是转发器、审计中会保存原 prompt、监控页可
预览 `codegraph_explore`/`session-start`/`full-probe` 的限定 Markdown 字段。

- [ ] **Step 2: 运行相关回归与前端检查**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_hook_audit_logs.py tests/test_memory_hooks.py tests/test_memory_api.py tests/test_memory_cli.py tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_hook.py tests/test_retrieval_probe_reminder.py`

Expected: PASS。

Run: `cd frontend/capabilities && npm run check`

Expected: PASS（node:test、vue-tsc 与 Vite 生产构建均成功）。

- [ ] **Step 3: 审阅任务文件并提交文档与验证改动**

Run: `git diff --check && git status --short`

确认仅包含本功能文件后执行：

```bash
git add CLAUDE.md README.md docs/integrations/retrieval-probe-hook/README.md
git commit -m "docs: document full probe hook logging"
```
