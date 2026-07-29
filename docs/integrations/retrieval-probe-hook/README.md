# Claude Code 全量检索探测 Hook

## 用途

该集成在 Claude Code 收到用户问题后，异步调用 Agent Bridge，对当前 Profile
允许访问的工作流产出物执行多关键词轻量探测。
探测结果只告诉 Agent “哪些关键词在哪些资源中命中、建议继续调用哪个工具”，
不返回正文，也不替 Agent 生成答案。

`profile use` 会在所选 project/user scope 自动安装这个 Hook，并在 Claude
托管说明块中声明 `<system-reminder>` 是补充的系统信息。重复执行不会累加；
切换 Profile 时会替换 Agent Bridge 管理的旧 Hook，用户已有 Hook 保持不变。

## 工作方式

```text
UserPromptSubmit
  → Claude Code 后台启动 Hook
  → CLI 原样转发 stdin 的 Hook payload
  → POST /retrieval/hooks/claude-code/full-probe
  → 小模型结合会话历史提取本轮新增短句
  → 并发探测 Profile 允许的工作流产出物
  → 服务端生成 stdout JSON additionalContext + exit 0
  → 通用 Hook 审计保存原始 prompt、请求和响应
  → Claude Code 在下一次对话轮次将路由提醒交给 Agent
```

当前 full-probe 只注册 Artifact 来源，只搜索该 Profile 范围内的 current 工作流产出物；
Wiki、CodeGraph 和 Memory 不参与本探测。

请求具有统一的整体 deadline，超时后仍会返回已经完成的部分结果。后端不可用、
超时和正常无命中分别表示为 `unavailable`、`timeout` 和 `no_hit`。

关键词由系统配置中的 OpenAI Chat 兼容小模型结合会话历史提取为 0–8 个业务短句；服务端
还会过滤空值、停用词、单字、过长项和历史重复项。当前请求只向模型提供同一 Profile/session
最近 3 轮成功结果，缓存最多保留 12 轮，DiskCache 使用 30 天滑动 TTL。配置未完成、模型调用
失败、超时或输出结构不合法时会静默结束，不回退短词分词，也不修改历史。有效的空列表会
记录到历史，但不会产生检索任务；模型抽取最多 10 秒，完整探测最多 20 秒。

## 前置条件

1. Agent Bridge 服务已启动。
2. 指定的 Profile 存在且为 active。
3. Claude Code 运行环境可以执行 `agent-bridge`，并访问 Agent Bridge API。
4. Profile 已配置相应资源权限；API 不接受客户端传入资源列表绕过 Profile。

## 自动安装与手工配置

正常使用时执行：

```bash
agent-bridge profile use chengdu \
  --scope project \
  --url http://127.0.0.1:8765/mcp
```

Agent Bridge 会生成等价于下方内容的 Claude Code settings。下方 JSON 仅用于
排障或需要绕过 `profile use` 的独立调试：

在所需作用域的 Claude Code settings 中加入：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agent-bridge profile hook claude-code retrieval-probe --profile chengdu --server-url http://127.0.0.1:8765 --timeout 12 --agent-bridge-hook-id agent-bridge-retrieval-probe",
            "async": true,
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

将 `chengdu` 替换为实际 Profile key。如果项目中通过 `uv` 运行源码，可把命令
改成：

```text
uv run agent-bridge profile hook claude-code retrieval-probe ...
```

命令从 stdin 读取 Claude Code 的 `UserPromptSubmit` JSON，作为轻量转发器将原始
payload 交给服务端的 `POST /retrieval/hooks/claude-code/full-probe`。服务端生成标准
Hook 输出：只有事件正确、prompt 非空且至少一个资源命中时，stdout 才包含
`additionalContext` 的合法 JSON；无命中、输入不适用或 API 暂时不可用时不输出内容，
同样以状态码 0 结束。每次 `full-probe` 调用都会进入通用 Hook 审计，审计请求中保留
原始 payload（包括 prompt），响应中保留 Hook 的 stdout、stderr、退出码和状态。

普通 `async` 会让 Hook 在后台运行。探测结果在下一次对话轮次作为 system reminder
上下文交付：Agent 仍在工作时，可在后续模型轮次消费；会话已经空闲时不会主动唤醒，
而是等待下一次用户交互。

若 Claude Code 通过 LiteLLM 的 Anthropic → OpenAI Chat 适配链访问只接受
user/assistant 的后端，应启用相邻目录
`claude-mem-litellm-vllm/custom_callbacks.py`：它会把中途 system reminder
原位置转换成 user，同时保留 `<system-reminder>` 标签。

提醒包含唯一 `delivery_id`，并明确要求 Agent：

- 不要只回复“已收到”；
- 只在结果有助于当前任务时继续检索；
- 同一 `delivery_id` 只处理一次。

## 独立 API

请求：

```bash
curl -X POST http://127.0.0.1:8765/retrieval/probe \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Bridge-User: alice' \
  -d '{
    "profile_key": "chengdu",
    "prompt": "之前订单同步失败最终怎么处理的？",
    "session_id": "session-123",
    "keyword_limit": 8,
    "result_limit": 3,
    "timeout_seconds": 10
  }'
```

响应示例：

```json
{
  "probe_id": "probe_01J...",
  "profile_key": "chengdu",
  "session_id": "session-123",
  "keywords": ["订单", "同步", "失败", "处理"],
  "source_statuses": {
    "artifact": "hit"
  },
  "targets": [
    {
      "source_type": "artifact",
      "resource_key": "chengdu",
      "resource_name": "工作流产出物",
      "suggested_tool": "artifacts_search",
      "status": "hit",
      "unique_hit_count": 3,
      "keyword_hits": [
        {
          "keyword": "订单",
          "status": "hit",
          "count": 3,
          "capped": true,
          "duration_ms": 180,
          "error_type": null
        }
      ]
    }
  ],
  "duration_ms": 1820,
  "keyword_extraction": {
    "status": "success",
    "model": "small",
    "duration_ms": 240,
    "error_type": null,
    "history_rounds": 3,
    "filtered_keyword_count": 1
  }
}
```

`count` 受 `result_limit` 限制；`capped=true` 表示实际候选可能更多，因此 Hook
会显示“至少命中 N 条”。API 不返回候选正文、标题和内部候选标识。

## 监控与审计

管理后台的调用日志可查看完整 JSON 请求/响应。为避免将任意日志内容当作 Markdown
渲染，页面只预览下列已定义字段：`codegraph_explore` 的 MCP 文本结果，以及
`session-start`、`full-probe` Hook stdout 中的 `hookSpecificOutput.additionalContext`。
其他工具或字段不会生成 Markdown 预览，仍可在 JSON 查看器中按需检查。由于审计会
保存原始 prompt，日志访问应按现有 Profile 和用户权限控制，并避免向无关人员导出。

参数边界：

- `keyword_limit`：0–8，默认 8。返回空列表时不会调用产物检索。
- `result_limit`：1–20，默认 3。
- `timeout_seconds`：大于 0 且不超过 20 秒，默认 20 秒。

## 临时停用

从 Claude Code settings 中删除带
`--agent-bridge-hook-id agent-bridge-retrieval-probe` 的 `UserPromptSubmit`
Hook entry 即可临时停用。再次执行 `profile use` 会重新安装它。

## 当前边界

- 当前只有“全量探测”策略，没有基于分类器的选择性探测。
- 关键词由 OpenAI Chat 兼容小模型生成，并由服务端执行确定性结构校验和过滤；不回退到
  jieba 短词分词。
- 传入 `session_id` 后，历史按 Profile/session 隔离保存；缺少 `session_id` 时仅执行单轮
  提取，不读写历史。
- 返回结果用于路由，不应被当成答案依据；Agent 仍需调用建议工具取得真实内容。
- 普通 `async` 不主动唤醒已结束的会话；若当前任务需要确保结果送达，应让 Agent
  在检索窗口内继续工作，或由用户发起下一次交互。
