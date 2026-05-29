# Wiki Manager Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first-phase wiki-manager ingestion ledger: CLI + localhost HTTP service, SQLite state, document archive, KB permissions, versioning, queue sync, and mock backend.

**Architecture:** Keep business behavior out of Typer and FastAPI handlers. CLI sends authenticated-by-convention localhost requests with `X-Wiki-User`; FastAPI delegates to application services; services use focused storage repositories, archive storage, and a mock backend adapter. The first phase trusts the Linux username header and does not implement search, MCP, Web UI, or real RAG backends.

**Tech Stack:** Python 3.11, Typer, FastAPI, Uvicorn, HTTPX, SQLite via stdlib `sqlite3`, pytest, FastAPI TestClient.

---

## Source Specs

- `docs/superpowers/specs/2026-05-29-wiki-manager-project-definition.md`
- `docs/superpowers/specs/2026-05-29-wiki-manager-phase1-design.md`

## Scope Check

The approved first-phase scope is one coherent subsystem: an ingestion ledger with mock synchronization. It includes CLI, service API, SQLite, archive storage, RBAC, queue execution, and mock backend because each part is needed for the end-to-end flow. Search, MCP, real backend adapters, API document generation, and Web UI remain outside this plan.

## Planned File Structure

Create or modify these files:

- Modify `pyproject.toml`: add runtime and test dependencies.
- Modify `src/wiki_manager/cli.py`: replace the hello stub with Typer command groups.
- Keep `src/wiki_manager/__main__.py`: existing entry point remains valid.
- Create `src/wiki_manager/domain.py`: enums, dataclasses, and domain exceptions.
- Create `src/wiki_manager/config.py`: default `/root/wiki-manager` paths, server config loading, admin detection.
- Create `src/wiki_manager/slug.py`: deterministic slug generation and collision suffixing.
- Create `src/wiki_manager/archive.py`: content hashing and archive file writes/removal.
- Create `src/wiki_manager/storage.py`: SQLite schema, connection helper, transactional repository methods.
- Create `src/wiki_manager/mock_backend.py`: local mock backend state and create/update/delete behavior.
- Create `src/wiki_manager/services.py`: application use cases for init, KB management, docs, sync, status.
- Create `src/wiki_manager/server.py`: FastAPI app factory and API routes.
- Create `src/wiki_manager/client.py`: HTTP client used by CLI.
- Create `src/wiki_manager/server_process.py`: local server start/stop/status helpers.
- Create `tests/test_domain.py`: slug and permission tests.
- Create `tests/test_storage.py`: SQLite and archive behavior.
- Create `tests/test_services.py`: application service tests.
- Create `tests/test_server.py`: HTTP API tests.
- Create `tests/test_cli.py`: Typer command tests with mocked HTTP client.
- Create `tests/test_e2e.py`: full local end-to-end smoke test against FastAPI TestClient.

## Task 1: Dependencies, Test Harness, and Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `src/wiki_manager/domain.py`
- Create: `src/wiki_manager/config.py`
- Create: `src/wiki_manager/storage.py`
- Create: `src/wiki_manager/services.py`
- Create: `src/wiki_manager/server.py`
- Create: `src/wiki_manager/client.py`
- Create: `src/wiki_manager/archive.py`
- Create: `src/wiki_manager/slug.py`
- Create: `src/wiki_manager/mock_backend.py`
- Create: `src/wiki_manager/server_process.py`

- [ ] **Step 1: Write the dependency update**

Update `pyproject.toml` so dependencies include the service and test stack:

```toml
[project]
name = "wiki-manager"
version = "0.1.0"
description = "A Python CLI for managing wiki content."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.124.0",
    "httpx>=0.28.0",
    "python-multipart>=0.0.20",
    "typer>=0.26.2",
    "uvicorn>=0.38.0",
]

[dependency-groups]
dev = [
    "pytest>=9.0.0",
]

[project.scripts]
wiki = "wiki_manager.cli:main"

[build-system]
requires = ["uv_build>=0.11.16,<0.12.0"]
build-backend = "uv_build"
```

- [ ] **Step 2: Add minimal import tests**

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import WikiManagerPaths


@pytest.fixture
def wm_paths(tmp_path: Path) -> WikiManagerPaths:
    return WikiManagerPaths.from_root(tmp_path / "wiki-manager")
```

Create `tests/test_domain.py`:

```python
from wiki_manager.domain import KbRole, SyncJobStatus


def test_domain_enums_have_expected_values() -> None:
    assert KbRole.viewer.value == "viewer"
    assert KbRole.contributor.value == "contributor"
    assert KbRole.admin.value == "admin"
    assert SyncJobStatus.pending.value == "pending"
```

- [ ] **Step 3: Run the failing import test**

Run:

```bash
uv sync
uv run pytest tests/test_domain.py -v
```

Expected: FAIL because `wiki_manager.domain` and `WikiManagerPaths` do not exist.

- [ ] **Step 4: Create empty modules and initial enums**

Create `src/wiki_manager/domain.py`:

```python
from __future__ import annotations

from enum import Enum


class KbRole(str, Enum):
    viewer = "viewer"
    contributor = "contributor"
    admin = "admin"


class SyncJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
```

Create `src/wiki_manager/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROOT = Path("/root/wiki-manager")


@dataclass(frozen=True)
class WikiManagerPaths:
    root: Path
    config_dir: Path
    data_dir: Path
    logs_dir: Path
    run_dir: Path
    db_path: Path
    archive_dir: Path
    mock_backend_dir: Path
    server_config_path: Path
    server_log_path: Path
    server_pid_path: Path

    @classmethod
    def from_root(cls, root: Path = DEFAULT_ROOT) -> "WikiManagerPaths":
        return cls(
            root=root,
            config_dir=root / "config",
            data_dir=root / "data",
            logs_dir=root / "logs",
            run_dir=root / "run",
            db_path=root / "data" / "wiki.db",
            archive_dir=root / "data" / "archive",
            mock_backend_dir=root / "data" / "backend" / "mock",
            server_config_path=root / "config" / "server.toml",
            server_log_path=root / "logs" / "server.log",
            server_pid_path=root / "run" / "server.pid",
        )
```

Create the remaining modules with a module docstring only:

```python
"""Archive storage for wiki-manager."""
```

Use that same one-line module shape for `storage.py`, `services.py`, `server.py`, `client.py`, `slug.py`, `mock_backend.py`, and `server_process.py`, changing the text to match the filename.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_domain.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/wiki_manager tests
git commit -m "test: add phase one project skeleton"
```

## Task 2: Domain Model, Slugs, and Permissions

**Files:**
- Modify: `src/wiki_manager/domain.py`
- Modify: `src/wiki_manager/slug.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Extend domain tests**

Replace `tests/test_domain.py` with:

```python
from __future__ import annotations

import pytest

from wiki_manager.domain import (
    AccessDenied,
    DocumentStatus,
    KbRole,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    can_manage_kb,
    can_view_kb,
    can_write_own_doc,
    require_admin_user,
)
from wiki_manager.slug import make_slug, unique_slug


def test_domain_enums_have_expected_values() -> None:
    assert KbRole.viewer.value == "viewer"
    assert KbRole.contributor.value == "contributor"
    assert KbRole.admin.value == "admin"
    assert DocumentStatus.active.value == "active"
    assert DocumentStatus.deleted.value == "deleted"
    assert SyncJobStatus.pending.value == "pending"
    assert SyncStateStatus.delete_failed.value == "delete_failed"


def test_slug_generation_keeps_readable_ascii_and_chinese() -> None:
    assert make_slug("API 说明 v2.pdf") == "api-说明-v2"
    assert make_slug("  Front End Guide.docx ") == "front-end-guide"


def test_slug_generation_falls_back_to_document() -> None:
    assert make_slug("!!!.pdf") == "document"


def test_unique_slug_adds_numeric_suffix() -> None:
    assert unique_slug("guide", {"guide", "guide-2"}) == "guide-3"


def test_permissions_by_role() -> None:
    assert can_view_kb(KbRole.viewer)
    assert can_view_kb(KbRole.contributor)
    assert can_view_kb(KbRole.admin)
    assert not can_write_own_doc(KbRole.viewer)
    assert can_write_own_doc(KbRole.contributor)
    assert can_manage_kb(KbRole.admin)


def test_global_admin_required() -> None:
    require_admin_user("root", {"root"})
    with pytest.raises(AccessDenied):
        require_admin_user("alice", {"root"})


def test_operation_values_are_stable() -> None:
    assert Operation.create.value == "create"
    assert Operation.update.value == "update"
    assert Operation.delete.value == "delete"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_domain.py -v
```

Expected: FAIL because the new enums, helpers, and slug functions are missing.

- [ ] **Step 3: Implement domain and slug helpers**

Replace `src/wiki_manager/domain.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WikiManagerError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AccessDenied(WikiManagerError):
    status_code = 403


class NotFound(WikiManagerError):
    status_code = 404


class ValidationError(WikiManagerError):
    status_code = 400


class ConflictError(WikiManagerError):
    status_code = 409


class KbRole(str, Enum):
    viewer = "viewer"
    contributor = "contributor"
    admin = "admin"


class DocumentStatus(str, Enum):
    active = "active"
    deleted = "deleted"


class SyncJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class SyncStateStatus(str, Enum):
    not_synced = "not_synced"
    synced = "synced"
    sync_failed = "sync_failed"
    delete_pending = "delete_pending"
    deleted = "deleted"
    delete_failed = "delete_failed"


class Operation(str, Enum):
    create = "create"
    update = "update"
    delete = "delete"


@dataclass(frozen=True)
class Actor:
    linux_user: str
    is_global_admin: bool


def can_view_kb(role: KbRole | None) -> bool:
    return role in {KbRole.viewer, KbRole.contributor, KbRole.admin}


def can_write_own_doc(role: KbRole | None) -> bool:
    return role in {KbRole.contributor, KbRole.admin}


def can_manage_kb(role: KbRole | None) -> bool:
    return role == KbRole.admin


def require_admin_user(linux_user: str, admins: set[str]) -> None:
    if linux_user not in admins:
        raise AccessDenied("global admin permission required")
```

Replace `src/wiki_manager/slug.py` with:

```python
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def make_slug(value: str) -> str:
    stem = Path(value.strip()).stem
    normalized = unicodedata.normalize("NFKC", stem).lower()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "-", normalized, flags=re.UNICODE)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    return normalized or "document"


def unique_slug(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_domain.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/domain.py src/wiki_manager/slug.py tests/test_domain.py
git commit -m "feat: add domain roles and slug helpers"
```

## Task 3: Configuration and Archive Storage

**Files:**
- Modify: `src/wiki_manager/config.py`
- Modify: `src/wiki_manager/archive.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write config and archive tests**

Create `tests/test_storage.py`:

```python
from __future__ import annotations

from pathlib import Path

from wiki_manager.archive import ArchiveStorage
from wiki_manager.config import ServerConfig, WikiManagerPaths, ensure_directories, load_server_config


def test_ensure_directories_creates_default_tree(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    ensure_directories(paths)
    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.archive_dir.is_dir()
    assert paths.mock_backend_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.run_dir.is_dir()


def test_load_server_config_writes_default_admin(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    config = load_server_config(paths)
    assert config == ServerConfig(host="127.0.0.1", port=8765, admins={"root"})
    assert "admins = [\"root\"]" in paths.server_config_path.read_text()


def test_archive_store_file_by_hash(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    ensure_directories(paths)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"hello wiki")
    result = ArchiveStorage(paths.archive_dir).store(source)
    assert result.content_hash == "60d59ad88b27b38af2af880116fb26099bdfac2b9d5f828ad2df1f9fe54367d1"
    assert result.file_size == 10
    assert result.archive_path.exists()
    assert result.archive_path.read_bytes() == b"hello wiki"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: FAIL because config and archive implementations are missing.

- [ ] **Step 3: Implement config loading**

Replace `src/wiki_manager/config.py` with:

```python
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROOT = Path("/root/wiki-manager")


@dataclass(frozen=True)
class WikiManagerPaths:
    root: Path
    config_dir: Path
    data_dir: Path
    logs_dir: Path
    run_dir: Path
    db_path: Path
    archive_dir: Path
    mock_backend_dir: Path
    server_config_path: Path
    server_log_path: Path
    server_pid_path: Path

    @classmethod
    def from_root(cls, root: Path = DEFAULT_ROOT) -> "WikiManagerPaths":
        return cls(
            root=root,
            config_dir=root / "config",
            data_dir=root / "data",
            logs_dir=root / "logs",
            run_dir=root / "run",
            db_path=root / "data" / "wiki.db",
            archive_dir=root / "data" / "archive",
            mock_backend_dir=root / "data" / "backend" / "mock",
            server_config_path=root / "config" / "server.toml",
            server_log_path=root / "logs" / "server.log",
            server_pid_path=root / "run" / "server.pid",
        )


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    admins: set[str]


def ensure_directories(paths: WikiManagerPaths) -> None:
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.archive_dir,
        paths.mock_backend_dir,
        paths.logs_dir,
        paths.run_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


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
    )
```

- [ ] **Step 4: Implement archive storage**

Replace `src/wiki_manager/archive.py` with:

```python
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchivedFile:
    content_hash: str
    file_size: int
    archive_path: Path


class ArchiveStorage:
    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir

    def store(self, source: Path) -> ArchivedFile:
        content_hash = self._sha256(source)
        suffix = source.suffix.lower()
        target_dir = self.archive_dir / content_hash[:2] / content_hash[2:4]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{content_hash}{suffix}"
        if not target.exists():
            shutil.copy2(source, target)
        return ArchivedFile(
            content_hash=content_hash,
            file_size=source.stat().st_size,
            archive_path=target,
        )

    def remove(self, archive_path: Path) -> None:
        archive_path.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
```

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/config.py src/wiki_manager/archive.py tests/test_storage.py
git commit -m "feat: add config and archive storage"
```

## Task 4: SQLite Schema and Repository

**Files:**
- Modify: `src/wiki_manager/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Add repository tests**

Append to `tests/test_storage.py`:

```python
from wiki_manager.domain import KbRole, Operation
from wiki_manager.storage import SQLiteStore


def test_sqlite_store_creates_kb_and_members(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    store.grant_member(kb_id=kb["id"], linux_user="alice", role=KbRole.contributor)
    visible = store.list_kbs_for_user("alice")
    assert [item["slug"] for item in visible] == ["frontend-docs"]
    assert store.get_member_role(kb["id"], "alice") == KbRole.contributor


def test_sqlite_store_document_version_and_jobs(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    doc = store.create_document(slug="guide", title="Guide", owner_user="alice")
    store.attach_document_to_kb(doc_id=doc["id"], kb_id=kb["id"], added_by="alice")
    version = store.create_document_version(
        doc_id=doc["id"],
        original_filename="Guide.pdf",
        content_hash="abc123",
        file_size=12,
        mime_type="application/pdf",
        archive_path="/archive/abc123.pdf",
        created_by="alice",
    )
    job = store.create_sync_job(doc_id=doc["id"], kb_id=kb["id"], operation=Operation.create, version_id=version["id"])
    assert version["version_no"] == 1
    assert job["status"] == "pending"
    assert store.list_docs_for_kb(kb_id=kb["id"])[0]["slug"] == "guide"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: FAIL because `SQLiteStore` is missing.

- [ ] **Step 3: Implement SQLiteStore schema and methods**

Implement `src/wiki_manager/storage.py` with these public methods:

```python
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from wiki_manager.domain import DocumentStatus, KbRole, Operation, SyncJobStatus, SyncStateStatus


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def create_kb(self, slug: str, name: str, description: str, created_by: str) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_bases (slug, name, description, created_by) VALUES (?, ?, ?, ?)",
                (slug, name, description, created_by),
            )
            return self.get_kb_by_id(cursor.lastrowid, conn)

    def get_kb_by_id(self, kb_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        own_conn = conn is None
        if own_conn:
            context = self.connect()
            conn = context.__enter__()
        try:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            if row is None:
                raise KeyError(f"kb not found: {kb_id}")
            return dict(row)
        finally:
            if own_conn:
                context.__exit__(None, None, None)
```

Add these public methods to the same `SQLiteStore` class. Each method should open its own transaction with `self.connect()` unless an existing connection is explicitly passed:

```python
    def get_kb_by_slug(self, slug: str) -> dict[str, Any] | None
    def list_kbs_for_user(self, linux_user: str) -> list[dict[str, Any]]
    def grant_member(self, kb_id: int, linux_user: str, role: KbRole) -> None
    def get_member_role(self, kb_id: int, linux_user: str) -> KbRole | None
    def list_document_slugs(self) -> set[str]
    def create_document(self, slug: str, title: str, owner_user: str) -> dict[str, Any]
    def get_document_by_slug(self, slug: str, include_deleted: bool = False) -> dict[str, Any] | None
    def attach_document_to_kb(self, doc_id: int, kb_id: int, added_by: str) -> None
    def create_document_version(self, doc_id: int, original_filename: str, content_hash: str, file_size: int, mime_type: str, archive_path: str, created_by: str) -> dict[str, Any]
    def create_sync_job(self, doc_id: int, kb_id: int, operation: Operation, version_id: int | None) -> dict[str, Any]
    def list_pending_jobs(self) -> list[dict[str, Any]]
    def update_job_status(self, job_id: int, status: SyncJobStatus, error: str | None = None) -> None
    def upsert_sync_state(self, doc_id: int, kb_id: int, backend_slug: str, backend_doc_id: str | None, status: SyncStateStatus) -> None
    def list_docs_for_kb(self, kb_id: int) -> list[dict[str, Any]]
    def list_jobs_for_user(self, linux_user: str) -> list[dict[str, Any]]
    def soft_delete_document(self, doc_id: int) -> None
    def purge_document(self, doc_id: int) -> list[str]
```

Use this `SCHEMA` constant in the same file:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS knowledge_base_members (
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  linux_user TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (kb_id, linux_user)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_user TEXT NOT NULL,
  current_version_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS document_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  original_filename TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  mime_type TEXT NOT NULL,
  archive_path TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (doc_id, version_no)
);
CREATE TABLE IF NOT EXISTS document_kbs (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  added_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  PRIMARY KEY (doc_id, kb_id)
);
CREATE TABLE IF NOT EXISTS backend_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  slug TEXT NOT NULL DEFAULT 'mock',
  backend_type TEXT NOT NULL DEFAULT 'mock',
  config_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kb_id, slug)
);
CREATE TABLE IF NOT EXISTS sync_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  backend_slug TEXT NOT NULL DEFAULT 'mock',
  operation TEXT NOT NULL,
  version_id INTEGER REFERENCES document_versions(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sync_states (
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  backend_slug TEXT NOT NULL DEFAULT 'mock',
  backend_doc_id TEXT,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (doc_id, kb_id, backend_slug)
);
"""
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_manager/storage.py tests/test_storage.py
git commit -m "feat: add sqlite ledger storage"
```

## Task 5: Application Services for KBs, Documents, Versions, and Deletion

**Files:**
- Modify: `src/wiki_manager/services.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Write service tests**

Create `tests/test_services.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import ensure_directories
from wiki_manager.domain import AccessDenied, KbRole, NotFound
from wiki_manager.services import WikiManagerService


def test_admin_creates_kb_and_grants_member(wm_paths) -> None:
    ensure_directories(wm_paths)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    kb = service.create_kb(actor="root", slug="frontend-docs", name="Frontend Docs", description="")
    service.grant_kb_member(actor="root", kb_slug="frontend-docs", linux_user="alice", role=KbRole.contributor)
    assert kb["slug"] == "frontend-docs"
    assert service.list_kbs(actor="alice")[0]["slug"] == "frontend-docs"


def test_non_admin_cannot_create_kb(wm_paths) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    with pytest.raises(AccessDenied):
        service.create_kb(actor="alice", slug="frontend-docs", name="Frontend Docs", description="")


def test_contributor_adds_doc_to_multiple_kbs(wm_paths, tmp_path: Path) -> None:
    ensure_directories(wm_paths)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "backend-docs", "Backend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    service.grant_kb_member("root", "backend-docs", "alice", KbRole.contributor)
    source = tmp_path / "接口说明.pdf"
    source.write_bytes(b"version one")
    doc = service.add_document(actor="alice", source=source, kb_slugs=["frontend-docs", "backend-docs"], later=True)
    assert doc["slug"] == "接口说明"
    assert doc["current_version_no"] == 1
    assert len(service.list_docs(actor="alice", kb_slug="frontend-docs")) == 1
    assert len(service.list_docs(actor="alice", kb_slug="backend-docs")) == 1


def test_update_document_creates_new_version(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("alice", v1, ["frontend-docs"], later=True)
    updated = service.update_document("alice", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_viewer_cannot_add_document(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "bob", KbRole.viewer)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with pytest.raises(AccessDenied):
        service.add_document("bob", source, ["frontend-docs"], later=True)


def test_invisible_kb_returns_not_found(wm_paths) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    with pytest.raises(NotFound):
        service.list_docs(actor="alice", kb_slug="frontend-docs")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_services.py -v
```

Expected: FAIL because `WikiManagerService` is missing.

- [ ] **Step 3: Implement service class**

Implement `src/wiki_manager/services.py` with:

```python
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from wiki_manager.archive import ArchiveStorage
from wiki_manager.config import WikiManagerPaths, ensure_directories
from wiki_manager.domain import (
    AccessDenied,
    KbRole,
    NotFound,
    Operation,
    ValidationError,
    can_write_own_doc,
    require_admin_user,
)
from wiki_manager.slug import make_slug, unique_slug
from wiki_manager.storage import SQLiteStore


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}


class WikiManagerService:
    def __init__(self, paths: WikiManagerPaths, admins: set[str], store: SQLiteStore, archive: ArchiveStorage) -> None:
        self.paths = paths
        self.admins = admins
        self.store = store
        self.archive = archive

    @classmethod
    def create(cls, paths: WikiManagerPaths, admins: set[str]) -> "WikiManagerService":
        ensure_directories(paths)
        return cls(paths=paths, admins=admins, store=SQLiteStore(paths.db_path), archive=ArchiveStorage(paths.archive_dir))

    def init_system(self) -> dict[str, str]:
        ensure_directories(self.paths)
        self.store.init_schema()
        return {"status": "initialized"}

    def create_kb(self, actor: str, slug: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        kb = self.store.create_kb(slug=slug, name=name, description=description, created_by=actor)
        self.store.grant_member(kb_id=kb["id"], linux_user=actor, role=KbRole.admin)
        self.store.ensure_backend_target(kb_id=kb["id"], slug="mock", backend_type="mock")
        return kb
```

Add these public methods to the same `WikiManagerService` class:

```python
    def grant_kb_member(self, actor: str, kb_slug: str, linux_user: str, role: KbRole) -> dict[str, str]
    def list_kbs(self, actor: str) -> list[dict[str, Any]]
    def add_document(self, actor: str, source: Path, kb_slugs: list[str], later: bool) -> dict[str, Any]
    def update_document(self, actor: str, doc_slug: str, source: Path, later: bool) -> dict[str, Any]
    def list_docs(self, actor: str, kb_slug: str) -> list[dict[str, Any]]
    def get_doc(self, actor: str, doc_slug: str) -> dict[str, Any]
    def delete_document(self, actor: str, doc_slug: str) -> dict[str, str]
    def purge_document(self, actor: str, doc_slug: str) -> dict[str, str]
```

Use these helper rules:

```python
    def _require_kb_visible(self, actor: str, kb_slug: str) -> dict[str, Any]:
        kb = self.store.get_kb_by_slug(kb_slug)
        if kb is None:
            raise NotFound("knowledge base not found")
        if actor in self.admins:
            return kb
        if self.store.get_member_role(kb["id"], actor) is None:
            raise NotFound("knowledge base not found")
        return kb

    def _require_kb_write(self, actor: str, kb: dict[str, Any]) -> KbRole:
        if actor in self.admins:
            return KbRole.admin
        role = self.store.get_member_role(kb["id"], actor)
        if not can_write_own_doc(role):
            raise AccessDenied("contributor permission required")
        return role

    def _validate_source(self, source: Path) -> None:
        if not source.is_file():
            raise ValidationError("source file does not exist")
        if source.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValidationError("unsupported file type")
```

Service semantics:

- `add_document`: validate each KB, require contributor/admin, create unique document slug from filename, archive file, create version 1, attach to each KB, create sync jobs with operation `create`, run sync immediately when `later` is false in Task 7 after mock sync exists.
- `update_document`: allow owner, KB admin, or global admin; archive new file; create next version; create update jobs for all attached KBs.
- `delete_document`: soft delete document; create delete jobs for all attached KBs.
- `purge_document`: remove archived paths returned by `store.purge_document`.

- [ ] **Step 4: Add missing storage methods used by service**

Extend `SQLiteStore` with:

```python
    def ensure_backend_target(self, kb_id: int, slug: str, backend_type: str) -> None
    def list_kbs(self) -> list[dict[str, Any]]
    def list_kbs_for_user_or_admin(self, linux_user: str, admins: set[str]) -> list[dict[str, Any]]
    def get_document_kbs(self, doc_id: int) -> list[dict[str, Any]]
    def list_versions(self, doc_id: int) -> list[dict[str, Any]]
    def next_version_no(self, doc_id: int) -> int
    def set_current_version(self, doc_id: int, version_id: int) -> None
```

Ensure every method is covered by `tests/test_services.py`.

- [ ] **Step 5: Run service and storage tests**

Run:

```bash
uv run pytest tests/test_storage.py tests/test_services.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/services.py src/wiki_manager/storage.py tests/test_services.py tests/test_storage.py
git commit -m "feat: add ingestion ledger services"
```

## Task 6: Mock Backend and Sync Execution

**Files:**
- Modify: `src/wiki_manager/mock_backend.py`
- Modify: `src/wiki_manager/services.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Add sync tests**

Append to `tests/test_services.py`:

```python
def test_immediate_add_syncs_to_mock_backend(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("alice", source, ["frontend-docs"], later=False)
    status = service.status(actor="alice")
    assert status["jobs"][0]["status"] == "succeeded"
    docs = service.list_docs(actor="alice", kb_slug="frontend-docs")
    assert docs[0]["sync_status"] == "synced"


def test_sync_processes_later_job(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("alice", source, ["frontend-docs"], later=True)
    before = service.status(actor="alice")
    assert before["jobs"][0]["status"] == "pending"
    result = service.sync(actor="alice", all_users=False)
    assert result["processed"] == 1
    after = service.status(actor="alice")
    assert after["jobs"][0]["status"] == "succeeded"


def test_delete_creates_delete_job_and_sync_marks_deleted(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=False)
    service.delete_document("alice", doc["slug"])
    service.sync(actor="alice", all_users=False)
    status = service.status(actor="alice")
    assert status["jobs"][-1]["operation"] == "delete"
    assert status["jobs"][-1]["status"] == "succeeded"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_services.py -v
```

Expected: FAIL because mock backend and sync methods are missing.

- [ ] **Step 3: Implement mock backend**

Replace `src/wiki_manager/mock_backend.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MockBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def upsert_document(self, kb_slug: str, doc_slug: str, version_no: int, archive_path: str) -> str:
        kb_dir = self.root / kb_slug
        kb_dir.mkdir(parents=True, exist_ok=True)
        backend_doc_id = f"{kb_slug}:{doc_slug}"
        payload = {
            "backend_doc_id": backend_doc_id,
            "doc_slug": doc_slug,
            "version_no": version_no,
            "archive_path": archive_path,
            "status": "active",
        }
        (kb_dir / f"{doc_slug}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return backend_doc_id

    def delete_document(self, kb_slug: str, doc_slug: str) -> None:
        path = self.root / kb_slug / f"{doc_slug}.json"
        path.unlink(missing_ok=True)

    def read_document(self, kb_slug: str, doc_slug: str) -> dict[str, Any] | None:
        path = self.root / kb_slug / f"{doc_slug}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Wire sync into services**

Modify `WikiManagerService.__init__` to accept `mock_backend`, and `create` to instantiate it:

```python
from wiki_manager.mock_backend import MockBackend


class WikiManagerService:
    def __init__(self, paths: WikiManagerPaths, admins: set[str], store: SQLiteStore, archive: ArchiveStorage, mock_backend: MockBackend) -> None:
        self.paths = paths
        self.admins = admins
        self.store = store
        self.archive = archive
        self.mock_backend = mock_backend
```

Add service methods:

```python
    def sync(self, actor: str, all_users: bool) -> dict[str, int]:
        if all_users:
            require_admin_user(actor, self.admins)
        jobs = self.store.list_runnable_jobs(actor=None if all_users or actor in self.admins else actor)
        processed = 0
        for job in jobs:
            self._run_job(job)
            processed += 1
        return {"processed": processed}

    def status(self, actor: str) -> dict[str, list[dict[str, Any]]]:
        jobs = self.store.list_all_jobs() if actor in self.admins else self.store.list_jobs_for_user(actor)
        return {"jobs": jobs}

    def _run_job(self, job: dict[str, Any]) -> None:
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        try:
            if job["operation"] == "delete":
                self.mock_backend.delete_document(job["kb_slug"], job["doc_slug"])
                self.store.upsert_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"], None, SyncStateStatus.deleted)
            else:
                backend_doc_id = self.mock_backend.upsert_document(
                    kb_slug=job["kb_slug"],
                    doc_slug=job["doc_slug"],
                    version_no=job["version_no"],
                    archive_path=job["archive_path"],
                )
                self.store.upsert_sync_state(job["doc_id"], job["kb_id"], job["backend_slug"], backend_doc_id, SyncStateStatus.synced)
            self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
        except Exception as exc:
            self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
```

Modify `add_document`, `update_document`, and `delete_document` so `later=False` calls `self.sync(actor=actor, all_users=False)` after creating jobs.

- [ ] **Step 5: Add storage query methods for sync**

Extend `SQLiteStore` with methods returning rows joined across jobs, docs, KBs, versions, and sync states:

```python
    def list_runnable_jobs(self, actor: str | None) -> list[dict[str, Any]]
    def list_all_jobs(self) -> list[dict[str, Any]]
```

`list_runnable_jobs(actor)` must filter to jobs whose document owner is `actor` or whose KB has actor as admin/contributor. `actor=None` returns every pending or failed job.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/test_services.py tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/mock_backend.py src/wiki_manager/services.py src/wiki_manager/storage.py tests/test_services.py
git commit -m "feat: add mock sync execution"
```

## Task 7: FastAPI Local Service

**Files:**
- Modify: `src/wiki_manager/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_server.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def test_health(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_kb_and_doc_api_flow(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    response = client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "root"},
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "frontend-docs"
    grant = client.post(
        "/kbs/frontend-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Wiki-User": "root"},
    )
    assert grant.status_code == 200
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert doc.status_code == 200
    assert doc.json()["slug"] == "guide"
    docs = client.get("/docs?kb=frontend-docs", headers={"X-Wiki-User": "alice"})
    assert docs.status_code == 200
    assert docs.json()[0]["slug"] == "guide"


def test_invisible_kb_returns_404(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/admin/init", headers={"X-Wiki-User": "root"})
    client.post("/kbs", json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""}, headers={"X-Wiki-User": "root"})
    response = client.get("/docs?kb=frontend-docs", headers={"X-Wiki-User": "alice"})
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: FAIL because `create_app` and routes are missing.

- [ ] **Step 3: Implement FastAPI app**

Replace `src/wiki_manager/server.py` with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from wiki_manager.config import DEFAULT_ROOT, WikiManagerPaths, load_server_config
from wiki_manager.domain import KbRole, WikiManagerError
from wiki_manager.services import WikiManagerService


class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""


class GrantMemberRequest(BaseModel):
    linux_user: str
    role: KbRole


def create_app(paths: WikiManagerPaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = WikiManagerService.create(resolved_paths, resolved_admins)
    app = FastAPI(title="wiki-manager")

    def actor(x_wiki_user: Annotated[str, Header(alias="X-Wiki-User")]) -> str:
        return x_wiki_user

    def call_safely(call):
        try:
            return call()
        except WikiManagerError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}
```

Add these routes inside the same `create_app` function:

```python
    @app.post("/admin/init")
    def admin_init(current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.init_system())

    @app.post("/kbs")
    def create_kb(payload: CreateKbRequest, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.create_kb(current_actor, payload.slug, payload.name, payload.description))

    @app.get("/kbs")
    def list_kbs(current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.list_kbs(current_actor))

    @app.post("/kbs/{kb_slug}/members")
    def grant_member(kb_slug: str, payload: GrantMemberRequest, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.grant_kb_member(current_actor, kb_slug, payload.linux_user, payload.role))

    @app.post("/docs")
    def add_doc(
        current_actor: Annotated[str, Depends(actor)],
        file: Annotated[UploadFile, File()],
        kb: Annotated[list[str], Form()],
        later: Annotated[bool, Form()] = False,
    ):
        tmp_path = resolved_paths.run_dir / f"upload-{current_actor}-{file.filename}"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("wb") as output:
            output.write(file.file.read())
        try:
            return call_safely(lambda: service.add_document(current_actor, tmp_path, kb, later))
        finally:
            tmp_path.unlink(missing_ok=True)
```

Add the remaining routes with this exact service mapping:

```python
    @app.get("/docs")
    def list_docs(kb: str, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.list_docs(current_actor, kb))

    @app.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.get_doc(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/versions")
    def update_doc(
        doc_slug: str,
        current_actor: Annotated[str, Depends(actor)],
        file: Annotated[UploadFile, File()],
        later: Annotated[bool, Form()] = False,
    ):
        tmp_path = resolved_paths.run_dir / f"upload-{current_actor}-{file.filename}"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("wb") as output:
            output.write(file.file.read())
        try:
            return call_safely(lambda: service.update_document(current_actor, doc_slug, tmp_path, later))
        finally:
            tmp_path.unlink(missing_ok=True)

    @app.post("/docs/{doc_slug}/delete")
    def delete_doc(doc_slug: str, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.delete_document(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/purge")
    def purge_doc(doc_slug: str, current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.purge_document(current_actor, doc_slug))

    @app.get("/status")
    def status(current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.status(current_actor))

    @app.post("/sync")
    def sync(payload: dict[str, bool], current_actor: Annotated[str, Depends(actor)]):
        return call_safely(lambda: service.sync(current_actor, all_users=payload.get("all_users", False)))
```

Return `app` as the final statement of `create_app`.

- [ ] **Step 4: Run API tests**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: PASS.

- [ ] **Step 5: Run all non-CLI tests**

Run:

```bash
uv run pytest tests/test_domain.py tests/test_storage.py tests/test_services.py tests/test_server.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/server.py tests/test_server.py
git commit -m "feat: add localhost service api"
```

## Task 8: HTTP Client and Typer CLI Commands

**Files:**
- Modify: `src/wiki_manager/client.py`
- Modify: `src/wiki_manager/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:

```python
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wiki_manager.cli import app


runner = CliRunner()


def test_kb_list_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def list_kbs(self):
            calls.append("list_kbs")
            return [{"slug": "frontend-docs", "role": "contributor"}]

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["kb", "list"])
    assert result.exit_code == 0
    assert "frontend-docs" in result.stdout
    assert calls == ["list_kbs"]


def test_add_command_sends_file_and_kbs(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    captured = {}

    class FakeClient:
        def add_document(self, source, kb_slugs, later):
            captured["source"] = source
            captured["kb_slugs"] = kb_slugs
            captured["later"] = later
            return {"slug": "guide", "current_version_no": 1}

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["add", str(source), "--kb", "frontend-docs", "--later"])
    assert result.exit_code == 0
    assert "guide" in result.stdout
    assert captured == {"source": source, "kb_slugs": ["frontend-docs"], "later": True}


def test_sync_command_prints_processed_count(monkeypatch) -> None:
    class FakeClient:
        def sync(self, all_users):
            return {"processed": 2}

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "processed: 2" in result.stdout
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: FAIL because `WikiManagerClient` and commands are missing.

- [ ] **Step 3: Implement HTTP client**

Replace `src/wiki_manager/client.py` with:

```python
from __future__ import annotations

import getpass
from pathlib import Path
from typing import Any

import httpx


class WikiManagerClient:
    def __init__(self, base_url: str, linux_user: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.linux_user = linux_user

    @classmethod
    def from_config(cls) -> "WikiManagerClient":
        return cls(base_url="http://127.0.0.1:8765", linux_user=getpass.getuser())

    def _headers(self) -> dict[str, str]:
        return {"X-Wiki-User": self.linux_user}

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            raise RuntimeError(str(detail))

    def list_kbs(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/kbs", headers=self._headers(), timeout=10)
        self._raise(response)
        return response.json()
```

Add methods:

```python
    def create_kb(self, slug: str, name: str, description: str) -> dict[str, Any]
    def grant_member(self, kb_slug: str, linux_user: str, role: str) -> dict[str, Any]
    def add_document(self, source: Path, kb_slugs: list[str], later: bool) -> dict[str, Any]
    def update_document(self, doc_slug: str, source: Path, later: bool) -> dict[str, Any]
    def list_docs(self, kb_slug: str) -> list[dict[str, Any]]
    def get_doc(self, doc_slug: str) -> dict[str, Any]
    def delete_document(self, doc_slug: str) -> dict[str, Any]
    def purge_document(self, doc_slug: str) -> dict[str, Any]
    def status(self) -> dict[str, Any]
    def sync(self, all_users: bool = False) -> dict[str, Any]
```

Use multipart upload for `add_document` and `update_document`:

```python
with source.open("rb") as handle:
    response = httpx.post(
        f"{self.base_url}/docs",
        data=[("kb", kb) for kb in kb_slugs] + [("later", str(later).lower())],
        files={"file": (source.name, handle)},
        headers=self._headers(),
        timeout=60,
    )
```

- [ ] **Step 4: Replace Typer hello stub with commands**

Modify `src/wiki_manager/cli.py`:

```python
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from wiki_manager.client import WikiManagerClient
from wiki_manager.server_process import server_status, start_server, stop_server


app = typer.Typer(help="Manage wiki content from the command line.", no_args_is_help=True)
kb_app = typer.Typer(help="Manage logical knowledge bases.")
server_app = typer.Typer(help="Manage the local wiki-manager service.")
app.add_typer(kb_app, name="kb")
app.add_typer(server_app, name="server")
```

Keep `_package_version`, `_version_callback`, `root`, and `main`. Remove `hello`.

Add commands:

```python
@kb_app.command("list")
def kb_list() -> None:
    for kb in WikiManagerClient.from_config().list_kbs():
        typer.echo(f"{kb['slug']}\t{kb.get('role', '')}")


@kb_app.command("create")
def kb_create(slug: str, name: Annotated[str, typer.Option("--name")], description: Annotated[str, typer.Option("--description")] = "") -> None:
    kb = WikiManagerClient.from_config().create_kb(slug, name, description)
    typer.echo(f"created: {kb['slug']}")


@kb_app.command("grant")
def kb_grant(kb_slug: str, linux_user: str, role: str) -> None:
    WikiManagerClient.from_config().grant_member(kb_slug, linux_user, role)
    typer.echo(f"granted: {linux_user} {role} on {kb_slug}")
```

Add top-level commands for `add`, `update`, `delete`, `purge`, `docs`, `doc`, `status`, and `sync`. Each command should call `WikiManagerClient.from_config()` once, print concise success lines, and raise `typer.Exit(1)` after printing RuntimeError messages to stderr.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/client.py src/wiki_manager/cli.py tests/test_cli.py
git commit -m "feat: add cli client commands"
```

## Task 9: Server Process Commands

**Files:**
- Modify: `src/wiki_manager/server_process.py`
- Modify: `src/wiki_manager/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add server command tests**

Append to `tests/test_cli.py`:

```python
def test_server_status_command(monkeypatch) -> None:
    monkeypatch.setattr("wiki_manager.cli.server_status", lambda: {"running": True, "pid": 123})
    result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 0
    assert "running" in result.stdout
    assert "123" in result.stdout
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: FAIL until server process helpers and commands are wired.

- [ ] **Step 3: Implement server process helpers**

Replace `src/wiki_manager/server_process.py` with:

```python
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from wiki_manager.config import WikiManagerPaths, load_server_config


def server_status(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    if not resolved.server_pid_path.exists():
        return {"running": False, "pid": None}
    pid = int(resolved.server_pid_path.read_text(encoding="utf-8").strip())
    config = load_server_config(resolved)
    try:
        response = httpx.get(f"http://{config.host}:{config.port}/health", timeout=2)
        return {"running": response.status_code == 200, "pid": pid}
    except httpx.HTTPError:
        return {"running": False, "pid": pid}


def start_server(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    status = server_status(resolved)
    if status["running"]:
        return status
    config = load_server_config(resolved)
    log = resolved.server_log_path.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "wiki_manager.server:create_app",
            "--factory",
            "--host",
            config.host,
            "--port",
            str(config.port),
        ],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    resolved.server_pid_path.write_text(str(process.pid), encoding="utf-8")
    return {"running": True, "pid": process.pid}


def stop_server(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    if not resolved.server_pid_path.exists():
        return {"stopped": True, "pid": None}
    pid = int(resolved.server_pid_path.read_text(encoding="utf-8").strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    resolved.server_pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
```

- [ ] **Step 4: Add server CLI commands**

In `src/wiki_manager/cli.py`, add:

```python
@server_app.command("start")
def server_start() -> None:
    status = start_server()
    typer.echo(f"running: {status['running']} pid: {status['pid']}")


@server_app.command("stop")
def server_stop() -> None:
    result = stop_server()
    typer.echo(f"stopped: {result['stopped']} pid: {result['pid']}")


@server_app.command("status")
def server_status_cmd() -> None:
    status = server_status()
    typer.echo(f"running: {status['running']} pid: {status['pid']}")
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/server_process.py src/wiki_manager/cli.py tests/test_cli.py
git commit -m "feat: add local server process commands"
```

## Task 10: End-to-End Smoke Test and Documentation Refresh

**Files:**
- Create: `tests/test_e2e.py`
- Modify: `README.md`

- [ ] **Step 1: Write end-to-end smoke test**

Create `tests/test_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def test_phase_one_smoke_flow(wm_paths, tmp_path: Path) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    assert client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "root"},
    ).status_code == 200
    assert client.post(
        "/kbs/frontend-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Wiki-User": "root"},
    ).status_code == 200

    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")

    with v1.open("rb") as handle:
        added = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert added.status_code == 200
    assert added.json()["current_version_no"] == 1

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Wiki-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1

    with v2.open("rb") as handle:
        updated = client.post(
            "/docs/guide/versions",
            data={"later": "true"},
            files={"file": ("Guide-v2.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert updated.status_code == 200
    assert updated.json()["current_version_no"] == 2

    deleted = client.post("/docs/guide/delete", headers={"X-Wiki-User": "alice"})
    assert deleted.status_code == 200
    status = client.get("/status", headers={"X-Wiki-User": "alice"})
    assert status.status_code == 200
    assert len(status.json()["jobs"]) >= 3
```

- [ ] **Step 2: Run smoke test**

Run:

```bash
uv run pytest tests/test_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Update README**

Replace `README.md` with:

```markdown
# wiki-manager

`wiki-manager` is a Python 3.11 CLI and local service for managing an internal knowledge-base ingestion ledger.

Phase 1 focuses on:

- logical knowledge bases
- Linux-user based KB permissions
- original document archiving
- immutable document versions
- immediate and planned sync jobs
- a local mock backend

## Setup

```bash
uv sync
```

## Run Tests

```bash
uv run pytest -v
```

## Local Usage

```bash
uv run wiki server start
uv run wiki kb create frontend-docs --name "Frontend Docs"
uv run wiki kb grant frontend-docs alice contributor
uv run wiki add ./Guide.pdf --kb frontend-docs --later
uv run wiki sync
uv run wiki docs --kb frontend-docs
uv run wiki status
```

By default the service stores configuration, data, logs, and pid files under `/root/wiki-manager`.
The first phase trusts the Linux username sent by the CLI in `X-Wiki-User` and is intended for an internal trusted VM.
```

- [ ] **Step 4: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 5: Verify CLI help**

Run:

```bash
uv run wiki --help
uv run wiki kb --help
uv run wiki server --help
```

Expected: each command exits with code 0 and shows the relevant command group.

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_e2e.py
git commit -m "test: add phase one smoke flow"
```

## Task 11: Final Review and Integration Check

**Files:**
- Review all files changed by Tasks 1-10.

- [ ] **Step 1: Run all verification**

Run:

```bash
uv run pytest -v
uv run wiki --version
uv run wiki --help
```

Expected:

- pytest passes.
- `wiki --version` prints `wiki-manager 0.1.0` or the editable package version.
- `wiki --help` lists `add`, `update`, `delete`, `purge`, `docs`, `doc`, `status`, `sync`, `kb`, and `server`.

- [ ] **Step 2: Inspect git diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: no uncommitted changes after Task 10. If the implementation worker intentionally made final polish changes during review, commit them with:

```bash
git add <changed-files>
git commit -m "chore: polish phase one implementation"
```

- [ ] **Step 3: Confirm spec coverage**

Check these features manually against the implemented commands and tests:

- `/root/wiki-manager` default path exists in config.
- `X-Wiki-User` is sent by CLI and consumed by API.
- Global admins come from `server.toml`, defaulting to `root`.
- KB roles include viewer, contributor, admin.
- One document can attach to multiple KBs.
- Document updates create immutable versions.
- Delete is soft delete with sync job.
- Purge removes archived paths and ledger rows.
- `mock` backend records sync state.
- Search, MCP, Web UI, and real backends are absent.

- [ ] **Step 4: Commit verification notes if README changed**

If the manual review changes README wording or adds troubleshooting notes, run:

```bash
git add README.md
git commit -m "docs: clarify phase one usage"
```

If no files changed, do not create an empty commit.
