# 知识库检索策略与适配层设计

日期: 2026-06-10

## 背景

当前 agent-bridge 的知识库检索/对话流程不区分 Weknora agent。每个 KB 在 `ask()` 时通过 `knowledge-chat` 端点调用，但没有指定 agent_id，无法利用 Weknora 的混合检索、Wiki 研究等智能体能力。此外，多后端场景下（RagFlow + Weknora）缺少统一的检索编排层。

## 已验证的关键 API 事实

- `POST /api/v1/knowledge-chat/{session_id}` **支持** `agent_enabled` + `agent_id` 参数
- `POST /api/v1/knowledge-search` 不涉及 agent，纯检索不变
- 无需切换到 `agent-chat` 端点，`knowledge-chat` 即可完成 agent 对话
- Weknora agent 列表接口: `GET /api/v1/agents`
- Weknora agent 预设接口: `GET /api/v1/agents/type-presets`
- Weknora 创建 agent 接口: `POST /api/v1/agents`，body 包含 `name`, `description`, `is_builtin`, `config`

---

## 功能一：自动新建 Weknora 混合智能体

### 需求

- Weknora 自带 builtin-smart-reasoning、builtin-wiki-researcher 等内置智能体，但不包含 hybrid-rag-wiki 类型
- 如果 Weknora 中不存在 `agent_type == "hybrid-rag-wiki"` 的智能体，需自动创建一个，名称固定为 "AgentBridge混合智能体"
- 创建使用 `/api/v1/agents/type-presets` 中的 hybrid-rag-wiki 预设配置

### 设计

#### WeknoraBackend 新增方法

```python
class WeknoraBackend:
    # 已有方法不变...

    def list_agents(self) -> list[dict]:
        """GET /api/v1/agents — 返回所有智能体列表"""
        ...

    def get_type_presets(self) -> list[dict]:
        """GET /api/v1/agents/type-presets — 返回预设配置列表"""
        ...

    def create_agent(self, name: str, preset_config: dict) -> dict:
        """POST /api/v1/agents — 创建智能体，返回完整 agent 对象"""
        ...

    def ensure_hybrid_agent(self) -> str:
        """确保 hybrid-rag-wiki 智能体存在，返回 agent_id"""
        ...
```

#### ensure_hybrid_agent() 逻辑

```
1. GET /api/v1/agents
2. 遍历 agents，查找 config.agent_type == "hybrid-rag-wiki"
3. 如果找到 → 缓存 agent_id 到 self._hybrid_agent_id，返回
4. 如果没找到:
   a. GET /api/v1/agents/type-presets
   b. 找到 id == "hybrid-rag-wiki" 的预设
   c. 用预设 config + name="AgentBridge混合智能体" + is_builtin=False
   d. POST /api/v1/agents 创建
   e. 缓存 agent_id，返回
```

#### 触发时机

- `AgentBridgeService` 初始化 registry 后，对每个 Weknora 后端调用 `ensure_hybrid_agent()`
- 如果初始化失败（Weknora 不可达），延迟到第一次 `ask()` 调用时重试
- `agent_id` 缓存在 WeknoraBackend 实例变量中，不持久化（每次启动重新确认）

#### ask() 方法改造

当前 `WeknoraBackend.ask()` 调用 `POST /api/v1/knowledge-chat/{session_id}`，body 为:
```json
{"query": "...", "knowledge_base_ids": ["..."], "disable_title": true, "channel": "api"}
```

改造后，增加 `agent_enabled` + `agent_id` 参数:
```json
{
  "query": "...",
  "knowledge_base_ids": ["..."],
  "disable_title": true,
  "channel": "api",
  "agent_enabled": true,
  "agent_id": "hybrid-rag-wiki 的 agent id"
}
```

#### BackendAdapter 协议变更

`ask()` 签名增加可选参数:
```python
def ask(
    self,
    backend_kb_id: str,
    question: str,
    chat_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,  # 新增
) -> tuple[AskResult, str]:
```

`agent_id` 为 None 时，不传 agent 参数（兼容现有行为）。

---

## 功能二：知识库默认检索策略

### 需求

- 每个 KB 可设置默认检索后端（backend_slug）和默认 agent（agent_id）
- KB 配置到能力平面时，可覆盖后端和 agent
- 如果平面未覆盖，使用 KB 自身的默认值
- 如果 KB 也未设置，使用第一个活跃 backend_target + hybrid-rag-wiki agent

### 数据模型变更

#### knowledge_bases 表新增列

| 列名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `default_backend_slug` | TEXT | NULL | 默认检索后端 slug，引用 backends.slug |
| `default_agent_id` | TEXT | NULL | 默认 Weknora agent ID，仅 weknora 后端有效 |

#### profile_resource_rules 表新增列

| 列名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `retrieval_backend_slug` | TEXT | NULL | 平面级后端覆盖 |
| `retrieval_agent_id` | TEXT | NULL | 平面级 agent 覆盖 |

### 检索策略解析链

```python
def resolve_retrieval_strategy(kb_slug: str, profile_key: str | None) -> RetrievalStrategy:
    """
    解析检索策略，返回 (backend_slug, agent_id)
    优先级: profile 覆盖 > KB 默认 > 系统兜底
    """
    kb = store.get_kb(kb_slug)

    # 1. 如果有 profile，查 profile_resource_rules
    if profile_key:
        rule = store.get_profile_resource_rule(profile_key, "wiki_kb", kb_slug)
        if rule:
            backend = rule.retrieval_backend_slug or kb.default_backend_slug
            agent = rule.retrieval_agent_id or kb.default_agent_id
            return RetrievalStrategy(backend, agent)

    # 2. 回退到 KB 默认
    if kb.default_backend_slug:
        return RetrievalStrategy(kb.default_backend_slug, kb.default_agent_id)

    # 3. 系统兜底
    active_target = first_active_target(kb)
    agent = "hybrid-rag-wiki" if active_target.backend_type == "weknora" else None
    return RetrievalStrategy(active_target.backend_slug, agent)
```

### API 变更

#### 更新 KB

`PUT /api/kbs/{slug}` 新增字段:
```json
{
  "default_backend_slug": "weknora",
  "default_agent_id": "builtin-wiki-researcher"
}
```

#### 更新平面-KB 关联

`PUT /resource-profiles/{resource_type}/{resource_key}` 新增字段:
```json
{
  "profile_keys": ["profile-a"],
  "overrides": {
    "profile-a": {
      "retrieval_backend_slug": "ragflow",
      "retrieval_agent_id": null
    }
  }
}
```

### retrieve() 与 ask() 的区别

- `retrieve()` (纯检索): 只受 `default_backend_slug` 影响，不使用 agent
- `ask()` (对话): 同时受 backend 和 agent 影响

### 前端变更

#### KB 设置

在 KB 编辑/详情中新增:
- "默认检索后端"下拉框: 列出已配置的所有后端（weknora / ragflow）
- "默认 Agent"下拉框: 仅在默认后端为 weknora 时显示，列出可用 agent（builtin-smart-reasoning / builtin-wiki-researcher / hybrid-rag-wiki），默认选 hybrid-rag-wiki

#### 能力平面分配

在平面-KB 关联中，每行 KB 旁增加可选的后端/agent 覆盖:
- "检索后端覆盖" (可选)
- "Agent 覆盖" (可选，仅当选了 weknora 后端时显示)

---

## 功能三：知识库适配层（大致思路）

### 目标

上层 MCP 工具和能力平面只表达检索意图，不感知后端差异。适配层负责路由、拆分、合并、归一。

### 架构

```
┌─────────────────────────────────────────────┐
│  上层：WikiBuiltinProvider / MCP 工具       │
│  调用：retrieve(kbs, query, mode)            │
│        ask(kbs, query, mode, agent_id)       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  适配层 (KnowledgeAdapter)                  │
│                                             │
│  职责:                                      │
│  1. 策略解析 → 确定每个 KB 用哪个后端/agent │
│  2. 请求分组 → 按 backend 分组              │
│     • Weknora: 支持多 KB → 一次调用         │
│     • RagFlow: 仅单 KB → 拆成多次调用       │
│  3. 结果合并 → 去重 + 重排序 + 截断         │
│  4. 格式归一 → 统一 RetrievalResult 格式    │
│  5. 来源归一 → 统一引用格式                 │
│  6. 异常降级 → 某后端失败不影响整体          │
│  7. 后续扩展 → 新后端只需实现 BackendAdapter│
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  BackendRegistry + BackendAdapter           │
│  (WeknoraBackend / RagFlowBackend / ...)    │
└─────────────────────────────────────────────┘
```

### 关键设计点

1. **按 backend 分组**: 多个 KB 可能分布在不同后端。适配层按 backend_slug 分组，同组 KB 尽量批量调用（Weknora 支持多 KB），异组分开调用。
2. **RagFlow 单库拆分**: RagFlow 只支持单 KB 检索 → 适配层自动拆为 N 次调用再合并。
3. **结果合并策略**: 按 similarity 排序去重，取 top_k；来源标注后端类型。
4. **异常隔离**: 某个后端超时/报错 → 该组返回空结果 + 日志告警，不影响其他组。
5. **增量实现**: 本次先实现策略解析 + 按 backend 分组 + 多库拆分 + 结果合并。格式归一和来源引用可后续迭代。

### 与现有代码的关系

- `AgentBridgeService._resolve_retrieval_target()` 目前只解析单个 target → 扩展为 `KnowledgeAdapter`，支持多 target 编排
- `WikiBuiltinProvider` 调用适配层而非直接调 `AgentBridgeService`
- `BackendAdapter` 协议保持不变，适配层在其之上编排

### 本次范围

本次只实现功能一和功能二。功能三作为后续迭代目标，本次不编码实现。

---

## 影响范围

### 后端文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `knowledge/backends/weknora.py` | 修改 | 新增 agent 管理方法，ask() 增加 agent 支持 |
| `core/domain.py` | 修改 | BackendAdapter.ask() 签名增加 agent_id |
| `core/domain.py` | 修改 | 新增 RetrievalStrategy dataclass |
| `knowledge/backends/ragflow.py` | 修改 | ask() 签名对齐（agent_id 忽略） |
| `knowledge/backends/mock.py` | 修改 | ask() 签名对齐 |
| `storage/schema.py` | 修改 | knowledge_bases 新增列，profile_resource_rules 新增列 |
| `storage/repositories/knowledge.py` | 修改 | 新增列的 CRUD |
| `storage/repositories/governance.py` | 修改 | profile_resource_rules 新增列 |
| `knowledge/service.py` | 修改 | 集成 ensure_hybrid_agent，解析检索策略 |
| `api/routes/knowledge.py` | 修改 | KB 更新接口支持新字段 |
| `api/routes/governance.py` | 修改 | 平面-KB 关联接口支持覆盖 |

### 前端文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `KnowledgeView.vue` | 修改 | KB 设置中增加默认后端/agent 选择 |
| 平面分配对话框 | 修改 | 增加后端/agent 覆盖 |
| `api/client.ts` | 修改 | API 调用增加新字段 |
