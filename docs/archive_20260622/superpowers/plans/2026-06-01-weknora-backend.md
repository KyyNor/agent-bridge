# Weknora Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an API-only Weknora backend that satisfies wiki-manager's existing BackendAdapter contract.

**Architecture:** Implement a focused HTTP adapter in `src/wiki_manager/weknora_backend.py`, register it through `BackendRegistry`, and keep Weknora bootstrap credentials in `/Users/kyynor/DockerData/wiki-manager-weknora-env.md`. Runtime calls use Weknora HTTP APIs only; the `weknora` CLI is not part of implementation or verification.

**Tech Stack:** Python 3.11, httpx, pytest, respx, FastAPI-independent adapter tests.

---

## File Structure

- Create `src/wiki_manager/weknora_backend.py`: Weknora HTTP adapter, SSE parser, model/bootstrap helpers used by integration tests.
- Modify `src/wiki_manager/registry.py`: add `backend_type = "weknora"` lazy import.
- Create `tests/test_weknora_backend.py`: unit tests for API mappings, status mapping, retrieval mapping, SSE ask parsing, and errors.
- Create `tests/test_weknora_integration.py`: live test against `http://localhost`, marked `weknora`.
- Modify `pyproject.toml`: register `weknora` pytest marker.
- Optionally modify `tests/test_registry.py` and `tests/test_config_backends.py`: cover Weknora registration/config.

## Task 1: Weknora Adapter Unit Tests

**Files:**
- Create: `tests/test_weknora_backend.py`

- [ ] **Step 1: Write failing tests**

Create tests covering:

```python
def test_create_kb_posts_document_kb(respx_mock): ...
def test_upload_uses_file_endpoint(respx_mock, tmp_path): ...
def test_status_maps_completed_and_failed(respx_mock): ...
def test_retrieve_maps_results_and_applies_top_k(respx_mock): ...
def test_ask_creates_session_and_parses_sse(respx_mock): ...
def test_business_error_raises(respx_mock): ...
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_weknora_backend.py -v --tb=short
```

Expected: import failure because `wiki_manager.weknora_backend` does not exist.

## Task 2: Implement WeknoraBackend

**Files:**
- Create: `src/wiki_manager/weknora_backend.py`

- [ ] **Step 1: Implement minimal adapter**

Implement:

```python
class WeknoraBackend:
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus: ...
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]: ...
    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None) -> tuple[AskResult, str]: ...
```

- [ ] **Step 2: Verify GREEN**

Run:

```bash
uv run pytest tests/test_weknora_backend.py -v --tb=short
```

Expected: all tests pass.

## Task 3: Registry and Marker

**Files:**
- Modify: `src/wiki_manager/registry.py`
- Modify: `tests/test_registry.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing registry test**

Add `test_registry_with_weknora_config()` asserting `BackendRegistry` instantiates `WeknoraBackend`.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_registry.py::test_registry_with_weknora_config -v --tb=short
```

Expected: unknown backend type or missing class.

- [ ] **Step 3: Register Weknora**

Add lazy import branch for `backend_type == "weknora"` and marker in `pyproject.toml`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest tests/test_registry.py tests/test_weknora_backend.py -v --tb=short
```

Expected: pass.

## Task 4: Live Integration Bootstrap

**Files:**
- Create: `tests/test_weknora_integration.py`

- [ ] **Step 1: Write integration test**

The test should:

1. Read `/Users/kyynor/.config/gowiki/config.yaml`.
2. Register or log in a deterministic wiki-manager user through Weknora API.
3. Create or reuse DeepSeek `KnowledgeQA` and SiliconFlow `Embedding` models.
4. Write `/Users/kyynor/DockerData/wiki-manager-weknora-env.md`.
5. Create a temp KB, upload markdown, poll status, retrieve, ask, then clean up.

- [ ] **Step 2: Verify live behavior**

Run:

```bash
uv run pytest tests/test_weknora_integration.py -v -m weknora --tb=short
```

Expected: pass when Weknora is reachable on `http://localhost`.

## Task 5: Full Verification

**Files:**
- All changed files

- [ ] **Step 1: Run unit suite**

```bash
uv run pytest tests/test_weknora_backend.py tests/test_registry.py tests/test_config_backends.py -v --tb=short
```

- [ ] **Step 2: Run non-live suite**

```bash
uv run pytest -v -m "not ragflow and not weknora" --tb=short
```

- [ ] **Step 3: Check git diff**

```bash
git diff --check
git status --short --branch
```

- [ ] **Step 4: Commit**

```bash
git add src/wiki_manager/weknora_backend.py src/wiki_manager/registry.py tests/test_weknora_backend.py tests/test_weknora_integration.py tests/test_registry.py pyproject.toml docs/superpowers/plans/2026-06-01-weknora-backend.md
git commit -m "feat: add Weknora backend adapter"
```
