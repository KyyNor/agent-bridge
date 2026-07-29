# 会话感知 full-probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 full-probe 中按 Profile/session 保留磁盘会话历史，模型据此前 3 轮只提取本轮新增的 0–8 个工作流产物检索短句。

**Architecture:** 新增 DiskCache 驱动的 `ProbeSessionHistoryStore`，独立负责 12 条会话记录、滑动 30 天 TTL 和 Profile 隔离。抽取器读取最近 3 条历史、通过 OpenAI SDK 生成结构化 0–8 条结果，服务端执行确定性二次过滤后才缓存和检索。

**Tech Stack:** Python 3、DiskCache、OpenAI SDK、pytest、respx。

## Global Constraints

- 缓存键必须包含 `profile_key + session_id`；没有 session ID 时不读写缓存。
- 单 session 保留最近 12 条成功结果，模型只接收最近 3 条，TTL 为滑动 30 天。
- 有效空列表写缓存；超时、未配置、网络失败和无效输出不写缓存。
- 模型及服务端都允许 0–8 条；服务端在缓存/检索前过滤历史完全重复、停用词、单字、空值、过长值和非字符串。
- 当前仍仅检索 ArtifactProbeAdapter，空结果不创建检索任务。
- 不记录 API Key、原始模型输出或缓存中的历史提示词全文到普通日志/公开 API。

---

### Task 1: 建立 DiskCache 会话历史存储

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/agent_bridge/core/config.py`
- Create: `src/agent_bridge/knowledge_management/retrieval_probe/session_history.py`
- Test: `tests/test_retrieval_probe_session_history.py`

**Interfaces:**
- Produces: `ProbeHistoryEntry(prompt: str, keywords: tuple[str, ...], created_at: str)`.
- Produces: `ProbeSessionHistoryStore.recent(profile_key: str, session_id: str, limit: int) -> tuple[ProbeHistoryEntry, ...]` and `append(profile_key: str, session_id: str, entry: ProbeHistoryEntry) -> None`.
- Constants: `SESSION_HISTORY_RETAINED_ROUNDS = 12`, `SESSION_HISTORY_PROMPT_ROUNDS = 3`, `SESSION_HISTORY_TTL_SECONDS = 30 * 24 * 60 * 60`.

- [ ] **Step 1: Write failing store tests**

```python
def test_history_is_profile_scoped_and_keeps_recent_twelve(tmp_path) -> None:
    store = DiskCacheProbeSessionHistoryStore(tmp_path)
    for index in range(13):
        store.append("profile-a", "session-1", ProbeHistoryEntry(f"p{index}", (), utc_iso()))
    assert [item.prompt for item in store.recent("profile-a", "session-1", 3)] == ["p10", "p11", "p12"]
    assert store.recent("profile-b", "session-1", 3) == ()


def test_empty_keywords_are_persisted_but_missing_session_is_not(tmp_path) -> None:
    store = DiskCacheProbeSessionHistoryStore(tmp_path)
    store.append("profile-a", "session-1", ProbeHistoryEntry("p", (), utc_iso()))
    store.append("profile-a", "", ProbeHistoryEntry("ignored", (), utc_iso()))
    assert store.recent("profile-a", "session-1", 3)[0].keywords == ()
    assert store.recent("profile-a", "", 3) == ()
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_session_history.py`

Expected: FAIL because DiskCache and the history store do not exist.

- [ ] **Step 3: Implement storage**

Add `diskcache>=5.6.3,<6` to project dependencies and add
`retrieval_probe_session_cache_dir` to `AgentBridgePaths` as
`data_dir / "retrieval-probe-sessions"`. Use an explicit encoded composite key
`f"{profile_key}:{session_id}"`; never cache an empty session ID. Store only JSON-serializable
entry data. On append, retain the newest 12 records and call `cache.set(key, payload, expire=SESSION_HISTORY_TTL_SECONDS)`, which renews TTL. Parse malformed cache values as an empty history and delete them.

- [ ] **Step 4: Run focused tests and commit**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_session_history.py`

Expected: PASS.

```bash
git add pyproject.toml src/agent_bridge/core/config.py src/agent_bridge/knowledge_management/retrieval_probe/session_history.py tests/test_retrieval_probe_session_history.py
git commit -m "feat: cache retrieval probe session history"
```

### Task 2: 让 SDK 抽取器使用会话上下文并允许空结果

**Files:**
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/extractor.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/__init__.py`
- Test: `tests/test_retrieval_probe_extractor.py`

**Interfaces:**
- Consumes: `ProbeSessionHistoryStore` and `ProbeHistoryEntry` from Task 1.
- Produces: `ProbeKeywordExtractor.extract(prompt: str, *, profile_key: str, session_id: str, max_keywords: int, timeout_seconds: float) -> KeywordExtraction`.
- Produces: `KeywordExtraction(history_rounds: int, filtered_keyword_count: int)`; status success permits `keywords=()`.

- [ ] **Step 1: Write failing context/empty-result tests**

```python
def test_extractor_supplies_latest_three_rounds_and_persists_empty_result(monkeypatch, history_store) -> None:
    for index in range(4):
        history_store.append("dev", "s1", ProbeHistoryEntry(f"历史{index}", (f"k{index}",), utc_iso()))
    extractor = OpenAIChatProbeKeywordExtractor(store=config_store, history=history_store)
    result = extractor.extract("本轮", profile_key="dev", session_id="s1", max_keywords=8, timeout_seconds=10)
    assert result.keywords == ()
    assert result.history_rounds == 3
    assert captured_user_message_contains(captured, ["历史1", "历史2", "历史3", "本轮"])
    assert history_store.recent("dev", "s1", 1)[0].keywords == ()
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_extractor.py`

Expected: FAIL because extraction has no profile/session/history parameters and JSON schema requires two items.

- [ ] **Step 3: Implement context prompt and secondary filtering**

Change JSON Schema and `parse_probe_keywords` to allow 0–8. Construct a clearly delimited user message:
`历史第 N 轮提示词` + `历史第 N 轮提取结果` for the newest three entries, then
`本轮提示词`. Require only newly useful phrases and `{"keywords":[]}` when none exist.

After validating JSON structure and string item types, normalize each value with trim/casefold, filter
existing low-quality values, and remove items whose normalized value is in the history keyword set. A nonempty provider response that becomes
empty after this filtering remains `success`, reports the removed count, writes `[]`, and never
raises. Append only a success result, including `[]`; all other status returns bypass history writes.

- [ ] **Step 4: Run focused tests and commit**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_extractor.py tests/test_retrieval_probe_session_history.py`

Expected: PASS.

```bash
git add src/agent_bridge/knowledge_management/retrieval_probe/extractor.py src/agent_bridge/knowledge_management/retrieval_probe/__init__.py tests/test_retrieval_probe_extractor.py tests/test_retrieval_probe_session_history.py
git commit -m "feat: extract probe keywords with session context"
```

### Task 3: 传递 session 信息、维持空结果 fail-closed，并更新文档

**Files:**
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/service.py`
- Modify: `src/agent_bridge/knowledge_management/retrieval_probe/models.py`
- Modify: `tests/test_retrieval_probe_service.py`
- Modify: `docs/integrations/retrieval-probe-hook/README.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 2 session-aware extractor.
- Produces: public `keyword_extraction` metadata with history round count and filtered count, without history prompt text.
- Produces: application assembly that injects `DiskCacheProbeSessionHistoryStore(paths.retrieval_probe_session_cache_dir)`.

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_successful_empty_extraction_does_not_probe_artifacts() -> None:
    service, artifact = service_with(FakeExtractor.success(()))
    response = await service.probe(actor="root", profile_key="dev", prompt="无新增", session_id="s1")
    assert response.keyword_extraction.status is KeywordExtractionStatus.success
    assert response.keywords == ()
    assert artifact.calls == []


async def test_service_forwards_profile_and_session_to_extractor() -> None:
    extractor = RecordingExtractor.success(("新术语",))
    service, _ = service_with(extractor)
    await service.probe(actor="root", profile_key="dev", prompt="问题", session_id="s1")
    assert extractor.calls[0]["profile_key"] == "dev"
    assert extractor.calls[0]["session_id"] == "s1"
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py`

Expected: FAIL because the extractor receives neither Profile nor session ID and the service treats no keywords as an error path.

- [ ] **Step 3: Implement service propagation and safe metadata**

Pass `profile_key` and `session_id` into the extractor before target discovery. A successful empty
tuple returns a normal `ProbeResponse` with empty targets/source statuses, audits only metadata, and
does not enumerate or call ArtifactProbeAdapter. Inject the DiskCache history store during app
assembly. Keep raw historical prompts out of `ProbeResponse`, reminder text and normal logs.

Update documentation with 0–8 behavior, 12/3 history windows, Profile/session keying, 30-day sliding
TTL, empty-result behavior, and the fact that model failures do not mutate history.

- [ ] **Step 4: Run relevant regression and commit**

Run: `uv run python -m pytest -q -o addopts='' tests/test_retrieval_probe_service.py tests/test_retrieval_probe_api.py tests/test_retrieval_probe_hook.py tests/test_retrieval_probe_extractor.py tests/test_retrieval_probe_session_history.py`

Expected: PASS.

```bash
git add src/agent_bridge/app/service.py src/agent_bridge/knowledge_management/retrieval_probe/service.py src/agent_bridge/knowledge_management/retrieval_probe/models.py tests/test_retrieval_probe_service.py README.md CLAUDE.md docs/integrations/retrieval-probe-hook/README.md
git commit -m "feat: make full probe session aware"
```

### Task 4: 全量验证与敏感数据审查

**Files:**
- Verify only: all Task 1–3 files.

- [ ] **Step 1: Inspect secret and history exposure**

Run: `git diff main...HEAD -- src/agent_bridge frontend/capabilities docs README.md CLAUDE.md`

Confirm no public response, normal log format or reminder contains API Key, raw model output, cached historical prompt, or cached history entries.

- [ ] **Step 2: Run all required verification**

Run: `./scripts/test.sh full -q`

Expected: PASS.

- [ ] **Step 3: Confirm a clean handoff**

Run: `git status --short`

Expected: empty output after committing all intended changes.

## Plan Self-Review

- Task 1 covers persistent Profile/session cache, 12 records and sliding 30-day expiration.
- Task 2 covers latest-three history context, 0–8 schema/parser behavior, history duplicate filtering, and empty-success writes.
- Task 3 covers session propagation, no-artifact behavior for empty extraction, assembly, audit-safe metadata, and docs.
- Task 4 covers full verification and confidential data boundaries.
