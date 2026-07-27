# Agent Bridge 全量检索探测 API 与 Claude Code Hook 设计

## 1. 背景

Agent Bridge 当前向 Agent 暴露文档知识库、CodeGraph、记忆和工作流产出物等检索能力。能力平面会告诉 Agent 可用资源和工具，但较弱模型仍可能无法根据用户问题稳定选择正确的数据源。

本功能在 Claude Code 收到用户请求后，后台将请求拆成多个关键词，对当前 Profile 可访问的 Wiki、CodeGraph、Memory 和 Artifact 执行一次全量轻量探测。探测完成后，通过 Claude Code 普通 `async` Hook 的 `additionalContext` 将“哪些资源命中、每个关键词命中多少候选”作为路由信息交给 Agent。

本期只解决“告诉 Agent 去哪里进一步检索”，不返回完整检索内容，不替 Agent 生成答案。

## 2. 范围

本期包含：

- 独立的全量检索探测领域服务。
- Wiki、CodeGraph、Memory、Artifact 四类探测 adapter 和 registry。
- 用户问题的确定性分词。
- 独立 HTTP API。
- 由 `profile use` 自动安装的 Claude Code
  `UserPromptSubmit + async` Hook。
- 相关单元测试、API 测试、CLI Hook 测试和使用说明。

本期不包含：

- 不增加独立的 Hook 开关或前端配置；启停随 `profile use` 管理。
- 不改变现有 MCP 工具暴露；Profile 指引只增加 system-reminder 语义说明。
- 不调用 `wiki_ask` 或 CodeGraph Explore。
- 不返回正文、片段或候选标题。
- 不增加前端页面、数据库表或持久化配置。
- 不增加 LLM 查询改写、统一重排或学习路由。

执行 `profile use` 后，Agent Bridge 会在对应 project/user scope 的 Claude settings
中幂等安装 retrieval-probe Hook；重复执行不会累加，切换 Profile 会替换旧命令，
用户自己的 Hook 保持不变。

## 3. 总体架构

数据流如下：

```text
Claude Code UserPromptSubmit
  → 后台 async command Hook
  → POST /retrieval/probe
  → 确定性分词
  → Profile 资源解析
  → RetrievalProbeAdapter registry
  → 资源 × 关键词并发探测
  → 结构化探测结果
  → Hook 渲染路由提醒
  → 有命中时 stdout JSON additionalContext + exit 0
  → 下一次对话轮次的 Claude Code system reminder
```

Hook 只负责协议转换和消息交付。权限、资源枚举、分词、并发、后端调用、超时、结果聚合与审计全部位于 Agent Bridge 服务端。

## 4. 领域组件

新增目录：

```text
src/agent_bridge/knowledge_management/retrieval_probe/
├── __init__.py
├── models.py
├── tokenizer.py
├── adapters.py
├── registry.py
└── service.py
```

### 4.1 数据模型

核心模型：

- `ProbeRequest`：Profile、用户问题、关键词数量、单次查询结果上限和整体超时。
- `ProbeTarget`：来源类型、资源标识和显示名称。
- `KeywordProbeResult`：单个资源对单个关键词的状态、内部候选标识、候选数、是否达到上限、耗时和错误类型。候选标识仅用于跨关键词去重，不通过 API 暴露。
- `TargetProbeSummary`：按资源聚合的关键词命中和去重候选数。
- `ProbeResponse`：探测 ID、关键词、各来源总体状态、所有目标结果和整体耗时。

来源状态固定为：

- `hit`：至少一个关键词有候选。
- `no_hit`：正常完成但没有候选。
- `not_configured`：Profile 未配置该类资源。
- `unavailable`：后端或索引不可用。
- `timeout`：超过本次探测预算。

后端异常不得转换成 `no_hit`。

### 4.2 Adapter 协议

同一探测能力通过 Protocol、adapter 和 registry 扩展：

```python
class RetrievalProbeAdapter(Protocol):
    source_type: str

    def list_targets(
        self,
        actor: str,
        profile_key: str,
    ) -> list[ProbeTarget]: ...

    def probe(
        self,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult: ...
```

应用服务只遍历 registry，不依据来源类型编写 `isinstance` 或 `if/elif` 分发。

四类实现：

- Wiki adapter：枚举 Profile 允许的知识库，调用文档后端原始 `retrieve`。
- CodeGraph adapter：枚举 Profile 允许且 active 的代码仓库，调用正式 CodeGraph `query`。
- Memory adapter：解析 Profile 绑定的 active memory block，调用 memory search。
- Artifact adapter：以 Profile 作为结构化范围条件，搜索 current 工作流产出物。

## 5. 分词

分词只用于生成多个独立查询，不生成一个包含多个关键词的 `AND` 查询。

规则：

1. 中文使用 jieba 精确模式。
2. ASCII 标识符、路径、错误码保持连续形式。
3. 去除空白、标点、常见停用词和无意义单字。
4. 保持原始出现顺序。
5. 去重。
6. 默认最多保留 8 个关键词。
7. 不调用 LLM，不依赖外部服务。

示例：

```text
输入：之前订单同步失败最终怎么处理的？
输出：订单、同步、失败、处理
```

每个 adapter 使用相同关键词列表，但可以在自身内部执行满足后端契约的参数转换。

## 6. 并发和超时

探测任务粒度为“资源 × 关键词”。

服务端并发执行任务，并使用共享 semaphore 限制并发量，第一版默认上限为 8。请求有一个整体 deadline，默认 10 秒；响应允许包含部分成功结果。

同步后端调用通过线程执行，不阻塞 FastAPI 事件循环。各后端仍保留自身网络或进程超时。达到整体 deadline 后，尚未完成的任务标记为 `timeout`，已经完成的结果正常返回。

日志至少包含：

- `probe_id`
- `profile_key`
- 来源和资源 key
- 关键词
- 状态
- 候选数
- 阶段耗时和整体耗时

核心失败、超时和降级使用中文日志，不静默吞掉异常。

## 7. HTTP API

新增：

```http
POST /retrieval/probe
X-Agent-Bridge-User: <user>
Content-Type: application/json
```

请求示例：

```json
{
  "profile_key": "chengdu",
  "prompt": "之前订单同步失败最终怎么处理的？",
  "session_id": "session-123",
  "keyword_limit": 8,
  "result_limit": 3,
  "timeout_seconds": 10
}
```

响应示例：

```json
{
  "probe_id": "probe_01J...",
  "profile_key": "chengdu",
  "session_id": "session-123",
  "keywords": ["订单", "同步", "失败", "处理"],
  "source_statuses": {
    "wiki": "hit",
    "codegraph": "no_hit",
    "memory": "not_configured",
    "artifact": "hit"
  },
  "targets": [
    {
      "source_type": "wiki",
      "resource_key": "data-platform",
      "resource_name": "数据平台知识库",
      "status": "hit",
      "unique_hit_count": 5,
      "keyword_hits": [
        {
          "keyword": "订单",
          "status": "hit",
          "count": 2,
          "capped": false,
          "duration_ms": 120
        },
        {
          "keyword": "同步",
          "status": "hit",
          "count": 3,
          "capped": true,
          "duration_ms": 180
        }
      ],
      "suggested_tool": "wiki_ask"
    }
  ],
  "duration_ms": 1820
}
```

`count` 是受 `result_limit` 限制的候选数。`capped=true` 表示真实候选数可能更多，Hook 应渲染为“至少 N 条”，不得表示为精确总数。

当某类来源没有任何可访问目标时，通过 `source_statuses` 返回 `not_configured`，不创建空的伪目标。某来源存在多个目标时，只要任一目标命中，该来源总体状态就是 `hit`；否则按 `timeout`、`unavailable`、`no_hit` 的优先级汇总。

Profile 必须存在且处于 active 状态。所有资源都按当前 Profile 规则过滤，API 不接受客户端传入资源列表绕过治理。

## 8. Claude Code Hook

Hook 命令放在现有 `profile` CLI 下，避免增加新的根命令：

```bash
agent-bridge profile hook claude-code retrieval-probe \
  --profile chengdu \
  --server-url http://127.0.0.1:8765 \
  --timeout 12
```

Hook 从 stdin 读取 Claude Code `UserPromptSubmit` JSON，使用其中的：

- `prompt`
- `session_id`
- `cwd`
- `hook_event_name`

Hook 请求探测 API 后：

- 存在任意 `hit`：向 stdout 写入包含 `additionalContext` 的合法 JSON，以
  exit code 0 退出。
- 没有命中：不输出内容，以 exit code 0 退出。
- API 不可用或请求失败：不交付上下文，以 exit code 0 退出。

服务不可用的具体错误由服务端或 CLI 日志记录，不能把失败伪装成“所有来源无命中”。

`profile use` 生成的配置示例：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "shell": "bash",
            "command": "agent-bridge profile hook claude-code retrieval-probe --profile chengdu --server-url http://127.0.0.1:8765 --timeout 12 --agent-bridge-hook-id agent-bridge-retrieval-probe",
            "async": true,
            "timeout": 12
          }
        ]
      }
    ]
  }
}
```

普通 `async` 在后台运行命令，并在完成后的下一次对话轮次把
`additionalContext` 包装为 system reminder 交付；会话空闲时不会主动唤醒。
对于经 LiteLLM 转发到未知 Chat Template 的内网模型，兼容 callback 保持顶层
system 不变，并将 messages 中的 system reminder 原位置改为 user；不合并相邻
消息，不改写 `<system-reminder>` 标签。

`profile use` 写入单独的 `UserPromptSubmit` Hook entry：

```json
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
```

Agent Bridge 使用独立 hook id 清理自己安装的旧 entry，不删除 claude-mem Hook
或用户 Hook。

## 9. Hook 提醒格式

提醒只包含路由信息，不包含正文：

```text
[Agent Bridge 后台全量探测]
delivery_id: probe_01J...
这是针对当前用户请求的后台路由信息，不是新的用户请求。
不要仅回复确认；同一 delivery_id 只处理一次。

关键词「订单」：
- 产出物：命中 2 条
- Wiki「数据平台知识库」：至少命中 3 条

关键词「同步」：
- CodeGraph「order-service」：命中 1 条
- Memory「项目记忆」：命中 2 条

建议优先使用：
1. artifacts_search
2. wiki_ask(kb="data-platform")
3. codegraph_explore(repo="order-service")
```

渲染规则：

- 只展示 `hit`。
- 相同资源只在建议列表中出现一次。
- `capped=true` 显示“至少命中 N 条”。
- 输出总长度受限，不超过 Claude Code Hook 上下文上限。
- 资源名称和关键词中的控制字符、XML 标签及换行必须安全处理。
- 明确提醒 Agent 不要只确认收到，不要重复处理同一 `delivery_id`。

## 10. 审计与安全

- API 与 Hook 调用进入现有规范化审计流。
- 审计请求可记录用户 prompt，但遵守现有长 payload 安全相对引用规则。
- Probe API 使用正常的 actor header 和 Profile 治理。
- 不返回检索正文，降低敏感内容暴露和提示注入风险。
- 客户端不能指定未授权资源。
- 不把后端异常对象动态附加业务状态。

## 11. 测试

至少覆盖：

### 分词

- 中文分词。
- ASCII 标识符、路径和错误码保留。
- 停用词、单字过滤。
- 顺序去重。
- 关键词数量上限。

### 领域服务

- registry 驱动四类 adapter。
- Profile 资源过滤。
- 每个关键词独立查询。
- 同一资源多关键词命中聚合。
- 候选去重和 `capped`。
- 部分后端失败不影响其他来源。
- 整体超时返回部分结果。
- `not_configured`、`unavailable`、`timeout` 不转换为 `no_hit`。

### API

- 正常请求和响应契约。
- Profile 不存在或禁用。
- 参数边界。
- 后端部分失败。

### Hook

- stdin payload 解析。
- 有命中时 stdout JSON `additionalContext` + exit 0。
- 无命中时静默 exit 0。
- API 不可用时静默 exit 0。
- 消息长度和字符清理。
- `delivery_id` 和 Agent 消费说明。

### 回归

- `profile use` 重复执行只保留一条 retrieval-probe Hook，切换 Profile 后命令
  指向新 Profile。
- 用户已有 Hook 和 claude-mem Hook 保持不变。
- Claude Profile 引用块保留 system-reminder 语义说明。
- 现有 MCP 工具列表保持不变。

### Profile 提示词

后台探测开始承担数据源路由后，删除以下固定先后顺序：

- “用户描述需求时，优先使用 `artifacts_search`”。
- “用户询问过去做过什么时，先调用 `memory_search`”。

保留每类工具的适用范围、CodeGraph/非代码边界和可用资源列表。新增一条中性说明：
收到 Agent Bridge 后台探测结果时，根据命中资源及建议工具继续检索，不把探测数量
当作答案证据。

## 12. 文档与交付

同步更新 README 或独立集成说明，说明：

- API 请求与响应。
- `profile use` 自动安装的 Claude Code Hook 配置。
- 普通 `async` 的延迟 `additionalContext` 交付语义。
- 当前只做路由探测、不返回内容的边界。
- 如何通过重新生成或删除 Agent Bridge 管理的 Hook 停用功能。
