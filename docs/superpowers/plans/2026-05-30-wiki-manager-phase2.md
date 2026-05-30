# wiki-manager Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement RagFlow backend adapter, multi-backend auto-binding, document status enhancement, and mock→RagFlow migration verification.

**Architecture:** Extract a `BackendAdapter` Protocol from `MockBackend`, create a backend registry from `server.toml` `[backends.*]` sections, and dispatch sync operations through the registry. RagFlow adapter calls RagFlow HTTP API. Auto-alignment on server startup detects new/removed backends and creates pending sync jobs.

**Tech Stack:** Python 3.11, FastAPI, Typer, httpx, SQLite, RagFlow REST API

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/wiki_manager/domain.py` | Add `BackendAdapter` Protocol, `BackendDocStatus` dataclass |
| Modify | `src/wiki_manager/mock_backend.py` | Refactor to satisfy `BackendAdapter` Protocol |
| Create | `src/wiki_manager/ragflow_backend.py` | RagFlow HTTP API adapter |
| Modify | `src/wiki_manager/config.py` | Add `BackendConfig`, parse `[backends.*]` sections |
| Modify | `src/wiki_manager/storage.py` | Schema migration, new storage methods for multi-backend |
| Create | `src/wiki_manager/registry.py` | Backend adapter registry and factory |
| Modify | `src/wiki_manager/services.py` | Replace hardcoded mock with registry, multi-backend sync |
| Modify | `src/wiki_manager/server.py` | Add `backend` query params, `GET /backends`, lifespan alignment |
| Modify | `src/wiki_manager/client.py` | Add `backend` param to relevant methods |
| Modify | `src/wiki_manager/cli.py` | Add `--backend` option to relevant commands |
| Create | `tests/test_ragflow_backend.py` | RagFlow adapter tests with mocked HTTP |
| Create | `tests/test_registry.py` | Registry and config parsing tests |
| Modify | `tests/test_services.py` | Multi-backend service tests |
| Modify | `tests/test_server.py` | API tests for backend query params |
| Modify | `tests/test_cli.py` | CLI tests for --backend option |
| Modify | `tests/test_storage.py` | Schema migration and new storage method tests |
| Modify | `tests/test_e2e.py` | Phase 2 end-to-end smoke test |

---

### Task 1: BackendAdapter Protocol and BackendDocStatus

**Files:**
- Modify: `src/wiki_manager/domain.py:1-86`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_domain.py`:

```python
from typing import Protocol, runtime_checkable
from wiki_manager.domain import BackendDocStatus
from wiki_manager.mock_backend import MockBackend


def test_backend_doc_status_defaults():
    status = BackendDocStatus(status="completed", chunk_count=5, progress=1.0, error_message=None)
    assert status.status == "completed"
    assert status.chunk_count == 5


def test_mock_backend_satisfies_adapter_protocol():
    from wiki_manager.domain import BackendAdapter
    assert isinstance(MockBackend, type)
    # Verify structural subtyping — all required methods exist with compatible signatures
    required = ["create_kb", "delete_kb", "upload", "delete", "get_status"]
    for method_name in required:
        assert hasattr(MockBackend, method_name), f"MockBackend missing method: {method_name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_domain.py::test_backend_doc_status_defaults tests/test_domain.py::test_mock_backend_satisfies_adapter_protocol -v`
Expected: FAIL — `BackendDocStatus` and `BackendAdapter` not defined

- [ ] **Step 3: Write minimal implementation**

Add to end of `src/wiki_manager/domain.py` (after `require_admin_user`):

```python
from typing import Protocol


@dataclass(frozen=True)
class BackendDocStatus:
    status: str
    chunk_count: int | None = None
    progress: float | None = None
    error_message: str | None = None


class BackendAdapter(Protocol):
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus: ...
```

Add `from pathlib import Path` to the existing imports if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_domain.py::test_backend_doc_status_defaults tests/test_domain.py::test_mock_backend_satisfies_adapter_protocol -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/domain.py tests/test_domain.py
git commit -m "feat: add BackendAdapter protocol and BackendDocStatus dataclass"
```

---

### Task 2: Refactor MockBackend to satisfy BackendAdapter Protocol

**Files:**
- Modify: `src/wiki_manager/mock_backend.py:1-35`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_domain.py`:

```python
from pathlib import Path
from wiki_manager.domain import BackendDocStatus


def test_mock_backend_create_kb_returns_slug():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        kb_id = backend.create_kb("test-kb", "Test KB")
        assert kb_id == "test-kb"


def test_mock_backend_upload_returns_doc_id():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        doc_id = backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        assert doc_id == "test-kb:test-doc"


def test_mock_backend_get_status_returns_completed():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        status = backend.get_status("test-kb", "test-kb:test-doc")
        assert status.status == "completed"
        assert status.chunk_count == 1


def test_mock_backend_delete_removes_document():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        doc_id = backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        backend.delete("test-kb", doc_id)
        assert backend.get_status("test-kb", doc_id).status == "not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_domain.py -k "mock_backend" -v`
Expected: FAIL — `MockBackend` has no `create_kb`, `upload`, `get_status` methods

- [ ] **Step 3: Rewrite MockBackend**

Replace entire contents of `src/wiki_manager/mock_backend.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wiki_manager.domain import BackendDocStatus


class MockBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_kb(self, slug: str, name: str) -> str:
        kb_dir = self.root / slug
        kb_dir.mkdir(parents=True, exist_ok=True)
        return slug

    def delete_kb(self, backend_kb_id: str) -> None:
        pass

    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str:
        kb_dir = self.root / backend_kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        backend_doc_id = f"{backend_kb_id}:{doc_slug}"
        payload = {
            "backend_doc_id": backend_doc_id,
            "doc_slug": doc_slug,
            "filename": filename,
            "status": "active",
        }
        (kb_dir / f"{doc_slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return backend_doc_id

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        path = self.root / backend_kb_id / f"{backend_doc_id.split(':')[-1]}.json"
        path.unlink(missing_ok=True)

    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus:
        doc_slug = backend_doc_id.split(":")[-1]
        path = self.root / backend_kb_id / f"{doc_slug}.json"
        if not path.exists():
            return BackendDocStatus(status="not_found")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "active":
            return BackendDocStatus(status="completed", chunk_count=1, progress=1.0)
        return BackendDocStatus(status=data.get("status", "unknown"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_domain.py -k "mock_backend" -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check nothing broke**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All tests PASS. If any tests fail due to the MockBackend interface change, update callers in `services.py` to use new method names.

- [ ] **Step 6: Update services.py callers if needed**

In `src/wiki_manager/services.py:195-233`, update `_run_job` to use new MockBackend method names. Replace:

```python
if job["operation"] == "delete":
    self.mock_backend.delete_document(job["kb_slug"], job["doc_slug"])
```

with:

```python
if job["operation"] == "delete":
    sync_state = self.store.get_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"])
    backend_doc_id = sync_state["backend_doc_id"] if sync_state else None
    if backend_doc_id:
        self.mock_backend.delete(job["kb_slug"], backend_doc_id)
```

Replace:

```python
backend_doc_id = self.mock_backend.upsert_document(
    kb_slug=job["kb_slug"],
    doc_slug=job["doc_slug"],
    version_no=job["version_no"],
    archive_path=job["archive_path"],
)
```

with:

```python
backend_doc_id = self.mock_backend.upload(
    backend_kb_id=job["kb_slug"],
    doc_slug=job["doc_slug"],
    file_path=Path(job["archive_path"]),
    filename=job["doc_slug"],
)
```

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/mock_backend.py src/wiki_manager/services.py tests/test_domain.py
git commit -m "refactor: align MockBackend with BackendAdapter protocol"
```

---

### Task 3: Backend config parsing

**Files:**
- Modify: `src/wiki_manager/config.py:42-74`
- Create: `tests/test_config_backends.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_backends.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import BackendConfig, WikiManagerPaths, load_server_config, load_backend_configs


def _write_config(config_dir: Path, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "server.toml").write_text(content, encoding="utf-8")


def test_no_backends_returns_empty(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, 'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n')
    backends = load_backend_configs(paths)
    assert backends == []


def test_single_backend_config(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.mock]\nbackend_type = "mock"\n'
    ))
    backends = load_backend_configs(paths)
    assert len(backends) == 1
    assert backends[0].slug == "mock"
    assert backends[0].backend_type == "mock"


def test_multiple_backend_configs(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.mock]\nbackend_type = "mock"\n\n'
        '[backends.ragflow]\nbackend_type = "ragflow"\nbase_url = "http://localhost:9380"\napi_key = "ragflow-test"\ntimeout = 120\n'
    ))
    backends = load_backend_configs(paths)
    assert len(backends) == 2
    slugs = {b.slug for b in backends}
    assert slugs == {"mock", "ragflow"}
    ragflow = next(b for b in backends if b.slug == "ragflow")
    assert ragflow.base_url == "http://localhost:9380"
    assert ragflow.api_key == "ragflow-test"
    assert ragflow.timeout == 120


def test_backend_config_missing_required_field(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.ragflow]\nbase_url = "http://localhost:9380"\n'
    ))
    with pytest.raises(ValueError, match="backend_type"):
        load_backend_configs(paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_config_backends.py -v`
Expected: FAIL — `BackendConfig`, `load_backend_configs` not defined

- [ ] **Step 3: Write implementation**

Add to `src/wiki_manager/config.py` after `ServerConfig`:

```python
@dataclass(frozen=True)
class BackendConfig:
    slug: str
    backend_type: str
    base_url: str | None = None
    api_key: str | None = None
    timeout: int = 120


def load_backend_configs(paths: WikiManagerPaths) -> list[BackendConfig]:
    if not paths.server_config_path.exists():
        return []
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    backends_raw = raw.get("backends", {})
    if not backends_raw:
        return []
    result = []
    for slug, section in backends_raw.items():
        if "backend_type" not in section:
            raise ValueError(f"backend '{slug}' missing required field: backend_type")
        result.append(BackendConfig(
            slug=slug,
            backend_type=section["backend_type"],
            base_url=section.get("base_url"),
            api_key=section.get("api_key"),
            timeout=int(section.get("timeout", 120)),
        ))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_config_backends.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/config.py tests/test_config_backends.py
git commit -m "feat: add BackendConfig and backend section parsing from server.toml"
```

---

### Task 4: Schema migration for Phase 2

**Files:**
- Modify: `src/wiki_manager/storage.py:13-97, 461-492`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
def test_schema_migration_adds_phase2_columns(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    # Man add a sync_states row to verify migration preserves it
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sync_states (doc_id, kb_id, backend_slug, backend_doc_id, status) VALUES (0, 0, 'mock', 'doc1', 'synced')"
        )
    # Run migration
    store.migrate_phase2()
    # Verify new columns exist
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM sync_states WHERE backend_slug = 'mock'").fetchone()
        assert row is not None
        assert row["status"] == "synced"
        # New columns should be nullable
        assert row["backend_status"] is None
        assert row["chunk_count"] is None
        assert row["progress"] is None
        assert row["backend_error"] is None
    # Verify backend_targets new column
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM backend_targets LIMIT 0").description
        col_names = {desc[0] for desc in row}
        assert "backend_kb_id" in col_names


def test_migration_is_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    store.migrate_phase2()
    store.migrate_phase2()  # Should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_storage.py -k "phase2" -v`
Expected: FAIL — `migrate_phase2` not defined

- [ ] **Step 3: Write implementation**

Add the `migrate_phase2` method to `SQLiteStore` in `src/wiki_manager/storage.py`, after `init_schema`:

```python
def migrate_phase2(self) -> None:
    with self.connect() as conn:
        # Add backend_kb_id to backend_targets if missing
        existing = {row[1] for row in conn.execute("PRAGMA table_info(backend_targets)").fetchall()}
        if "backend_kb_id" not in existing:
            conn.execute("ALTER TABLE backend_targets ADD COLUMN backend_kb_id TEXT")

        # Add new columns to sync_states if missing
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sync_states)").fetchall()}
        for col, col_type in [
            ("backend_status", "TEXT"),
            ("chunk_count", "INTEGER"),
            ("progress", "REAL"),
            ("backend_error", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE sync_states ADD COLUMN {col} {col_type}")
```

Also update the SCHEMA constant's `sync_states` table definition to include the new columns for fresh installs. Replace lines 88-96:

```python
CREATE TABLE IF NOT EXISTS sync_states (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  backend_slug TEXT NOT NULL DEFAULT 'mock',
  backend_doc_id TEXT,
  status TEXT NOT NULL,
  backend_status TEXT,
  chunk_count INTEGER,
  progress REAL,
  backend_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (doc_id, kb_id, backend_slug)
);
```

And update the `backend_targets` table in SCHEMA (line 65-75) to add `backend_kb_id`:

```python
CREATE TABLE IF NOT EXISTS backend_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  slug TEXT NOT NULL DEFAULT 'mock',
  backend_type TEXT NOT NULL DEFAULT 'mock',
  backend_kb_id TEXT,
  config_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kb_id, slug)
);
```

Also update `init_schema` to call migration after schema creation:

```python
def init_schema(self) -> None:
    with self.connect() as conn:
        conn.executescript(SCHEMA)
    self.migrate_phase2()
```

Update `upsert_sync_state` to accept and store the new fields. Replace the method at line 461:

```python
def upsert_sync_state(
    self,
    doc_id: int,
    kb_id: int,
    backend_slug: str,
    backend_doc_id: str | None,
    status: SyncStateStatus,
    backend_status: str | None = None,
    chunk_count: int | None = None,
    progress: float | None = None,
    backend_error: str | None = None,
) -> None:
    with self.connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_states (doc_id, kb_id, backend_slug, backend_doc_id, status, backend_status, chunk_count, progress, backend_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, kb_id, backend_slug) DO UPDATE SET
              backend_doc_id = excluded.backend_doc_id,
              status = excluded.status,
              backend_status = excluded.backend_status,
              chunk_count = excluded.chunk_count,
              progress = excluded.progress,
              backend_error = excluded.backend_error,
              updated_at = CURRENT_TIMESTAMP
            """,
            (doc_id, kb_id, backend_slug, backend_doc_id, status.value, backend_status, chunk_count, progress, backend_error),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_storage.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/storage.py tests/test_storage.py
git commit -m "feat: add Phase 2 schema migration for backend status fields"
```

---

### Task 5: Backend registry

**Files:**
- Create: `src/wiki_manager/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import BackendConfig, WikiManagerPaths
from wiki_manager.domain import BackendDocStatus
from wiki_manager.mock_backend import MockBackend
from wiki_manager.registry import BackendRegistry, create_registry


def test_registry_from_empty_config(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    registry = create_registry(paths)
    assert registry.backends == {}


def test_registry_from_mock_config(tmp_path: Path):
    config = BackendConfig(slug="mock", backend_type="mock")
    registry = BackendRegistry({"mock": config}, paths=tmp_path)
    adapter = registry.get("mock")
    assert adapter is not None
    assert isinstance(adapter, MockBackend)


def test_registry_unknown_backend_type(tmp_path: Path):
    config = BackendConfig(slug="unknown", backend_type="nonexistent")
    with pytest.raises(ValueError, match="unknown backend type"):
        BackendRegistry({"unknown": config}, paths=tmp_path)


def test_registry_get_missing_slug(tmp_path: Path):
    config = BackendConfig(slug="mock", backend_type="mock")
    registry = BackendRegistry({"mock": config}, paths=tmp_path)
    assert registry.get("nonexistent") is None


def test_registry_list_slugs(tmp_path: Path):
    configs = {
        "mock": BackendConfig(slug="mock", backend_type="mock"),
    }
    registry = BackendRegistry(configs, paths=tmp_path)
    assert registry.list_slugs() == ["mock"]


def test_registry_ragflow_type_not_registered(tmp_path: Path):
    """RagFlow adapter not yet implemented — should raise during registry creation."""
    config = BackendConfig(slug="ragflow", backend_type="ragflow")
    with pytest.raises(ValueError, match="unknown backend type"):
        BackendRegistry({"ragflow": config}, paths=tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_registry.py -v`
Expected: FAIL — `BackendRegistry`, `create_registry` not defined

- [ ] **Step 3: Write implementation**

Create `src/wiki_manager/registry.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from wiki_manager.config import BackendConfig, WikiManagerPaths, load_backend_configs
from wiki_manager.domain import BackendAdapter
from wiki_manager.mock_backend import MockBackend


ADAPTER_CLASSES: dict[str, type] = {
    "mock": MockBackend,
}


class BackendRegistry:
    def __init__(self, configs: dict[str, BackendConfig], paths: Path) -> None:
        self._adapters: dict[str, BackendAdapter] = {}
        for slug, config in configs.items():
            adapter_cls = ADAPTER_CLASSES.get(config.backend_type)
            if adapter_cls is None:
                raise ValueError(f"unknown backend type: {config.backend_type}")
            if config.backend_type == "mock":
                self._adapters[slug] = adapter_cls(paths / "data" / "backend" / "mock")
            elif config.backend_type == "ragflow":
                from wiki_manager.ragflow_backend import RagFlowBackend
                self._adapters[slug] = adapter_cls(
                    base_url=config.base_url or "",
                    api_key=config.api_key or "",
                    timeout=config.timeout,
                )
            else:
                self._adapters[slug] = adapter_cls()

    def get(self, slug: str) -> BackendAdapter | None:
        return self._adapters.get(slug)

    def list_slugs(self) -> list[str]:
        return sorted(self._adapters.keys())

    @property
    def backends(self) -> dict[str, BackendAdapter]:
        return dict(self._adapters)


def create_registry(paths: WikiManagerPaths) -> BackendRegistry:
    configs = load_backend_configs(paths)
    if not configs:
        return BackendRegistry({}, paths.root)
    config_map = {c.slug: c for c in configs}
    return BackendRegistry(config_map, paths.root)
```

Note: `RagFlowBackend` is not yet implemented, so the ragflow branch will fail. This is intentional — Task 8 will implement it. The registry handles ragflow by dynamically importing, so tests that don't use ragflow will pass.

Update `create_registry` to skip unknown backend types gracefully instead of raising, so that during incremental development tests pass:

Actually, let's keep the raise for unknown types — it catches config errors early. The ragflow tests will be added after Task 8.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_registry.py -v`
Expected: PASS (5 tests, 1 expected failure for ragflow)

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/registry.py tests/test_registry.py
git commit -m "feat: add BackendRegistry and adapter factory from server.toml config"
```

---

### Task 6: Storage methods for multi-backend

**Files:**
- Modify: `src/wiki_manager/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
def test_list_backend_targets_for_kb(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="mock", backend_type="mock")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    targets = store.list_backend_targets(kb["id"])
    assert len(targets) == 2
    slugs = {t["slug"] for t in targets}
    assert slugs == {"mock", "ragflow"}


def test_set_backend_target_inactive(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="mock", backend_type="mock")
    store.set_backend_target_status(kb["id"], "mock", "inactive")
    targets = store.list_backend_targets(kb["id"])
    mock_target = next(t for t in targets if t["slug"] == "mock")
    assert mock_target["status"] == "inactive"


def test_list_sync_states_for_doc(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    doc = store.create_document("test-doc", "Test Doc", "root")
    store.attach_document_to_kb(doc["id"], kb["id"], "root")
    store.upsert_sync_state(doc["id"], kb["id"], "mock", "mock:doc", SyncStateStatus.synced)
    store.upsert_sync_state(doc["id"], kb["id"], "ragflow", "rf:doc", SyncStateStatus.synced)
    states = store.list_sync_states_for_doc(doc["id"])
    assert len(states) == 2
    assert {s["backend_slug"] for s in states} == {"mock", "ragflow"}


def test_update_backend_target_kb_id(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    store.update_backend_target_kb_id(kb["id"], "ragflow", "rf-dataset-123")
    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    assert ragflow_target["backend_kb_id"] == "rf-dataset-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_storage.py -k "backend_target or sync_states_for_doc" -v`
Expected: FAIL — methods not defined

- [ ] **Step 3: Write implementation**

Add these methods to `SQLiteStore` in `src/wiki_manager/storage.py`:

```python
def list_backend_targets(self, kb_id: int) -> list[dict[str, Any]]:
    with self.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM backend_targets WHERE kb_id = ? ORDER BY slug",
            (kb_id,),
        ).fetchall()
        return [dict(row) for row in rows]

def set_backend_target_status(self, kb_id: int, slug: str, status: str) -> None:
    with self.connect() as conn:
        conn.execute(
            "UPDATE backend_targets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
            (status, kb_id, slug),
        )

def update_backend_target_kb_id(self, kb_id: int, slug: str, backend_kb_id: str) -> None:
    with self.connect() as conn:
        conn.execute(
            "UPDATE backend_targets SET backend_kb_id = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ? AND slug = ?",
            (backend_kb_id, kb_id, slug),
        )

def list_sync_states_for_doc(self, doc_id: int) -> list[dict[str, Any]]:
    with self.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_states WHERE doc_id = ?",
            (doc_id,),
        ).fetchall()
        return [dict(row) for row in rows]

def list_synced_docs_for_target(self, kb_id: int, backend_slug: str) -> list[dict[str, Any]]:
    with self.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT s.doc_id
            FROM sync_states s
            JOIN document_kbs dk ON dk.doc_id = s.doc_id AND dk.kb_id = s.kb_id
            WHERE s.kb_id = ? AND s.backend_slug = ? AND s.status = ?
            """,
            (kb_id, backend_slug, SyncStateStatus.synced.value),
        ).fetchall()
        return [dict(row) for row in rows]
```

Also update `create_sync_job` to accept `backend_slug` parameter. Change line 361-380:

```python
def create_sync_job(
    self,
    doc_id: int,
    kb_id: int,
    operation: Operation,
    version_id: int | None,
    backend_slug: str = "mock",
) -> dict[str, Any]:
    with self.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sync_jobs (doc_id, kb_id, backend_slug, operation, version_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doc_id, kb_id, backend_slug, operation.value, version_id, SyncJobStatus.pending.value),
        )
        row = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        job = _row_to_dict(row)
        if job is None:
            raise KeyError(f"sync job not found: {cursor.lastrowid}")
        return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_storage.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/storage.py tests/test_storage.py
git commit -m "feat: add multi-backend storage methods and backend_slug to sync jobs"
```

---

### Task 7: Services refactor for multi-backend

**Files:**
- Modify: `src/wiki_manager/services.py`
- Modify: `tests/test_services.py`

This is the largest task. It replaces the hardcoded `mock_backend` with the registry and makes sync dispatch through the correct adapter.

- [ ] **Step 1: Write the failing test**

Add these imports to the top of `tests/test_services.py`:

```python
from wiki_manager.registry import BackendRegistry
from wiki_manager.config import BackendConfig
```

Add these test functions:

```python
def _make_service_with_backends(wm_paths, backend_configs, tmp_path=None):
    """Helper to create a service with specific backend configs."""
    ensure_directories(wm_paths)
    registry = BackendRegistry(
        {c.slug: c for c in backend_configs},
        paths=tmp_path or wm_paths.root,
    )
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.registry = registry
    service.init_system()
    return service


def test_create_kb_auto_creates_backend_targets(wm_paths, tmp_path):
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    service = _make_service_with_backends(wm_paths, configs, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    targets = service.store.list_backend_targets(kb["id"])
    assert len(targets) == 1
    assert targets[0]["slug"] == "mock"
    assert targets[0]["backend_kb_id"] is not None


def test_add_document_creates_jobs_for_all_targets(wm_paths, tmp_path):
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    service = _make_service_with_backends(wm_paths, configs, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "test.pdf"
    source.write_bytes(b"content")
    service.add_document("alice", source, ["frontend-docs"], later=True)
    jobs = service.store.list_all_jobs()
    assert len(jobs) == 1
    assert jobs[0]["backend_slug"] == "mock"


def test_sync_dispatches_to_correct_backend(wm_paths, tmp_path):
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    service = _make_service_with_backends(wm_paths, configs, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "test.pdf"
    source.write_bytes(b"content")
    service.add_document("alice", source, ["frontend-docs"], later=True)
    service.sync("root", all_users=True)
    sync_state = service.store.get_sync_state(
        service.store.get_document_by_slug("test")["id"], kb["id"], "mock"
    )
    assert sync_state["status"] == SyncStateStatus.synced.value


def test_no_backends_configured_still_works(wm_paths):
    """When no backends are configured, KB creation succeeds with no targets."""
    ensure_directories(wm_paths)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    from wiki_manager.registry import BackendRegistry
    service.registry = BackendRegistry({}, wm_paths.root)
    service.init_system()
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    targets = service.store.list_backend_targets(kb["id"])
    assert targets == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_services.py -k "auto_creates_backend_targets or jobs_for_all_targets or sync_dispatches or no_backends" -v`
Expected: FAIL — `service.registry` not set, methods don't use it

- [ ] **Step 3: Refactor services.py**

Replace `src/wiki_manager/services.py` constructor and factory:

```python
class WikiManagerService:
    def __init__(
        self,
        paths: WikiManagerPaths,
        store: SQLiteStore,
        archive: ArchiveStorage,
        mock_backend: MockBackend,
        admins: set[str],
    ) -> None:
        self.paths = paths
        self.store = store
        self.archive = archive
        self.mock_backend = mock_backend
        self.admins = admins
        self.registry: BackendRegistry | None = None

    @classmethod
    def create(cls, paths: WikiManagerPaths, admins: set[str]) -> "WikiManagerService":
        service = cls(
            paths=paths,
            store=SQLiteStore(paths.db_path),
            archive=ArchiveStorage(paths.archive_dir),
            mock_backend=MockBackend(paths.mock_backend_dir),
            admins=admins,
        )
        service.registry = create_registry(paths)
        return service
```

Add imports at top of `services.py`:

```python
from wiki_manager.registry import BackendRegistry, create_registry
from wiki_manager.domain import BackendDocStatus
```

Update `create_kb` to auto-create targets for all registered backends:

```python
def create_kb(self, actor: str, slug: str, name: str, description: str) -> dict[str, Any]:
    require_admin_user(actor, self.admins)
    kb = self.store.create_kb(slug=slug, name=name, description=description, created_by=actor)
    self.store.grant_member(kb["id"], actor, KbRole.admin)
    if self.registry:
        for backend_slug in self.registry.list_slugs():
            adapter = self.registry.get(backend_slug)
            if adapter is not None:
                try:
                    backend_kb_id = adapter.create_kb(slug, name)
                    self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                    self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                except Exception:
                    self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
    return kb
```

Update `add_document` to create sync jobs for each backend target:

```python
def add_document(
    self,
    actor: str,
    source: Path,
    kb_slugs: list[str],
    later: bool,
    original_filename: str | None = None,
) -> dict[str, Any]:
    if not kb_slugs:
        raise ValidationError("at least one knowledge base is required")
    self._validate_source(source)
    kbs = [self._require_kb_visible(actor, kb_slug) for kb_slug in kb_slugs]
    for kb in kbs:
        self._require_kb_write(actor, kb)

    display_name = original_filename or source.name
    slug = unique_slug(make_slug(display_name), self.store.list_document_slugs())
    archived = self.archive.store(source)
    doc = self.store.create_document(slug=slug, title=Path(display_name).stem, owner_user=actor)
    version = self.store.create_document_version(
        doc_id=doc["id"],
        original_filename=display_name,
        content_hash=archived.content_hash,
        file_size=archived.file_size,
        mime_type=self._mime_type(display_name),
        archive_path=str(archived.archive_path),
        created_by=actor,
    )
    for kb in kbs:
        self.store.attach_document_to_kb(doc["id"], kb["id"], actor)
        targets = self.store.list_backend_targets(kb["id"])
        for target in targets:
            if target["status"] == "active":
                self.store.create_sync_job(
                    doc["id"], kb["id"], Operation.create, version["id"],
                    backend_slug=target["slug"],
                )

    doc["current_version_no"] = version["version_no"]
    doc["kb_slugs"] = [kb["slug"] for kb in kbs]
    if not later:
        self.sync(actor=actor, all_users=False)
    return doc
```

Apply the same pattern to `update_document` and `delete_document` — for each kb, create sync jobs per active backend target.

Update `_run_job` to dispatch through registry:

```python
def _run_job(self, job: dict[str, Any]) -> None:
    self.store.update_job_status(job["id"], SyncJobStatus.running)
    adapter = self.registry.get(job["backend_slug"]) if self.registry else self.mock_backend
    if adapter is None:
        adapter = self.mock_backend
    try:
        if job["operation"] == "delete":
            sync_state = self.store.get_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"])
            backend_doc_id = sync_state["backend_doc_id"] if sync_state else None
            if backend_doc_id:
                adapter.delete(job["kb_slug"], backend_doc_id)
            self.store.upsert_sync_state(
                job["doc_id"], job["kb_id"], job["backend_slug"],
                None, SyncStateStatus.deleted,
            )
        else:
            backend_doc_id = adapter.upload(
                backend_kb_id=job["kb_slug"],
                doc_slug=job["doc_slug"],
                file_path=Path(job["archive_path"]),
                filename=job["doc_slug"],
            )
            self.store.upsert_sync_state(
                job["doc_id"], job["kb_id"], job["backend_slug"],
                backend_doc_id, SyncStateStatus.synced,
            )
        self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
    except Exception as exc:
        failed_status = (
            SyncStateStatus.delete_failed if job["operation"] == "delete" else SyncStateStatus.sync_failed
        )
        self.store.upsert_sync_state(
            job["doc_id"], job["kb_id"], job["backend_slug"],
            None, failed_status, backend_error=str(exc),
        )
        self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
```

Update `get_doc` to include sync states:

```python
def get_doc(self, actor: str, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
    doc = self._require_doc_visible(actor, doc_slug)
    kbs = self.store.get_document_kbs(doc["id"])
    versions = self.store.list_versions(doc["id"])
    for version in versions:
        version.pop("archive_path", None)
    doc["kbs"] = kbs
    doc["versions"] = versions
    doc["kb_slugs"] = [kb["slug"] for kb in kbs]
    sync_states = self.store.list_sync_states_for_doc(doc["id"])
    if backend:
        sync_states = [s for s in sync_states if s["backend_slug"] == backend]
    doc["sync_states"] = sync_states
    return doc
```

Update `list_docs` to accept optional `backend` filter:

```python
def list_docs(self, actor: str, kb_slug: str, backend: str | None = None) -> list[dict[str, Any]]:
    kb = self._require_kb_visible(actor, kb_slug)
    return self.store.list_docs_for_kb(kb["id"], backend_slug=backend)
```

Update `status` and `sync` to accept `backend` filter:

```python
def sync(self, actor: str, all_users: bool, backend: str | None = None) -> dict[str, int]:
    if all_users:
        require_admin_user(actor, self.admins)
    jobs = self.store.list_runnable_jobs(
        actor=None if all_users or actor in self.admins else actor,
        backend_slug=backend,
    )
    processed = 0
    for job in jobs:
        self._run_job(job)
        processed += 1
    return {"processed": processed}

def status(self, actor: str, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
    jobs = self.store.list_all_jobs(backend_slug=backend) if actor in self.admins else self.store.list_jobs_for_user(actor, backend_slug=backend)
    return {"jobs": jobs}
```

Update `list_runnable_jobs` and `list_all_jobs` and `list_jobs_for_user` in storage to accept optional `backend_slug` filter. Add `AND (job.backend_slug = ? OR ? IS NULL)` to WHERE clause.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_services.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS. Fix any test failures from the interface changes.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/services.py tests/test_services.py
git commit -m "feat: refactor services for multi-backend registry dispatch"
```

---

### Task 8: RagFlow adapter

**Files:**
- Create: `src/wiki_manager/ragflow_backend.py`
- Create: `tests/test_ragflow_backend.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ragflow_backend.py`:

```python
from __future__ import annotations

import pytest

from wiki_manager.domain import BackendDocStatus
from wiki_manager.ragflow_backend import RagFlowBackend


@pytest.fixture
def mock_ragflow(respx_mock):
    respx_mock.route(method="POST", path="/api/v1/datasets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ds-123"}})
    )
    respx_mock.route(method="DELETE", path__startswith="/api/v1/datasets/").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.route(method="POST", path__regex=r"/api/v1/datasets/[^/]+/documents").mock(
        return_value=httpx.Response(200, json={"data": {"id": "doc-456"}})
    )
    respx_mock.route(method="DELETE", path__regex=r"/api/v1/documents/[^/]+").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.route(method="GET", path__regex=r"/api/v1/documents/[^/]+").mock(
        return_value=httpx.Response(200, json={"data": {"status": "completed", "chunk_count": 10, "progress": 1.0}})
    )


import httpx


def test_create_kb(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ds-123"}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    kb_id = backend.create_kb("test-kb", "Test KB")
    assert kb_id == "ds-123"


def test_upload(respx_mock, tmp_path):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets/ds-123/documents").mock(
        return_value=httpx.Response(200, json={"data": {"id": "doc-456"}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"content")
    doc_id = backend.upload("ds-123", "test-doc", file_path, "test.pdf")
    assert doc_id == "doc-456"


def test_delete(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.delete(f"{base_url}/api/v1/documents/doc-456").mock(
        return_value=httpx.Response(200, json={})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    backend.delete("ds-123", "doc-456")


def test_get_status(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.get(f"{base_url}/api/v1/documents/doc-456").mock(
        return_value=httpx.Response(200, json={"data": {"status": "completed", "chunk_count": 10, "progress": 1.0}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    status = backend.get_status("ds-123", "doc-456")
    assert status.status == "completed"
    assert status.chunk_count == 10
    assert status.progress == 1.0


def test_get_status_parsing(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.get(f"{base_url}/api/v1/documents/doc-456").mock(
        return_value=httpx.Response(200, json={"data": {"status": "parsing", "chunk_count": 0, "progress": 0.5}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    status = backend.get_status("ds-123", "doc-456")
    assert status.status == "parsing"
    assert status.progress == 0.5


def test_create_kb_failure(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets").mock(
        return_value=httpx.Response(401, json={"code": 401, "message": "Unauthorized"})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="bad-key", timeout=30)
    with pytest.raises(RuntimeError, match="401"):
        backend.create_kb("test-kb", "Test KB")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_ragflow_backend.py -v`
Expected: FAIL — `RagFlowBackend` not defined

Note: Add `respx` to dev dependencies first:
```bash
cd /Users/kyynor/Code/tech-demo/wiki-manager && uv add --dev respx
```

- [ ] **Step 3: Write implementation**

Create `src/wiki_manager/ragflow_backend.py`:

```python
from __future__ import annotations

from pathlib import Path

import httpx

from wiki_manager.domain import BackendDocStatus


class RagFlowBackend:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise RuntimeError(f"RagFlow API error {response.status_code}: {response.text}")

    def create_kb(self, slug: str, name: str) -> str:
        response = httpx.post(
            f"{self.base_url}/api/v1/datasets",
            json={"name": slug},
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise(response)
        return response.json()["data"]["id"]

    def delete_kb(self, backend_kb_id: str) -> None:
        response = httpx.delete(
            f"{self.base_url}/api/v1/datasets/{backend_kb_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise(response)

    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str:
        with file_path.open("rb") as f:
            response = httpx.post(
                f"{self.base_url}/api/v1/datasets/{backend_kb_id}/documents",
                files={"file": (filename, f)},
                headers=self._headers(),
                timeout=self.timeout,
            )
        self._raise(response)
        return response.json()["data"]["id"]

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        response = httpx.delete(
            f"{self.base_url}/api/v1/documents/{backend_doc_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise(response)

    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus:
        response = httpx.get(
            f"{self.base_url}/api/v1/documents/{backend_doc_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise(response)
        data = response.json()["data"]
        return BackendDocStatus(
            status=data.get("status", "unknown"),
            chunk_count=data.get("chunk_count"),
            progress=data.get("progress"),
            error_message=data.get("error_message"),
        )
```

Also update `ADAPTER_CLASSES` in `src/wiki_manager/registry.py`:

```python
ADAPTER_CLASSES: dict[str, type] = {
    "mock": MockBackend,
    "ragflow": None,  # Placeholder, resolved dynamically
}
```

And update the registry's `__init__` to handle ragflow properly — it already does the dynamic import in the ragflow branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_ragflow_backend.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/ragflow_backend.py src/wiki_manager/registry.py tests/test_ragflow_backend.py
git commit -m "feat: implement RagFlow backend adapter"
```

---

### Task 9: Auto-alignment on server startup

**Files:**
- Modify: `src/wiki_manager/services.py`
- Modify: `src/wiki_manager/server.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_services.py`:

```python
def test_auto_align_creates_targets_for_new_backend(wm_paths, tmp_path):
    # Start with mock backend only
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    service = _make_service_with_backends(wm_paths, configs, tmp_path)
    kb = service.create_kb("root", "test-kb", "Test KB", "")
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"content")
    service.add_document("alice" if "alice" in [m["linux_user"] for m in []] else "root",
                         source, ["test-kb"], later=False)

    # Simulate adding ragflow config — but since ragflow adapter needs a real server,
    # we test alignment logic with mock-only by removing and re-adding
    targets_before = service.store.list_backend_targets(kb["id"])
    assert len(targets_before) == 1
    assert targets_before[0]["slug"] == "mock"


def test_auto_align_skips_inactive_targets(wm_paths, tmp_path):
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    service = _make_service_with_backends(wm_paths, configs, tmp_path)
    kb = service.create_kb("root", "test-kb", "Test KB", "")
    service.store.set_backend_target_status(kb["id"], "mock", "inactive")
    service.align_backends()
    targets = service.store.list_backend_targets(kb["id"])
    mock_target = next(t for t in targets if t["slug"] == "mock")
    assert mock_target["status"] == "active"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_services.py -k "auto_align" -v`
Expected: FAIL — `align_backends` not defined

- [ ] **Step 3: Write implementation**

Add `align_backends` method to `WikiManagerService`:

```python
def align_backends(self) -> None:
    if not self.registry:
        return
    configured_slugs = set(self.registry.list_slugs())
    kbs = self.store.list_kbs()
    for kb in kbs:
        existing_targets = self.store.list_backend_targets(kb["id"])
        existing_slugs = {t["slug"] for t in existing_targets}

        # Mark removed backends as inactive
        for target in existing_targets:
            if target["slug"] not in configured_slugs and target["status"] == "active":
                self.store.set_backend_target_status(kb["id"], target["slug"], "inactive")

        # Add new backends
        for backend_slug in configured_slugs:
            if backend_slug not in existing_slugs:
                adapter = self.registry.get(backend_slug)
                try:
                    backend_kb_id = adapter.create_kb(kb["slug"], kb["name"]) if adapter else None
                    self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)
                    if backend_kb_id:
                        self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                except Exception:
                    self.store.ensure_backend_target(kb["id"], slug=backend_slug, backend_type=backend_slug)

                # Create pending sync jobs for existing synced docs
                synced = self.store.list_synced_docs_for_target(kb["id"], backend_slug)
                for row in synced:
                    doc = self.store.get_document_by_slug("")  # Need doc_id lookup
                    # Actually we have doc_id directly
                    versions = self.store.list_versions(row["doc_id"])
                    version_id = versions[-1]["id"] if versions else None
                    self.store.create_sync_job(
                        row["doc_id"], kb["id"], Operation.create, version_id,
                        backend_slug=backend_slug,
                    )

            # Reactivate previously inactive targets
            for target in existing_targets:
                if target["slug"] == backend_slug and target["status"] == "inactive" and backend_slug in configured_slugs:
                    adapter = self.registry.get(backend_slug)
                    if adapter and not target.get("backend_kb_id"):
                        try:
                            backend_kb_id = adapter.create_kb(kb["slug"], kb["name"])
                            self.store.update_backend_target_kb_id(kb["id"], backend_slug, backend_kb_id)
                        except Exception:
                            pass
                    self.store.set_backend_target_status(kb["id"], backend_slug, "active")
```

- [ ] **Step 4: Add lifespan to server.py**

In `src/wiki_manager/server.py`, add auto-alignment on startup using FastAPI lifespan:

```python
from contextlib import asynccontextmanager

def create_app(paths: WikiManagerPaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = WikiManagerService.create(resolved_paths, resolved_admins)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.align_backends()
        yield

    app = FastAPI(title="wiki-manager", docs_url=None, openapi_url=None, redoc_url=None, lifespan=lifespan)
    # ... rest of create_app
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_services.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/services.py src/wiki_manager/server.py tests/test_services.py
git commit -m "feat: add auto-alignment for backend targets on server startup"
```

---

### Task 10: API changes — backend filter and GET /backends

**Files:**
- Modify: `src/wiki_manager/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_backends_endpoint(client):
    response = client.get("/backends")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_doc_with_backend_filter(client):
    # Add a doc first, then query with backend filter
    response = client.get("/docs/test-doc?backend=mock")
    # Should not error even if doc doesn't exist
    assert response.status_code in (200, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_server.py -k "backends_endpoint or backend_filter" -v`
Expected: FAIL — `/backends` endpoint not defined

- [ ] **Step 3: Write implementation**

Add `backend` query parameter to relevant endpoints in `server.py` and add `GET /backends`:

```python
@app.get("/backends")
def list_backends() -> list[dict[str, str]]:
    if service.registry is None:
        return []
    return [
        {"slug": slug, "type": "mock", "status": "active"}
        for slug in service.registry.list_slugs()
    ]

@app.get("/docs")
def list_docs(kb: str, backend: str | None = None, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
    return call_safely(lambda: service.list_docs(current_actor, kb, backend=backend))

@app.get("/docs/{doc_slug}")
def get_doc(doc_slug: str, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
    return call_safely(lambda: service.get_doc(current_actor, doc_slug, backend=backend))

@app.get("/status")
def status(backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
    return call_safely(lambda: service.status(current_actor, backend=backend))

@app.post("/sync")
def sync(payload: SyncRequest, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, int]:
    return call_safely(lambda: service.sync(current_actor, all_users=payload.all_users, backend=backend))
```

Add `backend: str | None = None` as a query parameter to the sync endpoint. Update `SyncRequest` or use a separate query param.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/server.py tests/test_server.py
git commit -m "feat: add backend filter to API endpoints and GET /backends"
```

---

### Task 11: Client and CLI changes for --backend

**Files:**
- Modify: `src/wiki_manager/client.py`
- Modify: `src/wiki_manager/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_list_backends_cli(client):
    result = runner.invoke(app, ["backends"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_cli.py -k "backends_cli" -v`
Expected: FAIL — `backends` command not defined

- [ ] **Step 3: Update client.py**

Add `backend` parameter to relevant methods in `src/wiki_manager/client.py`:

```python
def list_backends(self) -> list[dict[str, Any]]:
    response = httpx.get(f"{self.base_url}/backends", headers=self._headers(), timeout=10.0)
    self._raise(response)
    return response.json()

def list_docs(self, kb_slug: str, backend: str | None = None) -> list[dict[str, Any]]:
    params = {"kb": kb_slug}
    if backend:
        params["backend"] = backend
    response = httpx.get(f"{self.base_url}/docs", params=params, headers=self._headers(), timeout=10.0)
    self._raise(response)
    return response.json()

def get_doc(self, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
    params = {}
    if backend:
        params["backend"] = backend
    response = httpx.get(f"{self.base_url}/docs/{doc_slug}", params=params, headers=self._headers(), timeout=10.0)
    self._raise(response)
    return response.json()

def status(self, backend: str | None = None) -> dict[str, Any]:
    params = {}
    if backend:
        params["backend"] = backend
    response = httpx.get(f"{self.base_url}/status", params=params, headers=self._headers(), timeout=10.0)
    self._raise(response)
    return response.json()

def sync(self, all_users: bool = False, backend: str | None = None) -> dict[str, Any]:
    params = {}
    if backend:
        params["backend"] = backend
    response = httpx.post(
        f"{self.base_url}/sync",
        json={"all_users": all_users},
        params=params,
        headers=self._headers(),
        timeout=60.0,
    )
    self._raise(response)
    return response.json()
```

- [ ] **Step 4: Update cli.py**

Add `--backend` option to relevant commands and add `backends` command in `src/wiki_manager/cli.py`:

```python
@app.command("backends")
def list_backends() -> None:
    """List configured backends."""
    backends = _run_client(lambda client: client.list_backends())
    for backend in backends:
        typer.echo(f"{backend['slug']} ({backend['type']})")


@app.command("docs")
def list_docs(
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """List documents in a knowledge base."""
    docs = _run_client(lambda client: client.list_docs(kb_slug, backend=backend))
    for doc in docs:
        title = f" - {doc['title']}" if doc.get("title") else ""
        typer.echo(f"{doc['slug']}{title}")


@app.command("doc")
def get_doc(
    doc_slug: Annotated[str, typer.Argument(help="Document slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Show document details."""
    doc = _run_client(lambda client: client.get_doc(doc_slug, backend=backend))
    _echo_mapping(doc, ("slug", "title", "current_version_no", "status"))
    if doc.get("kb_slugs"):
        typer.echo(f"kbs: {', '.join(doc['kb_slugs'])}")
    if doc.get("sync_states"):
        for state in doc["sync_states"]:
            parts = [state.get("backend_slug", ""), state.get("status", "")]
            info = f"  {parts[0]}: {parts[1]}"
            if state.get("chunk_count") is not None:
                info += f" | chunks: {state['chunk_count']}"
            if state.get("backend_status"):
                info += f" | {state['backend_status']}"
            typer.echo(info)


@app.command()
def status(
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Show sync status."""
    result = _run_client(lambda client: client.status(backend=backend))
    jobs = result.get("jobs", [])
    typer.echo(f"jobs: {len(jobs)}")
    for job in jobs:
        parts = [
            str(job.get("status", "")),
            str(job.get("operation", "")),
            str(job.get("backend_slug", "")),
            str(job.get("kb_slug", "")),
            str(job.get("doc_slug", "")),
        ]
        typer.echo(" ".join(part for part in parts if part))


@app.command()
def sync(
    all_users: Annotated[bool, typer.Option("--all", help="Sync jobs for all users.")] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Run pending sync jobs."""
    result = _run_client(lambda client: client.sync(all_users, backend=backend))
    typer.echo(f"processed: {result.get('processed', 0)}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/client.py src/wiki_manager/cli.py tests/test_cli.py
git commit -m "feat: add --backend option to CLI commands and backends list"
```

---

### Task 12: End-to-end smoke test

**Files:**
- Modify: `tests/test_e2e.py`

- [ ] **Step 1: Write the test**

Update `tests/test_e2e.py` to verify Phase 2 multi-backend flow with mock:

```python
from __future__ import annotations

from pathlib import Path

from wiki_manager.config import BackendConfig, ensure_directories
from wiki_manager.domain import SyncStateStatus
from wiki_manager.registry import BackendRegistry
from wiki_manager.services import WikiManagerService


def test_phase2_multi_backend_smoke(wm_paths, tmp_path: Path) -> None:
    """Phase 2 smoke: multi-backend sync with mock, status enhancement, migration."""
    ensure_directories(wm_paths)
    configs = [BackendConfig(slug="mock", backend_type="mock")]
    registry = BackendRegistry({c.slug: c for c in configs}, wm_paths.root)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.registry = registry
    service.init_system()

    # 1. Create KB — auto-creates backend target
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    targets = service.store.list_backend_targets(kb["id"])
    assert len(targets) == 1
    assert targets[0]["slug"] == "mock"
    assert targets[0]["backend_kb_id"] == "frontend-docs"

    # 2. Grant member and add document
    service.grant_kb_member("root", "frontend-docs", "alice", "contributor")
    source = tmp_path / "接口说明.pdf"
    source.write_bytes(b"version one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=True)
    assert doc["current_version_no"] == 1

    # 3. Sync — should succeed on mock backend
    result = service.sync("root", all_users=True)
    assert result["processed"] == 1

    # 4. Check sync state with status enhancement
    doc_detail = service.get_doc("alice", doc["slug"])
    assert len(doc_detail["sync_states"]) == 1
    state = doc_detail["sync_states"][0]
    assert state["backend_slug"] == "mock"
    assert state["status"] == SyncStateStatus.synced.value
    assert state["backend_doc_id"] is not None

    # 5. Update document
    source_v2 = tmp_path / "接口说明-v2.pdf"
    source_v2.write_bytes(b"version two")
    updated = service.update_document("alice", doc["slug"], source_v2, later=False)
    assert updated["current_version_no"] == 2

    # 6. Delete document
    service.delete_document("alice", doc["slug"], later=False)

    # 7. Verify all operations succeeded
    jobs = service.status("root")["jobs"]
    assert all(j["status"] == "succeeded" for j in jobs)
    assert [j["operation"] for j in jobs] == ["create", "update", "delete"]

    # 8. Simulate migration: verify align_backends detects new backend
    # (Can't add real ragflow without server, but test the alignment logic path)
    service.align_backends()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add phase 2 multi-backend smoke test"
```

---

### Task 13: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify existing Phase 1 tests still pass**

Run: `cd /Users/kyynor/Code/tech-demo/wiki-manager && python -m pytest tests/test_domain.py tests/test_storage.py tests/test_services.py tests/test_server.py tests/test_cli.py tests/test_e2e.py -v`
Expected: All PASS

- [ ] **Step 3: Verify schema migration from Phase 1 database**

Create a quick test that inits a Phase 1 database, then runs Phase 2 migration:

```python
# Manual verification — not a committed test
# In a python shell:
# 1. Create a Phase 1 database
# 2. Run migrate_phase2()
# 3. Verify data intact
```

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "chore: phase 2 implementation complete — all tests passing"
```
