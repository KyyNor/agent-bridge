# DSH 作为 Coding Agent 后端 — 可行性调研与资源评估

对应 Issue #4（AgentRuntime 新增 DSH 后端）。本文记录接入方式选型、协议映射、已知坑与
实测资源数据。实现见 `src/agent_bridge/agent_runtime/adapters/dsh.py` 与
`src/agent_bridge/dsh/agent_runtime.py`。

调研环境：macOS（arm64）、`@deepseek-ai/dsh` 0.1.5-rc.2、Agent Bridge 托管
OpenAI 兼容网关 + deepseek-flash。数据为单机粗测，供后端选型参考，非严格 benchmark。

## 接入方式选型

DSH 0.1.5-rc.2 提供四种程序化面：

| 方式 | 结论 |
| --- | --- |
| `--profile acp`（ACP v1，JSON-RPC over stdio） | **采用**。官方 automation-only 通道：`initialize` → `session/new`（绝对 cwd + stdio/HTTP MCP 声明）→ `session/prompt`（流式 `session/update`）→ `session/close`；支持 `session/cancel`、`session/set_config_option`、`session/list/resume`。stdout 只有协议帧。 |
| `--profile headless`（一次性任务文本输出） | 否决。仅"stream reasoning to stderr, print final message"，无结构化事件流。 |
| TUI 文本解析 | 否决（issue 明确禁止）。 |
| 复用 Web Runtime 进程 | 否决。Web 是长生命周期人机交互服务；AgentRuntime 要求独立、短生命周期、不依赖 Web 是否启动。 |

## 模型路由的关键事实

- acp profile 的 `dsh-acp` 行把 provider/model 钉死为 `deepseek-official`；用户
  `settings.yaml` 的 `agent-default-model` **不会**覆盖它，必须以后到 `--patch`
  显式改写该行（`{"id": "acp", "config": {"provider": "agent-bridge", "model": ...}}`）。
- `llm-pi-ai.providers.agent-bridge`（Base URL/模型目录/`apiKeyEnv`）来自用户级
  `settings.yaml`，API Key 只经 `AGENT_BRIDGE_DSH_API_KEY` 环境变量注入，不落盘。
- agent-bridge 路由下 session 只广告 `model` 配置项，无 `reasoning_effort`——因此
  DSH 后端 `supported_efforts` 为空集合，配置 effort 在 registry 即被拒绝。

## ACP update → 统一事件映射

| ACP `sessionUpdate` | 统一事件 |
| --- | --- |
| `agent_message_chunk`（带 `messageId`） | `agent_message`（`stream_id=messageId`、`partial=true`，时间轴按 id 拼接） |
| `agent_thought_chunk` | `status`（`thinking`，reasoning 文本进 message） |
| `tool_call`（`rawInput` 为原始工具输入） | `tool_call`（started） |
| `tool_call_update`（completed/failed） | `tool_result`（content 文本归并为 output） |
| `usage_update`（used/size） | `status`（`usage`，上下文 token 用量） |
| `compaction_summary_chunk` | `status`（`compaction`） |
| `user_message_chunk` / `config_option_update` / `plan*` 等 | 不展开（原始 update 仍进 `messages.jsonl`） |

`session/prompt` 结算 `stopReason`：`end_turn` 成功；`cancelled`/`refusal`/
`max_tokens`/`max_turn_requests` 为错误终态（subtype 保留原值）。最终结果文本按
`messageId` 合并 chunk。

## 已知坑（已在内置对策）

1. **全新 DSH_HOME 缺标准子目录 → provider 注册失败**：`sessions/`、`storages/`、
   `change-ledger/`、`task-board/` 四目录缺任一，`session/new` 即报
   `no adapter registered for provider "agent-bridge"`（实测缺一不可，原因在 DSH 侧
   插件初始化链）。resolver 预创建全部四目录规避。
2. **provider 注册竞态**：即使目录齐全，DSH 的 LLM provider 注册也可能晚于 ACP 服务
   开始应答。adapter 对 `no adapter registered` 错误在同一进程上退避重试（1s × 30）。
3. **权限自动应答**：无人值守 run 对 `session/request_permission` 自动选第一个
   `allow_*` 选项；workspace-write 沙箱与 bash 超时仍由 DSH 侧执行。实测读操作与
   沙箱内写不触发权限请求。
4. **`session/set_config_option` 参数名是 `configId`**（非 `optionId`），v1 调试时注意。

## 资源与稳定性实测

- 单 run（进程启动 → 真实模型调用 → 一次 bash 工具 → close 退出）：**6.2–9.1s** 全程，
  其中 dsh 进程冷启动约 2–4s。
- 进程 RSS：活跃 run 主进程约 **175MB**（node），工具执行时 +1 个短命子进程；run
  结束后进程全部退出，**无遗留**（连续 3 轮 process 集成测试后孤儿进程为 0）。
- 并发 5 个 run（各自独立进程）：全部成功，墙钟 13.7s，约 5×175MB。
- 取消：`session/cancel` 后 prompt 以 `stopReason=cancelled` 结算，5–13s 内完成回收；
  取消后立即可启动新 run，无状态残留。
- MCP 稳定性：HTTP MCP 声明接入 Agent Bridge MetaMCP 网关（Profile 头鉴权），连续
  多轮 run 中 search 工具发现与调用全部成功，调用进入 `tool_call_logs` 审计。
- session/上下文恢复：ACP 侧支持 `session/list`/`session/resume`（持久化在 DSH_HOME），
  当前 adapter 按 run 独立会话、未启用 resume；后续如需断点续跑可复用。
- LiteLLM → vLLM 兼容性：DSH 走 `openai-completions` wire protocol，与 Agent Bridge
  托管网关（OpenAI 兼容）直连成功；自部署 vLLM 同为 OpenAI 兼容接口，预期可用，
  未在本次环境实测。

## 与现有后端差异（能力口径）

| 能力 | Claude | OpenCode | Codex | Pi | DSH |
| --- | --- | --- | --- | --- | --- |
| MCP | ✅ | ✅ | ❌ | ❌ | ✅（ACP 声明） |
| 原生 JSON Schema | ✅ | ✅ | ✅ | ❌ | ❌（system prompt 回落） |
| 成本/轮数 | ✅ | ✅ | ✅ | ✅ | ❌（仅上下文用量） |
| 子代理事件 | ✅ | — | — | — | ❌（subagent 以普通 tool_call 出现） |
| reasoning 事件 | ❌ | ✅ | — | — | ✅（thought chunk） |
| 取消 | 外部超时 | ✅ | ✅ | ✅ | ✅（session/cancel） |

## 运行集成测试

真实进程测试按仓库惯例挂 `process` marker（`./scripts/test.sh all`），并需环境提供
可用的 OpenAI 兼容网关：

```bash
export AGENT_BRIDGE_DSH_TEST_BASE_URL=https://gateway.example/v1
export AGENT_BRIDGE_DSH_TEST_API_KEY=sk-...
export AGENT_BRIDGE_DSH_TEST_MODEL=deepseek-flash   # 可选，默认 deepseek-flash
pytest tests/test_dsh_agent_process.py -m process
```
