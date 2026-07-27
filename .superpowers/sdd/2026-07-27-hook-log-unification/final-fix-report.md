# Hook 日志统一：最终审查修复报告

## 修复结果

1. `LogMarkdownPreview` 改用 `renderSafeMarkdown`。该渲染器使用 marked 的
   `Renderer` 将原始 HTML 转义为文本，并在 token 阶段移除 `javascript:`、`vbscript:`
   与 `data:` URL；标准 Markdown 标题和 HTTPS 链接仍正常输出。
2. Markdown 预览 allowlist 仅保留 `codegraph_explore`、`session-start`、
   `full-probe` 和历史 `full_probe`；`session-init` 现在返回 `null`。
3. 所有当前支持的 Claude memory Hook action 都有中文展示标签：`version-check`、
   `start`、`context`、`session-start`、`session-end`、`session-init`、
   `observation`、`file-context`、`summarize`，以及 `full-probe` / `full_probe`。
4. `audit_claude_code_hook_call` 将请求/状态准备纳入保护边界，并把不可解析的
   `exit_code` 记录为错误审计行，不会替换已从 worker 返回的 Hook 结果。

## RED / GREEN 证据

- RED：新增前端测试在修复前确认 `session-init` 会被错误预览、中文标签缺失、
  日志预览仍调用不安全的 `renderMarkdown`，并且安全渲染 helper 尚不存在。
- GREEN：
  - `PYTHONPATH=src /Users/kyynor/Code/agent-bridge/.venv/bin/python -m pytest -q -o addopts='' tests/test_hook_audit_logs.py tests/test_memory_hooks.py tests/test_memory_api.py tests/test_memory_cli.py tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_hook.py tests/test_retrieval_probe_reminder.py`
    — 61 passed（仅 FastAPI/Starlette 现有 deprecation warning）。
  - `cd frontend/capabilities && npm run typecheck` — passed。
  - `cd frontend/capabilities && npm run build` — passed。
  - `cd frontend/capabilities && npm test -- tests/logMarkdownPreview.test.ts tests/toolCallDisplay.test.ts tests/markdown.test.ts tests/logsViewLayout.test.ts`
    — 新增覆盖均通过；测试命令会加载全套 node:test，其中仅下述已知无关失败存在。

## 已知无关问题

`cd frontend/capabilities && npm run check` 仍在运行测试阶段失败，唯一失败为
`tests/workflowIncrementalRun.test.ts`：测试期待 `复用节点`，当前
`WorkflowExecutionPlanPreview.vue` 输出 `复用候选`。本修复未改动该工作流文件或测试。
