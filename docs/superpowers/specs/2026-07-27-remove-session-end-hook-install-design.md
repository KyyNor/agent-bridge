# 停止安装 SessionEnd Profile 刷新 Hook

## 背景

`profile use` 当前会安装 `SessionEnd → session-end` Hook，在会话结束时把
Profile 与 Memory 上下文刷新到 Profile Markdown 文件。这是为规避旧版
LiteLLM 丢失 `SessionStart` 注入而增加的兜底。

现有 LiteLLM callback 已能正确处理注入消息，因此上下文统一由
`SessionStart → session-start` 注入，不再需要会话结束时刷新文件。

## 变更

- 从 `CLAUDE_MEM_COMPATIBLE_HOOKS` 删除 `SessionEnd` 安装项。
- `profile use` 继续先按 Agent Bridge hook marker 清理全部旧托管 Hook，再安装
  当前 Hook 集合，因此再次执行后会自动删除旧版 `SessionEnd` Hook。
- 用户自行配置的 `SessionEnd` Hook 不带 Agent Bridge marker，必须保持不变。
- 保留 `session-end` action、HTTP API、服务端处理逻辑和相关领域测试，兼容尚未
  重新执行 `profile use` 的旧配置。

## 验收

- 新配置不包含 Agent Bridge 管理的 `SessionEnd` Hook。
- 已有旧版 Agent Bridge `SessionEnd` Hook 在再次执行 `profile use` 后被删除。
- 用户自定义 `SessionEnd` Hook 保留。
- `SessionStart`、Memory 采集 Hook和 retrieval-probe Hook保持原有行为。

