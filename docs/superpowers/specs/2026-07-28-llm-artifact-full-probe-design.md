# LLM 驱动的工作流产物 full-probe 设计

## 背景与目标

当前 `full-probe` 使用 Jieba 将用户问题切成多个短词，并对每个词和每个已注册
来源分别检索。对于“新开发对公基础客户明细中‘当年新开未提升’中的‘提升’指的是什么”
这类业务问题，`当年`、`新开`、`提升`等短词会带来大量无关命中。

本次将关键词产生步骤替换为一次小模型调用。模型输出 2–8 个可直接用于检索的业务
关键词或短句；只有输出经过校验后才执行检索。当前阶段只检索当前 Profile 范围内的
工作流产物，Wiki、CodeGraph 和 Memory 不参与 full-probe。

目标：

- 保留业务专名、报表名、指标名和状态短语，避免中文短词噪音。
- 使用全局共用、可在“系统配置”页面管理的 OpenAI Chat 兼容小模型连接。
- 模型未配置、调用失败、超时或输出无效时 fail-closed：不检索，也不回退 Jieba。
- full-probe 仍是后台异步 Hook，模型提取最多 10 秒、完整探测最多 20 秒。

非目标：

- 不做问答、摘要、重排或检索结果解释。
- 不支持 Profile 级模型配置、多个模型路由或模型自动选择。
- 本阶段不恢复 Wiki、CodeGraph、Memory 的 full-probe。

## 方案选择

采用独立全局“检索关键词模型”配置，而不复用文档知识后端模型、也不将配置下沉到
Profile。这样可以单独使用低成本 2B/4B 模型，并且模型连接与知识后端生命周期解耦。

配置不写入 `server.toml` 或其他文件；通过系统配置 API 写入 SQLite。API Key 的存储和
脱敏规则与已有知识后端密钥一致。

## 配置与管理接口

新增全局单例表 `retrieval_probe_llm_config`：

| 字段 | 说明 |
| --- | --- |
| `id` | 固定为 `1` 的单例主键 |
| `base_url` | OpenAI Chat 兼容 API 根地址 |
| `api_key` | 调用认证密钥 |
| `model` | 2B/4B 等小模型的模型名称 |
| `updated_at` | 统一 UTC 格式的更新时间 |

新增管理员接口：

- `GET /retrieval-probe/llm-config`：返回 `base_url`、`model`、`api_key_set` 和更新时间；
  永不返回 `api_key`。
- `PUT /retrieval-probe/llm-config`：写入地址、模型及可选 API Key。空 API Key 表示保留
  已有密钥；`clear_api_key=true` 是唯一清除密钥的方式。

系统配置页增加“全量探测关键词模型”卡片，提供上述三项编辑与保存状态；未配置、已配置
和保存错误均有明确提示。保存完成后立即生效，无需服务重启。

## 模型抽取组件

新增 `ProbeKeywordExtractor` Protocol，由 `OpenAIChatProbeKeywordExtractor` 实现。
`RetrievalProbeService` 只接收该协议，不直接耦合 HTTP、请求头或 OpenAI 响应格式。

实现读取当前 SQLite 全局配置，并以如下规则请求模型：

- `POST {base_url}/chat/completions`，`Authorization: Bearer {api_key}`。
- `model` 取配置值，`temperature=0`，使用小输出上限。
- 不使用 `response_format` 或 JSON Schema，以兼容仅实现基础 Chat Completions 的网关。
- system prompt 要求只输出 `{"keywords":[...]}`；用户问题作为独立数据输入，不能改变
  输出规则。
- prompt 要求保留原问题中的高区分度短句，优先报表名、业务专名、指标名和状态短语；禁止
  单字、泛化动词、解释、补造词和将中文专名拆分。

示例输入应得到：

```json
{"keywords":["新开发对公基础客户明细","当年新开未提升"]}
```

服务端只接受严格 JSON 对象。解析和归一化规则如下：

- `keywords` 必须是数组，数量为 2–8。
- 去首尾空白后按原顺序、大小写无关去重。
- 每项必须是非空可检索短句，过滤停用词、单字和过长项；不接受解释或额外字段文本。
- 任何格式或数量错误均视为整个抽取失败，不能用其中一部分继续检索。

不执行自动重试，以免在后台 Hook 的固定预算内放大延迟和负载。

## 探测流与时限

```text
UserPromptSubmit（异步）
  -> full-probe 总预算 20 秒
  -> 读取 retrieval_probe_llm_config
  -> OpenAI Chat 关键词抽取（最多 10 秒）
  -> 校验 2–8 个短句
  -> 仅 ArtifactProbeAdapter 枚举并检索 current 工作流产物（剩余预算）
  -> 既有 reminder / Hook 审计输出
```

`RetrievalProbeService` 的默认及最大 `timeout_seconds` 调整为 20 秒。调用方传入更小
预算时可以缩短总预算；关键词抽取的实际等待值为 `min(10 秒, 剩余预算)`。

`profile use` 写入的 Claude Code Hook 同步调整：服务端传入 20 秒、CLI HTTP 等待至少
22 秒、Claude Code command Hook 等待至少 25 秒。这样外层不会在服务端 deadline 前取消
请求，同时依然保持 `async: true`。

应用装配阶段仅注册 `ArtifactProbeAdapter` 到 retrieval probe registry。Artifact 检索保持
现有 Profile 权限与 current 产物范围规则；短句依次探测、候选继续去重。其他来源的
adapter 不删除，但本功能阶段不装配到该 registry。

## 失败、响应与审计

关键词抽取状态为 `success`、`not_configured`、`timeout`、`unavailable` 或
`invalid_output`。除 `success` 外，probe 返回正常的空结果、Hook 输出 NOOP，且不创建
任何 Artifact 检索任务。

`ProbeResponse` 增加脱敏的 `keyword_extraction` 字段，包含状态、模型名、耗时和错误类型；
仅在成功时包含最终短句。原有 `keywords` 字段在成功时为最终短句，失败时为空。响应与
审计绝不包含 API Key、认证头或模型原始响应。

继续保留原始 Hook payload 的既有审计行为。审计新增关键词抽取状态、模型名、短句、耗时
和失败类别，方便管理员定位配置或网关问题；不会将模型返回的解释性文本写入日志。

## 验证

- SQLite 单例配置的默认读取、创建、更新、清除 API Key 和 API 脱敏测试。
- 系统配置页面的加载、保存、保留密钥和明确清除密钥行为测试。
- OpenAI Chat 请求的 URL、认证头、模型、低随机性参数、超时和 JSON 负载测试。
- 示例问题输出两个业务短句的解析测试。
- 无效 JSON、非数组、少于 2 条、多于 8 条、重复项、停用词、超时、未配置和网络失败测试。
- 任一抽取失败时断言 `ArtifactProbeAdapter.probe` 未被调用。
- 成功时断言 registry 只包含 artifact，响应不再包含 wiki/codegraph/memory 状态。
- 20 秒服务 deadline、10 秒抽取子 deadline，以及 CLI/Claude Hook 外层等待值测试。
- 文档和 API 示例更新，明确“仅工作流产物”和 fail-closed 策略。
