# OpenAPI Capability Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAPI as a first-class Agent Bridge capability source where one OpenAPI service owns N saved interface tools, with spec import used only as a page-level helper.

**Architecture:** Keep the existing MCP tables and behavior intact, and add parallel `openapi_services` / `openapi_tools` tables plus an OpenAPI source adapter under `capability_hub/sources/openapi`. `CapabilityService` remains the MetaMCP orchestration point, but delegates OpenAPI service/tool CRUD, import preview, search, and execute through the new source code. The persisted `openapi_tools` rows are the final execution contract; parsed spec operations returned by import are not stored until an admin saves them.

**Tech Stack:** Python 3.11, FastAPI, SQLite, httpx, PyYAML, Vue 3, TypeScript, node:test, pytest.

---

## File Structure

Create these backend files:

- `src/agent_bridge/capability_hub/sources/openapi/__init__.py`: package marker.
- `src/agent_bridge/capability_hub/sources/openapi/parser.py`: OpenAPI 3.x JSON/YAML parsing, operation-to-tool preview conversion, stable tool name generation.
- `src/agent_bridge/capability_hub/sources/openapi/http_client.py`: execute saved OpenAPI tools through HTTP with path/query/header/body mapping.
- `src/agent_bridge/capability_hub/sources/openapi/adapter.py`: service-level OpenAPI operations used by `CapabilityService`.
- `tests/test_openapi_parser.py`: parser tests.
- `tests/test_openapi_storage.py`: repository/schema tests.
- `tests/test_openapi_capability_service.py`: service/search/execute behavior tests.
- `tests/test_openapi_api.py`: FastAPI route tests.

Modify these backend files:

- `pyproject.toml`: add `pyyaml>=6.0.0`.
- `src/agent_bridge/capability_hub/models.py`: add `SourceType.openapi_service`, OpenAPI failure stage/owner naming if needed.
- `src/agent_bridge/storage/schema.py`: add `openapi_services` and `openapi_tools`.
- `src/agent_bridge/storage/repositories/capabilities.py`: add OpenAPI service/tool CRUD methods.
- `src/agent_bridge/capability_hub/service.py`: add OpenAPI adapter dependency, root search inclusion, path search, execute dispatch, catalog detail helpers, tool type update.
- `src/agent_bridge/api/schemas.py`: add OpenAPI request schemas.
- `src/agent_bridge/api/routes/capabilities.py`: add OpenAPI service/tool/import routes and catalog detail support.
- `src/agent_bridge/capability_hub/governance.py`: allow `openapi_service` as source type.
- `src/agent_bridge/capability_hub/profiles/pins.py`: allow OpenAPI service keys in pin previews if source search returns OpenAPI tools.
- `src/agent_bridge/storage/repositories/governance.py`: no schema change expected, but tests must verify generic `source_type` accepts OpenAPI through service validation.

Modify these frontend files:

- `frontend/capabilities/src/api/types.ts`: add `CapabilityService`, `OpenApiService`, `OpenApiTool`, `OpenApiImportOperation`.
- `frontend/capabilities/src/api/client.ts`: add OpenAPI endpoints and widen catalog/source detail typing.
- `frontend/capabilities/src/views/capabilities/serviceForm.ts`: support `service_type`, OpenAPI fields, OpenAPI payload builders.
- `frontend/capabilities/src/views/capabilities/ServicesView.vue`: add type filter and OpenAPI create/edit/import UI.
- `frontend/capabilities/src/views/capabilities/ToolsView.vue`: show OpenAPI method/path columns when present.
- `frontend/capabilities/tests/serviceForm.test.ts`: add OpenAPI form payload tests.
- `frontend/capabilities/tests/openapiImport.test.ts`: add import-selection and payload tests for frontend helpers.

---

### Task 1: Add OpenAPI Source Models, Schema, and Repository

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/agent_bridge/capability_hub/models.py`
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/repositories/capabilities.py`
- Test: `tests/test_openapi_storage.py`

- [ ] **Step 1: Write the failing storage tests**

Create `tests/test_openapi_storage.py`:

```python
from __future__ import annotations

import json

from agent_bridge.capability_hub.models import SourceType, ToolType
from agent_bridge.storage.sqlite import SQLiteStore


def test_openapi_service_and_tool_round_trip(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    service = store.capabilities.create_openapi_service(
        service_key="crm-api",
        name="CRM API",
        base_url="https://crm.example.test",
        spec_url="https://crm.example.test/openapi.json",
        spec_content={},
        auth_config={"type": "bearer", "token": "secret-token"},
        headers={"X-Tenant": "demo"},
        description="CRM read APIs",
        tags=["crm", "readonly"],
        created_by="root",
    )

    assert service["service_key"] == "crm-api"
    assert service["base_url"] == "https://crm.example.test"
    assert json.loads(service["auth_config_json"])["type"] == "bearer"

    tool = store.capabilities.upsert_openapi_tool(
        service_key="crm-api",
        tool_name="get_user",
        operation_id="getUser",
        method="GET",
        path="/users/{id}",
        display_name="Get User",
        description="Fetch one user",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        request_mapping={
            "path": {"id": "id"},
            "query": {},
            "headers": {},
            "body": None,
        },
        response_schema={},
        tool_type=ToolType.detail,
        tags=["users"],
        examples=[{"id": "u_123"}],
    )

    assert tool["service_key"] == "crm-api"
    assert tool["tool_name"] == "get_user"
    assert tool["method"] == "GET"
    assert json.loads(tool["request_mapping_json"])["path"] == {"id": "id"}
    assert store.capabilities.get_openapi_tool("crm-api", "get_user")["tool_name"] == "get_user"
    assert len(store.capabilities.list_openapi_tools("crm-api")) == 1


def test_openapi_service_update_preserves_auth_when_omitted(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.capabilities.create_openapi_service(
        service_key="crm-api",
        name="CRM API",
        base_url="https://crm.example.test",
        spec_url="",
        spec_content={},
        auth_config={"type": "api_key", "header": "X-Key", "value": "abc"},
        headers={"X-Tenant": "demo"},
        description="",
        tags=[],
        created_by="root",
    )

    updated = store.capabilities.update_openapi_service(
        "crm-api",
        name="CRM API v2",
        base_url="https://crm-v2.example.test",
        spec_url="https://crm-v2.example.test/openapi.json",
        spec_content={},
        auth_config=None,
        headers=None,
        description="Updated",
        tags=["crm"],
    )

    assert updated["name"] == "CRM API v2"
    assert json.loads(updated["auth_config_json"])["value"] == "abc"
    assert json.loads(updated["headers_json"]) == {"X-Tenant": "demo"}


def test_openapi_tool_delete_hides_tool(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.capabilities.create_openapi_service(
        service_key="crm-api",
        name="CRM API",
        base_url="https://crm.example.test",
        spec_url="",
        spec_content={},
        auth_config={},
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.capabilities.upsert_openapi_tool(
        service_key="crm-api",
        tool_name="get_user",
        operation_id="getUser",
        method="GET",
        path="/users/{id}",
        display_name="Get User",
        description="Fetch one user",
        input_schema={"type": "object", "properties": {}, "required": []},
        request_mapping={"path": {}, "query": {}, "headers": {}, "body": None},
        response_schema={},
        tool_type=ToolType.detail,
        tags=[],
        examples=[],
    )

    deleted = store.capabilities.delete_openapi_tool("crm-api", "get_user")

    assert deleted is True
    assert store.capabilities.get_openapi_tool("crm-api", "get_user") is None
    assert store.capabilities.list_openapi_tools("crm-api") == []


def test_source_type_accepts_openapi_service() -> None:
    assert SourceType.openapi_service.value == "openapi_service"
```

- [ ] **Step 2: Run the storage test to verify it fails**

Run:

```bash
uv run pytest tests/test_openapi_storage.py -q
```

Expected: FAIL because `SourceType.openapi_service`, `create_openapi_service`, and `upsert_openapi_tool` do not exist.

- [ ] **Step 3: Add dependency and enum**

Modify `pyproject.toml` dependencies:

```toml
"pyyaml>=6.0.0",
```

Modify `src/agent_bridge/capability_hub/models.py`:

```python
class SourceType(str, Enum):
    builtin = "builtin"
    mcp_service = "mcp_service"
    openapi_service = "openapi_service"
```

- [ ] **Step 4: Add OpenAPI tables**

In `src/agent_bridge/storage/schema.py`, immediately after `mcp_tools`, add:

```sql
CREATE TABLE IF NOT EXISTS openapi_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  spec_url TEXT NOT NULL DEFAULT '',
  spec_content_json TEXT NOT NULL DEFAULT '{}',
  auth_config_json TEXT NOT NULL DEFAULT '{}',
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
CREATE TABLE IF NOT EXISTS openapi_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_key TEXT NOT NULL REFERENCES openapi_services(service_key) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  operation_id TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  request_mapping_json TEXT NOT NULL DEFAULT '{}',
  response_schema_json TEXT NOT NULL DEFAULT '{}',
  tool_type TEXT NOT NULL DEFAULT 'unconfigured',
  tags_json TEXT NOT NULL DEFAULT '[]',
  examples_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (service_key, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_openapi_tools_service ON openapi_tools(service_key, status);
```

- [ ] **Step 5: Add repository methods**

In `src/agent_bridge/storage/repositories/capabilities.py`, add methods mirroring the MCP repository style:

```python
    def create_openapi_service(
        self,
        *,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: dict[str, Any],
        auth_config: dict[str, Any],
        headers: dict[str, Any],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openapi_services (
                  service_key, name, base_url, spec_url, spec_content_json,
                  auth_config_json, headers_json, description, tags_json, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_key,
                    name,
                    base_url,
                    spec_url,
                    json.dumps(spec_content, ensure_ascii=False),
                    json.dumps(auth_config, ensure_ascii=False),
                    json.dumps(headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    created_by,
                ),
            )
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"openapi service not found: {service_key}")
            return service

    def update_openapi_service(
        self,
        service_key: str,
        *,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: dict[str, Any],
        auth_config: dict[str, Any] | None,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        existing = self.get_openapi_service(service_key)
        if existing is None:
            raise KeyError(f"openapi service not found: {service_key}")
        next_auth = auth_config if auth_config is not None else json.loads(existing.get("auth_config_json") or "{}")
        next_headers = headers if headers is not None else json.loads(existing.get("headers_json") or "{}")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_services
                SET name = ?,
                    base_url = ?,
                    spec_url = ?,
                    spec_content_json = ?,
                    auth_config_json = ?,
                    headers_json = ?,
                    description = ?,
                    tags_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (
                    name,
                    base_url,
                    spec_url,
                    json.dumps(spec_content, ensure_ascii=False),
                    json.dumps(next_auth, ensure_ascii=False),
                    json.dumps(next_headers, ensure_ascii=False),
                    description,
                    json.dumps(tags, ensure_ascii=False),
                    service_key,
                ),
            )
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            service = row_to_dict(row)
            if service is None:
                raise KeyError(f"openapi service not found: {service_key}")
            return service

    def get_openapi_service(self, service_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM openapi_services WHERE service_key = ?", (service_key,)).fetchone()
            return row_to_dict(row)

    def list_openapi_services(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM openapi_services ORDER BY service_key").fetchall()
            return [dict(row) for row in rows]

    def update_openapi_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_services
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ?
                """,
                (enum_value(status), service_key),
            )

    def mark_openapi_service_import(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        with self._connect() as conn:
            next_status = McpServiceStatus.error.value if not success else None
            if next_status is None:
                conn.execute(
                    """
                    UPDATE openapi_services
                    SET last_synced_at = CURRENT_TIMESTAMP,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                    """,
                    (error, service_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE openapi_services
                    SET status = ?,
                        last_synced_at = CURRENT_TIMESTAMP,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE service_key = ?
                    """,
                    (next_status, error, service_key),
                )

    def upsert_openapi_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        operation_id: str,
        method: str,
        path: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        request_mapping: dict[str, Any],
        response_schema: dict[str, Any],
        tool_type: ToolType | str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openapi_tools (
                  service_key, tool_name, operation_id, method, path, display_name,
                  description, input_schema_json, request_mapping_json,
                  response_schema_json, tool_type, tags_json, examples_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_key, tool_name) DO UPDATE SET
                  operation_id = excluded.operation_id,
                  method = excluded.method,
                  path = excluded.path,
                  display_name = excluded.display_name,
                  description = excluded.description,
                  input_schema_json = excluded.input_schema_json,
                  request_mapping_json = excluded.request_mapping_json,
                  response_schema_json = excluded.response_schema_json,
                  tool_type = excluded.tool_type,
                  tags_json = excluded.tags_json,
                  examples_json = excluded.examples_json,
                  status = 'active',
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    service_key,
                    tool_name,
                    operation_id,
                    method.upper(),
                    path,
                    display_name,
                    description,
                    json.dumps(input_schema, ensure_ascii=False),
                    json.dumps(request_mapping, ensure_ascii=False),
                    json.dumps(response_schema, ensure_ascii=False),
                    enum_value(tool_type),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(examples, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ?",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"openapi tool not found: {service_key}/{tool_name}")
            return tool

    def list_openapi_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if service_key is None:
                rows = conn.execute("SELECT * FROM openapi_tools WHERE status = 'active' ORDER BY service_key, tool_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM openapi_tools WHERE service_key = ? AND status = 'active' ORDER BY tool_name",
                    (service_key,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_openapi_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ? AND status = 'active'",
                (service_key, tool_name),
            ).fetchone()
            return row_to_dict(row)

    def update_openapi_tool_type(self, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE openapi_tools
                SET tool_type = ?, updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ? AND tool_name = ? AND status = 'active'
                """,
                (enum_value(tool_type), service_key, tool_name),
            )
            row = conn.execute(
                "SELECT * FROM openapi_tools WHERE service_key = ? AND tool_name = ? AND status = 'active'",
                (service_key, tool_name),
            ).fetchone()
            tool = row_to_dict(row)
            if tool is None:
                raise KeyError(f"openapi tool not found: {service_key}/{tool_name}")
            return tool

    def delete_openapi_tool(self, service_key: str, tool_name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE openapi_tools
                SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
                WHERE service_key = ? AND tool_name = ? AND status = 'active'
                """,
                (service_key, tool_name),
            )
            return cur.rowcount > 0
```

- [ ] **Step 6: Run storage tests**

Run:

```bash
uv run pytest tests/test_openapi_storage.py tests/test_storage_facade.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/agent_bridge/capability_hub/models.py src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/capabilities.py tests/test_openapi_storage.py
git commit -m "feat: add openapi capability storage"
```

---

### Task 2: Implement OpenAPI Import Parser Without Persistence

**Files:**
- Create: `src/agent_bridge/capability_hub/sources/openapi/__init__.py`
- Create: `src/agent_bridge/capability_hub/sources/openapi/parser.py`
- Test: `tests/test_openapi_parser.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_openapi_parser.py`:

```python
from __future__ import annotations

from agent_bridge.capability_hub.sources.openapi.parser import parse_openapi_operations


SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "CRM API", "version": "1.0.0"},
    "paths": {
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "summary": "Get User",
                "description": "Fetch one user",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "User ID"},
                    {"name": "include_orders", "in": "query", "schema": {"type": "boolean"}, "description": "Include orders"},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/orders/search": {
            "post": {
                "summary": "Search Orders",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"keyword": {"type": "string", "description": "Search keyword"}},
                                "required": ["keyword"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
}


def test_parse_openapi_operations_maps_operation_to_candidate_tool() -> None:
    operations = parse_openapi_operations(SPEC)

    first = operations[0]
    assert first["operation_id"] == "getUser"
    assert first["tool_name"] == "get_user"
    assert first["method"] == "GET"
    assert first["path"] == "/users/{id}"
    assert first["tool_type"] == "detail"
    assert first["input_schema"]["required"] == ["id"]
    assert first["input_schema"]["properties"]["id"]["description"] == "User ID"
    assert first["request_mapping"] == {
        "path": {"id": "id"},
        "query": {"include_orders": "include_orders"},
        "headers": {},
        "body": None,
    }


def test_parse_openapi_operations_generates_tool_name_without_operation_id() -> None:
    operations = parse_openapi_operations(SPEC)

    second = operations[1]
    assert second["operation_id"] == ""
    assert second["tool_name"] == "post_orders_search"
    assert second["method"] == "POST"
    assert second["tool_type"] == "unconfigured"
    assert second["input_schema"]["properties"]["body"]["properties"]["keyword"]["type"] == "string"
    assert second["request_mapping"]["body"] == "body"


def test_parse_openapi_operations_deduplicates_tool_names() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Duplicate API", "version": "1.0.0"},
        "paths": {
            "/a": {"get": {"operationId": "find", "responses": {"200": {"description": "OK"}}}},
            "/b": {"get": {"operationId": "find", "responses": {"200": {"description": "OK"}}}},
        },
    }

    operations = parse_openapi_operations(spec)

    assert operations[0]["tool_name"] == "find"
    assert operations[1]["tool_name"].startswith("find_")
    assert operations[1]["tool_name"] != "find"


def test_parse_openapi_operations_accepts_yaml_text() -> None:
    yaml_text = """
openapi: 3.0.3
info:
  title: CRM API
  version: 1.0.0
paths:
  /users:
    get:
      operationId: listUsers
      responses:
        "200":
          description: OK
"""

    operations = parse_openapi_operations(yaml_text)

    assert operations[0]["tool_name"] == "list_users"
```

- [ ] **Step 2: Run parser tests to verify failure**

Run:

```bash
uv run pytest tests/test_openapi_parser.py -q
```

Expected: FAIL because parser module does not exist.

- [ ] **Step 3: Implement parser**

Create `src/agent_bridge/capability_hub/sources/openapi/parser.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def parse_openapi_operations(spec: dict[str, Any] | str) -> list[dict[str, Any]]:
    document = _load_spec(spec)
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return []
    seen: set[str] = set()
    operations: list[dict[str, Any]] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        path_parameters = _parameters(path_item.get("parameters"))
        for method, operation in path_item.items():
            if str(method).lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            merged_parameters = [*path_parameters, *_parameters(operation.get("parameters"))]
            operation_id = str(operation.get("operationId") or "")
            base_tool_name = _tool_name(operation_id, str(method), path)
            tool_name = _dedupe_tool_name(base_tool_name, seen, str(method), path)
            seen.add(tool_name)
            input_schema, request_mapping = _input_schema_and_mapping(merged_parameters, operation)
            operations.append(
                {
                    "operation_id": operation_id,
                    "tool_name": tool_name,
                    "method": str(method).upper(),
                    "path": path,
                    "display_name": str(operation.get("summary") or operation_id or tool_name),
                    "description": str(operation.get("description") or operation.get("summary") or ""),
                    "input_schema": input_schema,
                    "request_mapping": request_mapping,
                    "response_schema": _response_schema(operation),
                    "tool_type": _default_tool_type(str(method).upper(), path),
                    "tags": [str(tag) for tag in operation.get("tags", []) if isinstance(tag, str)],
                    "examples": [_schema_example(input_schema)],
                }
            )
    return operations


def _load_spec(spec: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(spec, dict):
        return spec
    loaded = yaml.safe_load(spec)
    return loaded if isinstance(loaded, dict) else {}


def _parameters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _tool_name(operation_id: str, method: str, path: str) -> str:
    if operation_id:
        return _snake(operation_id)
    path_bits = re.sub(r"[{}]", "", path.strip("/")).replace("/", "_")
    return _snake(f"{method}_{path_bits}") or _snake(method)


def _snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value or "operation"


def _dedupe_tool_name(name: str, seen: set[str], method: str, path: str) -> str:
    if name not in seen:
        return name
    digest = hashlib.sha1(f"{method.upper()} {path}".encode("utf-8")).hexdigest()[:6]
    return f"{name}_{digest}"


def _input_schema_and_mapping(parameters: list[dict[str, Any]], operation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    mapping: dict[str, Any] = {"path": {}, "query": {}, "headers": {}, "body": None}
    for parameter in parameters:
        name = str(parameter.get("name") or "")
        location = str(parameter.get("in") or "")
        if not name or location not in {"path", "query", "header"}:
            continue
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {"type": "string"}
        prop = dict(schema)
        if parameter.get("description"):
            prop["description"] = str(parameter["description"])
        properties[name] = prop
        if parameter.get("required") or location == "path":
            required.append(name)
        if location == "header":
            mapping["headers"][name] = name
        else:
            mapping[location][name] = name
    body_schema = _json_request_body_schema(operation)
    if body_schema is not None:
        properties["body"] = body_schema
        mapping["body"] = "body"
        request_body = operation.get("requestBody")
        if isinstance(request_body, dict) and request_body.get("required"):
            required.append("body")
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(set(required), key=required.index)
    return schema, mapping


def _json_request_body_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    return dict(schema) if isinstance(schema, dict) else {"type": "object"}


def _response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return {}
    ok = responses.get("200") or responses.get("201") or responses.get("default")
    if not isinstance(ok, dict):
        return {}
    content = ok.get("content")
    if not isinstance(content, dict):
        return {}
    media = content.get("application/json")
    if not isinstance(media, dict):
        return {}
    schema = media.get("schema")
    return dict(schema) if isinstance(schema, dict) else {}


def _default_tool_type(method: str, path: str) -> str:
    if method == "HEAD":
        return "detail"
    if method == "GET":
        last = path.rstrip("/").split("/")[-1]
        return "detail" if last.startswith("{") and last.endswith("}") else "search"
    return "unconfigured"


def _schema_example(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {name: _example_value(definition) for name, definition in properties.items()}


def _example_value(definition: Any) -> Any:
    if not isinstance(definition, dict):
        return None
    if "example" in definition:
        return definition["example"]
    if "default" in definition:
        return definition["default"]
    value_type = definition.get("type")
    if value_type == "string":
        return "<string>"
    if value_type in {"integer", "number"}:
        return 0
    if value_type == "boolean":
        return False
    if value_type == "array":
        return []
    if value_type == "object":
        return {}
    return None
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
uv run pytest tests/test_openapi_parser.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/agent_bridge/capability_hub/sources/openapi tests/test_openapi_parser.py
git commit -m "feat: parse openapi operations for import"
```

---

### Task 3: Add OpenAPI HTTP Execution Client

**Files:**
- Create: `src/agent_bridge/capability_hub/sources/openapi/http_client.py`
- Test: `tests/test_openapi_http_client.py`

- [ ] **Step 1: Write HTTP client tests**

Create `tests/test_openapi_http_client.py`:

```python
from __future__ import annotations

import respx
from httpx import Response

from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient


@respx.mock
def test_openapi_client_maps_path_query_headers_and_body() -> None:
    route = respx.post("https://crm.example.test/users/u_123/orders?dry_run=true").mock(
        return_value=Response(200, json={"ok": True})
    )
    client = OpenApiHttpClient()

    result = client.call_tool(
        service={
            "base_url": "https://crm.example.test",
            "headers": {"X-Tenant": "demo"},
            "auth_config": {"type": "bearer", "token": "secret"},
        },
        tool={
            "method": "POST",
            "path": "/users/{id}/orders",
            "request_mapping": {
                "path": {"id": "id"},
                "query": {"dry_run": "dry_run"},
                "headers": {"X-Request-Id": "request_id"},
                "body": "body",
            },
        },
        params={
            "id": "u_123",
            "dry_run": True,
            "request_id": "req_1",
            "body": {"sku": "book"},
        },
    )

    assert result == {"status_code": 200, "headers": {"content-type": "application/json"}, "body": {"ok": True}}
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["x-tenant"] == "demo"
    assert request.headers["x-request-id"] == "req_1"
    assert request.content == b'{"sku":"book"}'


@respx.mock
def test_openapi_client_supports_api_key_header() -> None:
    route = respx.get("https://crm.example.test/users").mock(return_value=Response(200, json=[]))
    client = OpenApiHttpClient()

    result = client.call_tool(
        service={
            "base_url": "https://crm.example.test",
            "headers": {},
            "auth_config": {"type": "api_key", "header": "X-API-Key", "value": "abc"},
        },
        tool={
            "method": "GET",
            "path": "/users",
            "request_mapping": {"path": {}, "query": {}, "headers": {}, "body": None},
        },
        params={},
    )

    assert result["body"] == []
    assert route.calls[0].request.headers["x-api-key"] == "abc"
```

- [ ] **Step 2: Run HTTP client tests to verify failure**

Run:

```bash
uv run pytest tests/test_openapi_http_client.py -q
```

Expected: FAIL because `OpenApiHttpClient` does not exist.

- [ ] **Step 3: Implement HTTP client**

Create `src/agent_bridge/capability_hub/sources/openapi/http_client.py`:

```python
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class OpenApiHttpClient:
    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def call_tool(self, *, service: dict[str, Any], tool: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        method = str(tool["method"]).upper()
        path = str(tool["path"])
        mapping = tool.get("request_mapping")
        if not isinstance(mapping, dict):
            mapping = {"path": {}, "query": {}, "headers": {}, "body": None}
        url = self._url(str(service["base_url"]), path, mapping.get("path"), params)
        headers = self._headers(service, mapping.get("headers"), params)
        query = self._mapped(mapping.get("query"), params)
        body_key = mapping.get("body")
        body = params.get(body_key) if isinstance(body_key, str) else None
        response = httpx.request(
            method,
            url,
            params=query,
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            body_value: Any = response.json()
        else:
            body_value = response.text
        return {
            "status_code": response.status_code,
            "headers": {"content-type": content_type} if content_type else {},
            "body": body_value,
        }

    def _url(self, base_url: str, path: str, path_mapping: Any, params: dict[str, Any]) -> str:
        next_path = path
        for name, param_name in self._mapping_items(path_mapping):
            value = params.get(param_name)
            next_path = next_path.replace("{" + name + "}", quote(str(value), safe=""))
        return base_url.rstrip("/") + "/" + next_path.lstrip("/")

    def _headers(self, service: dict[str, Any], header_mapping: Any, params: dict[str, Any]) -> dict[str, str]:
        headers = {str(key): str(value) for key, value in service.get("headers", {}).items()}
        auth = service.get("auth_config") if isinstance(service.get("auth_config"), dict) else {}
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = f"Bearer {auth['token']}"
        if auth.get("type") == "api_key" and auth.get("header") and auth.get("value"):
            headers[str(auth["header"])] = str(auth["value"])
        for header_name, param_name in self._mapping_items(header_mapping):
            if param_name in params:
                headers[header_name] = str(params[param_name])
        return headers

    def _mapped(self, mapping: Any, params: dict[str, Any]) -> dict[str, Any]:
        return {name: params[param_name] for name, param_name in self._mapping_items(mapping) if param_name in params}

    def _mapping_items(self, mapping: Any) -> list[tuple[str, str]]:
        if not isinstance(mapping, dict):
            return []
        return [(str(name), str(param_name)) for name, param_name in mapping.items()]
```

- [ ] **Step 4: Run HTTP client tests**

Run:

```bash
uv run pytest tests/test_openapi_http_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_bridge/capability_hub/sources/openapi/http_client.py tests/test_openapi_http_client.py
git commit -m "feat: execute saved openapi tools over http"
```

---

### Task 4: Add OpenAPI Adapter and CapabilityService Integration

**Files:**
- Create: `src/agent_bridge/capability_hub/sources/openapi/adapter.py`
- Modify: `src/agent_bridge/capability_hub/service.py`
- Modify: `src/agent_bridge/capability_hub/governance.py`
- Modify: `src/agent_bridge/capability_hub/profiles/pins.py`
- Test: `tests/test_openapi_capability_service.py`

- [ ] **Step 1: Write capability service tests**

Create `tests/test_openapi_capability_service.py`:

```python
from __future__ import annotations

import asyncio
import json

import pytest

from agent_bridge.capability_hub.models import SourceType, ToolType
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.domain import ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


class FakeOpenApiClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def call_tool(self, *, service, tool, params):
        self.calls.append({"service": service, "tool": tool, "params": params})
        return {"status_code": 200, "headers": {"content-type": "application/json"}, "body": {"id": params["id"]}}


def _service(wm_paths) -> tuple[CapabilityService, SQLiteStore, FakeOpenApiClient]:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client = FakeOpenApiClient()
    svc = CapabilityService(store=store, admins={"root"}, openapi_client=client)
    return svc, store, client


def _add_openapi_tool(store: SQLiteStore, tool_type: ToolType = ToolType.detail) -> None:
    store.capabilities.create_openapi_service(
        service_key="crm-api",
        name="CRM API",
        base_url="https://crm.example.test",
        spec_url="",
        spec_content={},
        auth_config={},
        headers={},
        description="CRM read APIs",
        tags=["crm"],
        created_by="root",
    )
    store.capabilities.upsert_openapi_tool(
        service_key="crm-api",
        tool_name="get_user",
        operation_id="getUser",
        method="GET",
        path="/users/{id}",
        display_name="Get User",
        description="Fetch one user",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        request_mapping={"path": {"id": "id"}, "query": {}, "headers": {}, "body": None},
        response_schema={},
        tool_type=tool_type,
        tags=["users"],
        examples=[{"id": "u_123"}],
    )


def test_search_lists_openapi_service_and_tools(wm_paths) -> None:
    svc, store, _ = _service(wm_paths)
    _add_openapi_tool(store)

    root = svc.search("root", None, None)
    assert any(item["service"] == "crm-api" and item["kind"] == "service" for item in root["items"])

    detail = svc.search("root", "crm-api", None)
    assert detail["items"][0]["tool"] == "get_user"
    assert detail["items"][0]["method"] == "GET"
    assert detail["items"][0]["path"] == "/users/{id}"


def test_execute_openapi_readonly_tool_writes_log(wm_paths) -> None:
    svc, store, client = _service(wm_paths)
    _add_openapi_tool(store)

    result = asyncio.run(svc.execute("root", "crm-api", "get_user", {"id": "u_123"}))

    assert result["success"] is True
    assert result["result"]["body"] == {"id": "u_123"}
    assert client.calls[0]["params"] == {"id": "u_123"}
    logs = svc.governance.list_logs(actor="root", source_type=SourceType.openapi_service.value, source_key="crm-api")
    assert logs[0]["tool_name"] == "get_user"
    assert json.loads(logs[0]["response_json"])["success"] is True


def test_execute_openapi_action_is_blocked(wm_paths) -> None:
    svc, store, client = _service(wm_paths)
    _add_openapi_tool(store, ToolType.action)

    with pytest.raises(ValidationError, match="tool type is not executable"):
        asyncio.run(svc.execute("root", "crm-api", "get_user", {"id": "u_123"}))

    assert client.calls == []
```

- [ ] **Step 2: Run capability service tests to verify failure**

Run:

```bash
uv run pytest tests/test_openapi_capability_service.py -q
```

Expected: FAIL because `CapabilityService` has no OpenAPI integration.

- [ ] **Step 3: Implement OpenAPI adapter**

Create `src/agent_bridge/capability_hub/sources/openapi/adapter.py`:

```python
from __future__ import annotations

import json
from typing import Any

from agent_bridge.capability_hub.models import McpServiceStatus, ToolType
from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore


def json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


class OpenApiCapabilityAdapter:
    def __init__(self, *, store: SQLiteStore, admins: set[str], client: OpenApiHttpClient | None = None) -> None:
        self.store = store
        self.admins = admins
        self.client = client or OpenApiHttpClient()

    def require_enabled_service(self, service_key: str) -> dict[str, Any]:
        service = self.store.capabilities.get_openapi_service(service_key)
        if service is None:
            raise NotFound("service not found")
        if service["status"] != McpServiceStatus.enabled.value:
            raise ValidationError("OpenAPI service is not enabled")
        return service

    def service_payload(self, service: dict[str, Any], *, redact_headers: bool = False) -> dict[str, Any]:
        payload = dict(service)
        headers = json_loads(payload.pop("headers_json", None), {})
        auth_config = json_loads(payload.pop("auth_config_json", None), {})
        if redact_headers:
            headers = {key: "***" if value else value for key, value in headers.items()}
            auth_config = {key: "***" if key in {"token", "value"} and value else value for key, value in auth_config.items()}
        payload["headers"] = headers
        payload["auth_config"] = auth_config
        payload["spec_content"] = json_loads(payload.pop("spec_content_json", None), {})
        payload["tags"] = json_loads(payload.pop("tags_json", None), [])
        payload["source_type"] = "openapi_service"
        return payload

    def tool_payload(self, tool: dict[str, Any]) -> dict[str, Any]:
        payload = dict(tool)
        input_schema = json_loads(payload.pop("input_schema_json", None), {})
        request_mapping = json_loads(payload.pop("request_mapping_json", None), {})
        response_schema = json_loads(payload.pop("response_schema_json", None), {})
        tags = json_loads(payload.pop("tags_json", None), [])
        examples = json_loads(payload.pop("examples_json", None), [])
        return {
            **payload,
            "service": tool["service_key"],
            "tool": tool["tool_name"],
            "name": tool["display_name"],
            "input_schema": input_schema,
            "request_mapping": request_mapping,
            "response_schema": response_schema,
            "tags": tags,
            "examples": examples,
            "execute_example": examples[0] if examples else {},
            "executable": tool["tool_type"] in {"overview", "search", "detail"},
            "source_type": "openapi_service",
        }

    def list_services(self, actor: str) -> list[dict[str, Any]]:
        return [self.service_payload(item, redact_headers=True) for item in self.store.capabilities.list_openapi_services()]

    def list_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        self.require_enabled_service(service_key)
        return [self.tool_payload(item) for item in self.store.capabilities.list_openapi_tools(service_key)]

    def execute(self, service_key: str, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        service = self.service_payload(self.require_enabled_service(service_key), redact_headers=False)
        tool_row = self.store.capabilities.get_openapi_tool(service_key, tool_name)
        if tool_row is None:
            raise NotFound("tool not found")
        if tool_row["tool_type"] not in {ToolType.overview.value, ToolType.search.value, ToolType.detail.value}:
            raise ValidationError("tool type is not executable")
        tool = self.tool_payload(tool_row)
        result = self.client.call_tool(service=service, tool=tool, params=params)
        return {"service": service_key, "tool": tool_name, "tool_name": tool_name, "success": True, "result": result}
```

- [ ] **Step 4: Wire adapter into CapabilityService**

Modify `src/agent_bridge/capability_hub/service.py`:

```python
from agent_bridge.capability_hub.sources.openapi.adapter import OpenApiCapabilityAdapter
from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient
```

Update `CapabilityService.__init__`:

```python
        openapi_client: OpenApiHttpClient | None = None,
```

and inside the constructor:

```python
        self.openapi = OpenApiCapabilityAdapter(store=store, admins=admins, client=openapi_client)
```

Update root search to include enabled OpenAPI services:

```python
        openapi_services = [
            service for service in self.store.capabilities.list_openapi_services()
            if service["status"] == McpServiceStatus.enabled.value
        ]
```

Build external items with `kind: "service"`, `source_type: "openapi_service"`, `service`, `name`, `description`, `tags`, `tool_count`, and `status`.

In `_search_without_log`, when normalized path is not builtin or MCP, check OpenAPI:

```python
                if self.store.capabilities.get_openapi_service(normalized_path) is not None:
                    if not self.governance.is_source_allowed(actor, profile_key, SourceType.openapi_service.value, normalized_path):
                        return {"path": normalized_path, "items": []}
                    items = [self._openapi_tool_search_item(tool) for tool in self.openapi.list_tools(actor, normalized_path)]
                    response_path = normalized_path
                else:
                    ...
```

Add:

```python
    def _openapi_tool_search_item(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "tool",
            "service": tool["service"],
            "tool": tool["tool"],
            "name": tool["name"],
            "description": tool["description"],
            "tags": tool["tags"],
            "tool_type": tool["tool_type"],
            "input_schema": tool["input_schema"],
            "execute_example": tool["execute_example"],
            "executable": tool["executable"],
            "method": tool["method"],
            "path": tool["path"],
            "source_type": SourceType.openapi_service.value,
        }
```

In `execute`, calculate `source_type` with OpenAPI before MCP fallback:

```python
        if service in self.builtin_providers:
            source_type = SourceType.builtin.value
        elif self.store.capabilities.get_openapi_service(service) is not None:
            source_type = SourceType.openapi_service.value
        else:
            source_type = SourceType.mcp_service.value
```

In the non-builtin branch, dispatch OpenAPI:

```python
            elif source_type == SourceType.openapi_service.value:
                if not self.governance.is_source_allowed(actor, profile_key, SourceType.openapi_service.value, service):
                    raise _mark_call_log_failure(
                        _mark_call_log_status(ValidationError("source is blocked by profile policy"), CallLogStatus.blocked.value),
                        stage=FailureStage.profile_policy.value,
                        owner=FailureOwner.policy.value,
                        error_type="profile_policy_blocked",
                    )
                result = self.openapi.execute(service, tool_name, params)
            elif not self.governance.is_source_allowed(actor, profile_key, SourceType.mcp_service.value, service):
```

- [ ] **Step 5: Allow OpenAPI source type in governance validation**

Modify `src/agent_bridge/capability_hub/governance.py` wherever valid source type checks use `SourceType`; after adding enum, the existing `SourceType(source_type)` validation should pass. Add tests in `tests/test_capability_governance.py` if current coverage does not assert OpenAPI rules:

```python
def test_policy_filters_openapi_sources(wm_paths) -> None:
    svc = _service(wm_paths)
    svc.upsert_profile("root", "crm", "CRM", "", "active")
    svc.replace_profile_rules(
        "root",
        "crm",
        [{"source_type": "openapi_service", "source_key": "crm-api", "effect": "allow"}],
    )
    assert svc.is_source_allowed("root", "crm", "openapi_service", "crm-api") is True
    assert svc.is_source_allowed("root", "crm", "openapi_service", "hr-api") is False
```

- [ ] **Step 6: Run OpenAPI capability tests**

Run:

```bash
uv run pytest tests/test_openapi_capability_service.py tests/test_capability_governance.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_bridge/capability_hub/service.py src/agent_bridge/capability_hub/governance.py src/agent_bridge/capability_hub/profiles/pins.py src/agent_bridge/capability_hub/sources/openapi/adapter.py tests/test_openapi_capability_service.py tests/test_capability_governance.py
git commit -m "feat: route openapi tools through capability service"
```

---

### Task 5: Add OpenAPI FastAPI Routes and Schemas

**Files:**
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/capabilities.py`
- Test: `tests/test_openapi_api.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_openapi_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def test_openapi_service_crud_and_tool_save_api(wm_paths) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    headers = {"X-Agent-Bridge-User": "root"}

    created = client.post(
        "/capabilities/openapi-services",
        headers=headers,
        json={
            "service_key": "crm-api",
            "name": "CRM API",
            "base_url": "https://crm.example.test",
            "spec_url": "",
            "spec_content": {},
            "auth_config": {"type": "bearer", "token": "secret"},
            "headers": {"X-Tenant": "demo"},
            "description": "CRM read APIs",
            "tags": ["crm"],
        },
    )
    assert created.status_code == 200
    assert created.json()["source_type"] == "openapi_service"
    assert created.json()["auth_config"]["token"] == "secret"

    listed = client.get("/capabilities/openapi-services", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["auth_config"]["token"] == "***"

    tool = client.put(
        "/capabilities/openapi-services/crm-api/tools/get_user",
        headers=headers,
        json={
            "tool_name": "get_user",
            "operation_id": "getUser",
            "method": "GET",
            "path": "/users/{id}",
            "display_name": "Get User",
            "description": "Fetch one user",
            "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            "request_mapping": {"path": {"id": "id"}, "query": {}, "headers": {}, "body": None},
            "response_schema": {},
            "tool_type": "detail",
            "tags": ["users"],
            "examples": [{"id": "u_123"}],
        },
    )
    assert tool.status_code == 200
    assert tool.json()["tool"] == "get_user"

    tools = client.get("/capabilities/openapi-services/crm-api/tools", headers=headers)
    assert tools.status_code == 200
    assert tools.json()[0]["method"] == "GET"


def test_openapi_import_preview_does_not_persist_tools(wm_paths) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capabilities/openapi-services",
        headers=headers,
        json={
            "service_key": "crm-api",
            "name": "CRM API",
            "base_url": "https://crm.example.test",
            "spec_content": {
                "openapi": "3.0.3",
                "info": {"title": "CRM", "version": "1"},
                "paths": {"/users": {"get": {"operationId": "listUsers", "responses": {"200": {"description": "OK"}}}}},
            },
        },
    )

    imported = client.post("/capabilities/openapi-services/crm-api/import", headers=headers)

    assert imported.status_code == 200
    assert imported.json()["operations"][0]["tool_name"] == "list_users"
    assert client.get("/capabilities/openapi-services/crm-api/tools", headers=headers).json() == []
```

- [ ] **Step 2: Run API tests to verify failure**

Run:

```bash
uv run pytest tests/test_openapi_api.py -q
```

Expected: FAIL because routes and schemas do not exist.

- [ ] **Step 3: Add schemas**

In `src/agent_bridge/api/schemas.py`, add:

```python
class RegisterOpenApiServiceRequest(BaseModel):
    service_key: str
    name: str
    base_url: str
    spec_url: str = ""
    spec_content: dict[str, Any] = Field(default_factory=dict)
    auth_config: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ImportOpenApiOperationsRequest(BaseModel):
    spec_url: str | None = None
    spec_content: dict[str, Any] | str | None = None


class UpsertOpenApiToolRequest(BaseModel):
    tool_name: str
    operation_id: str = ""
    method: str
    path: str
    display_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    request_mapping: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    tool_type: str = "unconfigured"
    tags: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 4: Add CapabilityService OpenAPI admin methods**

In `src/agent_bridge/capability_hub/service.py`, add methods:

```python
    def register_openapi_service(
        self,
        actor: str,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: dict[str, Any],
        auth_config: dict[str, Any] | None,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._validate_service_key(service_key)
        existing = self.store.capabilities.get_openapi_service(service_key)
        if existing is None:
            service = self.store.capabilities.create_openapi_service(
                service_key=service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config or {},
                headers=headers or {},
                description=description,
                tags=tags,
                created_by=actor,
            )
        else:
            service = self.store.capabilities.update_openapi_service(
                service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config,
                headers=headers,
                description=description,
                tags=tags,
            )
        return self.openapi.service_payload(service)

    def list_openapi_services(self, actor: str) -> list[dict[str, Any]]:
        return self.openapi.list_services(actor)

    def list_openapi_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        return self.openapi.list_tools(actor, service_key)

    def upsert_openapi_tool(self, actor: str, service_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.capabilities.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        return self.openapi.tool_payload(
            self.store.capabilities.upsert_openapi_tool(service_key=service_key, **payload)
        )

    def delete_openapi_tool(self, actor: str, service_key: str, tool_name: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        deleted = self.store.capabilities.delete_openapi_tool(service_key, tool_name)
        if not deleted:
            raise NotFound("tool not found")
        return {"service_key": service_key, "tool_name": tool_name, "deleted": True}
```

Add import preview method:

```python
    async def import_openapi_operations(
        self,
        actor: str,
        service_key: str,
        spec_url: str | None = None,
        spec_content: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        service = self.store.capabilities.get_openapi_service(service_key)
        if service is None:
            raise NotFound("service not found")
        from agent_bridge.capability_hub.sources.openapi.parser import parse_openapi_operations
        import httpx

        try:
            source: dict[str, Any] | str
            if spec_content is not None:
                source = spec_content
            else:
                next_url = spec_url or service.get("spec_url") or ""
                if not next_url:
                    source = json_loads(service.get("spec_content_json"), {})
                else:
                    response = httpx.get(next_url, timeout=30.0)
                    response.raise_for_status()
                    source = response.text
            operations = parse_openapi_operations(source)
            self.store.capabilities.mark_openapi_service_import(service_key, success=True)
            return {"service_key": service_key, "operations": operations}
        except Exception as exc:
            self.store.capabilities.mark_openapi_service_import(service_key, success=False, error=str(exc))
            raise ValidationError(f"OpenAPI import failed: {exc}") from exc
```

Use the existing module-level `_json_loads` helper instead of `json_loads` if available in `service.py`.

- [ ] **Step 5: Add routes**

In `src/agent_bridge/api/routes/capabilities.py`, import new schemas and add routes:

```python
    @router.post("/capabilities/openapi-services")
    def register_openapi_service(payload: RegisterOpenApiServiceRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.capabilities.register_openapi_service(
                current_actor,
                payload.service_key,
                payload.name,
                payload.base_url,
                payload.spec_url,
                payload.spec_content,
                payload.auth_config,
                payload.headers,
                payload.description,
                payload.tags,
            )
        )

    @router.get("/capabilities/openapi-services")
    def list_openapi_services(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_openapi_services(current_actor))

    @router.post("/capabilities/openapi-services/{service_key}/import")
    async def import_openapi_operations(service_key: str, payload: ImportOpenApiOperationsRequest | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        payload = payload or ImportOpenApiOperationsRequest()
        return await call_safely_async(
            lambda: service.capabilities.import_openapi_operations(
                current_actor,
                service_key,
                payload.spec_url,
                payload.spec_content,
            )
        )

    @router.get("/capabilities/openapi-services/{service_key}/tools")
    def list_openapi_tools(service_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_openapi_tools(current_actor, service_key))

    @router.put("/capabilities/openapi-services/{service_key}/tools/{tool_name}")
    def upsert_openapi_tool(service_key: str, tool_name: str, payload: UpsertOpenApiToolRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        body = payload.model_dump()
        body["tool_name"] = tool_name
        return call_safely(lambda: service.capabilities.upsert_openapi_tool(current_actor, service_key, body))

    @router.delete("/capabilities/openapi-services/{service_key}/tools/{tool_name}")
    def delete_openapi_tool(service_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.delete_openapi_tool(current_actor, service_key, tool_name))
```

- [ ] **Step 6: Extend catalog detail routes**

Update `capability_source_detail` and `capability_tool_detail` to accept `source_type == "openapi_service"` by calling the OpenAPI methods. Preserve existing MCP behavior.

- [ ] **Step 7: Run API tests**

Run:

```bash
uv run pytest tests/test_openapi_api.py tests/test_capability_api.py::test_capability_catalog_source_and_tool_details -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/capabilities.py src/agent_bridge/capability_hub/service.py tests/test_openapi_api.py
git commit -m "feat: expose openapi capability management api"
```

---

### Task 6: Add Frontend API Types and Form Helpers

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/capabilities/serviceForm.ts`
- Test: `frontend/capabilities/tests/serviceForm.test.ts`

- [ ] **Step 1: Write frontend helper tests**

Add to `frontend/capabilities/tests/serviceForm.test.ts`:

```ts
test('buildServicePayload builds openapi service payload', () => {
  const payload = buildServicePayload(
    {
      ...defaultServiceForm(),
      service_type: 'openapi_service',
      service_key: 'crm-api',
      name: 'CRM API',
      base_url: 'https://crm.example.test',
      spec_url: 'https://crm.example.test/openapi.json',
      auth_config: '{"type":"bearer","token":"secret"}',
      headers: '{"X-Tenant":"demo"}',
      description: 'CRM read APIs',
      tags: 'crm, readonly',
    },
    'create',
  )

  assert.deepEqual(payload, {
    service_type: 'openapi_service',
    service_key: 'crm-api',
    name: 'CRM API',
    base_url: 'https://crm.example.test',
    spec_url: 'https://crm.example.test/openapi.json',
    spec_content: {},
    auth_config: { type: 'bearer', token: 'secret' },
    headers: { 'X-Tenant': 'demo' },
    description: 'CRM read APIs',
    tags: ['crm', 'readonly'],
  })
})
```

- [ ] **Step 2: Run frontend helper test to verify failure**

Run:

```bash
npm --prefix frontend/capabilities test -- serviceForm.test.ts
```

Expected: FAIL because `service_type`, `base_url`, `spec_url`, and `auth_config` are not supported.

- [ ] **Step 3: Add TypeScript types**

In `frontend/capabilities/src/api/types.ts`, add:

```ts
export type CapabilitySourceType = 'mcp_service' | 'openapi_service'

export interface OpenApiService {
  source_type: 'openapi_service'
  service_key: string
  name: string
  base_url: string
  spec_url: string
  spec_content?: Record<string, unknown>
  auth_config?: Record<string, unknown>
  headers?: Record<string, unknown>
  description: string
  tags: string[]
  status: string
  created_by: string
  created_at: string
  updated_at: string
  last_synced_at: string | null
  last_error: string | null
}

export interface OpenApiTool extends McpTool {
  source_type: 'openapi_service'
  operation_id: string
  method: string
  path: string
  request_mapping: Record<string, unknown>
  response_schema: Record<string, unknown>
}

export interface OpenApiImportOperation {
  operation_id: string
  tool_name: string
  method: string
  path: string
  display_name: string
  description: string
  input_schema: Record<string, unknown>
  request_mapping: Record<string, unknown>
  response_schema: Record<string, unknown>
  tool_type: string
  tags: string[]
  examples: Record<string, unknown>[]
}

export type CapabilityService = McpService | OpenApiService
export type CapabilityTool = McpTool | OpenApiTool
```

- [ ] **Step 4: Add API client methods**

In `frontend/capabilities/src/api/client.ts`, import new types and add:

```ts
  listOpenApiServices: () => get<OpenApiService[]>('/capabilities/openapi-services'),
  registerOpenApiService: (s: Partial<OpenApiService> & { service_key: string; name: string; base_url: string }) =>
    post<OpenApiService>('/capabilities/openapi-services', s),
  importOpenApiOperations: (key: string, body?: { spec_url?: string; spec_content?: Record<string, unknown> | string }) =>
    post<{ service_key: string; operations: OpenApiImportOperation[] }>(`/capabilities/openapi-services/${key}/import`, body || {}),
  listOpenApiTools: (key: string) => get<OpenApiTool[]>(`/capabilities/openapi-services/${key}/tools`),
  upsertOpenApiTool: (serviceKey: string, toolName: string, tool: Partial<OpenApiTool> & { method: string; path: string }) =>
    put<OpenApiTool>(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`, tool),
  deleteOpenApiTool: (serviceKey: string, toolName: string) =>
    fetch(`/capabilities/openapi-services/${serviceKey}/tools/${toolName}`, { method: 'DELETE', headers: headers() }).then(async r => {
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
      return r.json()
    }),
```

- [ ] **Step 5: Update serviceForm helpers**

Modify `frontend/capabilities/src/views/capabilities/serviceForm.ts` so `defaultServiceForm()` returns:

```ts
{
  service_type: 'mcp_service',
  service_key: '',
  name: '',
  endpoint_url: '',
  base_url: '',
  spec_url: '',
  spec_content: '{}',
  auth_config: '',
  description: '',
  tags: '',
  headers: '',
}
```

Add:

```ts
export function parseOptionalJsonObject(value: string, label: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error(`${label} 必须是合法的 JSON 对象`)
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} 必须是 JSON 对象`)
  }
  return parsed as Record<string, unknown>
}
```

Update `buildServicePayload` to return MCP payload for `mcp_service`, and OpenAPI payload for `openapi_service`.

- [ ] **Step 6: Run frontend helper tests**

Run:

```bash
npm --prefix frontend/capabilities test -- serviceForm.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/capabilities/serviceForm.ts frontend/capabilities/tests/serviceForm.test.ts
git commit -m "feat: add frontend openapi service helpers"
```

---

### Task 7: Add OpenAPI Service UI and Import-to-Save Flow

**Files:**
- Modify: `frontend/capabilities/src/views/capabilities/ServicesView.vue`
- Modify: `frontend/capabilities/src/views/capabilities/ToolsView.vue`
- Create: `frontend/capabilities/tests/openapiImport.test.ts`
- Test: `frontend/capabilities/tests/openapiImport.test.ts`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write frontend import helper test**

Create a helper file `frontend/capabilities/src/views/capabilities/openapiImport.ts` during implementation, and first write `frontend/capabilities/tests/openapiImport.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'

import { selectedOperationsToTools } from '../src/views/capabilities/openapiImport.ts'

test('selectedOperationsToTools returns edited selected operations only', () => {
  const tools = selectedOperationsToTools([
    {
      selected: true,
      operation_id: 'getUser',
      tool_name: 'get_user',
      method: 'GET',
      path: '/users/{id}',
      display_name: 'Get User',
      description: 'Fetch one user',
      input_schema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      request_mapping: { path: { id: 'id' }, query: {}, headers: {}, body: null },
      response_schema: {},
      tool_type: 'detail',
      tags: ['users'],
      examples: [{ id: 'u_123' }],
    },
    {
      selected: false,
      operation_id: 'deleteUser',
      tool_name: 'delete_user',
      method: 'DELETE',
      path: '/users/{id}',
      display_name: 'Delete User',
      description: 'Delete one user',
      input_schema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      request_mapping: { path: { id: 'id' }, query: {}, headers: {}, body: null },
      response_schema: {},
      tool_type: 'unconfigured',
      tags: [],
      examples: [],
    },
  ])

  assert.equal(tools.length, 1)
  assert.equal(tools[0].tool_name, 'get_user')
  assert.equal(tools[0].method, 'GET')
})
```

- [ ] **Step 2: Run frontend import test to verify failure**

Run:

```bash
npm --prefix frontend/capabilities test -- openapiImport.test.ts
```

Expected: FAIL because `openapiImport.ts` does not exist.

- [ ] **Step 3: Implement import helper**

Create `frontend/capabilities/src/views/capabilities/openapiImport.ts`:

```ts
import type { OpenApiImportOperation } from '../../api/types'

export interface SelectableOpenApiOperation extends OpenApiImportOperation {
  selected: boolean
}

export function selectedOperationsToTools(items: SelectableOpenApiOperation[]): OpenApiImportOperation[] {
  return items
    .filter(item => item.selected)
    .map(({ selected, ...operation }) => operation)
}
```

- [ ] **Step 4: Update ServicesView UI**

In `ServicesView.vue`:

- Load MCP and OpenAPI services with `Promise.all([api.listServices(), api.listOpenApiServices()])`.
- Display a type badge: `MCP` or `OpenAPI`.
- Switch form fields by `form.service_type`.
- For OpenAPI services show `base_url`, `spec_url`, `auth_config`, `headers`, `description`, `tags`.
- For MCP services keep current `endpoint_url`.
- Add an OpenAPI service detail/import dialog:
  - button text: `导入接口`
  - call `api.importOpenApiOperations(service_key)`
  - keep candidates only in component state
  - allow selecting candidates
  - call `api.upsertOpenApiTool` for selected items on save

Use existing `Dialog`, `Button`, `Input`, `Badge`, and table styles. Avoid adding a landing page or explanatory text blocks.

- [ ] **Step 5: Update ToolsView**

In `ToolsView.vue`, ensure tools that include `method` and `path` render these fields in compact columns. MCP tools should show `-` in those columns.

- [ ] **Step 6: Run frontend tests**

Run:

```bash
npm --prefix frontend/capabilities test -- serviceForm.test.ts openapiImport.test.ts
```

Expected: PASS.

- [ ] **Step 7: Run backend static source checks that cover frontend strings**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_frontend_knowledge_navigation_groups_document_code_and_config tests/test_capability_api.py::test_frontend_stats_view_uses_calls_field_from_backend -q
```

Expected: PASS. Add a focused assertion test in `tests/test_capability_api.py` if project convention expects static source checks for new frontend text:

```python
def test_frontend_services_view_supports_openapi_source_type() -> None:
    source = Path("frontend/capabilities/src/views/capabilities/ServicesView.vue").read_text(encoding="utf-8")
    assert "OpenAPI" in source
    assert "importOpenApiOperations" in source
    assert "listOpenApiServices" in source
```

- [ ] **Step 8: Commit**

```bash
git add frontend/capabilities/src/views/capabilities/ServicesView.vue frontend/capabilities/src/views/capabilities/ToolsView.vue frontend/capabilities/src/views/capabilities/openapiImport.ts frontend/capabilities/tests/openapiImport.test.ts tests/test_capability_api.py
git commit -m "feat: add openapi service import ui"
```

---

### Task 8: Final Verification and Documentation Touch-Up

**Files:**
- Modify: `docs/superpowers/specs/2026-06-19-openapi-capability-source-design.html` only if implementation reveals a design correction.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest -q tests/test_openapi_storage.py tests/test_openapi_parser.py tests/test_openapi_http_client.py tests/test_openapi_capability_service.py tests/test_openapi_api.py tests/test_capability_service.py tests/test_capability_governance.py tests/test_metamcp_http_gateway.py
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests**

Run:

```bash
npm --prefix frontend/capabilities test -- serviceForm.test.ts openapiImport.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run broad backend suite with known baseline exclusions**

Run:

```bash
uv run pytest -q -m 'not ragflow and not weknora' -k 'not capability_admin_page and not capability_static_assets and not workflow_service_artifact_upsert_keeps_id'
```

Expected: PASS with the same deselected category as the current baseline.

- [ ] **Step 4: Run import collection check**

Run:

```bash
uv run pytest --collect-only -q
```

Expected: all tests collect without import errors.

- [ ] **Step 5: Inspect old MCP behavior**

Run:

```bash
uv run pytest -q tests/test_mcp_server.py tests/test_capability_service.py tests/test_capability_storage.py
```

Expected: PASS. Existing MCP registration, sync, search, execute, and tool type behavior must remain unchanged.

- [ ] **Step 6: Commit documentation correction if any**

If the spec was updated, commit it:

```bash
git add docs/superpowers/specs/2026-06-19-openapi-capability-source-design.html
git commit -m "docs: update openapi capability source design"
```

If the spec did not change, skip this commit.

---

## Self-Review Notes

- Spec coverage: The plan covers OpenAPI source/service modeling, two-table persistence, page-only import, manual tool save/delete, MetaMCP search/execute, profile governance, admin API, frontend service import UI, and testing.
- Placeholder scan: The plan intentionally avoids deferred placeholders; every task includes concrete files, commands, and expected outcomes.
- Type consistency: `service_key`, `tool_name`, `operation_id`, `request_mapping`, `input_schema`, `response_schema`, `tool_type`, and `source_type="openapi_service"` are used consistently across storage, service, API, frontend, and tests.
