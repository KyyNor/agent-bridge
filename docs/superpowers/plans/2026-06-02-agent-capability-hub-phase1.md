# Agent Capability Hub Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 Agent Capability Hub loop: register HTTP MCP services in the web app, sync their tools, expose a MetaMCP gateway with `search` and `execute`, and reject action tools at execution time.

**Architecture:** Add a capability registry alongside the existing knowledge-base ledger. Keep storage in SQLite, service logic in focused Python service modules, FastAPI as the admin/API surface, and the existing MCP server entrypoint as the MetaMCP gateway. The web page is a lightweight server-rendered HTML shell with browser `fetch` calls, avoiding a new frontend build system for Phase 1.

**Tech Stack:** Python 3.11, FastAPI, SQLite, `mcp` Python SDK, `httpx`, pytest, FastAPI `TestClient`.

---

## Source Documents

- Product design: `docs/agent-capability-hub-phase1-structured-design.html`
- Page definition: `docs/agent-capability-hub-page-definition.html`

## File Structure

- Create `src/wiki_manager/capabilities.py`
  - Dataclasses and enums for MCP services, MCP tools, search results, and execute results.
- Modify `src/wiki_manager/storage.py`
  - Add SQLite tables and CRUD methods for `mcp_services` and `mcp_tools`.
- Create `src/wiki_manager/mcp_http_client.py`
  - Async client wrapper for Streamable HTTP MCP service `list_tools` and `call_tool`.
- Create `src/wiki_manager/capability_service.py`
  - Business logic for service registration, tool sync, `search`, and `execute`.
- Create `src/wiki_manager/web_pages.py`
  - Minimal HTML admin page for Phase 1 MCP registration and tool browsing.
- Modify `src/wiki_manager/server.py`
  - Add REST endpoints for MCP service registration, sync, listing, tool listing, and the HTML page route.
- Modify `src/wiki_manager/mcp_server.py`
  - Replace the old knowledge-base-only MCP tools with MetaMCP `search` and `execute`.
- Create `tests/test_capability_storage.py`
  - Storage schema and CRUD tests.
- Create `tests/test_capability_service.py`
  - Service-level search, sync, and execute behavior tests with a fake MCP client.
- Create `tests/test_capability_api.py`
  - FastAPI API and HTML route tests.
- Modify `tests/test_mcp_server.py`
  - Update MCP server expectations from `search/ask` to MetaMCP `search/execute`.
- Modify `README.md`
  - Update the project name and Phase 1 usage examples.

## Task 1: Capability Domain and Storage

**Files:**
- Create: `src/wiki_manager/capabilities.py`
- Modify: `src/wiki_manager/storage.py`
- Test: `tests/test_capability_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_capability_storage.py`:

```python
from __future__ import annotations

import json

from wiki_manager.capabilities import McpServiceStatus, ToolType
from wiki_manager.storage import SQLiteStore


def test_mcp_service_crud_round_trip(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    service = store.create_mcp_service(
        service_key="mysql",
        name="MySQL Query MCP",
        endpoint_url="http://localhost:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        description="Query read-only reporting databases.",
        tags=["database", "report"],
        created_by="root",
    )

    assert service["service_key"] == "mysql"
    assert service["status"] == McpServiceStatus.enabled.value
    assert json.loads(service["headers_json"]) == {"Authorization": "Bearer secret"}
    assert json.loads(service["tags_json"]) == ["database", "report"]

    listed = store.list_mcp_services()
    assert [item["service_key"] for item in listed] == ["mysql"]

    store.update_mcp_service_status("mysql", McpServiceStatus.disabled.value)
    assert store.get_mcp_service("mysql")["status"] == McpServiceStatus.disabled.value


def test_mcp_tool_upsert_replaces_synced_schema(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )

    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run a read-only SQL query.",
        input_schema={
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
        tool_type=ToolType.search.value,
        tags=["sql"],
        examples=[
            {
                "service": "mysql",
                "tool": "query_sql",
                "arguments": {"sql": "select 1"},
            }
        ],
    )
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run SQL against reporting databases.",
        input_schema={
            "type": "object",
            "properties": {"db": {"type": "string"}, "sql": {"type": "string"}},
            "required": ["db", "sql"],
        },
        tool_type=ToolType.search.value,
        tags=["sql", "report"],
        examples=[],
    )

    tools = store.list_mcp_tools("mysql")
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "query_sql"
    assert json.loads(tools[0]["input_schema_json"])["required"] == ["db", "sql"]
    assert json.loads(tools[0]["tags_json"]) == ["sql", "report"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_storage.py -v
```

Expected: FAIL with import errors for `wiki_manager.capabilities` and missing `SQLiteStore` methods.

- [ ] **Step 3: Add capability dataclasses and enums**

Create `src/wiki_manager/capabilities.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class McpServiceStatus(str, Enum):
    enabled = "enabled"
    disabled = "disabled"
    error = "error"


class ToolType(str, Enum):
    overview = "overview"
    search = "search"
    detail = "detail"
    action = "action"


@dataclass(frozen=True)
class SearchRequest:
    path: str | None = None
    query: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class ExecuteRequest:
    service: str
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ExecuteResult:
    service: str
    tool: str
    success: bool
    result: Any
    error: str | None = None
```

- [ ] **Step 4: Extend SQLite schema**

Modify `SCHEMA` in `src/wiki_manager/storage.py` by appending these tables after `sync_states`:

```sql
CREATE TABLE IF NOT EXISTS mcp_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  endpoint_url TEXT NOT NULL,
  headers_json TEXT NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'enabled',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at TEXT,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS mcp_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL REFERENCES mcp_services(service_key) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  tool_type TEXT NOT NULL DEFAULT 'search',
  tags_json TEXT NOT NULL DEFAULT '[]',
  examples_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (service_key, tool_name)
);
```

- [ ] **Step 5: Add storage CRUD methods**

Add these methods to `SQLiteStore` in `src/wiki_manager/storage.py`:

```python
    def create_mcp_service(
        self,
        *,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, str],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO mcp_services
                  (service_key, name, endpoint_url, headers_json, description, tags_json, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_key,
                    name,
                    endpoint_url,
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    created_by,
                ),
            )
            row = conn.execute("SELECT * FROM mcp_services WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def update_mcp_service(
        self,
        service_key: str,
        *,
        name: str,
        endpoint_url: str,
        headers: dict[str, str],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE mcp_services
                SET name = ?, endpoint_url = ?, headers_json = ?, description = ?,
                    tags_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (
                    name,
                    endpoint_url,
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    service_key,
                ),
            )
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            if row is None:
                raise KeyError(f"mcp service not found: {service_key}")
            return dict(row)

    def get_mcp_service(self, service_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM mcp_services WHERE service_key = ?", (service_key,)).fetchone()
            return _row_to_dict(row)

    def list_mcp_services(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM mcp_services ORDER BY service_key").fetchall()
            return [dict(row) for row in rows]

    def update_mcp_service_status(self, service_key: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE mcp_services SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE service_key = ?",
                (status, service_key),
            )

    def mark_mcp_service_sync(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE mcp_services
                SET last_synced_at = CURRENT_TIMESTAMP,
                    last_error = ?,
                    status = CASE WHEN ? THEN status ELSE 'error' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (error, 1 if success else 0, service_key),
            )

    def upsert_mcp_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        tool_type: str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_tools
                  (service_key, tool_name, display_name, description, input_schema_json,
                   tool_type, tags_json, examples_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_key, tool_name) DO UPDATE SET
                  display_name = excluded.display_name,
                  description = excluded.description,
                  input_schema_json = excluded.input_schema_json,
                  tool_type = excluded.tool_type,
                  tags_json = excluded.tags_json,
                  examples_json = excluded.examples_json,
                  status = 'active',
                  synced_at = CURRENT_TIMESTAMP,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    service_key,
                    tool_name,
                    display_name,
                    description,
                    json.dumps(input_schema, ensure_ascii=False),
                    tool_type,
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(examples, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            return dict(row)

    def list_mcp_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if service_key is None:
                rows = conn.execute("SELECT * FROM mcp_tools ORDER BY service_key, tool_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_tools WHERE service_key = ? ORDER BY tool_name",
                    (service_key,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_mcp_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            return _row_to_dict(row)
```

- [ ] **Step 6: Run storage tests**

Run:

```bash
uv run pytest tests/test_capability_storage.py -v
```

Expected: PASS.

- [ ] **Step 7: Run existing storage regression tests**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/wiki_manager/capabilities.py src/wiki_manager/storage.py tests/test_capability_storage.py
git commit -m "feat: add MCP capability registry storage"
```

## Task 2: HTTP MCP Client Wrapper

**Files:**
- Create: `src/wiki_manager/mcp_http_client.py`
- Test: `tests/test_mcp_http_client.py`

- [ ] **Step 1: Write failing client normalization tests**

Create `tests/test_mcp_http_client.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from mcp.types import TextContent, Tool

from wiki_manager.mcp_http_client import normalize_call_tool_result, normalize_tool


def test_normalize_tool_returns_plain_dict() -> None:
    tool = Tool(
        name="query_sql",
        description="Run SQL",
        inputSchema={
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    )

    assert normalize_tool(tool) == {
        "name": "query_sql",
        "description": "Run SQL",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }


def test_normalize_call_tool_result_handles_text_content() -> None:
    result = SimpleNamespace(
        isError=False,
        content=[TextContent(type="text", text='{"rows": [{"id": 1}]}')],
        structuredContent=None,
    )

    payload = normalize_call_tool_result(result)

    assert payload == {
        "is_error": False,
        "structured": None,
        "content": [{"type": "text", "text": '{"rows": [{"id": 1}]}'}],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_http_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'wiki_manager.mcp_http_client'`.

- [ ] **Step 3: Implement wrapper and normalization helpers**

Create `src/wiki_manager/mcp_http_client.py`:

```python
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def normalize_tool(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


def normalize_call_tool_result(result: Any) -> dict[str, Any]:
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured": _plain(getattr(result, "structuredContent", None)),
        "content": _plain(getattr(result, "content", [])),
    }


class McpHttpClient:
    async def list_tools(self, endpoint_url: str, headers: dict[str, str], timeout: float = 30.0) -> list[dict[str, Any]]:
        async with streamablehttp_client(endpoint_url, headers=headers, timeout=timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                await session.initialize()
                result = await session.list_tools()
                return [normalize_tool(tool) for tool in result.tools]

    async def call_tool(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        async with streamablehttp_client(endpoint_url, headers=headers, timeout=timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return normalize_call_tool_result(result)
```

- [ ] **Step 4: Run client tests**

Run:

```bash
uv run pytest tests/test_mcp_http_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/wiki_manager/mcp_http_client.py tests/test_mcp_http_client.py
git commit -m "feat: add HTTP MCP client wrapper"
```

## Task 3: Capability Service

**Files:**
- Create: `src/wiki_manager/capability_service.py`
- Modify: `src/wiki_manager/services.py`
- Test: `tests/test_capability_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_capability_service.py`:

```python
from __future__ import annotations

import asyncio
import pytest

from wiki_manager.capability_service import CapabilityService
from wiki_manager.capabilities import ToolType
from wiki_manager.domain import ValidationError
from wiki_manager.storage import SQLiteStore


class FakeMcpClient:
    def __init__(self) -> None:
        self.called = []

    async def list_tools(self, endpoint_url, headers, timeout=30.0):
        assert endpoint_url == "http://localhost:9001/mcp"
        return [
            {
                "name": "list_tables",
                "description": "List database tables.",
                "input_schema": {"type": "object", "properties": {"db": {"type": "string"}}},
            },
            {
                "name": "query_sql",
                "description": "Run SQL query.",
                "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}},
            },
        ]

    async def call_tool(self, endpoint_url, headers, tool_name, arguments, timeout=30.0):
        self.called.append((endpoint_url, headers, tool_name, arguments))
        return {"is_error": False, "structured": {"rows": [{"id": 1}]}, "content": []}


def _service(wm_paths) -> CapabilityService:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return CapabilityService(store=store, mcp_client=FakeMcpClient(), admins={"root"})


def test_register_and_sync_mcp_service(wm_paths) -> None:
    service = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        description="Reporting database tools.",
        tags=["database"],
    )

    result = asyncio.run(service.sync_tools(actor="root", service_key="mysql"))

    assert result == {"service_key": "mysql", "tool_count": 2}
    tools = service.list_tools(actor="root", service_key="mysql")
    assert [tool["tool_name"] for tool in tools] == ["list_tables", "query_sql"]


def test_search_root_and_service_path(wm_paths) -> None:
    service = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={},
        description="Reporting database tools.",
        tags=["database"],
    )
    asyncio.run(service.sync_tools(actor="root", service_key="mysql"))

    root = service.search(actor="root", path=None, query=None, limit=20)
    assert root["path"] == "/"
    assert root["items"][0]["kind"] == "service"
    assert root["items"][0]["service"] == "mysql"

    filtered_services = service.search(actor="root", path=None, query="report", limit=20)
    assert [item["service"] for item in filtered_services["items"]] == ["mysql"]

    tools = service.search(actor="root", path="mysql", query="sql", limit=20)
    assert tools["path"] == "mysql"
    assert [item["tool"] for item in tools["items"]] == ["query_sql"]
    assert tools["items"][0]["execute_example"]["service"] == "mysql"


def test_execute_rejects_action_tool(wm_paths) -> None:
    service = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={},
        description="",
        tags=[],
    )
    service.store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="drop_table",
        display_name="Drop Table",
        description="Drop a table.",
        input_schema={"type": "object", "properties": {"table": {"type": "string"}}},
        tool_type=ToolType.action.value,
        tags=["danger"],
        examples=[],
    )

    with pytest.raises(ValidationError, match="action tools are not executable in phase 1"):
        asyncio.run(service.execute(actor="root", service="mysql", tool="drop_table", arguments={"table": "abc"}))


def test_execute_calls_readonly_tool(wm_paths) -> None:
    service = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        description="",
        tags=[],
    )
    asyncio.run(service.sync_tools(actor="root", service_key="mysql"))

    result = asyncio.run(
        service.execute(
            actor="root",
            service="mysql",
            tool="query_sql",
            arguments={"sql": "select 1"},
        )
    )

    assert result["success"] is True
    assert result["service"] == "mysql"
    assert result["tool"] == "query_sql"
    assert result["result"]["structured"] == {"rows": [{"id": 1}]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_service.py -v
```

Expected: FAIL with missing `CapabilityService`.

- [ ] **Step 3: Implement `CapabilityService`**

Create `src/wiki_manager/capability_service.py`:

```python
from __future__ import annotations

import json
from typing import Any

from wiki_manager.capabilities import McpServiceStatus, ToolType
from wiki_manager.domain import NotFound, ValidationError, require_admin_user
from wiki_manager.mcp_http_client import McpHttpClient
from wiki_manager.storage import SQLiteStore


READONLY_TOOL_TYPES = {ToolType.overview.value, ToolType.search.value, ToolType.detail.value}


class CapabilityService:
    def __init__(self, *, store: SQLiteStore, mcp_client: Any | None = None, admins: set[str]) -> None:
        self.store = store
        self.mcp_client = mcp_client or McpHttpClient()
        self.admins = admins

    def register_service(
        self,
        *,
        actor: str,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, str],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if not service_key.replace("-", "").replace("_", "").isalnum():
            raise ValidationError("service_key may contain letters, numbers, hyphen, and underscore")
        existing = self.store.get_mcp_service(service_key)
        if existing:
            return self.store.update_mcp_service(
                service_key,
                name=name,
                endpoint_url=endpoint_url,
                headers=headers,
                description=description,
                tags=tags,
            )
        return self.store.create_mcp_service(
            service_key=service_key,
            name=name,
            endpoint_url=endpoint_url,
            headers=headers,
            description=description,
            tags=tags,
            created_by=actor,
        )

    def list_services(self, actor: str) -> list[dict[str, Any]]:
        return [self._service_payload(row) for row in self.store.list_mcp_services()]

    def set_service_status(self, *, actor: str, service_key: str, status: str) -> None:
        require_admin_user(actor, self.admins)
        if status not in {item.value for item in McpServiceStatus}:
            raise ValidationError("invalid MCP service status")
        if self.store.get_mcp_service(service_key) is None:
            raise NotFound("MCP service not found")
        self.store.update_mcp_service_status(service_key, status)

    async def sync_tools(self, *, actor: str, service_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        service = self._require_service(service_key)
        headers = json.loads(service["headers_json"])
        try:
            tools = await self.mcp_client.list_tools(service["endpoint_url"], headers)
        except Exception as exc:
            self.store.mark_mcp_service_sync(service_key, success=False, error=str(exc))
            raise ValidationError(f"MCP tool sync failed: {exc}") from exc
        for tool in tools:
            tool_type = self._infer_tool_type(tool)
            self.store.upsert_mcp_tool(
                service_key=service_key,
                tool_name=tool["name"],
                display_name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema") or {"type": "object", "properties": {}},
                tool_type=tool_type,
                tags=[],
                examples=[self._execute_example(service_key, tool["name"], tool.get("input_schema") or {})],
            )
        self.store.mark_mcp_service_sync(service_key, success=True, error=None)
        return {"service_key": service_key, "tool_count": len(tools)}

    def list_tools(self, *, actor: str, service_key: str) -> list[dict[str, Any]]:
        self._require_service(service_key)
        return [self._tool_payload(row) for row in self.store.list_mcp_tools(service_key)]

    def search(self, *, actor: str, path: str | None, query: str | None, limit: int = 20) -> dict[str, Any]:
        normalized_path = self._normalize_path(path)
        normalized_query = (query or "").strip().lower()
        if normalized_path == "/":
            services = [
                self._service_search_item(row)
                for row in self.store.list_mcp_services()
                if row["status"] == McpServiceStatus.enabled.value
            ]
            return {
                "path": "/",
                "items": self._filter_items(services, normalized_query)[:limit],
            }
        self._require_service(normalized_path)
        tools = [self._tool_search_item(row) for row in self.store.list_mcp_tools(normalized_path)]
        return {
            "path": normalized_path,
            "items": self._filter_items(tools, normalized_query)[:limit],
        }

    async def execute(self, *, actor: str, service: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service_row = self._require_service(service)
        tool_row = self.store.get_mcp_tool(service, tool)
        if tool_row is None:
            raise NotFound("MCP tool not found")
        if tool_row["tool_type"] not in READONLY_TOOL_TYPES:
            raise ValidationError("action tools are not executable in phase 1")
        headers = json.loads(service_row["headers_json"])
        result = await self.mcp_client.call_tool(
            service_row["endpoint_url"],
            headers,
            tool,
            arguments,
        )
        return {
            "service": service,
            "tool": tool,
            "success": not bool(result.get("is_error")),
            "result": result,
        }

    def _require_service(self, service_key: str) -> dict[str, Any]:
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("MCP service not found")
        return service

    def _normalize_path(self, path: str | None) -> str:
        if path is None or not path.strip() or path.strip() == "/":
            return "/"
        return path.strip().strip("/")

    def _filter_items(self, items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        if not query:
            return items
        return [item for item in items if query in json.dumps(item, ensure_ascii=False).lower()]

    def _infer_tool_type(self, tool: dict[str, Any]) -> str:
        text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        if any(word in text for word in ("create", "update", "delete", "drop", "write", "execute action")):
            return ToolType.action.value
        if any(word in text for word in ("get", "detail", "read")):
            return ToolType.detail.value
        if any(word in text for word in ("list", "overview")):
            return ToolType.overview.value
        return ToolType.search.value

    def _execute_example(self, service_key: str, tool_name: str, input_schema: dict[str, Any]) -> dict[str, Any]:
        properties = input_schema.get("properties", {}) if isinstance(input_schema, dict) else {}
        return {
            "service": service_key,
            "tool": tool_name,
            "arguments": {name: f"<{name}>" for name in properties},
        }

    def _service_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["headers"] = json.loads(row["headers_json"])
        payload["tags"] = json.loads(row["tags_json"])
        payload.pop("headers_json", None)
        payload.pop("tags_json", None)
        return payload

    def _tool_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["input_schema"] = json.loads(row["input_schema_json"])
        payload["tags"] = json.loads(row["tags_json"])
        payload["examples"] = json.loads(row["examples_json"])
        payload.pop("input_schema_json", None)
        payload.pop("tags_json", None)
        payload.pop("examples_json", None)
        return payload

    def _service_search_item(self, row: dict[str, Any]) -> dict[str, Any]:
        tools = self.store.list_mcp_tools(row["service_key"])
        return {
            "kind": "service",
            "service": row["service_key"],
            "name": row["name"],
            "description": row["description"],
            "tags": json.loads(row["tags_json"]),
            "tool_count": len(tools),
            "status": row["status"],
        }

    def _tool_search_item(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "tool",
            "service": row["service_key"],
            "tool": row["tool_name"],
            "name": row["display_name"],
            "description": row["description"],
            "tags": json.loads(row["tags_json"]),
            "tool_type": row["tool_type"],
            "input_schema": json.loads(row["input_schema_json"]),
            "execute_example": self._execute_example(
                row["service_key"],
                row["tool_name"],
                json.loads(row["input_schema_json"]),
            ),
            "executable": row["tool_type"] in READONLY_TOOL_TYPES,
        }
```

- [ ] **Step 4: Attach capability service to `WikiManagerService`**

Modify `src/wiki_manager/services.py`:

```python
from wiki_manager.capability_service import CapabilityService
```

Add to `WikiManagerService.__init__`:

```python
        self.capabilities = CapabilityService(store=store, admins=admins)
```

- [ ] **Step 5: Run service tests**

Run:

```bash
uv run pytest tests/test_capability_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Run existing service tests**

Run:

```bash
uv run pytest tests/test_services.py tests/test_domain.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/wiki_manager/capability_service.py src/wiki_manager/services.py tests/test_capability_service.py
git commit -m "feat: add capability service"
```

## Task 4: Web API and Minimal Admin Page

**Files:**
- Modify: `src/wiki_manager/server.py`
- Create: `src/wiki_manager/web_pages.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_capability_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def test_mcp_service_registration_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/admin/init", headers={"X-Wiki-User": "root"})

    response = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL",
            "endpoint_url": "http://localhost:9001/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "description": "Reporting database MCP.",
            "tags": ["database"],
        },
        headers={"X-Wiki-User": "root"},
    )

    assert response.status_code == 200
    assert response.json()["service_key"] == "mysql"

    listed = client.get("/capabilities/mcp-services", headers={"X-Wiki-User": "root"})
    assert listed.status_code == 200
    assert listed.json()[0]["service_key"] == "mysql"
    assert listed.json()[0]["tags"] == ["database"]


def test_mcp_service_registration_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/admin/init", headers={"X-Wiki-User": "root"})

    response = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL",
            "endpoint_url": "http://localhost:9001/mcp",
            "headers": {},
            "description": "",
            "tags": [],
        },
        headers={"X-Wiki-User": "alice"},
    )

    assert response.status_code == 403


def test_capability_admin_page_serves_html(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert "Agent Capability Hub" in response.text
    assert "MCP Services" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_api.py -v
```

Expected: FAIL with route 404 responses and missing `web_pages.py`.

- [ ] **Step 3: Add HTML page helper**

Create `src/wiki_manager/web_pages.py`:

```python
from __future__ import annotations


def capability_admin_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Capability Hub</title>
  <style>
    body { margin: 0; background: #f6f8fb; color: #17202a; font-family: Arial, "PingFang SC", sans-serif; }
    main { width: min(1120px, calc(100vw - 40px)); margin: 0 auto; padding: 28px 0 60px; }
    header, section { border: 1px solid #dce3ec; border-radius: 8px; background: #fff; padding: 22px; margin-top: 14px; }
    h1, h2 { margin: 0; }
    p { color: #647086; }
    label { display: block; margin-top: 12px; font-weight: 700; }
    input, textarea { width: 100%; border: 1px solid #b9c5d6; border-radius: 6px; padding: 9px; font: inherit; }
    button { margin-top: 14px; border: 0; border-radius: 6px; background: #2456d6; color: white; padding: 10px 14px; font-weight: 700; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border-bottom: 1px solid #dce3ec; padding: 10px; text-align: left; vertical-align: top; }
    code, pre { background: #f4f7fb; border: 1px solid #dce3ec; border-radius: 6px; padding: 2px 6px; }
    pre { overflow: auto; padding: 12px; }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Agent Capability Hub</h1>
      <p>Phase 1 MCP Services registry and MetaMCP gateway.</p>
    </header>
    <section>
      <h2>MCP Services</h2>
      <div id="services">Loading services...</div>
    </section>
    <section>
      <h2>Register HTTP MCP Service</h2>
      <label>Service Key</label>
      <input id="service_key" value="mysql">
      <label>Name</label>
      <input id="name" value="MySQL">
      <label>Endpoint URL</label>
      <input id="endpoint_url" value="http://localhost:9001/mcp">
      <label>Description</label>
      <textarea id="description">Reporting database MCP.</textarea>
      <label>Tags, comma separated</label>
      <input id="tags" value="database,report">
      <label>Headers JSON</label>
      <textarea id="headers">{}</textarea>
      <button onclick="saveService()">Save Service</button>
      <pre id="result"></pre>
    </section>
    <section>
      <h2>MetaMCP Search Examples</h2>
      <pre>{}</pre>
      <pre>{"query":"mysql"}</pre>
      <pre>{"path":"mysql"}</pre>
      <pre>{"path":"mysql","query":"sql"}</pre>
    </section>
  </main>
  <script>
    async function loadServices() {
      const response = await fetch('/capabilities/mcp-services', { headers: { 'X-Wiki-User': 'root' } });
      const services = await response.json();
      document.getElementById('services').innerHTML = '<table><thead><tr><th>Key</th><th>Name</th><th>Status</th><th>Tags</th></tr></thead><tbody>' +
        services.map(item => '<tr><td><code>' + item.service_key + '</code></td><td>' + item.name + '</td><td>' + item.status + '</td><td>' + item.tags.join(', ') + '</td></tr>').join('') +
        '</tbody></table>';
    }
    async function saveService() {
      const payload = {
        service_key: document.getElementById('service_key').value,
        name: document.getElementById('name').value,
        endpoint_url: document.getElementById('endpoint_url').value,
        description: document.getElementById('description').value,
        tags: document.getElementById('tags').value.split(',').map(item => item.trim()).filter(Boolean),
        headers: JSON.parse(document.getElementById('headers').value || '{}')
      };
      const response = await fetch('/capabilities/mcp-services', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Wiki-User': 'root' },
        body: JSON.stringify(payload)
      });
      document.getElementById('result').textContent = JSON.stringify(await response.json(), null, 2);
      await loadServices();
    }
    loadServices();
  </script>
</body>
</html>"""
```

- [ ] **Step 4: Add API request models and routes**

Modify `src/wiki_manager/server.py` imports:

```python
from fastapi.responses import HTMLResponse
from wiki_manager.web_pages import capability_admin_page
```

Add Pydantic models near the existing request models:

```python
class McpServiceRequest(BaseModel):
    service_key: str
    name: str
    endpoint_url: str
    headers: dict[str, str] = {}
    description: str = ""
    tags: list[str] = []


class McpServiceStatusRequest(BaseModel):
    status: str
```

Add routes inside `create_app`:

```python
    @app.get("/admin/capabilities", response_class=HTMLResponse)
    def capability_admin() -> str:
        return capability_admin_page()

    @app.post("/capabilities/mcp-services")
    def register_mcp_service(payload: McpServiceRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(
            lambda: service.capabilities.register_service(
                actor=current_actor,
                service_key=payload.service_key,
                name=payload.name,
                endpoint_url=payload.endpoint_url,
                headers=payload.headers,
                description=payload.description,
                tags=payload.tags,
            )
        )

    @app.get("/capabilities/mcp-services")
    def list_mcp_services(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.capabilities.list_services(current_actor))

    @app.post("/capabilities/mcp-services/{service_key}/status")
    def update_mcp_service_status(
        service_key: str,
        payload: McpServiceStatusRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        call_safely(
            lambda: service.capabilities.set_service_status(
                actor=current_actor,
                service_key=service_key,
                status=payload.status,
            )
        )
        return {"service_key": service_key, "status": payload.status}

    @app.post("/capabilities/mcp-services/{service_key}/sync")
    async def sync_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        try:
            return await service.capabilities.sync_tools(actor=current_actor, service_key=service_key)
        except WikiManagerError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/capabilities/mcp-services/{service_key}/tools")
    def list_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.capabilities.list_tools(actor=current_actor, service_key=service_key))
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/test_capability_api.py -v
```

Expected: PASS.

- [ ] **Step 6: Run existing server regression tests**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/wiki_manager/server.py src/wiki_manager/web_pages.py tests/test_capability_api.py
git commit -m "feat: add capability registry web API"
```

## Task 5: MetaMCP Search and Execute Tools

**Files:**
- Modify: `src/wiki_manager/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Replace MCP tests with MetaMCP expectations**

Modify `tests/test_mcp_server.py` to cover `search` and `execute`:

```python
from __future__ import annotations

import asyncio

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest


def _get_tools_sync(server):
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    return result.root.tools


def test_mcp_server_exposes_search_and_execute_tools():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tool_names = [tool.name for tool in tools]
    assert tool_names == ["search", "execute"]


def test_mcp_search_tool_has_path_query_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    search_tool = {tool.name: tool for tool in tools}["search"]
    schema = search_tool.inputSchema
    assert "path" in schema["properties"]
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]


def test_mcp_execute_tool_has_service_tool_arguments_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    execute_tool = {tool.name: tool for tool in tools}["execute"]
    schema = execute_tool.inputSchema
    assert schema["required"] == ["service", "tool", "arguments"]
    assert "service" in schema["properties"]
    assert "tool" in schema["properties"]
    assert "arguments" in schema["properties"]


def test_mcp_search_tool_calls_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    class FakeCapabilities:
        def search(self, *, actor, path, query, limit):
            assert actor == "root"
            assert path == "mysql"
            assert query == "sql"
            assert limit == 5
            return {"path": "mysql", "items": [{"kind": "tool", "tool": "query_sql"}]}

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(
        handler(
            CallToolRequest(
                params=CallToolRequestParams(
                    name="search",
                    arguments={"path": "mysql", "query": "sql", "limit": 5},
                )
            )
        )
    )

    assert result.root.structuredContent["items"][0]["tool"] == "query_sql"


def test_mcp_execute_tool_calls_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    class FakeCapabilities:
        async def execute(self, *, actor, service, tool, arguments):
            assert actor == "root"
            assert service == "mysql"
            assert tool == "query_sql"
            assert arguments == {"sql": "select 1"}
            return {
                "service": "mysql",
                "tool": "query_sql",
                "success": True,
                "result": {"structured": {"rows": [{"id": 1}]}},
            }

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(
        handler(
            CallToolRequest(
                params=CallToolRequestParams(
                    name="execute",
                    arguments={
                        "service": "mysql",
                        "tool": "query_sql",
                        "arguments": {"sql": "select 1"},
                    },
                )
            )
        )
    )

    assert result.root.structuredContent["success"] is True
```

- [ ] **Step 2: Run MCP tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: FAIL because current MCP server still exposes knowledge-base `search` and `ask`.

- [ ] **Step 3: Replace MCP server tool definitions**

Modify `src/wiki_manager/mcp_server.py` so `list_tools` returns:

```python
            Tool(
                name="search",
                description="Browse and search the Agent Capability Hub registry. With no arguments, returns visible MCP services. With path=service_key, returns tools under that service. query filters the current path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Registry path. Empty or '/' lists services; a service key lists tools under that service.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional natural language filter for services or tools under the selected path.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of items to return. Default 20.",
                        },
                    },
                },
            ),
            Tool(
                name="execute",
                description="Execute a registered read-only MCP tool through the Agent Capability Hub gateway.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "Registered MCP service key.",
                        },
                        "tool": {
                            "type": "string",
                            "description": "Tool name within the MCP service.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments passed to the target MCP tool.",
                        },
                    },
                    "required": ["service", "tool", "arguments"],
                },
            ),
```

Modify `call_tool` in `src/wiki_manager/mcp_server.py`:

```python
        svc = resolve_service()
        if name == "search":
            return svc.capabilities.search(
                actor=actor,
                path=arguments.get("path"),
                query=arguments.get("query"),
                limit=int(arguments.get("limit", 20)),
            )
        if name == "execute":
            return await svc.capabilities.execute(
                actor=actor,
                service=arguments["service"],
                tool=arguments["tool"],
                arguments=arguments.get("arguments") or {},
            )
        raise ValueError(f"unknown tool: {name}")
```

- [ ] **Step 4: Run MCP tests**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: PASS.

- [ ] **Step 5: Run related integration tests**

Run:

```bash
uv run pytest tests/test_capability_service.py tests/test_capability_api.py tests/test_mcp_server.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/wiki_manager/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose MetaMCP search and execute"
```

## Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with new project positioning**

Modify the opening section of `README.md`:

```markdown
# Agent Capability Hub

`Agent Capability Hub` is a Python 3.11 service for registering HTTP MCP services,
syncing their tool definitions, and exposing a stable MetaMCP gateway for agents.

Phase 1 focuses on:

- HTTP MCP service registration
- MCP tool list synchronization
- a lightweight web registration page at `/admin/capabilities`
- MetaMCP `search` for registry browsing with `path` and `query`
- MetaMCP `execute` for read-only tool execution
- preserving existing wiki-manager knowledge-base functionality as a capability source foundation
```

Add Phase 1 local usage:

````markdown
## Agent Capability Hub Usage

```bash
uv run wiki server start
uv run wiki server init
open http://127.0.0.1:8765/admin/capabilities
```

MetaMCP `search` examples:

```json
{}
{"query": "mysql"}
{"path": "mysql"}
{"path": "mysql", "query": "sql"}
```

MetaMCP `execute` example:

```json
{
  "service": "mysql",
  "tool": "query_sql",
  "arguments": {
    "db": "whjcbb",
    "sql": "select abc from aaa",
    "limit": 10
  }
}
```
````

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/test_capability_storage.py tests/test_mcp_http_client.py tests/test_capability_service.py tests/test_capability_api.py tests/test_mcp_server.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS, except tests marked `ragflow` or `weknora` may require live external systems if the local configuration enables them. If external integration tests fail because live services are unavailable, rerun the non-integration suite:

```bash
uv run pytest -v -m "not ragflow and not weknora"
```

Expected: PASS.

- [ ] **Step 4: Inspect git status**

Run:

```bash
git status --short
```

Expected: only `README.md` modified, because previous tasks committed their changes.

- [ ] **Step 5: Commit README**

```bash
git add README.md
git commit -m "docs: update Agent Capability Hub usage"
```

## Final Acceptance Checklist

- [ ] `uv run pytest tests/test_capability_storage.py -v` passes.
- [ ] `uv run pytest tests/test_mcp_http_client.py -v` passes.
- [ ] `uv run pytest tests/test_capability_service.py -v` passes.
- [ ] `uv run pytest tests/test_capability_api.py -v` passes.
- [ ] `uv run pytest tests/test_mcp_server.py -v` passes.
- [ ] `uv run pytest -v -m "not ragflow and not weknora"` passes.
- [ ] `/admin/capabilities` returns HTML containing `Agent Capability Hub` and `MCP Services`.
- [ ] `search` with `{}` returns service entries.
- [ ] `search` with `{"query": "mysql"}` returns matching service entries.
- [ ] `search` with `{"path": "mysql"}` returns tool entries.
- [ ] `search` with `{"path": "mysql", "query": "sql"}` filters tools under that service.
- [ ] `execute` can run a registered read-only tool.
- [ ] `execute` rejects `action` tools with `action tools are not executable in phase 1`.

## Plan Self-Review

- Spec coverage: The plan covers HTTP MCP registration, tool sync, `path/query` search, execute, action rejection, minimal page registration, and README updates.
- Scope boundary: Logs, Profile, audit pages, vector search implementation, stdio MCP, and operation governance are intentionally outside this Phase 1 implementation.
- Type consistency: The plan consistently uses `service_key` in storage/API, `service` in MetaMCP `execute`, `tool_name` in storage, `tool` in MetaMCP `execute`, and `arguments` for target MCP tool parameters.
