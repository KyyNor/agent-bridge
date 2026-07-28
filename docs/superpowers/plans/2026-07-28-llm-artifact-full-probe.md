# LLM 工作流产物 full-probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 full-probe 的 Jieba 短词切分替换为 OpenAI Chat 小模型生成的 2–8 个业务短句，并且当前只检索工作流产物。

**Architecture:** 新增 SQLite 全局模型配置和 OpenAI Chat 关键词抽取 adapter。RetrievalProbeService 通过 Protocol 调用抽取器，抽取成功后才将短句交给唯一装配的 ArtifactProbeAdapter；抽取失败返回脱敏状态和空结果，不降级到 Jieba。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、httpx、pytest/respx、Vue 3、TypeScript、Vitest。

## Global Constraints

- 配置只能在系统配置页面管理，必须落入 SQLite，不能写入 server.toml 或其他配置文件。
- 字段固定为 base_url、api_key、model；读取 API、前端、日志和审计均不得暴露 API Key、Authorization 或模型原始输出。
- 调用 OpenAI Chat 兼容的 POST `{base_url}/chat/completions`；不使用 response_format 或 JSON Schema。
- 模型只产生 2–8 个原始关键词/短句；未配置、超时、网络失败、非 2xx 或格式无效时不检索且不回退 Jieba。
- 关键词模型子预算为 10 秒，完整 probe 总预算为 20 秒；Claude Code Hook 必须保持 async。
- full-probe registry 当前只能注册 ArtifactProbeAdapter，保留既有 Profile 与 current 产物过滤。
- 新增持续时间使用 time.monotonic()；新增持久化时间使用 utc_iso()；新增日志使用中文、logger 和惰性参数。
- 前端交付前运行 `cd frontend/capabilities && npm run check`。

---

## File Structure

- `src/agent_bridge/storage/schema.py`：全局单例配置表。
- `src/agent_bridge/storage/repositories/retrieval_probe.py`：配置的原始读写。
- `src/agent_bridge/storage/{sqlite.py,store_facade.py,facades/core.py}`：repository 装配和窄 facade。
- `src/agent_bridge/knowledge_management/retrieval_probe/extractor.py`：抽取 Protocol、状态、严格 JSON 校验和 HTTP 实现。
- `src/agent_bridge/knowledge_management/retrieval_probe/{models.py,service.py}`：公开提取状态、预算编排和 fail-closed 分支。
- `src/agent_bridge/app/service.py`：只装配产物 adapter 和抽取器；系统配置服务方法。
- `src/agent_bridge/api/{schemas.py,routes/builtins.py,routes/retrieval_probe.py}`：配置接口与 20 秒边界。
- `src/agent_bridge/cli/{profile.py,profile_hooks.py}`：异步 Hook 的 20/22/25 秒调用链。
- `frontend/capabilities/src/{api/client.ts,api/types.ts,views/knowledge/KnowledgeProcessingConfigView.vue}`：系统配置卡片。
- `tests/test_retrieval_probe_{config,extractor,service,api,hook}.py` 和 `tests/test_cli.py`：核心回归。
- `docs/integrations/retrieval-probe-hook/README.md`、`README.md`、`CLAUDE.md`：用户文档。

### Task 1: 持久化全局关键词模型配置并提供管理员 API

**Files:**
- Create: `src/agent_bridge/storage/repositories/retrieval_probe.py`
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/store_facade.py`
- Modify: `src/agent_bridge/storage/facades/core.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/builtins.py`
- Test: `tests/test_retrieval_probe_config.py`

**Interfaces:**
- Consumes: `SQLiteStore.connect()`、`require_admin_user`、`utc_iso()`。
- Produces: `get_retrieval_probe_llm_config() -> dict[str, str | None]` and `save_retrieval_probe_llm_config(base_url: str, model: str, api_key: str | None, clear_api_key: bool) -> dict[str, str | None]` on the store.
- Produces: admin-only GET/PUT `/retrieval-probe/llm-config`; public output is `{base_url, model, api_key_set, updated_at}`.

- [ ] **Step 1: Write the failing persistence/API tests**

```python
def test_retrieval_probe_llm_config_round_trip(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    saved = store.save_retrieval_probe_llm_config(
        base_url="http://llm.test/v1",
        model="qwen2.5-3b-instruct",
        api_key="secret",
        clear_api_key=False,
    )
    assert saved["api_key"] == "secret"
    assert store.get_retrieval_probe_llm_config()["model"] == "qwen2.5-3b-instruct"


def test_config_api_masks_and_preserves_key(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        response = client.put("/retrieval-probe/llm-config", headers=headers, json={
            "base_url": "http://llm.test/v1", "model": "small", "api_key": "secret",
        })
        assert response.json()["api_key_set"] is True
        assert "api_key" not in response.json()
        retained = client.put("/retrieval-probe/llm-config", headers=headers, json={
            "base_url": "http://llm.test/v1", "model": "small", "api_key": "",
        })
        assert retained.json()["api_key_set"] is True
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_config.py`

Expected: FAIL because the table, store methods and endpoint do not exist.

- [ ] **Step 3: Add repository, schema and service/API boundary**

Add a single-row table:

```sql
CREATE TABLE IF NOT EXISTS retrieval_probe_llm_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  base_url TEXT NOT NULL DEFAULT '',
  api_key TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
```

Use an UPSERT on `id = 1`. Empty/missing `api_key` retains the prior secret; only
`clear_api_key=true` persists an empty key. Use `utc_iso()` for `updated_at`.
Add Pydantic validation: absolute http/https URL up to 2048 chars, model up to 256 chars,
API Key up to 4096 chars, and reject a nonempty key paired with `clear_api_key=true`.
The service must require an admin and return only:

```python
{
    "base_url": value["base_url"],
    "model": value["model"],
    "api_key_set": bool(value["api_key"]),
    "updated_at": value["updated_at"],
}
```

- [ ] **Step 4: Run focused tests and commit**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_config.py`

Expected: PASS.

```bash
git add src/agent_bridge/storage src/agent_bridge/app/service.py src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/builtins.py tests/test_retrieval_probe_config.py
git commit -m "feat: configure retrieval probe keyword model"
```

### Task 2: 实现 OpenAI Chat 关键词抽取器与严格输出校验

**Files:**
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/extractor.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/__init__.py`
- Test: `tests/test_retrieval_probe_extractor.py`

**Interfaces:**
- Consumes: Task 1 的 store 配置和 `httpx`。
- Produces: `ProbeKeywordExtractor.extract(prompt: str, *, max_keywords: int, timeout_seconds: float) -> KeywordExtraction`.
- Produces: `KeywordExtractionStatus = success | not_configured | timeout | unavailable | invalid_output`; only success has nonempty keywords.
- Produces: `parse_probe_keywords(content: str, *, max_keywords: int) -> tuple[str, ...]`, which raises `ValueError` for every invalid model payload.

- [ ] **Step 1: Write failing HTTP and parser tests**

```python
def test_openai_extractor_returns_business_phrases(respx_mock, store) -> None:
    store.save_retrieval_probe_llm_config(
        base_url="https://llm.test/v1", model="small", api_key="secret", clear_api_key=False,
    )
    route = respx_mock.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content":
                '{"keywords":["新开发对公基础客户明细","当年新开未提升"]}'}}]
        }),
    )
    result = OpenAIChatProbeKeywordExtractor(store=store).extract(
        "新开发对公基础客户明细中‘当年新开未提升’中的‘提升’指的是什么",
        max_keywords=8, timeout_seconds=10,
    )
    assert result.status is KeywordExtractionStatus.success
    assert result.keywords == ("新开发对公基础客户明细", "当年新开未提升")
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"


@pytest.mark.parametrize("content", ["not json", '{"keywords":["只有一个"]}', '{"keywords":[]}'])
def test_invalid_output_is_never_partially_accepted(respx_mock, store, content) -> None:
    with pytest.raises(ValueError):
        parse_probe_keywords(content, max_keywords=8)
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_extractor.py`

Expected: FAIL because the extractor module does not exist.

- [ ] **Step 3: Implement the result contract, prompt and HTTP call**

```python
@dataclass(frozen=True)
class KeywordExtraction:
    status: KeywordExtractionStatus
    keywords: tuple[str, ...] = ()
    model: str = ""
    duration_ms: int = 0
    error_type: str | None = None


@runtime_checkable
class ProbeKeywordExtractor(Protocol):
    def extract(self, prompt: str, *, max_keywords: int, timeout_seconds: float) -> KeywordExtraction: ...
```

Build the endpoint as `base_url.rstrip("/") + "/chat/completions"`. Send an Authorization Bearer
header, configured model, `temperature: 0`, a small `max_tokens`, a fixed system instruction, and
the original prompt as a separate user message. The instruction must request exactly a JSON object
with original high-discrimination business phrases, prioritizing report names, business terms,
indicators and status phrases; it must forbid single characters, generic verbs, explanations,
invented terms and splitting Chinese proper terms.

Parse only a JSON object whose only member is a `keywords` array. Trim, casefold-deduplicate in
first-occurrence order, reject stopwords, tokens shorter than two Chinese characters or three ASCII
characters, terms longer than 120 chars, and any final count outside 2–`max_keywords`.
Constrain `max_keywords` to 2–8. Time every outcome using `time.monotonic()`. Map
`httpx.TimeoutException` to timeout; transport/non-2xx/provider-shape errors to unavailable; text
parse errors to invalid_output. Do not retry or log request/response bodies.

- [ ] **Step 4: Run focused tests and commit**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_extractor.py`

Expected: PASS.

```bash
git add src/agent_bridge/knowledge_management/retrieval_probe/extractor.py src/agent_bridge/knowledge_management/retrieval_probe/__init__.py tests/test_retrieval_probe_extractor.py
git commit -m "feat: extract full probe phrases with chat model"
```

### Task 3: 接入 20 秒 full-probe 编排并限制为产物来源

**Files:**
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/models.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/service.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/api/routes/retrieval_probe.py`
- Modify: `tests/test_retrieval_probe_service.py`
- Modify: `tests/test_retrieval_probe_api.py`
- Modify: `tests/test_retrieval_probe_adapters.py`

**Interfaces:**
- Consumes: Task 2 的 `ProbeKeywordExtractor`。
- Produces: `ProbeResponse.keyword_extraction`, with status/model/duration/error type but no raw model text.
- Produces: `RetrievalProbeService(..., keyword_extractor: ProbeKeywordExtractor)` and an application registry containing only artifact.

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_probe_searches_only_model_phrases_in_artifacts() -> None:
    extractor = FakeExtractor.success("新开发对公基础客户明细", "当年新开未提升")
    service, artifact = make_service(extractor=extractor, adapters=[artifact_adapter])
    response = await service.probe(actor="root", profile_key="dev", prompt="任意原问题")
    assert response.keywords == ("新开发对公基础客户明细", "当年新开未提升")
    assert [call.keyword for call in artifact.calls] == [
        "新开发对公基础客户明细", "当年新开未提升",
    ]
    assert response.keyword_extraction.status is KeywordExtractionStatus.success


async def test_failed_extraction_never_invokes_artifact_search() -> None:
    service, artifact = make_service(
        extractor=FakeExtractor(status=KeywordExtractionStatus.invalid_output),
        adapters=[artifact_adapter],
    )
    response = await service.probe(actor="root", profile_key="dev", prompt="任意原问题")
    assert response.targets == ()
    assert artifact.calls == []
    assert response.keyword_extraction.status is KeywordExtractionStatus.invalid_output
```

Add deadline tests that assert an initial 20-second budget gives the extractor 10 seconds, and API
tests that default/max `timeout_seconds` are 20 and `keyword_limit` permits only 2–8.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_adapters.py`

Expected: FAIL because the service still calls `extract_probe_keywords` and app assembly registers four sources.

- [ ] **Step 3: Implement extraction-before-discovery and fail-closed behavior**

Create the probe ID and monotonic deadline before extraction. Invoke the extractor in
`asyncio.to_thread` with `min(10.0, max(0.0, deadline - time.monotonic()))`. If its status is not
success, create a normal `ProbeResponse` with empty keywords/targets/source statuses, include its
public extraction status, write the existing audit envelope enriched with only status/model/duration/
error type, and return without calling target discovery.

On success, use the returned phrases in the existing discovery/job aggregation flow. Update
`ProbeResponse.to_payload()` and test fixtures for `keyword_extraction`. Set default and maximum
API/service budget to 20 seconds, retain `keyword_limit` only as a 2–8 output cap. In app assembly,
inject `OpenAIChatProbeKeywordExtractor(store=store)` and register only
`ArtifactProbeAdapter(workflows=self.workflows)`; leave the unused adapter classes intact.

- [ ] **Step 4: Run focused tests and commit**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_adapters.py`

Expected: PASS.

```bash
git add src/agent_bridge/knowledge_management/retrieval_probe src/agent_bridge/app/service.py src/agent_bridge/api/routes/retrieval_probe.py tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_adapters.py
git commit -m "feat: probe workflow artifacts with llm phrases"
```

### Task 4: 调整后台 Hook 调用链的 20 秒预算

**Files:**
- Modify: `src/agent_bridge/cli/profile.py`
- Modify: `src/agent_bridge/cli/profile_hooks.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_retrieval_probe_hook.py`

**Interfaces:**
- Consumes: Task 3 的 Hook service budget.
- Produces: installed command `--timeout 20`, CLI transport wait 22 seconds, Claude command wait 25 seconds.

- [ ] **Step 1: Write failing timeout propagation tests**

```python
def test_profile_use_installs_probe_with_outer_timeout_headroom(monkeypatch, tmp_path) -> None:
    settings = _profile_use_and_read_settings(monkeypatch, tmp_path)
    probe = _agent_bridge_probe_entry(settings)
    argv = shlex.split(probe["hooks"][0]["command"])
    assert argv[argv.index("--timeout") + 1] == "20"
    assert probe["hooks"][0]["timeout"] == 25


def test_probe_hook_uses_transport_headroom(monkeypatch) -> None:
    captured = {}
    class FakeClient:
        def post_retrieval_probe_hook(self, payload, *, timeout):
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0}
    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )
    result = runner.invoke(app, [
        "profile", "hook", "claude-code", "retrieval-probe", "--profile", "dev",
    ], input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "订单同步失败"}))
    assert result.exit_code == 0
    assert captured["payload"]["hook_timeout_seconds"] == 20
    assert captured["timeout"] == 22.0
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_cli.py tests/test_retrieval_probe_hook.py`

Expected: FAIL because current values are 12/14/15.

- [ ] **Step 3: Make service, client and command waits explicit**

Define named constants `RETRIEVAL_PROBE_TIMEOUT_SECONDS = 20`,
`RETRIEVAL_PROBE_CLIENT_TIMEOUT_SECONDS = 22`, and
`RETRIEVAL_PROBE_COMMAND_TIMEOUT_SECONDS = 25`. Use them when generating the profile setting and as
the CLI default. Keep the CLI option maximum aligned to the API's 20-second service maximum; retain
`"async": true` and its existing NOOP handling.

- [ ] **Step 4: Run focused tests and commit**

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_cli.py tests/test_retrieval_probe_hook.py`

Expected: PASS.

```bash
git add src/agent_bridge/cli/profile.py src/agent_bridge/cli/profile_hooks.py tests/test_cli.py tests/test_retrieval_probe_hook.py
git commit -m "feat: extend full probe hook deadline"
```

### Task 5: 在系统配置页面暴露模型连接并更新文档

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeProcessingConfigView.vue`
- Modify: `docs/integrations/retrieval-probe-hook/README.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Test: `frontend/capabilities/tests/retrievalProbeLlmConfig.test.ts`

**Interfaces:**
- Consumes: Task 1 GET/PUT config contract.
- Produces: frontend `RetrievalProbeLlmConfig` / `RetrievalProbeLlmConfigUpdate` and a masked configuration form.

- [ ] **Step 1: Write failing type/client/component behavior tests**

```ts
it('supports explicit api key clearing without a returned secret', () => {
  const update: RetrievalProbeLlmConfigUpdate = {
    base_url: 'http://127.0.0.1:8000/v1',
    model: 'qwen2.5-3b-instruct',
    api_key: '',
    clear_api_key: true,
  }
  expect(update.clear_api_key).toBe(true)
})
```

Add a component/source test that confirms a successful save replaces local state with the response
and clears the password input, so a key is never rendered from server data.

- [ ] **Step 2: Run focused frontend test to verify failure**

Run: `cd frontend/capabilities && npm run test -- retrievalProbeLlmConfig.test.ts`

Expected: FAIL because the types/client/form test do not exist.

- [ ] **Step 3: Implement the system configuration card and document behavior**

```ts
export interface RetrievalProbeLlmConfig {
  base_url: string
  model: string
  api_key_set: boolean
  updated_at: string | null
}
export interface RetrievalProbeLlmConfigUpdate {
  base_url: string
  model: string
  api_key?: string | null
  clear_api_key?: boolean
}
```

Add `getRetrievalProbeLlmConfig` and `saveRetrievalProbeLlmConfig` methods. Load config alongside
the existing system-config calls. Add a “全量探测关键词模型” card with base URL, model and password
API Key fields, an explicit clear-key checkbox, configured/unconfigured indicator, saving state, and
visible request errors. An empty key save retains the server key; after success reset local key text
to empty and keep only `api_key_set`.

Update all three documents to say: config is globally managed in system configuration; one small
model generates 2–8 phrases; query scope is current workflow artifacts only; invalid/failed
extraction is a silent no-op; timeouts are 10 seconds for extraction and 20 seconds overall; Hook
waits are 20/22/25 seconds.

- [ ] **Step 4: Run UI and relevant backend tests, then commit**

Run: `cd frontend/capabilities && npm run check`

Expected: PASS.

Run: `./.venv/bin/python -m pytest -q -o addopts='' tests/test_retrieval_probe_config.py tests/test_retrieval_probe_extractor.py tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_hook.py tests/test_cli.py`

Expected: PASS.

```bash
git add frontend/capabilities/src frontend/capabilities/tests README.md CLAUDE.md docs/integrations/retrieval-probe-hook/README.md
git commit -m "feat: manage full probe keyword model"
```

### Task 6: 运行跨领域回归并审查敏感数据边界

**Files:**
- Verify only: Tasks 1–5 的全部变更。

**Interfaces:**
- Consumes: complete implementation.
- Produces: a clean, verified handoff.

- [ ] **Step 1: Inspect the final diff for explicit invariants**

Run: `git diff main...HEAD -- src/agent_bridge frontend/capabilities README.md CLAUDE.md docs/integrations/retrieval-probe-hook/README.md`

Confirm the diff has no public `api_key` field, no secret/model body log formatting, no probe-service
use of `extract_probe_keywords`, exactly one adapter registration, and an async Hook.

- [ ] **Step 2: Run full regression**

Run: `./scripts/test.sh full -q`

Expected: PASS.

- [ ] **Step 3: Confirm handoff state**

Run: `git status --short`

Expected: empty output. If a correction is needed, stage only its listed files, run the affected test,
and commit it with a precise message; do not include unrelated user changes.

## Plan Self-Review

- Spec coverage: Task 1 covers SQLite/global configuration and masking; Task 2 covers Chat API and strict 2–8 phrase validation; Task 3 covers artifact-only, fail-closed runtime and audit response; Task 4 covers 10/20/22/25 timing; Task 5 covers UI and documentation; Task 6 covers secret exposure and cross-domain verification.
- Placeholder scan: every task names files, symbols, inputs, commands and pass/fail outcomes.
- Type consistency: Task 2 defines KeywordExtraction/ProbeKeywordExtractor consumed by Task 3; Task 1 defines the exact API response consumed by Task 5.
