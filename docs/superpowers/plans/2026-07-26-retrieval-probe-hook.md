# Retrieval Probe Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个按 Profile 对 Wiki、CodeGraph、Memory、Artifact 执行多关键词全量轻量探测的 API，以及一个通过 Claude Code `asyncRewake` 交付命中路由的手工 Hook 命令。

**Architecture:** 新增独立 `retrieval_probe` 领域包，通过 `RetrievalProbeAdapter` Protocol 和 registry 注册四类来源；`RetrievalProbeService` 负责分词、并发、deadline、聚合和审计。FastAPI 只做请求校验与领域服务调用，CLI Hook 只做 Claude Code stdin、HTTP API、路由提醒和退出码之间的转换。

**Tech Stack:** Python 3.11、FastAPI、Typer、httpx、asyncio、jieba、pytest、pytest-asyncio。

## Global Constraints

- 不修改 `profile use`，不自动安装或删除 Hook。
- 不修改 `.claude/settings.json` 或 `.claude/settings.local.json`。
- 不改变现有 MCP 工具暴露和 Profile 提示词。
- 不调用 `wiki_ask` 或 CodeGraph Explore。
- 不返回正文、片段或候选标题。
- 不增加前端页面、数据库表、持久化配置或 LLM 查询改写。
- 应用层只编排领域服务和 adapter，不依据来源类型编写 `isinstance` 或 `if/elif` 分发。
- 中文查询拆成多个独立关键词分别查询，不拼成一个多词 `AND` 查询。
- 整体探测默认 10 秒，默认关键词上限 8，单关键词单资源默认最多返回 3 个候选，并发上限 8。
- 错误必须区分 `no_hit`、`not_configured`、`unavailable` 和 `timeout`，不能把后端失败伪装成无命中。
- 时间戳若有持久化需求必须使用 `agent_bridge.core.timeutil`；持续时间统一使用 monotonic 时钟。
- 日志使用 `logging.getLogger(__name__)` 和 `%s` 惰性参数，核心生命周期、失败和降级日志使用中文。
- 只提交当前功能涉及的文件，不覆盖或清理其他未提交修改。

---

## File Structure

### 新建

- `src/agent_bridge/knowledge_management/retrieval_probe/__init__.py`：导出领域公开类型和创建 registry 的入口。
- `src/agent_bridge/knowledge_management/retrieval_probe/models.py`：状态枚举、target、单关键词结果、资源汇总和响应模型。
- `src/agent_bridge/knowledge_management/retrieval_probe/tokenizer.py`：中文和 ASCII 确定性关键词提取。
- `src/agent_bridge/knowledge_management/retrieval_probe/adapters.py`：Adapter Protocol，以及 Wiki、CodeGraph、Memory、Artifact 四类实现。
- `src/agent_bridge/knowledge_management/retrieval_probe/registry.py`：adapter 注册、唯一性校验和稳定遍历。
- `src/agent_bridge/knowledge_management/retrieval_probe/service.py`：Profile 校验、并发 deadline、聚合、来源状态和审计。
- `src/agent_bridge/api/routes/retrieval_probe.py`：`POST /retrieval/probe` 请求模型与路由。
- `src/agent_bridge/cli/profile_hooks.py`：手工 Claude Code Hook 命令与提醒渲染。
- `tests/test_retrieval_probe_tokenizer.py`：分词边界。
- `tests/test_retrieval_probe_adapters.py`：四类 adapter 的资源过滤与候选映射。
- `tests/test_retrieval_probe_service.py`：并发、deadline、聚合和来源状态。
- `tests/test_retrieval_probe_api.py`：API 契约。
- `tests/test_retrieval_probe_hook.py`：CLI stdin、提醒、安全清理和退出码。
- `docs/integrations/retrieval-probe-hook/README.md`：API 和手工 Hook 配置说明。

### 修改

- `src/agent_bridge/app/service.py`：装配 registry、adapter 和 `RetrievalProbeService`，不承载探测逻辑。
- `src/agent_bridge/api/app.py`：注册 retrieval probe router，并同步动态 admin 集合。
- `src/agent_bridge/client.py`：增加 `probe_retrieval()`。
- `src/agent_bridge/cli/profile.py`：挂载独立的 `profile hook` 子应用，不修改 `profile use` 安装逻辑。
- `README.md`：增加独立 API/手工 Hook 文档入口。
- `CLAUDE.md`：记录新领域服务和“未接入 profile use”的兼容边界。

---

### Task 1: 领域模型与确定性分词

**Files:**

- Create: `src/agent_bridge/knowledge_management/retrieval_probe/__init__.py`
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/models.py`
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/tokenizer.py`
- Create: `tests/test_retrieval_probe_tokenizer.py`

**Interfaces:**

- Produces: `ProbeStatus`, `ProbeTarget`, `KeywordProbeResult`, `TargetProbeSummary`, `ProbeResponse`。
- Produces: `extract_probe_keywords(text: str, limit: int = 8) -> list[str]`。
- `KeywordProbeResult.candidate_keys` 只用于服务端跨关键词去重，`to_payload()` 不返回它。

- [ ] **Step 1: 写分词失败测试**

```python
from agent_bridge.knowledge_management.retrieval_probe.tokenizer import extract_probe_keywords


def test_extract_probe_keywords_splits_chinese_and_keeps_identifiers():
    assert extract_probe_keywords(
        "之前订单同步失败，检查 OrderSyncService 和 src/order_sync.py 的 ERR-1042"
    ) == [
        "订单",
        "同步",
        "失败",
        "OrderSyncService",
        "src/order_sync.py",
        "ERR-1042",
    ]


def test_extract_probe_keywords_deduplicates_and_applies_limit():
    assert extract_probe_keywords("订单 订单 同步 失败 补偿", limit=3) == ["订单", "同步", "失败"]


def test_extract_probe_keywords_rejects_invalid_limit():
    with pytest.raises(ValueError, match="limit must be positive"):
        extract_probe_keywords("订单", limit=0)
```

- [ ] **Step 2: 运行测试，确认因模块不存在失败**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_tokenizer.py
```

Expected: FAIL，提示 `agent_bridge.knowledge_management.retrieval_probe` 不存在。

- [ ] **Step 3: 实现模型和最小分词器**

在 `models.py` 定义：

```python
class ProbeStatus(str, Enum):
    hit = "hit"
    no_hit = "no_hit"
    not_configured = "not_configured"
    unavailable = "unavailable"
    timeout = "timeout"


@dataclass(frozen=True)
class ProbeTarget:
    source_type: str
    resource_key: str
    resource_name: str
    suggested_tool: str


@dataclass(frozen=True)
class KeywordProbeResult:
    target: ProbeTarget
    keyword: str
    status: ProbeStatus
    candidate_keys: tuple[str, ...] = ()
    count: int = 0
    capped: bool = False
    duration_ms: int = 0
    error_type: str | None = None
```

`TargetProbeSummary.to_payload()` 和 `ProbeResponse.to_payload()` 必须只序列化 API 字段，不能包含 `candidate_keys` 或异常正文。

在 `tokenizer.py`：

- 复用项目已有的中文/ASCII segment 思路。
- 中文使用 `jieba.lcut`。
- 保留 `[A-Za-z0-9_./:-]+`。
- 去除明确的问句停用词，例如“之前”“最终”“怎么”“如何”“什么”“一下”“请”“帮我”“的”“了”。
- 中文单字默认过滤。
- 按原始顺序去重并截断。

- [ ] **Step 4: 运行分词测试**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_tokenizer.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add \
  src/agent_bridge/knowledge_management/retrieval_probe/__init__.py \
  src/agent_bridge/knowledge_management/retrieval_probe/models.py \
  src/agent_bridge/knowledge_management/retrieval_probe/tokenizer.py \
  tests/test_retrieval_probe_tokenizer.py
git commit -m "feat(retrieval): add probe models and tokenizer"
```

---

### Task 2: 四类来源 Adapter 与 Registry

**Files:**

- Create: `src/agent_bridge/knowledge_management/retrieval_probe/adapters.py`
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/registry.py`
- Create: `tests/test_retrieval_probe_adapters.py`

**Interfaces:**

- Consumes: Task 1 的 `ProbeTarget`、`KeywordProbeResult`、`ProbeStatus`。
- Produces:

```python
@runtime_checkable
class RetrievalProbeAdapter(Protocol):
    source_type: str

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]: ...

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult: ...
```

- Produces: `WikiProbeAdapter`、`CodeGraphProbeAdapter`、`MemoryProbeAdapter`、`ArtifactProbeAdapter`。
- Produces: `RetrievalProbeRegistry.register(adapter)` 和 `RetrievalProbeRegistry.list() -> tuple[RetrievalProbeAdapter, ...]`。

- [ ] **Step 1: 写 registry 与 adapter 失败测试**

测试必须使用 fake collaborator，禁止依赖真实 Wiki、CodeGraph CLI 或 claude-mem：

```python
def test_registry_rejects_duplicate_source_type():
    registry = RetrievalProbeRegistry()
    registry.register(FakeAdapter("wiki"))
    with pytest.raises(ValueError, match="duplicate retrieval probe adapter"):
        registry.register(FakeAdapter("wiki"))


def test_wiki_adapter_lists_only_profile_allowed_kbs_and_maps_chunks():
    adapter = WikiProbeAdapter(
        store=FakeStore(kbs=[{"slug": "allowed", "name": "Allowed"}, {"slug": "blocked", "name": "Blocked"}]),
        governance=FakeGovernance(allowed=["allowed"]),
        search=lambda **kwargs: [
            RetrievalResult("c1", "body", "doc", 0.8, "d1"),
            RetrievalResult("c2", "body", "doc", 0.7, "d1"),
        ],
    )
    targets = adapter.list_targets(actor="root", profile_key="dev")
    result = adapter.probe(
        actor="root", profile_key="dev", target=targets[0], keyword="订单", limit=2
    )
    assert [target.resource_key for target in targets] == ["allowed"]
    assert result.candidate_keys == ("c1", "c2")
    assert result.capped is True
```

其余测试覆盖：

- CodeGraph 只枚举 active 且 Profile allow 的 repo，候选 key 使用稳定节点标识。
- Memory 未绑定返回空 target；worker `status != ok` 映射为 `unavailable`。
- Artifact 只使用 `profile_key`、`include_history=False`，候选 key 使用 `artifact_id`。
- 每个 adapter 的 `suggested_tool` 分别为 `wiki_ask`、`codegraph_explore`、`memory_search`、`artifacts_search`。

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_adapters.py
```

Expected: FAIL，提示 adapter/registry 尚未定义。

- [ ] **Step 3: 实现 Protocol、registry 和四类 adapter**

要求：

- `list_targets()` 只负责 Profile 可见资源解析。
- `probe()` 只查询一个 target 的一个 keyword。
- adapter 将预期中的后端无命中映射为 `no_hit`。
- adapter 不捕获并吞掉未知异常；由 Task 3 的服务统一映射 `unavailable` 并记录日志。
- Memory worker 返回 `worker_error` 时显式返回 `unavailable`。
- `capped = count >= limit`。
- Wiki 候选 key 优先 `chunk_id`。
- CodeGraph 候选 key 使用节点类型、名称和文件路径组成的稳定字符串。
- Artifact 候选 key 使用 `artifact_id`。
- Memory 候选 key 优先 observation `id`，缺失时使用内容哈希。

- [ ] **Step 4: 运行 adapter 测试**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_adapters.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add \
  src/agent_bridge/knowledge_management/retrieval_probe/adapters.py \
  src/agent_bridge/knowledge_management/retrieval_probe/registry.py \
  tests/test_retrieval_probe_adapters.py
git commit -m "feat(retrieval): add probe source adapters"
```

---

### Task 3: 并发探测领域服务与应用装配

**Files:**

- Create: `src/agent_bridge/knowledge_management/retrieval_probe/service.py`
- Create: `tests/test_retrieval_probe_service.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/__init__.py`
- Modify: `src/agent_bridge/app/service.py`

**Interfaces:**

- Consumes: Task 1/2 的模型、分词器和 registry。
- Produces:

```python
class RetrievalProbeService:
    async def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        prompt: str,
        session_id: str = "",
        keyword_limit: int = 8,
        result_limit: int = 3,
        timeout_seconds: float = 10.0,
    ) -> ProbeResponse: ...
```

- Produces: `AgentBridgeService.retrieval_probe`。

- [ ] **Step 1: 写并发、聚合和 deadline 失败测试**

```python
@pytest.mark.asyncio
async def test_probe_queries_every_keyword_and_target_and_deduplicates_candidates():
    adapter = FakeAdapter(
        source_type="wiki",
        targets=[ProbeTarget("wiki", "kb-a", "KB A", "wiki_ask")],
        results={
            "订单": ("c1", "c2"),
            "同步": ("c2", "c3"),
        },
    )
    service = RetrievalProbeService(
        registry=registry_with(adapter),
        governance=FakeGovernance(active_profile=True),
        concurrency=8,
    )
    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单同步",
        keyword_limit=8,
        result_limit=3,
        timeout_seconds=1,
    )
    assert adapter.calls == [("kb-a", "订单"), ("kb-a", "同步")]
    assert response.targets[0].unique_hit_count == 3
    assert response.source_statuses["wiki"] is ProbeStatus.hit


@pytest.mark.asyncio
async def test_probe_returns_partial_results_and_marks_slow_jobs_timeout():
    fast = FakeAdapter("artifact", delay=0, candidate_keys=("a1",))
    slow = FakeAdapter("wiki", delay=0.2, candidate_keys=("w1",))
    response = await service_with(fast, slow).probe(
        actor="root", profile_key="dev", prompt="订单", timeout_seconds=0.05
    )
    assert response.source_statuses["artifact"] is ProbeStatus.hit
    assert response.source_statuses["wiki"] is ProbeStatus.timeout
```

补充测试：

- Profile 不存在抛 `NotFound`，禁用抛 `ValidationError`。
- adapter 无 target 时来源状态为 `not_configured`。
- 单 target 异常标记 `unavailable`，其他来源正常返回。
- 来源状态优先级为 `hit > timeout > unavailable > no_hit > not_configured`。
- 参数边界：空 prompt、非正数 limit/timeout。
- 审计写入 `entrypoint="retrieval_probe"`、`source_type="hook"`、`tool_name="full_probe"`，长 prompt 不直接塞入短响应日志。

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_service.py
```

Expected: FAIL，提示 `RetrievalProbeService` 不存在。

- [ ] **Step 3: 实现并发服务**

实现要求：

```python
async def _run_job(adapter, target, keyword):
    async with semaphore:
        started = time.monotonic()
        return await asyncio.to_thread(
            adapter.probe,
            actor=actor,
            profile_key=profile_key,
            target=target,
            keyword=keyword,
            limit=result_limit,
        )
```

- 为所有 adapter 先调用 `list_targets()`，保留没有 target 的来源。
- 使用一个整体 `asyncio.timeout(timeout_seconds)` 或等价 wait/deadline。
- deadline 后未完成的 job 输出 `timeout`；底层线程可能继续结束，但结果不得重新写入已返回响应。
- 聚合时按 `candidate_keys` 集合计算 `unique_hit_count`。
- `source_statuses` 必须始终包含 registry 中的全部来源。
- 使用 `new_run_id("probe")` 生成 `probe_id`。
- 使用 `time.monotonic()` 记录耗时。
- 未知异常记录中文 warning，并映射为 `unavailable`，只暴露 `error_type`，不在 API 返回异常堆栈。

- [ ] **Step 4: 在 `AgentBridgeService` 只做装配**

装配顺序：

```python
registry = RetrievalProbeRegistry()
registry.register(WikiProbeAdapter(...))
registry.register(CodeGraphProbeAdapter(...))
registry.register(MemoryProbeAdapter(...))
registry.register(ArtifactProbeAdapter(...))
self.retrieval_probe = RetrievalProbeService(
    registry=registry,
    governance=self.governance,
)
```

Wiki adapter 通过注入 callable 调用现有 `AgentBridgeService.search()`；其他 adapter 分别依赖现有 `codegraph`、`memory` 和 `workflows` 服务。门面不增加检索算法。

- [ ] **Step 5: 运行领域测试和现有服务装配测试**

Run:

```bash
uv run pytest -q \
  tests/test_retrieval_probe_service.py \
  tests/test_services.py \
  tests/test_server.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add \
  src/agent_bridge/knowledge_management/retrieval_probe/__init__.py \
  src/agent_bridge/knowledge_management/retrieval_probe/service.py \
  src/agent_bridge/app/service.py \
  tests/test_retrieval_probe_service.py
git commit -m "feat(retrieval): add concurrent probe service"
```

---

### Task 4: 独立 Probe API 与客户端

**Files:**

- Create: `src/agent_bridge/api/routes/retrieval_probe.py`
- Create: `tests/test_retrieval_probe_api.py`
- Modify: `src/agent_bridge/api/app.py`
- Modify: `src/agent_bridge/client.py`

**Interfaces:**

- Consumes: `AgentBridgeService.retrieval_probe.probe(...)`。
- Produces: `POST /retrieval/probe`。
- Produces:

```python
AgentBridgeClient.probe_retrieval(
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]
```

- [ ] **Step 1: 写 API 失败测试**

```python
def test_retrieval_probe_api_returns_structured_payload(wm_paths, monkeypatch):
    app = create_app(paths=wm_paths, admins={"root"})

    async def fake_probe(**kwargs):
        return ProbeResponse(
            probe_id="probe_test",
            profile_key="dev",
            session_id="session-1",
            keywords=("订单",),
            source_statuses={"wiki": ProbeStatus.hit},
            targets=(target_summary(),),
            duration_ms=12,
        )

    monkeypatch.setattr(app.state.agent_bridge_service.retrieval_probe, "probe", fake_probe)
    response = TestClient(app).post(
        "/retrieval/probe",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "profile_key": "dev",
            "prompt": "订单",
            "session_id": "session-1",
            "keyword_limit": 8,
            "result_limit": 3,
            "timeout_seconds": 10,
        },
    )
    assert response.status_code == 200
    assert response.json()["probe_id"] == "probe_test"
    assert response.json()["targets"][0]["suggested_tool"] == "wiki_ask"
```

补充边界测试：

- 缺少 `profile_key` 或 `prompt` 返回 422。
- `keyword_limit` 范围 1～32。
- `result_limit` 范围 1～20。
- `timeout_seconds` 范围 0.1～30。
- 领域 `NotFound`、`ValidationError` 继续使用全局异常处理。
- client 使用传入 timeout，并发送 `X-Agent-Bridge-User`。

- [ ] **Step 2: 运行测试，确认 404/方法不存在**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_api.py
```

Expected: FAIL，API 返回 404 或 client 方法不存在。

- [ ] **Step 3: 实现 router 和 client**

路由：

```python
class RetrievalProbeRequest(BaseModel):
    profile_key: str
    prompt: str
    session_id: str = ""
    keyword_limit: int = Field(default=8, ge=1, le=32)
    result_limit: int = Field(default=3, ge=1, le=20)
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=30.0)


@router.post("/retrieval/probe")
async def probe(payload: RetrievalProbeRequest, current_actor: str = Depends(actor)):
    result = await service.retrieval_probe.probe(
        actor=current_actor,
        **payload.model_dump(),
    )
    return result.to_payload()
```

在 `create_app()` 中 include 新 router。`AgentBridgeClient.probe_retrieval()` 使用 `_request("POST", "/retrieval/probe", json=payload, timeout=timeout)`。

- [ ] **Step 4: 运行 API 测试**

Run:

```bash
uv run pytest -q \
  tests/test_retrieval_probe_api.py \
  tests/test_server.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add \
  src/agent_bridge/api/routes/retrieval_probe.py \
  src/agent_bridge/api/app.py \
  src/agent_bridge/client.py \
  tests/test_retrieval_probe_api.py
git commit -m "feat(api): expose retrieval probe endpoint"
```

---

### Task 5: 手工 Claude Code asyncRewake Hook

**Files:**

- Create: `src/agent_bridge/cli/profile_hooks.py`
- Create: `tests/test_retrieval_probe_hook.py`
- Modify: `src/agent_bridge/cli/profile.py`

**Interfaces:**

- Consumes: `AgentBridgeClient.probe_retrieval(payload, timeout=...)`。
- Produces CLI:

```text
agent-bridge profile hook claude-code retrieval-probe
  --profile <profile>
  --server-url <url>
  --timeout <seconds>
```

- Produces:

```python
render_probe_reminder(payload: dict[str, Any], *, max_chars: int = 8000) -> str
```

- [ ] **Step 1: 写 CLI Hook 失败测试**

```python
def test_probe_hook_posts_user_prompt_and_rewakes_on_hit(monkeypatch):
    captured = {}

    class FakeClient:
        def probe_retrieval(self, payload, *, timeout):
            captured["payload"] = payload
            captured["timeout"] = timeout
            return hit_probe_payload()

    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )
    result = runner.invoke(
        app,
        [
            "profile", "hook", "claude-code", "retrieval-probe",
            "--profile", "dev",
            "--server-url", "http://bridge.example",
            "--timeout", "12",
        ],
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "cwd": "/repo",
            "prompt": "订单同步失败",
        }),
    )
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "delivery_id: probe_test" in result.stderr
    assert captured["payload"]["prompt"] == "订单同步失败"
```

补充测试：

- 无 `hit` 时 stdout/stderr 均为空，exit 0。
- API 抛异常时静默 exit 0。
- 空 prompt 或非 `UserPromptSubmit` 时不请求 API，exit 0。
- `capped=true` 渲染“至少命中 N 条”。
- 相同 suggested tool/resource 只建议一次。
- 资源名和关键词中的换行、`<system-reminder>`、控制字符被压平成安全单行。
- 输出超过 `max_chars` 时在完整条目边界截断并带“其余结果已省略”，不得截断 UTF-8 字符。
- reminder 包含“不是新的用户请求”“不要仅回复确认”“同一 delivery_id 只处理一次”。

- [ ] **Step 2: 运行测试，确认命令不存在**

Run:

```bash
uv run pytest -q tests/test_retrieval_probe_hook.py
```

Expected: FAIL，Typer 报 `No such command 'hook'`。

- [ ] **Step 3: 实现独立 Hook 子应用**

CLI 层级：

```python
profile_hook_app = typer.Typer(...)
claude_code_hook_app = typer.Typer(...)
profile_hook_app.add_typer(claude_code_hook_app, name="claude-code")
profile_app.add_typer(profile_hook_app, name="hook")
```

命令处理：

```python
raw = sys.stdin.read()
payload = json.loads(raw) if raw.strip() else {}
if payload.get("hook_event_name") != "UserPromptSubmit" or not str(payload.get("prompt") or "").strip():
    raise typer.Exit(0)

response = AgentBridgeClient(server_url, default_user(getpass.getuser())).probe_retrieval(
    {
        "profile_key": profile,
        "prompt": payload["prompt"],
        "session_id": str(payload.get("session_id") or ""),
        "keyword_limit": 8,
        "result_limit": 3,
        "timeout_seconds": timeout,
    },
    timeout=float(timeout + 2),
)
message = render_probe_reminder(response)
if not message:
    raise typer.Exit(0)
typer.echo(message, err=True)
raise typer.Exit(2)
```

捕获 JSON、HTTP 和 RuntimeError 后静默 exit 0；使用 logger 写 warning，不向 stdout 输出 fallback JSON。

- [ ] **Step 4: 挂载子应用但不修改 profile use**

仅在 `src/agent_bridge/cli/profile.py` 导入并挂载 `profile_hook_app`。不得修改：

- `CLAUDE_MEM_COMPATIBLE_HOOKS`
- `_install_memory_hooks()`
- `_write_memory_hooks()`
- `profile use` 的配置写入逻辑

- [ ] **Step 5: 运行 Hook 与 profile use 回归测试**

Run:

```bash
uv run pytest -q \
  tests/test_retrieval_probe_hook.py \
  tests/test_memory_cli.py \
  tests/test_cli.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add \
  src/agent_bridge/cli/profile_hooks.py \
  src/agent_bridge/cli/profile.py \
  tests/test_retrieval_probe_hook.py
git commit -m "feat(cli): add manual retrieval probe hook"
```

---

### Task 6: 文档、审计检查与整体验证

**Files:**

- Create: `docs/integrations/retrieval-probe-hook/README.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: tests from Tasks 1–5 only if verification reveals an actual defect

**Interfaces:**

- Documents: `/retrieval/probe`。
- Documents: manual `asyncRewake` Hook configuration。
- Documents: silent exit 0 and stderr + exit 2 behavior。
- Documents: removal/disable procedure。

- [ ] **Step 1: 写集成文档**

文档必须包含可直接使用的配置：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "shell": "bash",
            "command": "agent-bridge profile hook claude-code retrieval-probe --profile chengdu --server-url http://127.0.0.1:8765 --timeout 12",
            "asyncRewake": true,
            "timeout": 12
          }
        ]
      }
    ]
  }
}
```

并明确：

- 该 Hook 当前不会被 `profile use` 自动安装。
- 删除这一条手工 Hook 配置即可停用。
- 有命中时使用 exit 2 主动唤醒；无命中或服务不可用时静默。
- 只做路由探测，不返回正文，不调用 Wiki Agent 或 CodeGraph Explore。
- `asyncRewake` 使用 Claude Code 的后台失败反馈通道，界面可能显示 Hook error 语义。

- [ ] **Step 2: 更新 README 与 CLAUDE**

README 的主要能力或使用区增加文档链接。CLAUDE 的知识与记忆架构段增加 `RetrievalProbeService` 边界，注明 Profile 安装流程暂不启用该 Hook。

- [ ] **Step 3: 运行目标测试**

Run:

```bash
uv run pytest -q \
  tests/test_retrieval_probe_tokenizer.py \
  tests/test_retrieval_probe_adapters.py \
  tests/test_retrieval_probe_service.py \
  tests/test_retrieval_probe_api.py \
  tests/test_retrieval_probe_hook.py \
  tests/test_memory_cli.py \
  tests/test_server.py
```

Expected: PASS。

- [ ] **Step 4: 运行静态检查**

Run:

```bash
uv run ruff check \
  src/agent_bridge/knowledge_management/retrieval_probe \
  src/agent_bridge/api/routes/retrieval_probe.py \
  src/agent_bridge/cli/profile_hooks.py \
  tests/test_retrieval_probe_tokenizer.py \
  tests/test_retrieval_probe_adapters.py \
  tests/test_retrieval_probe_service.py \
  tests/test_retrieval_probe_api.py \
  tests/test_retrieval_probe_hook.py
git diff --check
```

Expected: PASS，无 whitespace error。

- [ ] **Step 5: 运行跨领域完整测试**

Run:

```bash
./scripts/test.sh full -q
```

Expected: PASS。若真实外部 CodeGraph/claude-mem 测试被项目 marker 排除，应保持现有 marker 行为，不为本功能启用外部依赖测试。

- [ ] **Step 6: 检查非影响范围**

Run:

```bash
git diff main...HEAD -- src/agent_bridge/cli/profile.py
git diff main...HEAD -- src/agent_bridge/capability_hub/gateway/metamcp.py
git status --short
```

Expected:

- `profile.py` 只新增 `profile hook` 子应用挂载。
- `metamcp.py` 无差异。
- 工作区只包含本功能文件。

- [ ] **Step 7: 提交文档与最终修正**

```bash
git add \
  docs/integrations/retrieval-probe-hook/README.md \
  README.md \
  CLAUDE.md
git commit -m "docs: document retrieval probe hook"
```

- [ ] **Step 8: 最终提交范围检查**

Run:

```bash
git log --oneline main..HEAD
git diff --stat main...HEAD
git status --short --branch
```

Expected: 分支只包含设计、计划、实现、测试和文档提交，工作区干净。
