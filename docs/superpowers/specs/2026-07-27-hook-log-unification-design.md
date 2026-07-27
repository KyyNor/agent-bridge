# Hook 日志统一与 Markdown 预览设计

## 目标

将 Claude Code `full-probe` 的调用、审计记录和输出协议统一为与
`session-start` 相同的 Hook 约定；监控日志以中文展示内置工具与 Hook 的工具名，
并对指定的 Markdown 输出提供预览。

## 边界

- `full_probe` 的新审计工具名为 `full-probe`。CLI 命令
  `profile hook claude-code retrieval-probe` 保持不变，避免破坏已安装的 Hook。
- 不新增 Retrieval Probe 专用 Hook service。检索编排和 Hook 输出仍由
  `RetrievalProbeService` 负责。
- 不对 prompt 脱敏：full-probe 的审计请求记录 Claude Code 原始 Hook payload。
- 预览仅从明确定义的字段提取 Markdown；不对任意 JSON 字符串进行猜测式渲染。

## 后端设计

### 项目级 Claude Hook 审计

新增项目级模块，提取现有 `MemoryHookService._audit_hook_call` 的通用逻辑。它接收
调用者、Profile、入口、action 和标准 Hook 请求/结果，写入 `tool_call_log`：

```json
{
  "request": {
    "action": "session-start | full-probe",
    "event_name": "SessionStart | UserPromptSubmit",
    "matcher": "... | null",
    "payload": { "...": "原始 Claude Hook 输入" },
    "timeout_seconds": 60,
    "source": "claude-code"
  },
  "response": {
    "stdout": "...",
    "stderr": "",
    "exit_code": 0,
    "status": "ok"
  }
}
```

`MemoryHookService` 改用该项目级审计器，既有 memory Hook 的日志语义和入口名称保持
不变。`RetrievalProbeService` 同样调用它，`tool_name` 为 `full-probe`。

保留 `POST /retrieval/probe` 作为非 Hook 的探测 API，其汇总审计名称改为
`retrieval-probe`，不再占用 `full-probe`。新增
`POST /retrieval/hooks/claude-code/full-probe`：它接收标准 Claude Hook 的
`event_name`、`matcher`、原始 `payload` 和超时，并调用 `RetrievalProbeService` 的
Hook 处理方法。CLI 改为调用新路由。

### 服务端 full-probe 输出

`RetrievalProbeService` 在探测完成后调用迁移出的纯函数渲染探测提醒，并构造：

```json
{
  "stdout": "{\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\",\"additionalContext\":\"...\"}}",
  "stderr": "",
  "exit_code": 0,
  "status": "ok"
}
```

无命中时仍返回标准成功结果，`stdout` 为现有的无输出 Hook JSON；异常时遵循通用审计
的错误记录方式。检索提醒渲染函数从 CLI 移入 retrieval-probe 领域包，CLI 不再保留
渲染、脱敏或输出结构组装逻辑，只调用服务端并原样打印 `stdout`。

full-probe 的新 Claude Hook API 返回上述标准 Hook 结果，而不是由 CLI 再次加工的探测
payload；探测的聚合统计通过服务端日志保留。相关集成文档同步更新。

## 前端设计

### 工具显示名

在 `frontend/capabilities/src/lib` 新增日志工具显示 helper：

- 输入 `source_type`、`source_key`、`tool_name`；
- 对所有已知 builtin 和 Claude Hook action 返回中文名；
- 不在映射中的工具返回原始名；
- `full_probe` 作为历史别名映射到与 `full-probe` 相同的中文名。

日志表格和详情中的工具列显示该中文名；元素的 `title` 始终为原始 `tool_name`，浏览器
悬浮时可查看原始名称，不改变筛选、搜索和后端存储。

### Markdown 预览

新增可复用 `LogMarkdownPreview` 组件，接收标题和纯 Markdown 文本，在 Dialog 中使用
既有 `renderMarkdown` 显示。新增日志响应提取 helper，只支持以下固定规则：

| 工具 | 响应字段 | 预览标题 |
| --- | --- | --- |
| `codegraph_explore` | `mcp_result.content` 内首个 `type=text` 项 | CodeGraph 探索结果 |
| `session-start` | `stdout` JSON 的 `hookSpecificOutput.additionalContext` | 会话启动上下文 |
| `full-probe` / 历史 `full_probe` | 同上 | 全量探测提醒 |

`LogsView` 仅当提取到非空文本时显示“预览”按钮；请求和响应的 JSON 查看器保持不变。

## 验证

- 后端测试验证 memory 与 full-probe 写入相同的 Hook 请求/响应外层结构，full-probe
  包含未脱敏的原始 prompt，并使用 `full-probe`。
- CLI 测试验证 CLI 原样输出服务端 `stdout`，不再调用提醒渲染函数。
- 前端单元测试验证中文显示名和历史别名、原始名 title、三种明确字段的 Markdown 提取，
  以及无可预览字段时不显示按钮。
- 运行相关 Python 测试与 `cd frontend/capabilities && npm run check`。
