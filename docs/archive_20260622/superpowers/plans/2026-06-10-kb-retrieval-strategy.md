# Knowledge Base Retrieval Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Weknora agent management (auto-create hybrid-rag-wiki) and per-KB retrieval strategy (backend + agent selection) with profile-level overrides.

**Architecture:** Extend `WeknoraBackend` with agent CRUD methods and `ensure_hybrid_agent()` auto-creation. Add `default_backend_slug` + `default_agent_id` to the `knowledge_bases` schema and `retrieval_backend_slug` + `retrieval_agent_id` to `profile_resource_rules`. Thread agent_id through `BackendAdapter.ask()` → `WeknoraBackend.ask()`. Add strategy resolution in `AgentBridgeService`.

**Tech Stack:** Python 3.12+, SQLite, FastAPI, Pydantic, httpx, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/agent_bridge/core/domain.py` | Add `agent_id` param to `BackendAdapter.ask()` protocol, add `RetrievalStrategy` dataclass |
| Modify | `src/agent_bridge/knowledge/backends/weknora.py` | Add `list_agents()`, `get_type_presets()`, `create_agent()`, `ensure_hybrid_agent()`; update `ask()` to pass agent params |
| Modify | `src/agent_bridge/knowledge/backends/ragflow.py` | Add `agent_id` param to `ask()` (ignored) |
| Modify | `src/agent_bridge/knowledge/backends/mock.py` | Add `agent_id` param to `ask()` (ignored) |
| Modify | `src/agent_bridge/storage/schema.py` | Add columns to `knowledge_bases` and `profile_resource_rules` |
| Modify | `src/agent_bridge/storage/repositories/knowledge.py` | Add CRUD for new KB columns |
| Modify | `src/agent_bridge/storage/repositories/governance.py` | Add CRUD for new profile_resource_rules columns |
| Modify | `src/agent_bridge/knowledge/service.py` | Add `resolve_retrieval_strategy()`, update `ask()`, add `ensure_weknora_agents()`, add `update_kb_defaults()` |
| Modify | `src/agent_bridge/api/routes/knowledge.py` | Add KB update endpoint, thread agent_id into ask |
| Modify | `src/agent_bridge/api/routes/governance.py` | Thread retrieval overrides into resource-profile endpoints |
| Modify | `src/agent_bridge/api/schemas.py` | Add request models for new fields |
| Modify | `src/agent_bridge/capabilities/builtin_wiki.py` | Thread profile_key into ask for strategy resolution |
| Create | `tests/test_weknora_agent.py` | Tests for agent management and retrieval strategy |
| Create | `tests/test_retrieval_strategy.py` | Tests for strategy resolution logic |

---

### Task 1: BackendAdapter protocol + RetrievalStrategy domain model

**Files:**
- Modify: `src/agent_bridge/core/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_domain.py`:

```python
from agent_bridge.core.domain import BackendAdapter, RetrievalStrategy

def test_retrieval_strategy_dataclass():
    s = RetrievalStrategy(backend_slug="weknora", agent_id="hybrid-rag-wiki")
    assert s.backend_slug == "weknora"
    assert s.agent_id == "hybrid-rag-wiki"

def test_retrieval_strategy_agent_id_optional():
    s = RetrievalStrategy(backend_slug="ragflow")
    assert s.agent_id is None

def test_backend_adapter_ask_accepts_agent_id():
    """Verify the protocol's ask() signature includes agent_id."""
    import inspect
    sig = inspect.signature(BackendAdapter.ask)
    params = list(sig.parameters.keys())
    assert "agent_id" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_domain.py::test_retrieval_strategy_dataclass tests/test_domain.py::test_retrieval_strategy_agent_id_optional tests/test_domain.py::test_backend_adapter_ask_accepts_agent_id -v`
Expected: FAIL — `ImportError` or `AttributeError` for `RetrievalStrategy`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_bridge/core/domain.py`, add after the `AskResult` dataclass:

```python
@dataclass(frozen=True)
class RetrievalStrategy:
    backend_slug: str
    agent_id: str | None = None
```

Update the `BackendAdapter` protocol's `ask` method signature:

```python
class BackendAdapter(Protocol):
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus: ...
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]: ...
    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None, agent_id: str | None = None) -> tuple[AskResult, str]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_domain.py::test_retrieval_strategy_dataclass tests/test_domain.py::test_retrieval_strategy_agent_id_optional tests/test_domain.py::test_backend_adapter_ask_accepts_agent_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_bridge/core/domain.py tests/test_domain.py
git commit -m "feat: add RetrievalStrategy dataclass and agent_id to BackendAdapter.ask()"
```

---

### Task 2: Update RagFlowBackend and MockBackend ask() signatures

**Files:**
- Modify: `src/agent_bridge/knowledge/backends/ragflow.py`
- Modify: `src/agent_bridge/knowledge/backends/mock.py`

- [ ] **Step 1: Update RagFlowBackend.ask() signature**

In `src/agent_bridge/knowledge/backends/ragflow.py`, change the `ask` method signature (around line ~175):

```python
def ask(
    self,
    backend_kb_id: str,
    question: str,
    chat_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[AskResult, str]:
```

No logic changes needed — `agent_id` is unused for RagFlow.

- [ ] **Step 2: Update MockBackend.ask() signature**

In `src/agent_bridge/knowledge/backends/mock.py`, change the `ask` method signature (around line ~48):

```python
def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None, agent_id: str | None = None) -> tuple[AskResult, str]:
```

No logic changes needed.

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_ragflow_backend.py tests/test_services.py -v --timeout=30`
Expected: PASS (all existing tests still pass)

- [ ] **Step 4: Commit**

```bash
git add src/agent_bridge/knowledge/backends/ragflow.py src/agent_bridge/knowledge/backends/mock.py
git commit -m "feat: add agent_id param to RagFlowBackend and MockBackend ask()"
```

---

### Task 3: Weknora agent management methods

**Files:**
- Modify: `src/agent_bridge/knowledge/backends/weknora.py`
- Create: `tests/test_weknora_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weknora_agent.py`:

```python
"""Tests for WeknoraBackend agent management methods."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_bridge.knowledge.backends.weknora import WeknoraBackend


@pytest.fixture
def backend():
    return WeknoraBackend(base_url="http://localhost", api_key="test-key")


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def test_list_agents(backend):
    agents_data = {
        "data": [
            {"id": "builtin-smart-reasoning", "name": "Smart Reasoning", "is_builtin": True,
             "config": {"agent_type": "smart-reasoning"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)):
        agents = backend.list_agents()
    assert len(agents) == 1
    assert agents[0]["id"] == "builtin-smart-reasoning"


def test_get_type_presets(backend):
    presets_data = {
        "data": [
            {"id": "hybrid-rag-wiki", "config": {"system_prompt_id": "hybrid_rag_wiki_agent"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=presets_data)):
        presets = backend.get_type_presets()
    assert len(presets) == 1
    assert presets[0]["id"] == "hybrid-rag-wiki"


def test_ensure_hybrid_agent_found_existing(backend):
    agents_data = {
        "data": [
            {"id": "builtin-smart-reasoning", "is_builtin": True, "config": {"agent_type": "smart-reasoning"}},
            {"id": "existing-hybrid-id", "is_builtin": False, "config": {"agent_type": "hybrid-rag-wiki"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)):
        agent_id = backend.ensure_hybrid_agent()
    assert agent_id == "existing-hybrid-id"


def test_ensure_hybrid_agent_creates_new(backend):
    # First call: list_agents returns no hybrid
    empty_agents = {"data": [
        {"id": "builtin-smart-reasoning", "is_builtin": True, "config": {"agent_type": "smart-reasoning"}},
    ], "success": True}
    # Second call: get_type_presets
    presets = {"data": [
        {"id": "hybrid-rag-wiki", "config": {"system_prompt_id": "hybrid_rag_wiki_agent"}},
    ], "success": True}
    # Third call: create_agent
    created = {"data": {"id": "new-hybrid-uuid", "name": "AgentBridge混合智能体"}, "success": True}

    call_count = 0
    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "/api/v1/agents" == url.split("localhost")[-1].rstrip("/").split("?")[0] and method == "GET":
            return _mock_response(json_data=empty_agents)
        if "/api/v1/agents/type-presets" in url:
            return _mock_response(json_data=presets)
        if "/api/v1/agents" in url and method == "POST":
            return _mock_response(json_data=created)
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        agent_id = backend.ensure_hybrid_agent()
    assert agent_id == "new-hybrid-uuid"
    assert call_count == 3


def test_ensure_hybrid_agent_caches_result(backend):
    agents_data = {
        "data": [
            {"id": "cached-hybrid-id", "is_builtin": False, "config": {"agent_type": "hybrid-rag-wiki"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)) as mock_req:
        id1 = backend.ensure_hybrid_agent()
        id2 = backend.ensure_hybrid_agent()
    assert id1 == id2 == "cached-hybrid-id"
    assert mock_req.call_count == 1  # Only called once, cached after


def test_ask_passes_agent_id(backend):
    session_resp = _mock_response(json_data={"data": {"id": "sess-1"}, "success": True})
    chat_resp = MagicMock()
    chat_resp.status_code = 200
    chat_resp.text = (
        'event:message\ndata:{"response_type":"answer","content":"hello","done":true}\n\n'
        'event:message\ndata:{"response_type":"references","knowledge_references":[],"done":true}\n\n'
    )
    chat_resp.json.return_value = {"success": True}

    call_args = {}
    def capture_request(method, url, **kwargs):
        if method == "POST" and "sessions" in url:
            return session_resp
        if method == "POST" and "knowledge-chat" in url:
            call_args.update(kwargs)
            return chat_resp
        return _mock_response()

    with patch("httpx.request", side_effect=capture_request):
        result, chat_id = backend.ask("kb-123", "test question", agent_id="my-agent")

    body = call_args.get("json", {})
    assert body.get("agent_enabled") is True
    assert body.get("agent_id") == "my-agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_weknora_agent.py -v`
Expected: FAIL — `AttributeError: 'WeknoraBackend' object has no attribute 'list_agents'`

- [ ] **Step 3: Implement agent management methods**

In `src/agent_bridge/knowledge/backends/weknora.py`, add after `_fetch_models`:

```python
def list_agents(self) -> list[dict[str, Any]]:
    response = self._request("GET", "/api/v1/agents")
    self._raise(response)
    return self._data(response) or []

def get_type_presets(self) -> list[dict[str, Any]]:
    response = self._request("GET", "/api/v1/agents/type-presets")
    self._raise(response)
    return self._data(response) or []

def create_agent(self, name: str, preset_config: dict[str, Any]) -> dict[str, Any]:
    body = {
        "name": name,
        "description": preset_config.get("i18n", {}).get("zh-CN", {}).get("description", ""),
        "is_builtin": False,
        "config": preset_config.get("config", {}),
    }
    response = self._request("POST", "/api/v1/agents", json=body)
    self._raise(response)
    return self._data(response)

def ensure_hybrid_agent(self) -> str:
    if hasattr(self, "_hybrid_agent_id") and self._hybrid_agent_id:
        return self._hybrid_agent_id

    agents = self.list_agents()
    for agent in agents:
        cfg = agent.get("config") or {}
        if cfg.get("agent_type") == "hybrid-rag-wiki" or cfg.get("system_prompt_id") == "hybrid_rag_wiki_agent":
            self._hybrid_agent_id = agent["id"]
            return self._hybrid_agent_id

    presets = self.get_type_presets()
    hybrid_preset = next((p for p in presets if p["id"] == "hybrid-rag-wiki"), None)
    if hybrid_preset is None:
        raise RuntimeError("hybrid-rag-wiki preset not found in Weknora type-presets")

    created = self.create_agent("AgentBridge混合智能体", hybrid_preset)
    self._hybrid_agent_id = created["id"]
    logger.info("Created Weknora hybrid agent: %s", self._hybrid_agent_id)
    return self._hybrid_agent_id
```

- [ ] **Step 4: Update ask() to pass agent_id**

In `src/agent_bridge/knowledge/backends/weknora.py`, update the `ask` method signature and body:

```python
def ask(
    self,
    backend_kb_id: str,
    question: str,
    chat_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[AskResult, str]:
    if session_id is None:
        session_id = self._create_session()

    body: dict[str, Any] = {
        "query": question,
        "knowledge_base_ids": [backend_kb_id],
        "disable_title": True,
        "channel": "api",
    }
    if agent_id:
        body["agent_enabled"] = True
        body["agent_id"] = agent_id
        body["web_search_enabled"] = False

    response = self._request(
        "POST",
        f"/api/v1/knowledge-chat/{session_id}",
        json=body,
    )
    self._raise(response)
    answer, chunks = self._parse_sse_response(response.text, backend_kb_id)
    return AskResult(answer=answer, chunks=chunks, session_id=session_id), (chat_id or "")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_weknora_agent.py -v`
Expected: PASS

- [ ] **Step 6: Run existing weknora tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_weknora_backend.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/knowledge/backends/weknora.py tests/test_weknora_agent.py
git commit -m "feat: add Weknora agent management and ensure_hybrid_agent()"
```

---

### Task 4: Schema migration — add columns to knowledge_bases and profile_resource_rules

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/repositories/knowledge.py`
- Modify: `src/agent_bridge/storage/repositories/governance.py`

- [ ] **Step 1: Update schema.py**

In `src/agent_bridge/storage/schema.py`, update the `knowledge_bases` CREATE TABLE to add two columns:

```sql
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  default_backend_slug TEXT,
  default_agent_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Update the `profile_resource_rules` CREATE TABLE to add two columns:

```sql
CREATE TABLE IF NOT EXISTS profile_resource_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  resource_type TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  retrieval_backend_slug TEXT,
  retrieval_agent_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, resource_type, resource_key)
);
```

- [ ] **Step 2: Add migration methods to KnowledgeRepository**

In `src/agent_bridge/storage/repositories/knowledge.py`, add a method to update KB defaults:

```python
def update_kb_defaults(self, kb_id: int, default_backend_slug: str | None, default_agent_id: str | None) -> None:
    with self._connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_bases
            SET default_backend_slug = ?, default_agent_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (default_backend_slug, default_agent_id, kb_id),
        )
```

- [ ] **Step 3: Add migration for existing databases to KnowledgeRepository**

Add a migration method that adds the columns if they don't exist (for existing databases):

```python
def migrate_kb_defaults_columns(self) -> None:
    with self._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
        if "default_backend_slug" not in columns:
            conn.execute("ALTER TABLE knowledge_bases ADD COLUMN default_backend_slug TEXT")
        if "default_agent_id" not in columns:
            conn.execute("ALTER TABLE knowledge_bases ADD COLUMN default_agent_id TEXT")
```

- [ ] **Step 4: Add migration for profile_resource_rules to GovernanceRepository**

In `src/agent_bridge/storage/repositories/governance.py`, add a migration method:

```python
def migrate_profile_resource_retrieval_columns(self) -> None:
    with self._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profile_resource_rules)").fetchall()}
        if "retrieval_backend_slug" not in columns:
            conn.execute("ALTER TABLE profile_resource_rules ADD COLUMN retrieval_backend_slug TEXT")
        if "retrieval_agent_id" not in columns:
            conn.execute("ALTER TABLE profile_resource_rules ADD COLUMN retrieval_agent_id TEXT")
```

- [ ] **Step 5: Update replace_resource_rule_profiles to support retrieval overrides**

In `src/agent_bridge/storage/repositories/governance.py`, update `replace_resource_rule_profiles`:

```python
def replace_resource_rule_profiles(
    self, resource_type: str, resource_key: str, profile_keys: list[str],
    overrides: dict[str, dict[str, str | None]] | None = None,
) -> None:
    overrides = overrides or {}
    with self._connect() as conn:
        conn.execute(
            "DELETE FROM profile_resource_rules WHERE resource_type = ? AND resource_key = ?",
            (resource_type, resource_key),
        )
        for profile_key in profile_keys:
            ovr = overrides.get(profile_key, {})
            conn.execute(
                """
                INSERT INTO profile_resource_rules (profile_key, resource_type, resource_key, retrieval_backend_slug, retrieval_agent_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    profile_key,
                    resource_type,
                    resource_key,
                    ovr.get("retrieval_backend_slug"),
                    ovr.get("retrieval_agent_id"),
                ),
            )
```

- [ ] **Step 6: Add method to get a specific profile resource rule**

In `src/agent_bridge/storage/repositories/governance.py`:

```python
def get_profile_resource_rule(self, profile_key: str, resource_type: str, resource_key: str) -> dict[str, Any] | None:
    with self._connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM profile_resource_rules
            WHERE profile_key = ? AND resource_type = ? AND resource_key = ?
            """,
            (profile_key, resource_type, resource_key),
        ).fetchone()
        return row_to_dict(row)
```

- [ ] **Step 7: Run existing storage tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_storage.py tests/test_storage_facade.py tests/test_capability_governance_storage.py -v`
Expected: PASS (new columns are nullable, existing data unaffected)

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/knowledge.py src/agent_bridge/storage/repositories/governance.py
git commit -m "feat: add retrieval strategy columns to knowledge_bases and profile_resource_rules"
```

---

### Task 5: AgentBridgeService — ensure_weknora_agents() + update_kb_defaults() + resolve_retrieval_strategy()

**Files:**
- Modify: `src/agent_bridge/knowledge/service.py`
- Create: `tests/test_retrieval_strategy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retrieval_strategy.py`:

```python
"""Tests for retrieval strategy resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import AgentBridgePaths, BackendConfig, ensure_directories
from agent_bridge.core.domain import KbRole
from agent_bridge.knowledge.backends.registry import BackendRegistry
from agent_bridge.knowledge.service import AgentBridgeService


def _service(wm_paths: AgentBridgePaths, tmp_path: Path) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {
            "weknora": BackendConfig(slug="weknora", backend_type="weknora", base_url="http://localhost", api_key="test"),
            "ragflow": BackendConfig(slug="ragflow", backend_type="ragflow", base_url="http://localhost"),
        },
        paths=tmp_path,
    )
    service.init_system()
    return service


def test_resolve_strategy_uses_kb_defaults(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    svc.store.update_kb_defaults(
        kb_id=svc.store.get_kb_by_slug("test-kb")["id"],
        default_backend_slug="weknora",
        default_agent_id="builtin-smart-reasoning",
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key=None)
    assert strategy.backend_slug == "weknora"
    assert strategy.agent_id == "builtin-smart-reasoning"


def test_resolve_strategy_profile_overrides_kb_default(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb_id = svc.store.get_kb_by_slug("test-kb")["id"]
    svc.store.update_kb_defaults(kb_id=kb_id, default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    svc.governance.upsert_profile("root", "prof-a", "Profile A", "active")
    svc.store.replace_resource_rule_profiles(
        resource_type="wiki_kb", resource_key="test-kb",
        profile_keys=["prof-a"],
        overrides={"prof-a": {"retrieval_backend_slug": "ragflow", "retrieval_agent_id": None}},
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key="prof-a")
    assert strategy.backend_slug == "ragflow"
    assert strategy.agent_id is None


def test_resolve_strategy_profile_partial_override_falls_back(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb_id = svc.store.get_kb_by_slug("test-kb")["id"]
    svc.store.update_kb_defaults(kb_id=kb_id, default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    svc.governance.upsert_profile("root", "prof-a", "Profile A", "active")
    svc.store.replace_resource_rule_profiles(
        resource_type="wiki_kb", resource_key="test-kb",
        profile_keys=["prof-a"],
        overrides={"prof-a": {"retrieval_backend_slug": None, "retrieval_agent_id": "builtin-wiki-researcher"}},
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key="prof-a")
    assert strategy.backend_slug == "weknora"  # fell back to KB default
    assert strategy.agent_id == "builtin-wiki-researcher"  # override applied


def test_resolve_strategy_no_defaults_uses_first_active(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key=None)
    assert strategy.backend_slug in ("weknora", "ragflow")  # first active target


def test_update_kb_defaults_via_service(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    svc.update_kb_defaults("root", "test-kb", default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    kb = svc.store.get_kb_by_slug("test-kb")
    assert kb["default_backend_slug"] == "weknora"
    assert kb["default_agent_id"] == "hybrid-rag-wiki"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_retrieval_strategy.py -v`
Expected: FAIL — `AttributeError` for `resolve_retrieval_strategy`, `update_kb_defaults`

- [ ] **Step 3: Implement ensure_weknora_agents()**

In `src/agent_bridge/knowledge/service.py`, add after `init_system()`:

```python
def ensure_weknora_agents(self) -> None:
    if not self.registry:
        return
    for slug in self.registry.list_slugs():
        adapter = self.registry.get(slug)
        if isinstance(adapter, WeknoraBackend):
            try:
                adapter.ensure_hybrid_agent()
            except Exception:
                logger.warning("Failed to ensure hybrid agent for backend '%s'", slug, exc_info=True)
```

Add the import at the top of the file:

```python
from agent_bridge.knowledge.backends.weknora import WeknoraBackend
```

Update `create()` classmethod to call `ensure_weknora_agents()`:

```python
@classmethod
def create(cls, paths: AgentBridgePaths, admins: set[str]) -> "AgentBridgeService":
    service = cls(
        paths=paths,
        store=SQLiteStore(paths.db_path),
        archive=ArchiveStorage(paths.archive_dir),
        mock_backend=MockBackend(paths.mock_backend_dir),
        admins=admins,
    )
    service.store.init_schema()
    migrate_toml_backends_to_db(paths, service.store)
    service.registry = create_registry_from_db(paths, service.store)
    service.ensure_weknora_agents()
    return service
```

- [ ] **Step 4: Implement update_kb_defaults()**

In `src/agent_bridge/knowledge/service.py`:

```python
def update_kb_defaults(self, actor: str, kb_slug: str, *,
                       default_backend_slug: str | None = None,
                       default_agent_id: str | None = None) -> dict[str, Any]:
    require_admin_user(actor, self.admins)
    kb = self.store.get_kb_by_slug(kb_slug)
    if kb is None:
        raise NotFound("knowledge base not found")
    self.store.update_kb_defaults(kb["id"], default_backend_slug, default_agent_id)
    return self.store.get_kb_by_slug(kb_slug)
```

- [ ] **Step 5: Implement resolve_retrieval_strategy()**

In `src/agent_bridge/knowledge/service.py`:

```python
def resolve_retrieval_strategy(self, kb_slug: str, profile_key: str | None) -> tuple[dict[str, Any], "RetrievalStrategy"]:
    from agent_bridge.core.domain import RetrievalStrategy

    kb = self.store.get_kb_by_slug(kb_slug)
    if kb is None:
        raise NotFound("knowledge base not found")

    # 1. If profile provided, check profile resource rule overrides
    if profile_key:
        rule = self.store.get_profile_resource_rule(profile_key, "wiki_kb", kb_slug)
        if rule:
            backend = rule.get("retrieval_backend_slug")
            agent = rule.get("retrieval_agent_id")
            if backend or agent:
                # Use override for any field set, fall back to KB default for unset
                return kb, RetrievalStrategy(
                    backend_slug=backend or kb.get("default_backend_slug") or self._first_active_backend(kb),
                    agent_id=agent if agent is not None else kb.get("default_agent_id"),
                )

    # 2. KB-level defaults
    if kb.get("default_backend_slug"):
        return kb, RetrievalStrategy(
            backend_slug=kb["default_backend_slug"],
            agent_id=kb.get("default_agent_id"),
        )

    # 3. System fallback
    return kb, RetrievalStrategy(backend_slug=self._first_active_backend(kb))

def _first_active_backend(self, kb: dict[str, Any]) -> str:
    targets = self.store.list_backend_targets(kb["id"])
    active = [t for t in targets if t["status"] == "active"]
    if not active:
        raise NotFound(f"no retrieval backend available for knowledge base '{kb['slug']}'")
    return active[0]["slug"]
```

- [ ] **Step 6: Update ask() to use strategy resolution**

Replace the current `ask()` method in `src/agent_bridge/knowledge/service.py`:

```python
def ask(self, actor: str, kb_slug: str, question: str, *,
        backend_slug: str | None = None,
        session_id: str | None = None,
        profile_key: str | None = None) -> AskResult:
    kb, strategy = self.resolve_retrieval_strategy(kb_slug, profile_key)
    # Explicit backend_slug param overrides strategy
    resolved_backend = backend_slug or strategy.backend_slug
    target = self._resolve_retrieval_target(kb, resolved_backend)
    adapter = self._get_adapter(target["slug"])
    agent_id = strategy.agent_id if target["slug"] == strategy.backend_slug else None

    config_json = target.get("config_json")
    existing_chat_id = None
    if config_json:
        import json
        config = json.loads(config_json) if isinstance(config_json, str) else config_json
        existing_chat_id = config.get("chat_id")
    result, new_chat_id = adapter.ask(
        target["backend_kb_id"], question,
        chat_id=existing_chat_id, session_id=session_id,
        agent_id=agent_id,
    )
    if new_chat_id and new_chat_id != existing_chat_id:
        self.store.update_backend_target_config(
            target["kb_id"], target["slug"], {"chat_id": new_chat_id},
        )
    return result
```

- [ ] **Step 7: Update WikiBuiltinProvider to pass profile_key to ask()**

In `src/agent_bridge/capabilities/builtin_wiki.py`, update the `ask` tool execution (around line ~130):

Change:
```python
answer = self.service.ask(actor, kb_slug, question, session_id=arguments.get("session_id"))
```
To:
```python
answer = self.service.ask(actor, kb_slug, question, session_id=arguments.get("session_id"), profile_key=profile_key)
```

- [ ] **Step 8: Run the new tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_retrieval_strategy.py -v`
Expected: PASS

- [ ] **Step 9: Run all existing tests to check for regressions**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_services.py tests/test_builtin_wiki.py tests/test_capability_governance.py -v --timeout=30`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/agent_bridge/knowledge/service.py src/agent_bridge/capabilities/builtin_wiki.py tests/test_retrieval_strategy.py
git commit -m "feat: add retrieval strategy resolution with profile overrides"
```

---

### Task 6: API routes and request schemas

**Files:**
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/knowledge.py`
- Modify: `src/agent_bridge/api/routes/governance.py`

- [ ] **Step 1: Add request schemas**

In `src/agent_bridge/api/schemas.py`, add:

```python
class UpdateKbDefaultsRequest(BaseModel):
    default_backend_slug: str | None = None
    default_agent_id: str | None = None


class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None
    profile_key: str | None = None


class ResourceProfilesRequest(BaseModel):
    profile_keys: list[str] = Field(default_factory=list)
    overrides: dict[str, dict[str, str | None]] | None = None
```

Remove the existing `AskRequest` and `ResourceProfilesRequest` classes (they're being replaced).

- [ ] **Step 2: Add KB defaults update route**

In `src/agent_bridge/api/routes/knowledge.py`, add a new route and update the `ask` route:

```python
@router.put("/kbs/{kb_slug}/defaults")
def update_kb_defaults(kb_slug: str, payload: UpdateKbDefaultsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
    return call_safely(lambda: service.update_kb_defaults(current_actor, kb_slug, default_backend_slug=payload.default_backend_slug, default_agent_id=payload.default_agent_id))
```

Add the import:
```python
from agent_bridge.api.schemas import UpdateKbDefaultsRequest
```

- [ ] **Step 3: Update ask route to pass profile_key**

In `src/agent_bridge/api/routes/knowledge.py`, update the `ask` endpoint:

```python
@router.post("/ask")
def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
    result = call_safely(lambda: service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id, profile_key=payload.profile_key))
    return {"answer": result.answer, "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks], "session_id": result.session_id}
```

- [ ] **Step 4: Update governance route for resource profiles with overrides**

In `src/agent_bridge/api/routes/governance.py`, update `set_resource_profiles`:

```python
@router.put("/resource-profiles/{resource_type}/{resource_key}")
def set_resource_profiles(resource_type: str, resource_key: str, payload: ResourceProfilesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
    ensure_capability_schema()
    return call_safely(lambda: service.governance.set_resource_profiles(
        current_actor, resource_type, resource_key,
        payload.profile_keys, overrides=payload.overrides,
    ))
```

- [ ] **Step 5: Update governance service set_resource_profiles to accept overrides**

In `src/agent_bridge/capabilities/governance.py`, update `set_resource_profiles`:

```python
def set_resource_profiles(
    self,
    actor: str,
    resource_type: str,
    resource_key: str,
    profile_keys: list[str],
    overrides: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    require_admin_user(actor, self.admins)
    normalized_type = self._validate_resource_type(resource_type)
    for pk in profile_keys:
        if self.store.get_project_profile(pk) is None:
            raise NotFound(f"profile not found: {pk}")
    self.store.replace_resource_rule_profiles(
        resource_type=normalized_type,
        resource_key=resource_key,
        profile_keys=profile_keys,
        overrides=overrides,
    )
    return {
        "resource_type": normalized_type,
        "resource_key": resource_key,
        "profile_keys": profile_keys,
    }
```

- [ ] **Step 6: Run API tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_capability_api.py tests/test_profile_resources.py -v --timeout=30`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/knowledge.py src/agent_bridge/api/routes/governance.py src/agent_bridge/capabilities/governance.py
git commit -m "feat: add API routes for KB defaults and profile retrieval overrides"
```

---

### Task 7: Frontend — KB default backend/agent selection

**Files:**
- Modify: `frontend/capabilities/src/views/KnowledgeView.vue`
- Modify: `frontend/capabilities/src/views/KnowledgeProcessingConfigView.vue`
- Modify: `frontend/capabilities/src/api/client.ts`

- [ ] **Step 1: Add API methods to client.ts**

In `frontend/capabilities/src/api/client.ts`, add:

```typescript
export async function updateKbDefaults(kbSlug: string, defaults: {
  default_backend_slug?: string | null;
  default_agent_id?: string | null;
}) {
  const res = await fetch(`${API_BASE}/kbs/${kbSlug}/defaults`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(defaults),
  });
  return res.json();
}

export async function listWeknoraAgents(backendSlug: string) {
  const res = await fetch(`${API_BASE}/backends/${backendSlug}/agents`);
  return res.json();
}
```

- [ ] **Step 2: Add KB defaults section to KnowledgeView.vue**

This step adds a "Retrieval Strategy" section to the KB detail dialog. Implementation depends on the current Vue component structure — add a dropdown for backend selection and a dropdown for agent selection (populated from Weknora agents list when backend is weknora).

- [ ] **Step 3: Add override fields to profile assignment dialog**

In the profile-KB assignment dialog, add optional "Retrieval Backend Override" and "Agent Override" fields per KB entry.

- [ ] **Step 4: Test in browser**

Start the dev server, create a KB, verify the default backend/agent dropdowns work. Assign a profile, verify the override fields work.

- [ ] **Step 5: Commit**

```bash
git add frontend/capabilities/src/
git commit -m "feat: frontend KB default retrieval strategy and profile overrides"
```

---

### Task 8: End-to-end smoke test

**Files:**
- Modify: `tests/test_e2e.py`

- [ ] **Step 1: Add E2E test for retrieval strategy**

Add a test that:
1. Creates a KB with mock backend
2. Sets KB defaults via the API
3. Creates a profile, assigns the KB with retrieval override
4. Verifies `resolve_retrieval_strategy()` returns the expected values

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/ -v --timeout=60 -x --ignore=tests/test_weknora_integration.py --ignore=tests/test_ragflow_integration.py --ignore=tests/test_server_process.py`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add e2e test for retrieval strategy resolution"
```
