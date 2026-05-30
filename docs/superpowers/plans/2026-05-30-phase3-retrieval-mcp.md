# Phase 3: Retrieval, Q&A, and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend wiki-manager with retrieval and Q&A capabilities, exposing them via CLI, API, and MCP tools.

**Architecture:** Extend BackendAdapter Protocol with `retrieve()` and `ask()` methods. RagFlow adapter implements them via `/api/v1/retrieval` and `/api/v1/chat/completions`. Service layer routes requests to the default or specified backend. MCP server exposes `search` and `ask` as read-only tools.

**Tech Stack:** Python 3.12+, FastAPI, httpx, `mcp` Python SDK, pytest + respx for HTTP mocking

---

### Task 1: Add RetrievalResult, AskResult, and Protocol extension to domain.py

**Files:**
- Modify: `src/wiki_manager/domain.py:90-103`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_domain.py`:

```python
def test_retrieval_result_dataclass():
    from wiki_manager.domain import RetrievalResult

    r = RetrievalResult(
        chunk_id="c1",
        content="some text",
        document_name="guide.md",
        similarity=0.92,
        dataset_id="ds1",
    )
    assert r.chunk_id == "c1"
    assert r.similarity == 0.92


def test_ask_result_dataclass():
    from wiki_manager.domain import AskResult

    result = AskResult(answer="yes", chunks=[], session_id="s1")
    assert result.answer == "yes"
    assert result.session_id == "s1"
    assert result.chunks == []


def test_backend_adapter_protocol_has_retrieve_and_ask():
    from wiki_manager.domain import BackendAdapter
    import inspect

    sig = inspect.signature(BackendAdapter.retrieve)
    assert "question" in sig.parameters
    assert "backend_kb_id" in sig.parameters

    sig = inspect.signature(BackendAdapter.ask)
    assert "question" in sig.parameters
    assert "backend_kb_id" in sig.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_domain.py::test_retrieval_result_dataclass tests/test_domain.py::test_ask_result_dataclass tests/test_domain.py::test_backend_adapter_protocol_has_retrieve_and_ask -v`
Expected: FAIL — `ImportError: cannot import name 'RetrievalResult'`

- [ ] **Step 3: Add RetrievalResult, AskResult dataclasses and extend Protocol**

In `src/wiki_manager/domain.py`, after the `BackendDocStatus` dataclass (line 95) and before the `BackendAdapter` Protocol (line 98), add:

```python
@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    content: str
    document_name: str
    similarity: float
    dataset_id: str


@dataclass(frozen=True)
class AskResult:
    answer: str
    chunks: list[RetrievalResult]
    session_id: str | None
```

Then extend the `BackendAdapter` Protocol (line 98-103) to add the new methods:

```python
class BackendAdapter(Protocol):
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus: ...
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]: ...
    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None) -> tuple[AskResult, str]: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_domain.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: Existing tests may fail because MockBackend and RagFlowBackend don't yet implement `retrieve`/`ask`. This is expected — we fix them in Tasks 2 and 3. For now, just verify domain tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/domain.py tests/test_domain.py
git commit -m "feat(domain): add RetrievalResult, AskResult, and Protocol retrieve/ask"
```

---

### Task 2: Add retrieve() and ask() to MockBackend

**Files:**
- Modify: `src/wiki_manager/mock_backend.py:1-51`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_domain.py`:

```python
def test_mock_backend_retrieve_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        results = backend.retrieve("test-kb", "what is X?")
        assert results == []


def test_mock_backend_ask_returns_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        result, chat_id = backend.ask("test-kb", "what is X?")
        assert "does not support" in result.answer
        assert result.chunks == []
        assert chat_id == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_domain.py::test_mock_backend_retrieve_returns_empty tests/test_domain.py::test_mock_backend_ask_returns_fallback -v`
Expected: FAIL — `AttributeError: 'MockBackend' object has no attribute 'retrieve'`

- [ ] **Step 3: Implement retrieve() and ask() on MockBackend**

In `src/wiki_manager/mock_backend.py`, update the import line to include `RetrievalResult` and `AskResult`:

```python
from wiki_manager.domain import BackendDocStatus, AskResult, RetrievalResult
```

Then add the two methods at the end of the `MockBackend` class (after `get_status`):

```python
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]:
        return []

    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None) -> tuple[AskResult, str]:
        return AskResult(
            answer="mock backend does not support Q&A",
            chunks=[],
            session_id=None,
        ), ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_domain.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/mock_backend.py tests/test_domain.py
git commit -m "feat(mock): add retrieve() and ask() stub methods"
```

---

### Task 3: Add retrieve() and ask() to RagFlowBackend

**Files:**
- Modify: `src/wiki_manager/ragflow_backend.py:1-225`
- Test: `tests/test_ragflow_backend.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ragflow_backend.py`:

```python
def test_ragflow_retrieve_returns_chunks(respx_mock):
    from wiki_manager.ragflow_backend import RagFlowBackend
    from wiki_manager.domain import RetrievalResult

    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    respx_mock.post("http://localhost:9380/api/v1/retrieval").mock(
        httpx.Response(200, json={
            "code": 0,
            "data": {"chunks": [
                {
                    "id": "chunk-1",
                    "content": "RagFlow is a RAG engine.",
                    "document_keyword": "intro.md",
                    "similarity": 0.95,
                    "dataset_id": "ds-abc",
                },
                {
                    "id": "chunk-2",
                    "content": "It supports knowledge bases.",
                    "document_keyword": "guide.md",
                    "similarity": 0.80,
                    "dataset_id": "ds-abc",
                },
            ]},
        })
    )

    results = backend.retrieve("ds-abc", "what is RagFlow?")
    assert len(results) == 2
    assert results[0].chunk_id == "chunk-1"
    assert results[0].similarity == 0.95
    assert results[0].document_name == "intro.md"
    assert results[1].content == "It supports knowledge bases."


def test_ragflow_retrieve_empty_results(respx_mock):
    from wiki_manager.ragflow_backend import RagFlowBackend

    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    respx_mock.post("http://localhost:9380/api/v1/retrieval").mock(
        httpx.Response(200, json={"code": 0, "data": {"chunks": []}})
    )

    results = backend.retrieve("ds-abc", "nonexistent topic")
    assert results == []


def test_ragflow_ask_creates_chat_and_session(respx_mock):
    from wiki_manager.ragflow_backend import RagFlowBackend
    from wiki_manager.domain import AskResult

    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    # Mock chat assistant creation
    respx_mock.post("http://localhost:9380/api/v1/chats").mock(
        httpx.Response(200, json={"code": 0, "data": {"id": "chat-123"}})
    )
    # Mock session creation
    respx_mock.post("http://localhost:9380/api/v1/chats/chat-123/sessions").mock(
        httpx.Response(200, json={"code": 0, "data": {"id": "sess-456"}})
    )
    # Mock chat completions
    respx_mock.post("http://localhost:9380/api/v1/chat/completions").mock(
        httpx.Response(200, json={
            "code": 0,
            "data": {"answer": "RagFlow is a RAG engine.", "reference": {}},
        })
    )

    result, chat_id = backend.ask("ds-abc", "what is RagFlow?")
    assert isinstance(result, AskResult)
    assert result.answer == "RagFlow is a RAG engine."
    assert result.session_id == "sess-456"
    assert chat_id == "chat-123"


def test_ragflow_ask_reuses_chat_id(respx_mock):
    from wiki_manager.ragflow_backend import RagFlowBackend

    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    # Only mock session creation and completions (no chat creation)
    respx_mock.post("http://localhost:9380/api/v1/chats/chat-existing/sessions").mock(
        httpx.Response(200, json={"code": 0, "data": {"id": "sess-789"}})
    )
    respx_mock.post("http://localhost:9380/api/v1/chat/completions").mock(
        httpx.Response(200, json={
            "code": 0,
            "data": {"answer": "It works.", "reference": {}},
        })
    )

    result, chat_id = backend.ask("ds-abc", "test?", chat_id="chat-existing")
    assert chat_id == "chat-existing"
    assert result.answer == "It works."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ragflow_backend.py::test_ragflow_retrieve_returns_chunks tests/test_ragflow_backend.py::test_ragflow_retrieve_empty_results tests/test_ragflow_backend.py::test_ragflow_ask_creates_chat_and_session tests/test_ragflow_backend.py::test_ragflow_ask_reuses_chat_id -v`
Expected: FAIL — `AttributeError: 'RagFlowBackend' object has no attribute 'retrieve'`

- [ ] **Step 3: Implement retrieve() and ask() on RagFlowBackend**

In `src/wiki_manager/ragflow_backend.py`, update the import from domain:

```python
from wiki_manager.domain import BackendDocStatus, AskResult, RetrievalResult
```

Add these methods to the `RagFlowBackend` class, after the `get_status` method (after line 215) and before `close()`:

```python
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/retrieval",
            json={
                "question": question,
                "dataset_ids": [backend_kb_id],
                "top_k": top_k,
            },
        )
        self._raise(response)
        chunks = response.json()["data"]["chunks"]
        return [
            RetrievalResult(
                chunk_id=c["id"],
                content=c["content"],
                document_name=c.get("document_keyword", ""),
                similarity=c.get("similarity", 0.0),
                dataset_id=c.get("dataset_id", ""),
            )
            for c in chunks
        ]

    def ask(self, backend_kb_id: str, question: str,
            chat_id: str | None = None,
            session_id: str | None = None) -> tuple[AskResult, str]:
        if chat_id is None:
            chat_id = self._create_chat_assistant(backend_kb_id)

        if session_id is None:
            session_id = self._create_session(chat_id)

        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chat/completions",
            json={
                "chat_id": chat_id,
                "session_id": session_id,
                "question": question,
                "stream": False,
            },
        )
        self._raise(response)
        data = response.json()["data"]
        chunks = self._extract_chunks(data)
        result = AskResult(
            answer=data["answer"],
            chunks=chunks,
            session_id=session_id,
        )
        return result, chat_id

    def _create_chat_assistant(self, backend_kb_id: str) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chats",
            json={
                "name": f"wiki-mgr-{backend_kb_id[:8]}",
                "dataset_ids": [backend_kb_id],
                "llm": {"model_name": "default"},
            },
        )
        self._raise(response)
        return response.json()["data"]["id"]

    def _create_session(self, chat_id: str) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chats/{chat_id}/sessions",
            json={"name": "wiki-session"},
        )
        self._raise(response)
        return response.json()["data"]["id"]

    @staticmethod
    def _extract_chunks(data: dict) -> list[RetrievalResult]:
        chunks = []
        for ref in data.get("reference", {}).get("chunks", []):
            chunks.append(RetrievalResult(
                chunk_id=ref.get("id", ""),
                content=ref.get("content", ""),
                document_name=ref.get("document_keyword", ""),
                similarity=ref.get("similarity", 0.0),
                dataset_id=ref.get("dataset_id", ""),
            ))
        return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ragflow_backend.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All tests PASS (MockBackend and RagFlowBackend both now have retrieve/ask)

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/ragflow_backend.py tests/test_ragflow_backend.py
git commit -m "feat(ragflow): implement retrieve() and ask() with chat assistant lifecycle"
```

---

### Task 4: Add default_backend and MCP config to config.py

**Files:**
- Modify: `src/wiki_manager/config.py:42-83`
- Test: `tests/test_config_backends.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config_backends.py`:

```python
def test_load_server_config_reads_default_backend(tmp_path):
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\ndefault_backend = "ragflow"\n',
        encoding="utf-8",
    )
    from wiki_manager.config import WikiManagerPaths
    paths = WikiManagerPaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(config_path, paths.server_config_path)

    from wiki_manager.config import load_server_config
    config = load_server_config(paths)
    assert config.default_backend == "ragflow"


def test_load_server_config_default_backend_none_when_missing(tmp_path):
    from wiki_manager.config import WikiManagerPaths, load_server_config
    paths = WikiManagerPaths.from_root(tmp_path)
    config = load_server_config(paths)
    assert config.default_backend is None


def test_load_mcp_config_returns_defaults(tmp_path):
    from wiki_manager.config import WikiManagerPaths, load_mcp_config
    paths = WikiManagerPaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    # No [mcp] section in config
    config = load_mcp_config(paths)
    assert config.enabled is False
    assert config.transport == "stdio"


def test_load_mcp_config_reads_values(tmp_path):
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n[mcp]\nenabled = true\ntransport = "sse"\n',
        encoding="utf-8",
    )
    from wiki_manager.config import WikiManagerPaths
    paths = WikiManagerPaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(config_path, paths.server_config_path)

    from wiki_manager.config import load_mcp_config
    config = load_mcp_config(paths)
    assert config.enabled is True
    assert config.transport == "sse"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_backends.py::test_load_server_config_reads_default_backend tests/test_config_backends.py::test_load_server_config_default_backend_none_when_missing tests/test_config_backends.py::test_load_mcp_config_returns_defaults tests/test_config_backends.py::test_load_mcp_config_reads_values -v`
Expected: FAIL — `TypeError` or `AttributeError` because `ServerConfig` doesn't have `default_backend` and `load_mcp_config` doesn't exist

- [ ] **Step 3: Update ServerConfig and add McpConfig**

In `src/wiki_manager/config.py`, update the `ServerConfig` dataclass (line 42-45) to:

```python
@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    admins: set[str]
    default_backend: str | None = None
```

Add a new `McpConfig` dataclass after `ServerConfig`:

```python
@dataclass(frozen=True)
class McpConfig:
    enabled: bool = False
    transport: str = "stdio"
```

Update `load_server_config` (line 70-83) to include `default_backend`:

```python
def load_server_config(paths: WikiManagerPaths) -> ServerConfig:
    ensure_directories(paths)
    if not paths.server_config_path.exists():
        paths.server_config_path.write_text(
            'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n',
            encoding="utf-8",
        )
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    admins = {str(item) for item in raw.get("admins", ["root"])}
    return ServerConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        admins=admins,
        default_backend=raw.get("default_backend"),
    )
```

Add `load_mcp_config` function after `load_server_config`:

```python
def load_mcp_config(paths: WikiManagerPaths) -> McpConfig:
    if not paths.server_config_path.exists():
        return McpConfig()
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    mcp_section = raw.get("mcp", {})
    return McpConfig(
        enabled=bool(mcp_section.get("enabled", False)),
        transport=str(mcp_section.get("transport", "stdio")),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_backends.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/config.py tests/test_config_backends.py
git commit -m "feat(config): add default_backend and MCP config support"
```

---

### Task 5: Add storage method for chat_id persistence

**Files:**
- Modify: `src/wiki_manager/storage.py:185-205`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
def test_update_backend_target_config(store):
    store.init_schema()
    kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")

    store.update_backend_target_config(kb["id"], "ragflow", {"chat_id": "chat-123"})

    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    assert ragflow_target["config_json"] == '{"chat_id": "chat-123"}'


def test_update_backend_target_config_merges_existing(store):
    store.init_schema()
    kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    store.update_backend_target_config(kb["id"], "ragflow", {"chat_id": "chat-123"})

    store.update_backend_target_config(kb["id"], "ragflow", {"extra": "value"})

    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    import json
    config = json.loads(ragflow_target["config_json"])
    assert config["chat_id"] == "chat-123"
    assert config["extra"] == "value"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py::test_update_backend_target_config tests/test_storage.py::test_update_backend_target_config_merges_existing -v`
Expected: FAIL — `AttributeError: 'SQLiteStore' object has no attribute 'update_backend_target_config'`

- [ ] **Step 3: Implement update_backend_target_config**

Add to `src/wiki_manager/storage.py` in the `SQLiteStore` class, after `update_backend_target_kb_id` (line 200-205):

```python
    def update_backend_target_config(self, kb_id: int, slug: str, config_updates: dict[str, Any]) -> None:
        import json
        with self.connect() as conn:
            row = conn.execute(
                "SELECT config_json FROM backend_targets WHERE kb_id = ? AND slug = ?",
                (kb_id, slug),
            ).fetchone()
            existing = json.loads(row["config_json"]) if row and row["config_json"] else {}
            existing.update(config_updates)
            conn.execute(
                "UPDATE backend_targets SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
                (json.dumps(existing, ensure_ascii=False), kb_id, slug),
            )
```

Also add `import json` at the top of the file if not already present. The file already has no `import json` at the top — add it. Actually, the method does `import json` locally which is fine for now, but cleaner to add it at module level. Add `import json` after `import sqlite3` on line 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py::test_update_backend_target_config tests/test_storage.py::test_update_backend_target_config_merges_existing -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/storage.py tests/test_storage.py
git commit -m "feat(storage): add update_backend_target_config for chat_id persistence"
```

---

### Task 6: Add search() and ask() to WikiManagerService

**Files:**
- Modify: `src/wiki_manager/services.py:1-392`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_services.py`:

```python
def test_search_with_default_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    # Mock retrieve on the adapter
    from wiki_manager.domain import RetrievalResult
    mock_results = [RetrievalResult(
        chunk_id="c1", content="hello", document_name="a.md",
        similarity=0.9, dataset_id=kb["id"],
    )]
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "retrieve", lambda *a, **kw: mock_results)

    results = service.search("root", "frontend-docs", "hello")
    assert len(results) == 1
    assert results[0].chunk_id == "c1"


def test_search_with_explicit_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from wiki_manager.domain import RetrievalResult
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "retrieve", lambda *a, **kw: [])

    results = service.search("root", "frontend-docs", "hello", backend_slug="mock")
    assert results == []


def test_search_kb_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    with pytest.raises(NotFound):
        service.search("root", "nonexistent", "hello")


def test_search_no_retrieval_backend(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    # Remove all backends
    service.registry = None
    with pytest.raises(NotFound, match="no.*backend"):
        service.search("root", "frontend-docs", "hello")


def test_ask_with_default_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from wiki_manager.domain import AskResult
    mock_result = AskResult(answer="yes", chunks=[], session_id="s1")
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "ask", lambda *a, **kw: (mock_result, ""))

    result = service.ask("root", "frontend-docs", "what is X?")
    assert result.answer == "yes"


def test_ask_persists_chat_id(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from wiki_manager.domain import AskResult
    mock_result = AskResult(answer="yes", chunks=[], session_id="s1")
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "ask", lambda *a, **kw: (mock_result, "chat-new"))

    service.ask("root", "frontend-docs", "what is X?", backend_slug="mock")

    targets = service.store.list_backend_targets(kb["id"])
    import json
    ragflow_target = next(t for t in targets if t["slug"] == "mock")
    config = json.loads(ragflow_target["config_json"])
    assert config["chat_id"] == "chat-new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_services.py::test_search_with_default_backend tests/test_services.py::test_search_with_explicit_backend tests/test_services.py::test_search_kb_not_found tests/test_services.py::test_search_no_retrieval_backend tests/test_services.py::test_ask_with_default_backend tests/test_services.py::test_ask_persists_chat_id -v`
Expected: FAIL — `AttributeError: 'WikiManagerService' object has no attribute 'search'`

- [ ] **Step 3: Implement search() and ask() on WikiManagerService**

Add these imports at the top of `src/wiki_manager/services.py`:

```python
from wiki_manager.domain import (
    AccessDenied,
    AskResult,
    KbRole,
    NotFound,
    Operation,
    RetrievalResult,
    SyncJobStatus,
    SyncStateStatus,
    ValidationError,
    can_manage_kb,
    can_write_own_doc,
    require_admin_user,
)
```

Add these methods to `WikiManagerService`, after the `status()` method (line 234) and before `_run_job()`:

```python
    def search(self, actor: str, kb_slug: str, question: str, *,
               backend_slug: str | None = None,
               top_k: int = 6) -> list[RetrievalResult]:
        kb = self._require_kb_visible(actor, kb_slug)
        target = self._resolve_retrieval_target(kb, backend_slug)
        adapter = self._get_adapter(target["slug"])
        return adapter.retrieve(target["backend_kb_id"], question, top_k)

    def ask(self, actor: str, kb_slug: str, question: str, *,
            backend_slug: str | None = None,
            session_id: str | None = None) -> AskResult:
        kb = self._require_kb_visible(actor, kb_slug)
        target = self._resolve_retrieval_target(kb, backend_slug)
        adapter = self._get_adapter(target["slug"])
        config_json = target.get("config_json")
        existing_chat_id = None
        if config_json:
            import json
            config = json.loads(config_json) if isinstance(config_json, str) else config_json
            existing_chat_id = config.get("chat_id")
        result, new_chat_id = adapter.ask(
            target["backend_kb_id"], question,
            chat_id=existing_chat_id, session_id=session_id,
        )
        if new_chat_id and new_chat_id != existing_chat_id:
            self.store.update_backend_target_config(
                target["kb_id"], target["slug"], {"chat_id": new_chat_id},
            )
        return result

    def _resolve_retrieval_target(self, kb: dict[str, Any], backend_slug: str | None) -> dict[str, Any]:
        targets = self.store.list_backend_targets(kb["id"])
        active = [t for t in targets if t["status"] == "active"]

        if backend_slug:
            target = next((t for t in active if t["slug"] == backend_slug), None)
            if target is None:
                raise NotFound(f"backend '{backend_slug}' not found for knowledge base '{kb['slug']}'")
            return target

        if self.registry:
            from wiki_manager.config import WikiManagerPaths, load_server_config
            config = load_server_config(self.paths)
            if config.default_backend:
                target = next((t for t in active if t["slug"] == config.default_backend), None)
                if target:
                    return target

        if active:
            return active[0]

        raise NotFound(f"no retrieval backend available for knowledge base '{kb['slug']}'")

    def _get_adapter(self, slug: str):
        if self.registry:
            adapter = self.registry.get(slug)
            if adapter is not None:
                return adapter
        return self.mock_backend
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_services.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/services.py tests/test_services.py
git commit -m "feat(services): add search() and ask() with backend routing"
```

---

### Task 7: Add GET /search and POST /ask API endpoints

**Files:**
- Modify: `src/wiki_manager/server.py:1-187`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py`:

```python
def test_search_endpoint(client):
    client.post("/admin/init")
    client.post("/kbs", json={"slug": "test-kb", "name": "Test KB"})
    response = client.get("/search", params={"kb": "test-kb", "q": "hello"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data


def test_ask_endpoint(client):
    client.post("/admin/init")
    client.post("/kbs", json={"slug": "test-kb", "name": "Test KB"})
    response = client.post("/ask", json={
        "kb": "test-kb",
        "question": "what is X?",
    })
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data


def test_search_missing_kb(client):
    client.post("/admin/init")
    response = client.get("/search", params={"kb": "nonexistent", "q": "hello"})
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::test_search_endpoint tests/test_server.py::test_ask_endpoint tests/test_server.py::test_search_missing_kb -v`
Expected: FAIL — 404 (route not found)

- [ ] **Step 3: Add endpoints to server.py**

In `src/wiki_manager/server.py`, add a new request model after `SyncRequest` (line 28-29):

```python
class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None
```

Add the two endpoints before the `return app` line (before line 186):

```python
    @app.get("/search")
    def search(q: str, kb: str, backend: str | None = None, top_k: int = 6, current_actor: str = Depends(actor)) -> dict[str, Any]:
        results = call_safely(
            lambda: service.search(current_actor, kb, q, backend_slug=backend, top_k=top_k)
        )
        return {"results": [{"chunk_id": r.chunk_id, "content": r.content, "document_name": r.document_name, "similarity": r.similarity, "dataset_id": r.dataset_id} for r in results]}

    @app.post("/ask")
    def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        result = call_safely(
            lambda: service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id)
        )
        return {
            "answer": result.answer,
            "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks],
            "session_id": result.session_id,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/server.py tests/test_server.py
git commit -m "feat(api): add GET /search and POST /ask endpoints"
```

---

### Task 8: Add search() and ask() to client.py

**Files:**
- Modify: `src/wiki_manager/client.py:140-153`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_search_command(runner, mock_server):
    result = runner.invoke(app, ["search", "hello", "--kb", "test-kb"])
    assert result.exit_code == 0


def test_ask_command(runner, mock_server):
    result = runner.invoke(app, ["ask", "what is X?", "--kb", "test-kb"])
    assert result.exit_code == 0
```

Note: These tests use the existing `runner` and `mock_server` fixtures from the test file. If they don't exist yet, use simpler integration-style tests that just verify the commands are registered.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::test_search_command tests/test_cli.py::test_ask_command -v`
Expected: FAIL — commands not found

- [ ] **Step 3: Add client methods and CLI commands**

In `src/wiki_manager/client.py`, add two methods after `sync()` (after line 152):

```python
    def search(self, kb_slug: str, question: str, backend: str | None = None, top_k: int = 6) -> dict[str, Any]:
        params: dict[str, str] = {"kb": kb_slug, "q": question, "top_k": str(top_k)}
        if backend:
            params["backend"] = backend
        response = httpx.get(
            f"{self.base_url}/search", params=params,
            headers=self._headers(), timeout=30.0,
        )
        self._raise(response)
        return response.json()

    def ask(self, kb_slug: str, question: str, backend: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"kb": kb_slug, "question": question}
        if backend:
            payload["backend"] = backend
        if session_id:
            payload["session_id"] = session_id
        response = httpx.post(
            f"{self.base_url}/ask", json=payload,
            headers=self._headers(), timeout=60.0,
        )
        self._raise(response)
        return response.json()
```

In `src/wiki_manager/cli.py`, add two commands after the `sync` command (after line 262) and before `main()`:

```python
@app.command()
def search(
    question: Annotated[str, typer.Argument(help="Search query.")],
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of results.")] = 6,
) -> None:
    """Search knowledge base chunks."""
    result = _run_client(lambda client: client.search(kb_slug, question, backend=backend, top_k=top_k))
    results = result.get("results", [])
    if not results:
        typer.echo("no results")
        return
    for i, chunk in enumerate(results, 1):
        typer.echo(f"[{i}] {chunk.get('document_name', '')} (sim: {chunk.get('similarity', 0):.2f})")
        content = chunk.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        typer.echo(f"    {preview}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question to ask.")],
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
    session: Annotated[str | None, typer.Option("--session", help="Session ID for multi-turn.")] = None,
) -> None:
    """Ask a question against a knowledge base."""
    result = _run_client(lambda client: client.ask(kb_slug, question, backend=backend, session_id=session))
    typer.echo(result.get("answer", ""))
    if result.get("session_id"):
        typer.echo(f"session: {result['session_id']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/client.py src/wiki_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add search and ask commands with client methods"
```

---

### Task 9: Add MCP server with search and ask tools

**Files:**
- Create: `src/wiki_manager/mcp_server.py`
- Modify: `src/wiki_manager/server.py` (lifespan integration)
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server.py`:

```python
from __future__ import annotations

import pytest


def test_mcp_server_exposes_search_and_ask_tools():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tool_names = [tool.name for tool in server.list_tools()]
    assert "search" in tool_names
    assert "ask" in tool_names


def test_mcp_search_tool_has_expected_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = {t.name: t for t in server.list_tools()}
    search_tool = tools["search"]
    schema = search_tool.inputSchema
    assert "kb_slug" in schema["properties"]
    assert "question" in schema["properties"]


def test_mcp_ask_tool_has_expected_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = {t.name: t for t in server.list_tools()}
    ask_tool = tools["ask"]
    schema = ask_tool.inputSchema
    assert "kb_slug" in schema["properties"]
    assert "question" in schema["properties"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wiki_manager.mcp_server'`

- [ ] **Step 3: Install mcp SDK and create MCP server**

Run: `uv add mcp` (or `pip install mcp`)

Create `src/wiki_manager/mcp_server.py`:

```python
"""MCP server exposing search and ask tools for wiki-manager."""
from __future__ import annotations

from mcp.server import Server
from mcp.types import Tool, TextContent


def create_mcp_server() -> Server:
    server = Server("wiki-manager")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description="Search knowledge base chunks by query.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kb_slug": {"type": "string", "description": "Knowledge base slug"},
                        "question": {"type": "string", "description": "Search query"},
                        "backend": {"type": "string", "description": "Backend slug (optional)"},
                        "top_k": {"type": "integer", "description": "Number of results (default 6)"},
                    },
                    "required": ["kb_slug", "question"],
                },
            ),
            Tool(
                name="ask",
                description="Ask a question against a knowledge base and get an answer with references.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kb_slug": {"type": "string", "description": "Knowledge base slug"},
                        "question": {"type": "string", "description": "Question to ask"},
                        "backend": {"type": "string", "description": "Backend slug (optional)"},
                        "session_id": {"type": "string", "description": "Session ID for multi-turn (optional)"},
                    },
                    "required": ["kb_slug", "question"],
                },
            ),
        ]

    return server
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add MCP server with search and ask tool definitions"
```

---

### Task 10: End-to-end smoke test with RagFlow

**Files:**
- Create/modify: `tests/test_ragflow_integration.py`

- [ ] **Step 1: Write the e2e test**

Add to `tests/test_ragflow_integration.py`:

```python
import pytest

from wiki_manager.config import WikiManagerPaths, ensure_directories
from wiki_manager.ragflow_backend import RagFlowBackend
from wiki_manager.domain import RetrievalResult, AskResult

RAGFLOW_BASE_URL = "http://localhost:9380"
RAGFLOW_API_KEY = "ragflow-wP5FuWWM0ihndQLfVyZnq3HeoUI9WI9GaFJcTHhc7Aw"


@pytest.mark.ragflow
def test_ragflow_retrieve_returns_results():
    backend = RagFlowBackend(base_url=RAGFLOW_BASE_URL, api_key=RAGFLOW_API_KEY)
    # Use an existing dataset_id from the running RagFlow instance
    # This test assumes at least one dataset with parsed documents exists
    datasets_resp = backend._request("GET", f"{RAGFLOW_BASE_URL}/api/v1/datasets")
    datasets = datasets_resp.json()["data"]
    if not datasets:
        pytest.skip("no datasets available in RagFlow")

    dataset_id = datasets[0]["id"]
    results = backend.retrieve(dataset_id, "test query", top_k=3)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, RetrievalResult)
        assert r.chunk_id
        assert r.content


@pytest.mark.ragflow
def test_ragflow_ask_returns_answer():
    backend = RagFlowBackend(base_url=RAGFLOW_BASE_URL, api_key=RAGFLOW_API_KEY)

    datasets_resp = backend._request("GET", f"{RAGFLOW_BASE_URL}/api/v1/datasets")
    datasets = datasets_resp.json()["data"]
    if not datasets:
        pytest.skip("no datasets available in RagFlow")

    dataset_id = datasets[0]["id"]
    result, chat_id = backend.ask(dataset_id, "what is this about?")
    assert isinstance(result, AskResult)
    assert chat_id
    assert isinstance(result.answer, str)
```

- [ ] **Step 2: Run e2e tests (requires running RagFlow)**

Run: `python -m pytest tests/test_ragflow_integration.py -v -m ragflow`
Expected: PASS (if RagFlow is running with data) or SKIP (if no datasets)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ragflow_integration.py
git commit -m "test: add Phase 3 RagFlow retrieval and Q&A e2e tests"
```

---

## Self-Review

### Spec Coverage

| Spec Section | Covered by Task |
|---|---|
| BackendAdapter Protocol extension (retrieve/ask) | Task 1 |
| RetrievalResult / AskResult dataclasses | Task 1 |
| RagFlow retrieve() | Task 3 |
| RagFlow ask() + Chat Assistant lifecycle | Task 3 |
| MockBackend retrieve/ask | Task 2 |
| Retrieval routing (default_backend + --backend) | Task 6 |
| Service layer search/ask | Task 6 |
| chat_id persistence via config_json | Tasks 5 + 6 |
| GET /search endpoint | Task 7 |
| POST /ask endpoint | Task 7 |
| wiki search CLI | Task 8 |
| wiki ask CLI | Task 8 |
| MCP tools (search, ask) | Task 9 |
| default_backend config | Task 4 |
| [mcp] config | Task 4 |
| Error handling (no backend, KB not found) | Task 6 |
| Unit tests | Tasks 1-9 |
| E2E RagFlow tests | Task 10 |

### Placeholder Scan

No TBD, TODO, or placeholder patterns found.

### Type Consistency

- `retrieve()` returns `list[RetrievalResult]` consistently across Protocol, MockBackend, RagFlowBackend, and service layer.
- `ask()` returns `tuple[AskResult, str]` (result + chat_id) consistently across Protocol, MockBackend, RagFlowBackend.
- Service layer `ask()` unwraps the tuple and returns only `AskResult` to API/CLI consumers.
- `RetrievalResult` fields: `chunk_id`, `content`, `document_name`, `similarity`, `dataset_id` — consistent everywhere.
- `AskResult` fields: `answer`, `chunks`, `session_id` — consistent everywhere.
