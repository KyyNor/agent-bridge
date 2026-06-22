# Claude Mem Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate claude-mem as Agent Bridge's memory engine while keeping Agent Bridge as the UI, profile, hook injection, API, and MetaMCP control plane.

**Architecture:** Agent Bridge owns memory block metadata, profile bindings, Claude Code hook installation, a thin local CLI hook wrapper, service-side hook processing, and MetaMCP memory tools. Each memory block maps to an isolated server-side `CLAUDE_MEM_DATA_DIR`, while claude-mem keeps ownership of worker behavior, storage schema, compression, and retrieval.

**Tech Stack:** Python 3.11, FastAPI, Typer, SQLite, httpx, pytest, Vue 3, TypeScript, Vitest, FastMCP.

---

## File Structure

Create:
- `src/agent_bridge/storage/repositories/memory.py` - SQLite persistence for memory blocks and profile bindings.
- `src/agent_bridge/memory_management/__init__.py` - package marker.
- `src/agent_bridge/memory_management/models.py` - memory status constants and normalized payload helpers.
- `src/agent_bridge/memory_management/service.py` - memory block CRUD, binding, search/timeline/get, permission checks.
- `src/agent_bridge/memory_management/claude_mem/__init__.py` - package marker.
- `src/agent_bridge/memory_management/claude_mem/client.py` - claude-mem worker HTTP client.
- `src/agent_bridge/memory_management/claude_mem/worker.py` - server-side worker discovery, launch, health, and base URL resolution.
- `src/agent_bridge/memory_management/hooks.py` - service-side Claude Code hook action handler.
- `src/agent_bridge/capability_hub/sources/builtin/memory.py` - builtin capability provider for memory tools.
- `src/agent_bridge/api/routes/memory.py` - memory HTTP routes.
- `src/agent_bridge/cli/memory.py` - thin CLI hook command that proxies stdin payload to server.
- `frontend/capabilities/src/views/knowledge/MemoryView.vue` - memory block UI.
- `tests/test_memory_storage.py`
- `tests/test_memory_service.py`
- `tests/test_memory_hooks.py`
- `tests/test_memory_api.py`
- `tests/test_memory_cli.py`
- `tests/test_builtin_memory.py`
- `frontend/capabilities/tests/memoryView.test.ts`

Modify:
- `src/agent_bridge/storage/schema.py` - add `memory_blocks` and `profile_memory_bindings`.
- `src/agent_bridge/storage/sqlite.py` - enable WAL, instantiate memory repository, migrate memory tables.
- `src/agent_bridge/app/service.py` - instantiate `MemoryService` and register `MemoryBuiltinProvider`.
- `src/agent_bridge/api/app.py` - include memory routes and dynamic admin refresh for `service.memory`.
- `src/agent_bridge/api/schemas.py` - memory request schemas.
- `src/agent_bridge/client.py` - client methods for memory hook proxy, block CRUD, and profile binding.
- `src/agent_bridge/cli/app.py` - register `memory` Typer sub-app and add server URL derivation helper.
- `src/agent_bridge/cli/profile.py` - write/remove Agent Bridge-managed Claude Code hooks during `profile use`.
- `src/agent_bridge/capability_hub/gateway/metamcp.py` - add direct memory tools to `DIRECT_BUILTIN_TOOLS`.
- `frontend/capabilities/src/api/types.ts` - memory types.
- `frontend/capabilities/src/api/client.ts` - memory API methods.
- `frontend/capabilities/src/App.vue` - navigation and view registration.
- `frontend/capabilities/src/views/capabilities/ProfilesView.vue` - active memory block selector.

---

### Task 1: SQLite WAL, Memory Schema, and Repository

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Create: `src/agent_bridge/storage/repositories/memory.py`
- Test: `tests/test_memory_storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_memory_storage.py`:

```python
from __future__ import annotations

from agent_bridge.storage.sqlite import SQLiteStore


def test_sqlite_store_uses_wal_mode(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    with store.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "wal"


def test_memory_block_crud_and_profile_binding(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="dev",
        name="Dev",
        description="",
        status="active",
        created_by="root",
    )

    block = store.memory.create_memory_block(
        block_key="dev-memory",
        name="Dev Memory",
        description="Project memory",
        data_dir=str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
        created_by="root",
    )
    assert block["block_key"] == "dev-memory"
    assert block["status"] == "active"

    listed = store.memory.list_memory_blocks()
    assert [item["block_key"] for item in listed] == ["dev-memory"]

    store.memory.set_profile_memory_binding("dev", "dev-memory", enabled=True)
    binding = store.memory.get_profile_memory_binding("dev")
    assert binding == {
        "profile_key": "dev",
        "block_key": "dev-memory",
        "enabled": 1,
    }

    store.memory.set_memory_block_status("dev-memory", "disabled")
    updated = store.memory.get_memory_block("dev-memory")
    assert updated is not None
    assert updated["status"] == "disabled"


def test_memory_binding_survives_as_null_when_block_deleted(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="dev",
        name="Dev",
        description="",
        status="active",
        created_by="root",
    )
    store.memory.create_memory_block(
        block_key="dev-memory",
        name="Dev Memory",
        description="",
        data_dir=str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
        created_by="root",
    )
    store.memory.set_profile_memory_binding("dev", "dev-memory", enabled=True)
    store.memory.delete_memory_block("dev-memory")

    binding = store.memory.get_profile_memory_binding("dev")

    assert binding == {"profile_key": "dev", "block_key": None, "enabled": 1}
```

- [ ] **Step 2: Run storage tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_storage.py -v
```

Expected: fail with `AttributeError: 'SQLiteStore' object has no attribute 'memory'`.

- [ ] **Step 3: Add schema tables**

In `src/agent_bridge/storage/schema.py`, append these statements to `SCHEMA` after `profile_doc_cache`:

```sql
CREATE TABLE IF NOT EXISTS memory_blocks (
  block_key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  data_dir TEXT NOT NULL,
  worker_base_url TEXT,
  last_health_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS profile_memory_bindings (
  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  block_key TEXT REFERENCES memory_blocks(block_key) ON DELETE SET NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_status ON memory_blocks(status);
CREATE INDEX IF NOT EXISTS idx_profile_memory_bindings_block ON profile_memory_bindings(block_key);
```

- [ ] **Step 4: Implement memory repository**

Create `src/agent_bridge/storage/repositories/memory.py`:

```python
"""SQLite memory repository."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict


class MemoryRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def create_memory_block(
        self,
        *,
        block_key: str,
        name: str,
        description: str,
        data_dir: str,
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_blocks (block_key, name, description, data_dir, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (block_key, name, description, data_dir, created_by),
            )
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            block = row_to_dict(row)
            if block is None:
                raise KeyError(f"memory block not found: {block_key}")
            return block

    def list_memory_blocks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  block.*,
                  COUNT(binding.profile_key) AS bound_profile_count
                FROM memory_blocks block
                LEFT JOIN profile_memory_bindings binding ON binding.block_key = block.block_key
                GROUP BY block.block_key
                ORDER BY block.block_key
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_memory_block(self, block_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            return row_to_dict(row)

    def set_memory_block_status(self, block_key: str, status: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_blocks
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE block_key = ?
                """,
                (status, block_key),
            )
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            block = row_to_dict(row)
            if block is None:
                raise KeyError(f"memory block not found: {block_key}")
            return block

    def update_memory_block_health(self, block_key: str, health: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_blocks
                SET last_health_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE block_key = ?
                """,
                (json.dumps(health, ensure_ascii=False, default=str), block_key),
            )

    def delete_memory_block(self, block_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memory_blocks WHERE block_key = ?", (block_key,))

    def get_profile_memory_binding(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT profile_key, block_key, enabled
                FROM profile_memory_bindings
                WHERE profile_key = ?
                """,
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def set_profile_memory_binding(self, profile_key: str, block_key: str | None, *, enabled: bool) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_memory_bindings (profile_key, block_key, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                  block_key = excluded.block_key,
                  enabled = excluded.enabled,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, block_key, int(enabled)),
            )
            row = conn.execute(
                """
                SELECT profile_key, block_key, enabled
                FROM profile_memory_bindings
                WHERE profile_key = ?
                """,
                (profile_key,),
            ).fetchone()
            binding = row_to_dict(row)
            if binding is None:
                raise KeyError(f"profile memory binding not found: {profile_key}")
            return binding
```

- [ ] **Step 5: Enable WAL and wire repository**

In `src/agent_bridge/storage/sqlite.py`, update `__init__`:

```python
from agent_bridge.storage.repositories.memory import MemoryRepository

self.memory = MemoryRepository(db_path, self.connect)
```

Update `connect()` after `conn.row_factory = sqlite3.Row`:

```python
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA busy_timeout = 5000")
conn.execute("PRAGMA foreign_keys = ON")
```

Keep `foreign_keys` enabled after WAL. SQLite returns the journal mode row internally; no result handling is needed.

- [ ] **Step 6: Add migration guard**

In `SQLiteStore.migrate_phase2()`, after `profile_doc_cache` creation, add:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS memory_blocks (
      block_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'active',
      data_dir TEXT NOT NULL,
      worker_base_url TEXT,
      last_health_json TEXT NOT NULL DEFAULT '{}',
      created_by TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS profile_memory_bindings (
      profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
      block_key TEXT REFERENCES memory_blocks(block_key) ON DELETE SET NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_blocks_status ON memory_blocks(status)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_memory_bindings_block ON profile_memory_bindings(block_key)")
```

- [ ] **Step 7: Run storage tests**

Run:

```bash
uv run pytest tests/test_memory_storage.py tests/test_storage.py tests/test_storage_facade.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/memory.py tests/test_memory_storage.py
git commit -m "feat(memory): add memory block storage"
```

---

### Task 2: Memory Service and Normalized Contracts

**Files:**
- Create: `src/agent_bridge/memory_management/__init__.py`
- Create: `src/agent_bridge/memory_management/models.py`
- Create: `src/agent_bridge/memory_management/service.py`
- Modify: `src/agent_bridge/app/service.py`
- Test: `tests/test_memory_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_memory_service.py`:

```python
from __future__ import annotations

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound, ValidationError


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    return service


def test_create_memory_block_uses_server_side_default_data_dir(wm_paths):
    service = _service(wm_paths)

    block = service.memory.create_block(
        actor="root",
        block_key="dev-memory",
        name="Dev Memory",
        description="Project memory",
    )

    assert block["block_key"] == "dev-memory"
    assert block["data_dir"] == str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory")


def test_profile_binding_requires_existing_profile_and_active_block(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")

    binding = service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)

    assert binding["profile_key"] == "dev"
    assert binding["block_key"] == "dev-memory"


def test_profile_binding_rejects_disabled_block(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_block_status("root", "dev-memory", "disabled")

    try:
        service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    except ValidationError as exc:
        assert "memory block is not active" in exc.message
    else:
        raise AssertionError("expected ValidationError")


def test_resolve_profile_block_returns_not_configured_when_unbound(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    resolved = service.memory.resolve_profile_block("root", "dev")

    assert resolved["status"] == "not_configured"
    assert resolved["block"] is None


def test_memory_search_returns_not_configured_for_unbound_profile(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    result = service.memory.search(actor="root", profile_key="dev", query="deploy", limit=5)

    assert result == {"status": "not_configured", "block_key": None, "items": []}
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_service.py -v
```

Expected: fail because `AgentBridgeService` has no `memory`.

- [ ] **Step 3: Add memory models**

Create `src/agent_bridge/memory_management/__init__.py`:

```python
"""Memory management domain for Agent Bridge."""
```

Create `src/agent_bridge/memory_management/models.py`:

```python
from __future__ import annotations

from typing import Any


ACTIVE_MEMORY_STATUSES = {"active", "disabled"}
NOOP_HOOK_STDOUT = '{"continue":true,"suppressOutput":true}'


def normalized_search_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or raw.get("observation_id") or ""),
        "summary": str(raw.get("summary") or raw.get("title") or ""),
        "content_preview": str(raw.get("content_preview") or raw.get("preview") or raw.get("content") or "")[:1000],
        "score": raw.get("score"),
        "timestamp": raw.get("timestamp") or raw.get("created_at"),
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }


def normalized_timeline_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or raw.get("observation_id") or ""),
        "event_type": str(raw.get("event_type") or raw.get("type") or ""),
        "summary": str(raw.get("summary") or raw.get("title") or ""),
        "timestamp": raw.get("timestamp") or raw.get("created_at"),
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }
```

- [ ] **Step 4: Implement service skeleton and CRUD**

Create `src/agent_bridge/memory_management/service.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.core.slug import make_slug
from agent_bridge.memory_management.models import ACTIVE_MEMORY_STATUSES
from agent_bridge.storage.sqlite import SQLiteStore


class MemoryService:
    def __init__(self, *, paths, store: SQLiteStore, admins: set[str], worker_service: Any | None = None) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.worker_service = worker_service

    def create_block(self, actor: str, block_key: str, name: str, description: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = make_slug(block_key)
        if not normalized_key or normalized_key != block_key:
            raise ValidationError("memory block key must be a lowercase slug")
        if self.store.memory.get_memory_block(block_key) is not None:
            raise ValidationError("memory block already exists")
        data_dir = self._default_data_dir(block_key)
        return self.store.memory.create_memory_block(
            block_key=block_key,
            name=name,
            description=description,
            data_dir=str(data_dir),
            created_by=actor,
        )

    def list_blocks(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.memory.list_memory_blocks()

    def get_block(self, actor: str, block_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        block = self.store.memory.get_memory_block(block_key)
        if block is None:
            raise NotFound("memory block not found")
        return block

    def set_block_status(self, actor: str, block_key: str, status: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if status not in ACTIVE_MEMORY_STATUSES:
            raise ValidationError("invalid memory block status")
        if self.store.memory.get_memory_block(block_key) is None:
            raise NotFound("memory block not found")
        return self.store.memory.set_memory_block_status(block_key, status)

    def get_profile_binding(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_profile(profile_key)
        binding = self.store.memory.get_profile_memory_binding(profile_key)
        return binding or {"profile_key": profile_key, "block_key": None, "enabled": 1}

    def set_profile_binding(self, actor: str, profile_key: str, block_key: str | None, *, enabled: bool) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_profile(profile_key)
        if block_key:
            block = self.store.memory.get_memory_block(block_key)
            if block is None:
                raise NotFound("memory block not found")
            if block.get("status") != "active":
                raise ValidationError("memory block is not active")
        return self.store.memory.set_profile_memory_binding(profile_key, block_key, enabled=enabled)

    def resolve_profile_block(self, actor: str, profile_key: str | None) -> dict[str, Any]:
        if not profile_key:
            return {"status": "not_configured", "block": None}
        profile = self.store.get_project_profile(profile_key)
        if profile is None or profile.get("status") != "active":
            return {"status": "not_configured", "block": None}
        binding = self.store.memory.get_profile_memory_binding(profile_key)
        if not binding or not binding.get("enabled") or not binding.get("block_key"):
            return {"status": "not_configured", "block": None}
        block = self.store.memory.get_memory_block(str(binding["block_key"]))
        if block is None or block.get("status") != "active":
            return {"status": "not_configured", "block": None}
        return {"status": "ok", "block": block}

    def search(self, *, actor: str, profile_key: str | None, query: str, limit: int = 10, block_key: str | None = None) -> dict[str, Any]:
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            return {"status": resolved["status"], "block_key": None, "items": []}
        block = resolved["block"]
        if self.worker_service is None:
            return {"status": "worker_error", "block_key": block["block_key"], "items": []}
        return self.worker_service.search(block, query=query, limit=limit)

    def timeline(self, *, actor: str, profile_key: str | None, limit: int = 20, cursor: str | None = None, block_key: str | None = None) -> dict[str, Any]:
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            return {"status": resolved["status"], "block_key": None, "items": [], "next_cursor": None}
        block = resolved["block"]
        if self.worker_service is None:
            return {"status": "worker_error", "block_key": block["block_key"], "items": [], "next_cursor": None}
        return self.worker_service.timeline(block, limit=limit, cursor=cursor)

    def get_observation(self, *, actor: str, profile_key: str | None, observation_id: str, block_key: str | None = None) -> dict[str, Any]:
        resolved = self._resolve_runtime_block(actor, profile_key, block_key)
        if resolved["status"] != "ok":
            return {"status": resolved["status"], "block_key": None, "item": None}
        block = resolved["block"]
        if self.worker_service is None:
            return {"status": "worker_error", "block_key": block["block_key"], "item": None}
        return self.worker_service.get_observation(block, observation_id)

    def _resolve_runtime_block(self, actor: str, profile_key: str | None, block_key: str | None) -> dict[str, Any]:
        if block_key:
            require_admin_user(actor, self.admins)
            block = self.store.memory.get_memory_block(block_key)
            if block is None or block.get("status") != "active":
                return {"status": "not_configured", "block": None}
            return {"status": "ok", "block": block}
        return self.resolve_profile_block(actor, profile_key)

    def _require_profile(self, profile_key: str) -> dict[str, Any]:
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return profile

    def _default_data_dir(self, block_key: str) -> Path:
        return self.paths.data_dir / "claude-mem" / "blocks" / block_key
```

- [ ] **Step 5: Wire MemoryService into app service**

In `src/agent_bridge/app/service.py`, import:

```python
from agent_bridge.memory_management.service import MemoryService
```

Inside `AgentBridgeService.__init__`, after scripts setup:

```python
self.memory = MemoryService(paths=paths, store=store, admins=admins)
```

In `src/agent_bridge/api/app.py`, in `_reload_admins_if_dynamic`, add:

```python
service.memory.admins = reloaded
```

- [ ] **Step 6: Run service tests**

Run:

```bash
uv run pytest tests/test_memory_service.py tests/test_services.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/memory_management src/agent_bridge/app/service.py src/agent_bridge/api/app.py tests/test_memory_service.py
git commit -m "feat(memory): add memory service"
```

---

### Task 3: Claude Mem Client, Worker Service, and Hook Service

**Files:**
- Create: `src/agent_bridge/memory_management/claude_mem/__init__.py`
- Create: `src/agent_bridge/memory_management/claude_mem/client.py`
- Create: `src/agent_bridge/memory_management/claude_mem/worker.py`
- Create: `src/agent_bridge/memory_management/hooks.py`
- Modify: `src/agent_bridge/memory_management/service.py`
- Modify: `src/agent_bridge/app/service.py`
- Test: `tests/test_memory_hooks.py`
- Test: `tests/test_memory_service.py`

- [ ] **Step 1: Write failing hook service tests**

Create `tests/test_memory_hooks.py`:

```python
from __future__ import annotations

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


class FakeWorkerService:
    def __init__(self):
        self.calls = []

    def handle_hook(self, block, *, action, payload, event_name, matcher, timeout_seconds):
        self.calls.append(
            {
                "block_key": block["block_key"],
                "action": action,
                "payload": payload,
                "event_name": event_name,
                "matcher": matcher,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    return service


def test_hook_service_resolves_profile_binding_and_calls_worker(wm_paths):
    service = _service(wm_paths)
    fake_worker = FakeWorkerService()
    service.memory.worker_service = fake_worker
    service.memory.hooks.worker_service = fake_worker

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="observation",
        event_name="PostToolUse",
        matcher="*",
        payload={"tool_name": "Read"},
        timeout_seconds=120,
    )

    assert result == {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}
    assert fake_worker.calls == [
        {
            "block_key": "dev-memory",
            "action": "observation",
            "payload": {"tool_name": "Read"},
            "event_name": "PostToolUse",
            "matcher": "*",
            "timeout_seconds": 120,
        }
    ]


def test_hook_service_returns_noop_when_profile_unbound(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="context",
        event_name="SessionStart",
        matcher="startup|clear|compact",
        payload={"source": "startup"},
        timeout_seconds=60,
    )

    assert result == {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "not_configured"}


def test_hook_service_rejects_unknown_action(wm_paths):
    service = _service(wm_paths)

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="made-up",
        event_name="Stop",
        matcher=None,
        payload={},
        timeout_seconds=60,
    )

    assert result["exit_code"] == 0
    assert result["status"] == "unsupported_action"
    assert result["stdout"] == NOOP_HOOK_STDOUT
```

- [ ] **Step 2: Run hook tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_hooks.py -v
```

Expected: fail because `service.memory.hooks` does not exist.

- [ ] **Step 3: Add claude-mem client**

Create `src/agent_bridge/memory_management/claude_mem/__init__.py`:

```python
"""claude-mem worker integration."""
```

Create `src/agent_bridge/memory_management/claude_mem/client.py`:

```python
from __future__ import annotations

from typing import Any

import httpx

from agent_bridge.memory_management.models import normalized_search_item, normalized_timeline_item


class ClaudeMemClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, query: str, limit: int) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/api/search",
            params={"q": query, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        items = raw_items if isinstance(raw_items, list) else []
        return {"items": [normalized_search_item(item) for item in items if isinstance(item, dict)]}

    def timeline(self, limit: int, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = httpx.get(f"{self.base_url}/api/timeline", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else []
        next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
        return {"items": [normalized_timeline_item(item) for item in items if isinstance(item, dict)], "next_cursor": next_cursor}

    def get_observation(self, observation_id: str) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/api/observation/{observation_id}", timeout=self.timeout)
        response.raise_for_status()
        raw = response.json()
        item = raw if isinstance(raw, dict) else {"content": raw}
        return {
            "item": {
                "id": str(item.get("id") or item.get("observation_id") or observation_id),
                "content": item.get("content") or item.get("text") or "",
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                "raw": item,
            }
        }

```

- [ ] **Step 4: Add worker service**

Create `src/agent_bridge/memory_management/claude_mem/worker.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from agent_bridge.memory_management.claude_mem.client import ClaudeMemClient
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


class ClaudeMemWorkerService:
    def __init__(self, *, paths) -> None:
        self.paths = paths
        self._clients: dict[str, ClaudeMemClient] = {}

    def health(self, block: dict[str, Any]) -> dict[str, Any]:
        plugin_dir = self._plugin_dir()
        if plugin_dir is None:
            return {"status": "claude_mem_not_installed", "message": "claude-mem plugin scripts were not found on the server"}
        if not (plugin_dir / "scripts" / "worker-service.cjs").exists():
            return {"status": "claude_mem_not_installed", "message": "worker-service.cjs was not found"}
        base_url = self._base_url(block)
        return {"status": "worker_ready" if base_url else "worker_starting", "base_url": base_url, "plugin_dir": str(plugin_dir)}

    def search(self, block: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
        client = self._client(block)
        result = client.search(query, limit)
        return {"status": "ok", "block_key": block["block_key"], "items": result["items"]}

    def timeline(self, block: dict[str, Any], *, limit: int, cursor: str | None) -> dict[str, Any]:
        client = self._client(block)
        result = client.timeline(limit, cursor)
        return {"status": "ok", "block_key": block["block_key"], "items": result["items"], "next_cursor": result["next_cursor"]}

    def get_observation(self, block: dict[str, Any], observation_id: str) -> dict[str, Any]:
        client = self._client(block)
        result = client.get_observation(observation_id)
        return {"status": "ok", "block_key": block["block_key"], "item": result["item"]}

    def handle_hook(self, block: dict[str, Any], *, action: str, payload: dict[str, Any], event_name: str | None, matcher: str | None, timeout_seconds: int) -> dict[str, Any]:
        plugin_dir = self._plugin_dir()
        if plugin_dir is None:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "claude-mem plugin scripts were not found on the server", "exit_code": 0, "status": "claude_mem_not_installed"}
        env = os.environ.copy()
        env["CLAUDE_MEM_DATA_DIR"] = str(block["data_dir"])
        try:
            completed = subprocess.run(
                self._hook_command(plugin_dir, action),
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except Exception as exc:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": str(exc), "exit_code": 0, "status": "worker_error"}
        stdout = completed.stdout.strip()
        if action == "start" and not stdout:
            stdout = NOOP_HOOK_STDOUT
        return {"stdout": stdout or NOOP_HOOK_STDOUT, "stderr": completed.stderr, "exit_code": completed.returncode, "status": "ok" if completed.returncode == 0 else "worker_error"}

    def _hook_command(self, plugin_dir: Path, action: str) -> list[str]:
        scripts = plugin_dir / "scripts"
        if action == "version-check":
            return ["node", str(scripts / "version-check.js")]
        return [
            "node",
            str(scripts / "bun-runner.js"),
            str(scripts / "worker-service.cjs"),
            "start" if action == "start" else "hook",
            *([] if action == "start" else ["claude-code", action]),
        ]

    def _client(self, block: dict[str, Any]) -> ClaudeMemClient:
        block_key = str(block["block_key"])
        base_url = self._base_url(block)
        if not base_url:
            raise RuntimeError("claude-mem worker URL is not configured")
        existing = self._clients.get(block_key)
        if existing is not None and existing.base_url == base_url.rstrip("/"):
            return existing
        client = ClaudeMemClient(base_url)
        self._clients[block_key] = client
        return client

    def _base_url(self, block: dict[str, Any]) -> str:
        explicit = str(block.get("worker_base_url") or "").strip()
        if explicit:
            return explicit
        return os.environ.get("CLAUDE_MEM_WORKER_URL", "").strip()

    def _plugin_dir(self) -> Path | None:
        explicit = os.environ.get("CLAUDE_MEM_PLUGIN_ROOT", "").strip()
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
        cache_root = claude_dir / "plugins" / "cache" / "thedotmack" / "claude-mem"
        if cache_root.exists():
            candidates.extend(sorted([p for p in cache_root.iterdir() if p.is_dir()], reverse=True))
        candidates.append(claude_dir / "plugins" / "marketplaces" / "thedotmack" / "plugin")
        for candidate in candidates:
            plugin_dir = candidate / "plugin" if (candidate / "plugin" / "scripts").exists() else candidate
            if (plugin_dir / "scripts" / "bun-runner.js").exists() and (plugin_dir / "scripts" / "worker-service.cjs").exists():
                return plugin_dir
        return None
```

Hook handling intentionally runs the same server-side claude-mem runner commands used by the original plugin hooks. Search, timeline, and detail still use the worker HTTP API once a worker URL is configured or discovered.

- [ ] **Step 5: Add hook service**

Create `src/agent_bridge/memory_management/hooks.py`:

```python
from __future__ import annotations

from typing import Any

from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


CLAUDE_MEM_HOOK_ACTIONS = {
    "version-check",
    "start",
    "context",
    "session-init",
    "observation",
    "file-context",
    "summarize",
}


class MemoryHookService:
    def __init__(self, *, memory_service, worker_service: Any | None = None) -> None:
        self.memory_service = memory_service
        self.worker_service = worker_service

    def handle_claude_code_hook(
        self,
        *,
        actor: str,
        profile_key: str,
        action: str,
        event_name: str | None,
        matcher: str | None,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if action not in CLAUDE_MEM_HOOK_ACTIONS:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "unsupported_action"}
        resolved = self.memory_service.resolve_profile_block(actor, profile_key)
        if resolved["status"] != "ok":
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": resolved["status"]}
        worker = self.worker_service or self.memory_service.worker_service
        if worker is None:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "memory worker service is not configured", "exit_code": 0, "status": "worker_error"}
        return worker.handle_hook(
            resolved["block"],
            action=action,
            payload=payload,
            event_name=event_name,
            matcher=matcher,
            timeout_seconds=timeout_seconds,
        )
```

- [ ] **Step 6: Wire worker and hooks into MemoryService**

Update `src/agent_bridge/memory_management/service.py` imports:

```python
from agent_bridge.memory_management.claude_mem.worker import ClaudeMemWorkerService
from agent_bridge.memory_management.hooks import MemoryHookService
```

Update `MemoryService.__init__`:

```python
self.worker_service = worker_service or ClaudeMemWorkerService(paths=paths)
self.hooks = MemoryHookService(memory_service=self, worker_service=self.worker_service)
```

Add health method:

```python
def block_health(self, actor: str, block_key: str) -> dict[str, Any]:
    block = self.get_block(actor, block_key)
    health = self.worker_service.health(block) if self.worker_service else {"status": "worker_error"}
    self.store.memory.update_memory_block_health(block_key, health)
    return {"block_key": block_key, **health}
```

- [ ] **Step 7: Run hook tests**

Run:

```bash
uv run pytest tests/test_memory_hooks.py tests/test_memory_service.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/memory_management tests/test_memory_hooks.py tests/test_memory_service.py
git commit -m "feat(memory): proxy claude mem hook actions"
```

---

### Task 4: Memory HTTP API

**Files:**
- Modify: `src/agent_bridge/api/schemas.py`
- Create: `src/agent_bridge/api/routes/memory.py`
- Modify: `src/agent_bridge/api/app.py`
- Modify: `src/agent_bridge/client.py`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_memory_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _client(wm_paths):
    app = create_app(paths=wm_paths, admins={"root"})
    return TestClient(app)


def test_memory_block_api_crud_and_binding(wm_paths):
    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capability-profiles",
        json={"profile_key": "dev", "name": "Dev", "description": "", "status": "active"},
        headers=headers,
    )

    created = client.post(
        "/memory/blocks",
        json={"block_key": "dev-memory", "name": "Dev Memory", "description": "Project memory"},
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["block_key"] == "dev-memory"

    binding = client.put(
        "/capability-profiles/dev/memory",
        json={"block_key": "dev-memory", "enabled": True},
        headers=headers,
    )
    assert binding.status_code == 200
    assert binding.json()["block_key"] == "dev-memory"

    read_binding = client.get("/capability-profiles/dev/memory", headers=headers)
    assert read_binding.json()["block_key"] == "dev-memory"


def test_memory_hook_api_returns_noop_when_unbound(wm_paths):
    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capability-profiles",
        json={"profile_key": "dev", "name": "Dev", "description": "", "status": "active"},
        headers=headers,
    )

    response = client.post(
        "/memory/hooks/claude-code/context",
        json={
            "profile_key": "dev",
            "event_name": "SessionStart",
            "matcher": "startup|clear|compact",
            "payload": {"source": "startup"},
            "hook_timeout_seconds": 60,
            "source": "claude-code",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"
    assert response.json()["exit_code"] == 0
```

- [ ] **Step 2: Run API tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_api.py -v
```

Expected: fail with 404 for `/memory/blocks`.

- [ ] **Step 3: Add schemas**

In `src/agent_bridge/api/schemas.py`, add:

```python
class CreateMemoryBlockRequest(BaseModel):
    block_key: str
    name: str
    description: str = ""


class UpdateMemoryBlockStatusRequest(BaseModel):
    status: str


class ProfileMemoryBindingRequest(BaseModel):
    block_key: str | None = None
    enabled: bool = True


class MemoryHookRequest(BaseModel):
    profile_key: str
    event_name: str | None = None
    matcher: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    hook_timeout_seconds: int = 60
    source: str = "claude-code"
```

- [ ] **Step 4: Add API routes**

Create `src/agent_bridge/api/routes/memory.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import (
    CreateMemoryBlockRequest,
    MemoryHookRequest,
    ProfileMemoryBindingRequest,
    UpdateMemoryBlockStatusRequest,
)


def create_memory_routes(service, actor):
    router = APIRouter()

    @router.get("/memory/blocks")
    def list_memory_blocks(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.memory.list_blocks(current_actor)

    @router.post("/memory/blocks")
    def create_memory_block(payload: CreateMemoryBlockRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.create_block(current_actor, payload.block_key, payload.name, payload.description)

    @router.get("/memory/blocks/{block_key}")
    def get_memory_block(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.get_block(current_actor, block_key)

    @router.post("/memory/blocks/{block_key}/status")
    def update_memory_block_status(block_key: str, payload: UpdateMemoryBlockStatusRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.set_block_status(current_actor, block_key, payload.status)

    @router.get("/memory/blocks/{block_key}/health")
    def get_memory_block_health(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.block_health(current_actor, block_key)

    @router.get("/memory/blocks/{block_key}/search")
    def search_memory_block(block_key: str, q: str, limit: int = 10, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.search(actor=current_actor, profile_key=None, block_key=block_key, query=q, limit=limit)

    @router.get("/memory/blocks/{block_key}/timeline")
    def memory_block_timeline(block_key: str, limit: int = 20, cursor: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.timeline(actor=current_actor, profile_key=None, block_key=block_key, limit=limit, cursor=cursor)

    @router.get("/memory/blocks/{block_key}/observations/{observation_id}")
    def get_memory_observation(block_key: str, observation_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.get_observation(actor=current_actor, profile_key=None, block_key=block_key, observation_id=observation_id)

    @router.get("/capability-profiles/{profile_key}/memory")
    def get_profile_memory(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.get_profile_binding(current_actor, profile_key)

    @router.put("/capability-profiles/{profile_key}/memory")
    def set_profile_memory(profile_key: str, payload: ProfileMemoryBindingRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.set_profile_binding(current_actor, profile_key, payload.block_key, enabled=payload.enabled)

    @router.post("/memory/hooks/claude-code/{action}")
    def handle_claude_code_memory_hook(action: str, payload: MemoryHookRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.hooks.handle_claude_code_hook(
            actor=current_actor,
            profile_key=payload.profile_key,
            action=action,
            event_name=payload.event_name,
            matcher=payload.matcher,
            payload=payload.payload,
            timeout_seconds=payload.hook_timeout_seconds,
        )

    return router
```

- [ ] **Step 5: Register memory routes**

In `src/agent_bridge/api/app.py`, before MCP setup:

```python
from agent_bridge.api.routes.memory import create_memory_routes
app.include_router(create_memory_routes(service, actor))
```

- [ ] **Step 6: Add client methods**

In `src/agent_bridge/client.py`, add:

```python
def list_memory_blocks(self) -> list[dict[str, Any]]:
    return self._request("GET", "/memory/blocks").json()

def create_memory_block(self, block_key: str, name: str, description: str) -> dict[str, Any]:
    return self._request("POST", "/memory/blocks", json={"block_key": block_key, "name": name, "description": description}).json()

def get_profile_memory(self, profile_key: str) -> dict[str, Any]:
    return self._request("GET", f"/capability-profiles/{profile_key}/memory").json()

def set_profile_memory(self, profile_key: str, block_key: str | None, enabled: bool = True) -> dict[str, Any]:
    return self._request("PUT", f"/capability-profiles/{profile_key}/memory", json={"block_key": block_key, "enabled": enabled}).json()

def post_memory_hook(self, action: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    return self._request("POST", f"/memory/hooks/claude-code/{action}", json=payload, timeout=timeout).json()
```

- [ ] **Step 7: Run API tests**

Run:

```bash
uv run pytest tests/test_memory_api.py tests/test_capability_api.py tests/test_cli.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/memory.py src/agent_bridge/api/app.py src/agent_bridge/client.py tests/test_memory_api.py
git commit -m "feat(memory): expose memory API"
```

---

### Task 5: Thin CLI Hook Proxy

**Files:**
- Create: `src/agent_bridge/cli/memory.py`
- Modify: `src/agent_bridge/cli/app.py`
- Modify: `src/agent_bridge/client.py`
- Test: `tests/test_memory_cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_memory_cli.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_bridge.cli.app import app


runner = CliRunner()


def test_memory_hook_posts_stdin_payload_to_server(monkeypatch):
    captured = {}

    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            captured["action"] = action
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        [
            "memory",
            "hook",
            "claude-code",
            "observation",
            "--profile",
            "dev",
            "--server-url",
            "http://bridge.example",
            "--event",
            "PostToolUse",
            "--matcher",
            "*",
            "--timeout",
            "120",
        ],
        input=json.dumps({"tool_name": "Read"}),
    )

    assert result.exit_code == 0
    assert result.stdout == '{"continue":true}\n'
    assert captured == {
        "action": "observation",
        "payload": {
            "profile_key": "dev",
            "event_name": "PostToolUse",
            "matcher": "*",
            "payload": {"tool_name": "Read"},
            "hook_timeout_seconds": 120,
            "source": "claude-code",
        },
        "timeout": 125.0,
    }


def test_memory_hook_noops_when_server_unreachable(monkeypatch):
    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        ["memory", "hook", "claude-code", "context", "--profile", "dev", "--server-url", "http://bridge.example"],
        input="{}",
    )

    assert result.exit_code == 0
    assert result.stdout == '{"continue":true,"suppressOutput":true}\n'
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_cli.py -v
```

Expected: fail because `memory` command is not registered.

- [ ] **Step 3: Add server URL derivation helper**

In `src/agent_bridge/cli/app.py`, add:

```python
def _server_url_from_mcp_url(mcp_url: str) -> str:
    if mcp_url.endswith("/mcp"):
        return mcp_url[:-4].rstrip("/")
    return mcp_url.rstrip("/")
```

- [ ] **Step 4: Implement memory CLI**

Create `src/agent_bridge/cli/memory.py`:

```python
from __future__ import annotations

import getpass
import json
import sys
from typing import Annotated

import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.core.config import default_user
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


memory_app = typer.Typer(help="管理记忆与 Claude Code hook 代理", no_args_is_help=True)
hook_app = typer.Typer(help="Claude Code hook 代理", no_args_is_help=True)
memory_app.add_typer(hook_app, name="hook")


@hook_app.command("claude-code")
def claude_code_hook(
    action: Annotated[str, typer.Argument(help="claude-mem hook action")],
    profile: Annotated[str, typer.Option("--profile", help="Agent Bridge profile key")],
    server_url: Annotated[str, typer.Option("--server-url", help="Agent Bridge API base URL")] = "http://127.0.0.1:8765",
    event: Annotated[str | None, typer.Option("--event", help="Claude Code hook event name")] = None,
    matcher: Annotated[str | None, typer.Option("--matcher", help="Claude Code hook matcher")] = None,
    timeout: Annotated[int, typer.Option("--timeout", help="Hook timeout seconds")] = 60,
) -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        client = AgentBridgeClient(server_url, default_user(getpass.getuser()))
        result = client.post_memory_hook(
            action,
            {
                "profile_key": profile,
                "event_name": event,
                "matcher": matcher,
                "payload": payload,
                "hook_timeout_seconds": timeout,
                "source": "claude-code",
            },
            timeout=float(timeout + 5),
        )
        stdout = str(result.get("stdout") or NOOP_HOOK_STDOUT)
        if stdout:
            typer.echo(stdout)
        raise typer.Exit(int(result.get("exit_code") or 0))
    except typer.Exit:
        raise
    except Exception:
        typer.echo(NOOP_HOOK_STDOUT)
        raise typer.Exit(0) from None
```

- [ ] **Step 5: Register memory sub-app**

In `src/agent_bridge/cli/app.py`, import and register:

```python
from agent_bridge.cli.memory import memory_app  # noqa: E402

app.add_typer(memory_app, name="memory")
```

- [ ] **Step 6: Run CLI tests**

Run:

```bash
uv run pytest tests/test_memory_cli.py tests/test_cli.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/cli/app.py src/agent_bridge/cli/memory.py tests/test_memory_cli.py
git commit -m "feat(memory): add thin hook CLI"
```

---

### Task 6: Claude Code Hook Injection in `profile use`

**Files:**
- Modify: `src/agent_bridge/cli/profile.py`
- Modify: `src/agent_bridge/cli/app.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add failing profile use tests**

Append to `tests/test_cli.py`:

```python
def test_profile_use_installs_claude_mem_compatible_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            return {"markdown": f"# {profile_key}\n", "rendered_hash": "abc"}

        def get_profile_memory(self, profile_key):
            return {"profile_key": profile_key, "block_key": "dev-memory", "enabled": 1}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe-readonly", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp", "--yes"],
    )

    assert result.exit_code == 0
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert hooks["Setup"][0]["matcher"] == "*"
    assert hooks["Setup"][0]["hooks"][0]["timeout"] == 300
    assert hooks["Setup"][0]["hooks"][0]["command"].startswith("agent-bridge memory hook claude-code version-check")
    assert hooks["SessionStart"][0]["matcher"] == "startup|clear|compact"
    assert [hook["command"].split("claude-code ", 1)[1].split()[0] for hook in hooks["SessionStart"][0]["hooks"]] == ["start", "context"]
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert hooks["PreToolUse"][0]["matcher"] == "Read"
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 120


def test_profile_use_preserves_user_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user"}]}]}}),
        encoding="utf-8",
    )

    class FakeClient:
        def render_profile_doc(self, profile_key):
            return {"markdown": f"# {profile_key}\n", "rendered_hash": "abc"}

        def get_profile_memory(self, profile_key):
            return {"profile_key": profile_key, "block_key": None, "enabled": 1}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe-readonly", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp", "--yes"],
    )

    assert result.exit_code == 0
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["hooks"]["Stop"][0]["hooks"] == [{"type": "command", "command": "echo user"}]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py::test_profile_use_installs_claude_mem_compatible_hooks tests/test_cli.py::test_profile_use_preserves_user_hooks -v
```

Expected: fail because hooks are not written.

- [ ] **Step 3: Add hook spec helpers**

In `src/agent_bridge/cli/profile.py`, add:

```python
AGENT_BRIDGE_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-memory"

CLAUDE_MEM_COMPATIBLE_HOOKS = {
    "Setup": [
        {"matcher": "*", "actions": [("version-check", 300)]},
    ],
    "SessionStart": [
        {"matcher": "startup|clear|compact", "actions": [("start", 60), ("context", 60)]},
    ],
    "UserPromptSubmit": [
        {"matcher": None, "actions": [("session-init", 60)]},
    ],
    "PostToolUse": [
        {"matcher": "*", "actions": [("observation", 120)]},
    ],
    "PreToolUse": [
        {"matcher": "Read", "actions": [("file-context", 60)]},
    ],
    "Stop": [
        {"matcher": None, "actions": [("summarize", 120)]},
    ],
}


def _claude_settings_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.local.json"
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    raise ValueError("scope 必须是 project 或 user")


def _agent_bridge_hook_command(action: str, *, profile: str, server_url: str, event: str, matcher: str | None, timeout: int) -> str:
    parts = [
        "agent-bridge",
        "memory",
        "hook",
        "claude-code",
        action,
        "--profile",
        profile,
        "--server-url",
        server_url,
        "--event",
        event,
        "--timeout",
        str(timeout),
        "--agent-bridge-hook-id",
        "agent-bridge-memory",
    ]
    if matcher is not None:
        parts.extend(["--matcher", matcher])
    return " ".join(parts)
```

Add `--agent-bridge-hook-id` as a hidden ignored option to `claude_code_hook` in `src/agent_bridge/cli/memory.py`:

```python
agent_bridge_hook_id: Annotated[str, typer.Option("--agent-bridge-hook-id", help="Internal hook marker", hidden=True)] = "",
```

- [ ] **Step 4: Add idempotent hook settings mutation**

In `src/agent_bridge/cli/profile.py`, add:

```python
def _load_claude_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Claude settings must be a JSON object: {path}")
    return loaded


def _strip_agent_bridge_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    copied = dict(settings)
    raw_hooks = copied.get("hooks")
    if not isinstance(raw_hooks, dict):
        return copied
    cleaned: dict[str, Any] = {}
    for event, entries in raw_hooks.items():
        if not isinstance(entries, list):
            cleaned[event] = entries
            continue
        kept_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                kept_entries.append(entry)
                continue
            kept_hooks = [
                hook for hook in hooks
                if not (isinstance(hook, dict) and AGENT_BRIDGE_HOOK_MARKER in str(hook.get("command") or ""))
            ]
            if kept_hooks:
                new_entry = dict(entry)
                new_entry["hooks"] = kept_hooks
                kept_entries.append(new_entry)
        if kept_entries:
            cleaned[event] = kept_entries
    if cleaned:
        copied["hooks"] = cleaned
    else:
        copied.pop("hooks", None)
    return copied


def _install_memory_hooks(settings: dict[str, Any], *, profile: str, server_url: str) -> dict[str, Any]:
    copied = _strip_agent_bridge_hooks(settings)
    hooks = copied.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    else:
        hooks = dict(hooks)
    for event, specs in CLAUDE_MEM_COMPATIBLE_HOOKS.items():
        event_entries = list(hooks.get(event) or [])
        for spec in specs:
            matcher = spec["matcher"]
            entry: dict[str, Any] = {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "bash",
                        "command": _agent_bridge_hook_command(
                            action,
                            profile=profile,
                            server_url=server_url,
                            event=event,
                            matcher=matcher,
                            timeout=timeout,
                        ),
                        "timeout": timeout,
                    }
                    for action, timeout in spec["actions"]
                ]
            }
            if matcher is not None:
                entry["matcher"] = matcher
            event_entries.append(entry)
        hooks[event] = event_entries
    copied["hooks"] = hooks
    return copied


def _write_memory_hooks(scope: str, *, profile: str, server_url: str, enabled: bool) -> Path:
    settings_path = _claude_settings_path(scope)
    settings = _load_claude_settings(settings_path)
    updated = _install_memory_hooks(settings, profile=profile, server_url=server_url) if enabled else _strip_agent_bridge_hooks(settings)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings_path
```

- [ ] **Step 5: Call hook writer from `profile_use`**

In `profile_use`, after writing `.mcp.json`, add:

```python
from agent_bridge.cli.app import _server_url_from_mcp_url

memory_binding = _run_client(lambda client: client.get_profile_memory(profile))
hooks_enabled = bool(memory_binding.get("enabled")) and bool(memory_binding.get("block_key"))
hooks_path = _write_memory_hooks(
    resolved_scope,
    profile=profile,
    server_url=_server_url_from_mcp_url(url),
    enabled=hooks_enabled,
)
```

After echoing existing paths:

```python
typer.echo(f"已写入: {hooks_path}")
```

- [ ] **Step 6: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_memory_cli.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/cli/profile.py src/agent_bridge/cli/memory.py src/agent_bridge/cli/app.py tests/test_cli.py
git commit -m "feat(memory): install claude code memory hooks"
```

---

### Task 7: Builtin Memory Provider and MetaMCP Direct Tools

**Files:**
- Create: `src/agent_bridge/capability_hub/sources/builtin/memory.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/capability_hub/gateway/metamcp.py`
- Test: `tests/test_builtin_memory.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing builtin and MCP tests**

Create `tests/test_builtin_memory.py`:

```python
from __future__ import annotations

import asyncio

from agent_bridge.app.service import AgentBridgeService


def test_memory_builtin_returns_not_configured_for_unbound_profile(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    provider = service.capabilities.builtin_providers["memory"]

    result = asyncio.run(
        provider.execute("root", "search", {"query": "deploy", "limit": 5}, profile_key="dev")
    )

    assert result == {"status": "not_configured", "block_key": None, "items": []}
```

Append to `tests/test_mcp_server.py`:

```python
def test_mcp_exposes_memory_direct_tools_at_top_level():
    import asyncio

    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool

    class FakeProvider:
        def list_tools(self, actor, profile_key):
            return [
                BuiltinTool(
                    "search",
                    "Memory Search",
                    "Search active memory block.",
                    {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
                    "search",
                ),
                BuiltinTool(
                    "timeline",
                    "Memory Timeline",
                    "Read active memory timeline.",
                    {"type": "object", "properties": {"limit": {"type": "integer"}}},
                    "search",
                ),
                BuiltinTool(
                    "get",
                    "Memory Get",
                    "Read memory observation.",
                    {"type": "object", "properties": {"id": {"type": "string"}}},
                    "detail",
                ),
            ]

    class FakeCapabilities:
        builtin_providers = {"memory": FakeProvider()}

        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            return {"success": True, "service": service, "tool_name": tool_name, "result": params}

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService(), profile_key="dev")
    names = [tool.name for tool in asyncio.run(mcp.list_tools())]

    assert "memory_search" in names
    assert "memory_timeline" in names
    assert "memory_get" in names
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_builtin_memory.py tests/test_mcp_server.py::test_mcp_exposes_memory_direct_tools_at_top_level -v
```

Expected: fail because memory provider and direct specs do not exist.

- [ ] **Step 3: Implement provider**

Create `src/agent_bridge/capability_hub/sources/builtin/memory.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService


class MemoryBuiltinProvider:
    source_key = "memory"
    name = "Memory"
    description = "内置记忆检索能力"
    tags = ["builtin", "memory"]

    def __init__(self, service: "AgentBridgeService") -> None:
        self.service = service

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        resolved = self.service.memory.resolve_profile_block(actor, profile_key)
        if resolved["status"] != "ok":
            return []
        block = resolved["block"]
        return [{"resource_type": "memory_block", "resource_key": block["block_key"], "name": block["name"]}]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return [
            BuiltinTool(
                "search",
                "Memory Search",
                "Search the active memory block bound to this profile.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                        "block": {"type": "string"},
                    },
                    "required": ["query"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "timeline",
                "Memory Timeline",
                "Read recent timeline items from the active memory block.",
                {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 20},
                        "cursor": {"type": "string"},
                        "block": {"type": "string"},
                    },
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get",
                "Memory Get",
                "Read a memory observation by id.",
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "block": {"type": "string"},
                    },
                    "required": ["id"],
                },
                ToolType.detail.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]):
        return None

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        block_key = str(arguments.get("block") or "").strip() or None
        if tool == "search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ValidationError("query is required")
            return self.service.memory.search(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                query=query,
                limit=int(arguments.get("limit") or 10),
            )
        if tool == "timeline":
            return self.service.memory.timeline(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                limit=int(arguments.get("limit") or 20),
                cursor=arguments.get("cursor"),
            )
        if tool == "get":
            observation_id = str(arguments.get("id") or "").strip()
            if not observation_id:
                raise ValidationError("id is required")
            return self.service.memory.get_observation(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                observation_id=observation_id,
            )
        raise NotFound("tool not found")
```

- [ ] **Step 4: Register provider**

In `src/agent_bridge/app/service.py`, import and register:

```python
from agent_bridge.capability_hub.sources.builtin.memory import MemoryBuiltinProvider

self.capabilities.register_builtin_provider(MemoryBuiltinProvider(self))
```

- [ ] **Step 5: Add direct tool specs**

In `src/agent_bridge/capability_hub/gateway/metamcp.py`, extend `DIRECT_BUILTIN_TOOLS`:

```python
{"name": "memory_search", "service_key": "memory", "tool_name": "search"},
{"name": "memory_timeline", "service_key": "memory", "tool_name": "timeline"},
{"name": "memory_get", "service_key": "memory", "tool_name": "get"},
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/test_builtin_memory.py tests/test_mcp_server.py tests/test_builtin_wiki.py tests/test_builtin_codegraph.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/capability_hub/sources/builtin/memory.py src/agent_bridge/app/service.py src/agent_bridge/capability_hub/gateway/metamcp.py tests/test_builtin_memory.py tests/test_mcp_server.py
git commit -m "feat(memory): expose memory tools through metamcp"
```

---

### Task 8: Frontend API Types and Memory View

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/App.vue`
- Create: `frontend/capabilities/src/views/knowledge/MemoryView.vue`
- Test: `frontend/capabilities/tests/memoryView.test.ts`

- [ ] **Step 1: Write failing frontend test**

Create `frontend/capabilities/tests/memoryView.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import MemoryView from '../src/views/knowledge/MemoryView.vue'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => ({
  api: {
    listMemoryBlocks: vi.fn(),
    createMemoryBlock: vi.fn(),
    getMemoryBlockHealth: vi.fn(),
    searchMemoryBlock: vi.fn(),
    getMemoryTimeline: vi.fn(),
  },
}))

describe('MemoryView', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('lists memory blocks and shows health', async () => {
    vi.mocked(api.listMemoryBlocks).mockResolvedValue([
      {
        block_key: 'dev-memory',
        name: 'Dev Memory',
        description: 'Project memory',
        status: 'active',
        data_dir: '/data/claude-mem/blocks/dev-memory',
        worker_base_url: null,
        last_health: { status: 'worker_ready' },
        bound_profile_count: 1,
        created_by: 'root',
        created_at: '2026-06-22',
        updated_at: '2026-06-22',
      },
    ])

    const wrapper = mount(MemoryView)
    await Promise.resolve()
    await Promise.resolve()

    expect(wrapper.text()).toContain('Dev Memory')
    expect(wrapper.text()).toContain('worker_ready')
  })
})
```

- [ ] **Step 2: Run frontend test and verify it fails**

Run:

```bash
cd frontend/capabilities && npm test -- memoryView.test.ts
```

Expected: fail because `MemoryView.vue` does not exist.

- [ ] **Step 3: Add memory types**

In `frontend/capabilities/src/api/types.ts`, add:

```typescript
export interface MemoryBlock {
  block_key: string
  name: string
  description: string
  status: string
  data_dir: string
  worker_base_url: string | null
  last_health?: Record<string, unknown>
  bound_profile_count?: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface ProfileMemoryBinding {
  profile_key: string
  block_key: string | null
  enabled: number | boolean
}

export interface MemorySearchResult {
  status: string
  block_key: string | null
  items: Array<{
    id: string
    summary: string
    content_preview: string
    score: number | null
    timestamp: string | null
    metadata: Record<string, unknown>
  }>
}

export interface MemoryTimelineResult {
  status: string
  block_key: string | null
  items: Array<{
    id: string
    event_type: string
    summary: string
    timestamp: string | null
    metadata: Record<string, unknown>
  }>
  next_cursor: string | null
}
```

- [ ] **Step 4: Add API methods**

In `frontend/capabilities/src/api/client.ts`, import memory types and add:

```typescript
listMemoryBlocks: () => get<MemoryBlock[]>('/memory/blocks'),
createMemoryBlock: (block: { block_key: string; name: string; description?: string }) =>
  post<MemoryBlock>('/memory/blocks', block),
getMemoryBlockHealth: (blockKey: string) =>
  get<Record<string, unknown>>(`/memory/blocks/${blockKey}/health`),
searchMemoryBlock: (blockKey: string, query: string, limit = 10) => {
  const qs = new URLSearchParams({ q: query, limit: String(limit) })
  return get<MemorySearchResult>(`/memory/blocks/${blockKey}/search?${qs}`)
},
getMemoryTimeline: (blockKey: string, limit = 20, cursor?: string) => {
  const qs = new URLSearchParams({ limit: String(limit) })
  if (cursor) qs.set('cursor', cursor)
  return get<MemoryTimelineResult>(`/memory/blocks/${blockKey}/timeline?${qs}`)
},
getProfileMemory: (profileKey: string) =>
  get<ProfileMemoryBinding>(`/capability-profiles/${profileKey}/memory`),
setProfileMemory: (profileKey: string, blockKey: string | null, enabled = true) =>
  put<ProfileMemoryBinding>(`/capability-profiles/${profileKey}/memory`, { block_key: blockKey, enabled }),
```

- [ ] **Step 5: Implement MemoryView**

Create `frontend/capabilities/src/views/knowledge/MemoryView.vue` with:

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Database, Plus, RefreshCw, Search } from 'lucide-vue-next'
import { api } from '../../api/client'
import type { MemoryBlock, MemorySearchResult, MemoryTimelineResult } from '../../api/types'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Card, CardContent } from '../../components/ui/card'

const blocks = ref<MemoryBlock[]>([])
const selected = ref<MemoryBlock | null>(null)
const loading = ref(true)
const creating = ref(false)
const error = ref('')
const form = ref({ block_key: '', name: '', description: '' })
const query = ref('')
const searchResult = ref<MemorySearchResult | null>(null)
const timeline = ref<MemoryTimelineResult | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    blocks.value = await api.listMemoryBlocks()
    if (!selected.value && blocks.value.length) selected.value = blocks.value[0]
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createBlock() {
  creating.value = true
  error.value = ''
  try {
    await api.createMemoryBlock(form.value)
    form.value = { block_key: '', name: '', description: '' }
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '创建失败'
  } finally {
    creating.value = false
  }
}

async function runSearch() {
  if (!selected.value || !query.value.trim()) return
  searchResult.value = await api.searchMemoryBlock(selected.value.block_key, query.value.trim(), 10)
}

async function loadTimeline() {
  if (!selected.value) return
  timeline.value = await api.getMemoryTimeline(selected.value.block_key, 20)
}

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold text-foreground">记忆区块</h2>
        <p class="text-sm text-muted-foreground">管理 profile 绑定的 claude-mem 独立数据区。</p>
      </div>
      <Button variant="outline" size="sm" @click="load"><RefreshCw class="mr-2 size-4" />刷新</Button>
    </div>

    <p v-if="error" class="text-sm text-destructive">{{ error }}</p>

    <div class="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
      <div class="space-y-4">
        <Card>
          <CardContent class="space-y-3 p-4">
            <div class="flex items-center gap-2 text-sm font-medium"><Plus class="size-4" />新建记忆区块</div>
            <Input v-model="form.block_key" placeholder="dev-memory" />
            <Input v-model="form.name" placeholder="Dev Memory" />
            <Input v-model="form.description" placeholder="描述" />
            <Button size="sm" :disabled="creating || !form.block_key || !form.name" @click="createBlock">创建</Button>
          </CardContent>
        </Card>

        <button
          v-for="block in blocks"
          :key="block.block_key"
          class="w-full rounded-md border bg-card p-3 text-left hover:bg-accent/5"
          :class="selected?.block_key === block.block_key ? 'border-primary' : 'border-border'"
          @click="selected = block"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="font-medium">{{ block.name }}</span>
            <span class="text-xs text-muted-foreground">{{ block.status }}</span>
          </div>
          <div class="mt-1 text-xs text-muted-foreground">{{ block.block_key }}</div>
          <div class="mt-2 text-xs text-muted-foreground">profiles: {{ block.bound_profile_count || 0 }}</div>
          <div class="mt-1 text-xs text-muted-foreground">{{ String(block.last_health?.status || 'unknown') }}</div>
        </button>
      </div>

      <Card v-if="selected">
        <CardContent class="space-y-5 p-5">
          <div>
            <div class="flex items-center gap-2 text-base font-semibold"><Database class="size-4" />{{ selected.name }}</div>
            <p class="mt-1 text-sm text-muted-foreground">{{ selected.description || selected.block_key }}</p>
            <p class="mt-2 break-all rounded bg-muted px-2 py-1 text-xs text-muted-foreground">{{ selected.data_dir }}</p>
          </div>

          <div class="flex gap-2">
            <Input v-model="query" placeholder="搜索记忆" @keyup.enter="runSearch" />
            <Button size="sm" @click="runSearch"><Search class="mr-2 size-4" />搜索</Button>
            <Button size="sm" variant="outline" @click="loadTimeline">时间线</Button>
          </div>

          <div v-if="searchResult" class="space-y-2">
            <div v-for="item in searchResult.items" :key="item.id" class="rounded border p-3">
              <div class="text-sm font-medium">{{ item.summary || item.id }}</div>
              <p class="mt-1 text-sm text-muted-foreground">{{ item.content_preview }}</p>
            </div>
          </div>

          <div v-if="timeline" class="space-y-2">
            <div v-for="item in timeline.items" :key="item.id" class="rounded border p-3">
              <div class="text-xs text-muted-foreground">{{ item.timestamp }} · {{ item.event_type }}</div>
              <div class="text-sm">{{ item.summary || item.id }}</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  </div>
</template>
```

- [ ] **Step 6: Register navigation**

In `frontend/capabilities/src/App.vue`:

```typescript
const MemoryView = defineAsyncComponent(() => import('./views/knowledge/MemoryView.vue'))
```

Add nav item under `知识管理`:

```typescript
{ key: 'memory', label: '记忆区块', description: '管理 profile 绑定的 claude-mem 记忆区块' },
```

Add template branch:

```vue
<MemoryView v-else-if="view === 'memory'" />
```

- [ ] **Step 7: Run frontend tests**

Run:

```bash
cd frontend/capabilities && npm test -- memoryView.test.ts
cd frontend/capabilities && npm test
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/App.vue frontend/capabilities/src/views/knowledge/MemoryView.vue frontend/capabilities/tests/memoryView.test.ts
git commit -m "feat(memory): add memory block UI"
```

---

### Task 9: Profile UI Memory Binding

**Files:**
- Modify: `frontend/capabilities/src/views/capabilities/ProfilesView.vue`
- Modify: `frontend/capabilities/src/api/types.ts`
- Test: `frontend/capabilities/tests/navigation.test.ts`
- Test: `frontend/capabilities/tests/serviceForm.test.ts`

- [ ] **Step 1: Add memory binding state**

In `ProfilesView.vue`, import:

```typescript
import type { MemoryBlock, ProfileMemoryBinding } from '../../api/types'
```

Add refs near existing config refs:

```typescript
const allMemoryBlocks = ref<MemoryBlock[]>([])
const profileMemory = ref<ProfileMemoryBinding | null>(null)
const pendingMemoryBlock = ref<string>('')
const memoryError = ref('')
```

- [ ] **Step 2: Load binding in `openConfig`**

In the `Promise.all` inside `openConfig`, extend:

```typescript
const [catalog, kbs, repos, full, memoryBlocks, memoryBinding] = await Promise.all([
  api.catalog(),
  api.listWikiKbs(),
  api.listCodeRepos(),
  api.getProfile(p.profile_key),
  api.listMemoryBlocks(),
  api.getProfileMemory(p.profile_key),
])
```

Then assign:

```typescript
allMemoryBlocks.value = memoryBlocks
profileMemory.value = memoryBinding
pendingMemoryBlock.value = memoryBinding.block_key || ''
```

- [ ] **Step 3: Save binding with config**

Find the config save function in `ProfilesView.vue`. After saving source rules/resources/pins/doc settings, add:

```typescript
if (configProfile.value) {
  profileMemory.value = await api.setProfileMemory(
    configProfile.value.profile_key,
    pendingMemoryBlock.value || null,
    true,
  )
}
```

If the component has separate save sections, add this to the same action that saves profile resources.

- [ ] **Step 4: Add UI selector**

Inside the profile config dialog, add a compact section:

```vue
<section class="space-y-3 rounded-md border p-4">
  <div>
    <h3 class="text-sm font-medium text-foreground">记忆</h3>
    <p class="text-xs text-muted-foreground">为此能力平面绑定一个 active memory block。</p>
  </div>
  <select v-model="pendingMemoryBlock" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
    <option value="">未绑定</option>
    <option
      v-for="block in allMemoryBlocks.filter(item => item.status === 'active')"
      :key="block.block_key"
      :value="block.block_key"
    >
      {{ block.name }} ({{ block.block_key }})
    </option>
  </select>
  <p class="text-xs text-muted-foreground">
    运行 agent-bridge profile use {{ configProfile?.profile_key }} --scope project --url http://127.0.0.1:8765/mcp 安装或刷新 Claude Code hooks。
  </p>
</section>
```

- [ ] **Step 5: Run frontend tests and typecheck**

Run:

```bash
cd frontend/capabilities && npm test
cd frontend/capabilities && npm run build
```

Expected: all pass and build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/capabilities/src/views/capabilities/ProfilesView.vue frontend/capabilities/src/api/types.ts
git commit -m "feat(memory): bind memory blocks to profiles in UI"
```

---

### Task 10: Integration Verification and Documentation Updates

**Files:**
- Modify: `docs/TODO.md`
- Modify: `README.md`
- Test: full backend and frontend suites

- [ ] **Step 1: Update TODO checklist**

In `docs/TODO.md`, under `## 记忆`, mark the completed first-stage items:

```markdown
- [x] 新增「记忆区块」实体与 UI，可创建、列表查看、进入详情查看记忆内容。
- [x] profile 增加最多一个 active memory block 的单选绑定。
- [x] `profile use` 根据 active memory block 注入/更新/清理 Claude Code hooks。
- [x] 新增 agent-bridge hook wrapper：读取 Claude Code hook stdin payload，补充 profile/memory block 作用域，转发到 claude-mem worker；worker 不可用时静默降级。
- [x] 新增 claude-mem worker 管理与健康检查：处理未安装、端口变化、版本过旧、worker 未启动、Bun/uv 缺失等状态。
- [x] 在 agent-bridge FastMCP gateway 暴露 `memory_search` / `memory_timeline` / `memory_get`，背后代理 claude-mem 检索，避免用户面对两套 MCP 入口。
- [x] 设计 memory block 与 claude-mem project/data dir 的映射，避免 profile 作用域和 claude-mem 默认 project 作用域混乱。
- [ ] 保留 `<private>`/敏感字段过滤/截断策略，明确额外 observer LLM 调用带来的成本模型。
- [x] SQLite 改 WAL 作为前置或同期工作，降低 hook 高频写入带来的锁冲突风险。
```

If an item remains partially implemented because claude-mem worker discovery is intentionally minimal, leave it unchecked and add a short note below it:

```markdown
  - 第一阶段已支持服务端代理与配置化 worker URL；自动发现/安装向导放第二阶段。
```

- [ ] **Step 2: Update README usage**

In `README.md`, add a short "Memory Integration" section after profile usage:

```markdown
## Memory Integration

Agent Bridge can expose claude-mem memory through the same MetaMCP endpoint used by profiles.

```bash
uv run agent-bridge server start
uv run agent-bridge profile use safe-readonly --scope project --url http://127.0.0.1:8765/mcp
```

When the profile has an active memory block, `profile use` installs Claude Code hooks compatible with claude-mem's original hook events. The local hook command only sends the Claude Code hook payload to the Agent Bridge server; the server resolves the profile's memory block and calls claude-mem with the block's server-side data directory.
```
```

- [ ] **Step 3: Run focused backend suite**

Run:

```bash
uv run pytest \
  tests/test_memory_storage.py \
  tests/test_memory_service.py \
  tests/test_memory_hooks.py \
  tests/test_memory_api.py \
  tests/test_memory_cli.py \
  tests/test_builtin_memory.py \
  tests/test_mcp_server.py \
  tests/test_cli.py \
  -v
```

Expected: all pass.

- [ ] **Step 4: Run broader backend suite**

Run:

```bash
uv run pytest tests/ -v --timeout=60 -x \
  --ignore=tests/test_weknora_integration.py \
  --ignore=tests/test_ragflow_integration.py \
  --ignore=tests/test_server_process.py
```

Expected: all pass. If a pre-existing unrelated failure appears, capture the failing test name and output in the final handoff.

- [ ] **Step 5: Run frontend checks**

Run:

```bash
cd frontend/capabilities && npm test
cd frontend/capabilities && npm run build
```

Expected: all pass.

- [ ] **Step 6: Manual smoke test**

Run:

```bash
uv run agent-bridge server start
uv run agent-bridge profile create mem-dev --name "Memory Dev"
```

Open:

```text
http://127.0.0.1:8765/admin/capabilities#memory
```

Verify:
- Create `dev-memory`.
- Bind `mem-dev` to `dev-memory`.
- Run:

```bash
uv run agent-bridge profile use mem-dev --scope project --url http://127.0.0.1:8765/mcp --yes
```

Check:
- `.mcp.json` includes `agent-bridge`.
- `.claude/settings.local.json` includes exactly the claude-mem compatible hook events with Agent Bridge commands.
- `memory_search` appears in MCP tool list for the profile.

- [ ] **Step 7: Commit**

```bash
git add docs/TODO.md README.md
git commit -m "docs(memory): document claude mem integration"
```

---

## Plan Self-Review

Spec coverage:
- Memory block schema, profile binding, isolated `CLAUDE_MEM_DATA_DIR`, server-side hook proxy, hook compatibility, API, CLI, MetaMCP tools, UI, WAL, health states, and verification are covered by tasks.

No placeholder scan:
- This plan avoids unresolved implementation markers. The second-phase items are deliberately described as future productization, not as missing first-phase behavior; HTML input placeholders in Vue snippets are UI labels, not plan placeholders.

Type consistency:
- Profile binding fields use `profile_key`, `block_key`, and `enabled` consistently across storage, service, API, CLI, and frontend.
- Hook request fields use `profile_key`, `event_name`, `matcher`, `payload`, `hook_timeout_seconds`, and `source` consistently.
- MCP tool names are `memory_search`, `memory_timeline`, and `memory_get`; builtin tool names are `search`, `timeline`, and `get`.
