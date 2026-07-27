# Claude Code → LiteLLM → vLLM：system-reminder 兼容方案

## 问题

Claude Code 的 SessionStart、UserPromptSubmit 等 Hook 可能将补充信息放入
Anthropic Messages 的 `messages[].role = "system"`。LiteLLM 再将请求转换为
OpenAI Chat 格式并转发给 vLLM 时，这些消息会在 Anthropic → OpenAI 适配阶段
被丢弃；模型因而看不到记忆或异步检索结果。

典型现象是：claude-mem 的界面或 Hook 日志显示已经注入“Java 和 Rust”的历史，
但新会话询问此前讨论过什么语言时，模型无法回答。

## 环境边界

| 组件 | 版本/形态 |
| --- | --- |
| LiteLLM Proxy | Docker `litellm/litellm:v1.84.0` |
| 下游 | vLLM `0.17` 的 Anthropic Messages 兼容端点 |
| 客户端 | Claude Code + claude-mem |

vLLM 0.17 的 Anthropic Messages 兼容协议只允许 `messages[].role` 为 `user` 或
`assistant`，但支持请求顶层的 `system` 字段。LiteLLM 的对应适配器也只处理
顶层 system 以及 messages 中的 user/assistant。

## 根因与方案选择

统一提升到顶层 system 虽然兼容 SessionStart，但会改变 UserPromptSubmit
异步结果在上下文中的时间位置。不同内网模型的 Chat Template 对中途 system
支持也不一致，因此采用兼容性优先的原位降级：

1. Anthropic 顶层 `system` 保持原样；
2. 每条 `messages[].role == "system"` 在原位置改为 `role == "user"`；
3. 不合并相邻 user，不增加虚假的 assistant“收到”消息；
4. content 及 Claude Code 已有的 `<system-reminder>` 标签保持原样；
5. 移除 vLLM 0.17 不支持的 `context_management`，但保留 `output_config` 给
   LiteLLM 自行转换。

该方案会降低中途补充信息的协议优先级，但能在未知模型 Chat Template 的情况下
保留消息到达位置，并避免伪造对话历史。Claude Profile 指引会声明
`<system-reminder>` 是补充的系统信息，帮助模型恢复其语义。

```text
Claude Code / claude-mem
  → Anthropic Messages（可能含穿插 role=system）
  → LiteLLM custom callback（原位置改成 role=user）
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

自动化测试覆盖：

1. 顶层 system 的对象和值均保持不变；
2. 穿插 system 原位置变为 user，不与相邻 user 合并；
3. Anthropic content block 和 `<system-reminder>` 标签保持不变；
4. 非 Anthropic 请求不被 callback 改写。

### 提问语义注意事项

跨会话记忆是“历史索引”，不是当前对话的原始消息流。新会话中应问：

> 根据 claude-mem 注入的此前会话记忆，我们之前讨论过哪两种编程语言？

不要问“我们**刚刚**聊了什么”。“刚刚”通常表示当前新会话；若记忆中另有“本次
会话尚未讨论”的记录，模型按当前会话语义回答“没有”是合理的，而不是转发失败。

## 运维建议

- 若要记录请求用于调试，请按请求 ID/会话 ID 分文件；单一 `last_request.json`
  会被并发请求或 Claude Code 的辅助请求覆盖。
- system-reminder 仍会占用上下文窗口；在 Hook 侧限制检索条数/长度，并记录长度
  告警，避免静默截断。
- 升级 LiteLLM、vLLM 或 claude-mem 后，使用 Java/Rust 场景回归。
- 不要在启动脚本、Compose 或日志中明文保存 API token；使用环境变量或密钥管理。
