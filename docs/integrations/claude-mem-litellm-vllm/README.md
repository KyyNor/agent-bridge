# claude-mem → LiteLLM → vLLM：跨会话记忆兼容方案

## 问题

claude-mem 会在 Claude Code 新会话开始时，将检索到的历史记忆注入 Anthropic
Messages 请求。LiteLLM 再将请求转换为 OpenAI Chat 格式并转发给 vLLM 时，若
记忆作为 `messages` 内的 `role: "system"` 消息存在，会在 Anthropic → OpenAI
适配阶段被丢弃；模型因而看不到记忆。

典型现象是：claude-mem 的界面或 Hook 日志显示已经注入“Java 和 Rust”的历史，
但新会话询问此前讨论过什么语言时，模型无法回答。

## 环境边界

| 组件 | 版本/形态 |
| --- | --- |
| LiteLLM Proxy | Docker `litellm/litellm:v1.84.0` |
| 下游 | vLLM `0.17` 的 Anthropic Messages 兼容端点 |
| 客户端 | Claude Code + claude-mem |

vLLM 0.17 的 Anthropic Messages 兼容协议只允许 `messages[].role` 为 `user` 或
`assistant`，但支持请求顶层的 `system` 字段。LiteLLM 的对应适配器也会转换顶层
`system`，而不保留 `messages` 内的 system 项。

## 根因与方案选择

不要把 system 伪装为 `user` + 空/“收到” `assistant`：这会改变指令优先级，污染
对话历史，且模型可能将记忆视为普通用户内容。

采用的正确形式是：

1. 收集原请求顶层 `system` 以及所有 `messages[].role == "system"` 内容；
2. 拍平 Anthropic text block 列表；
3. 按出现顺序以两个换行符合并，写回**顶层** `system`；
4. 从 `messages` 移除这些 system 项，只保留 user/assistant；
5. 移除 vLLM 0.17 不支持的 `context_management`，但保留 `output_config` 给
   LiteLLM 自行转换。

```text
Claude Code / claude-mem
  → Anthropic Messages（可能含穿插 role=system）
  → LiteLLM custom callback（提升到顶层 system）
  → LiteLLM Anthropic → OpenAI Chat adapter
  → vLLM
```

## 文件与配置

本目录的 [`custom_callbacks.py`](custom_callbacks.py) 是可直接挂载到 LiteLLM
容器的回调实现。LiteLLM 配置中注册实例：

```yaml
litellm_settings:
  callbacks: ["custom_callbacks.proxy_handler_instance"]
```

Docker Compose 需要将脚本挂载到容器并使其可导入：

```yaml
services:
  litellm:
    volumes:
      - ./custom_callbacks.py:/app/custom_callbacks.py:ro
    environment:
      PYTHONPATH: /app
```

修改脚本后重启 LiteLLM：

```bash
docker compose restart litellm
docker compose ps litellm
```

## 验证

已完成两层验证：

1. 将含顶层 system、穿插 system、user/assistant 的请求传给 callback，再使用实际
   LiteLLM Anthropic → OpenAI 适配器验证：最终 OpenAI Chat 请求首条为合并后的
   `role: system`，记忆没有丢失。
2. 使用 `claude-mem-env/start.sh` 启动新的 Claude Code 会话，询问“此前会话中
   我们讨论过哪两种编程语言？”，模型正确回答 **Java 和 Rust**。

### 提问语义注意事项

跨会话记忆是“历史索引”，不是当前对话的原始消息流。新会话中应问：

> 根据 claude-mem 注入的此前会话记忆，我们之前讨论过哪两种编程语言？

不要问“我们**刚刚**聊了什么”。“刚刚”通常表示当前新会话；若记忆中另有“本次
会话尚未讨论”的记录，模型按当前会话语义回答“没有”是合理的，而不是转发失败。

## 运维建议

- 若要记录请求用于调试，请按请求 ID/会话 ID 分文件；单一 `last_request.json`
  会被并发请求或 Claude Code 的辅助请求覆盖。
- system 合并后会占用上下文窗口；在 claude-mem 侧限制检索条数/长度，并记录长度
  告警，避免静默截断。
- 升级 LiteLLM、vLLM 或 claude-mem 后，使用 Java/Rust 场景回归。
- 不要在启动脚本、Compose 或日志中明文保存 API token；使用环境变量或密钥管理。
