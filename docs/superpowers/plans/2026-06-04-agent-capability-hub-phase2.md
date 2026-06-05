# Agent Capability Hub Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 2 observability and built-in Wiki/CodeGraph capability governance for Agent Capability Hub.

**Architecture:** Extend the existing `CapabilityGovernanceService` and `SQLiteStore` first, because logs, stats, and profile resource rules are shared by every later feature. Then add built-in capability providers behind `CapabilityService`, keeping external MCP services and built-in Wiki/CodeGraph tools behind the same MetaMCP `search` and `execute` surface. Keep the current FastAPI HTML shell with static CSS/JS.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Typer, FastMCP, pytest, static HTML/CSS/JavaScript.

---

## Source Requirements

Use this requirements document as the source of truth:

- `docs/agent-capability-hub-phase2-requirements.html`

Key decisions from the requirements:

- Phase 2 is "observability and built-in capability expansion", not action-tool approval.
- External MCP remains governed by service-level allow/deny rules.
- Wiki and CodeGraph are built-in sources. Their fixed tools cannot be disabled by profile; profile rules restrict visible KBs and repositories.
- MetaMCP `search` and `execute` must enforce the same profile restrictions server-side.
- Logs must distinguish platform parsing failures, policy blocks, registry validation failures, MCP transport/protocol failures, upstream tool failures, and built-in backend failures.

## File Structure

Create or modify these files:

- Modify `src/wiki_manager/capabilities.py`: add source type, log failure enums, resource rule dataclasses.
- Modify `src/wiki_manager/storage.py`: schema migration, profile resource rules, extended log fields, stats aggregation, CodeGraph tables.
- Modify `src/wiki_manager/capability_governance.py`: profile resource rule APIs, log classification validation, stats API.
- Modify `src/wiki_manager/capability_service.py`: error classification, log metadata, built-in provider dispatch.
- Create `src/wiki_manager/builtin_capabilities.py`: common built-in provider protocol and tool result helpers.
- Create `src/wiki_manager/builtin_wiki.py`: fixed Wiki tools backed by `WikiManagerService`.
- Create `src/wiki_manager/codegraph_service.py`: repository management, sync runs, file/symbol index search.
- Create `src/wiki_manager/builtin_codegraph.py`: fixed CodeGraph tools backed by `CodeGraphService`.
- Modify `src/wiki_manager/services.py`: wire Wiki and CodeGraph built-in providers into `CapabilityService`.
- Modify `src/wiki_manager/server.py`: log filters, stats API, profile resource API, built-in Wiki/CodeGraph admin APIs.
- Modify `src/wiki_manager/client.py`: profile resource and CodeGraph endpoints.
- Modify `src/wiki_manager/cli.py`: interactive `metamcp add`, non-interactive scope validation, overwrite confirmation.
- Modify `src/wiki_manager/web_pages.py`: navigation, stats view, built-in resources view, log detail modal markup.
- Modify `src/wiki_manager/static/capabilities/app.js`: log filters/detail modal, stats loading, profile copy command, profile resource rules, Wiki/CodeGraph pages.
- Modify `src/wiki_manager/static/capabilities/app.css`: filter bar, modal JSON viewer, stats table/trend styling, built-in status badges.
- Add tests:
  - `tests/test_capability_log_analysis.py`
  - `tests/test_capability_stats.py`
  - `tests/test_profile_resources.py`
  - `tests/test_builtin_wiki.py`
  - `tests/test_codegraph_service.py`
  - `tests/test_builtin_codegraph.py`
- Modify existing tests:
  - `tests/test_capability_governance.py`
  - `tests/test_capability_governance_storage.py`
  - `tests/test_capability_service.py`
  - `tests/test_capability_api.py`
  - `tests/test_cli.py`
  - `tests/test_metamcp_http_gateway.py`

## Task 1: Extend Core Enums, Log Fields, And Storage Migration

**Files:**
- Modify: `src/wiki_manager/capabilities.py`
- Modify: `src/wiki_manager/storage.py`
- Test: `tests/test_capability_governance_storage.py`
- Test: `tests/test_capability_log_analysis.py`

- [ ] **Step 1: Write failing storage tests for extended log fields**

Add this test to `tests/test_capability_log_analysis.py`:

```python
from __future__ import annotations

import json

from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, SourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.storage import SQLiteStore


def test_tool_call_log_records_failure_classification_and_resource(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    log = store.create_tool_call_log(
        log_id="call_failure_classification",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        resource_type="service",
        resource_key="mysql",
        request={"sql": "select 1"},
        response={"error": "transport unavailable"},
        status=CallLogStatus.error.value,
        error_message="transport unavailable",
        failure_stage=FailureStage.mcp_transport.value,
        failure_owner=FailureOwner.upstream_mcp.value,
        error_type="mcp_transport_error",
        duration_ms=25,
    )

    assert log["failure_stage"] == "mcp_transport"
    assert log["failure_owner"] == "upstream_mcp"
    assert log["error_type"] == "mcp_transport_error"
    assert log["resource_type"] == "service"
    assert log["resource_key"] == "mysql"
    assert json.loads(log["request_summary_json"]) == {"keys": ["sql"], "bytes": 19}
    assert json.loads(log["response_summary_json"]) == {"keys": ["error"], "bytes": 34}


def test_tool_call_log_filters_by_failure_and_time_range(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_tool_call_log(
        log_id="call_policy_block",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="hive",
        tool_name="query_sql",
        request={},
        response={"error": "blocked"},
        status=CallLogStatus.blocked.value,
        failure_stage=FailureStage.profile_policy.value,
        failure_owner=FailureOwner.policy.value,
        error_type="profile_policy_blocked",
    )
    store.create_tool_call_log(
        log_id="call_success",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={"rows": []},
        status=CallLogStatus.success.value,
    )

    filtered = store.list_tool_call_logs(
        profile_key="safe-readonly",
        failure_owner=FailureOwner.policy.value,
        error_type="profile_policy_blocked",
    )

    assert [item["log_id"] for item in filtered] == ["call_policy_block"]
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_capability_log_analysis.py -v
```

Expected: FAIL because `FailureOwner`, `FailureStage`, and the new log fields do not exist.

- [ ] **Step 3: Add capability enums**

Modify `src/wiki_manager/capabilities.py`:

```python
class SourceType(str, Enum):
    mcp_service = "mcp_service"
    builtin = "builtin"


class FailureStage(str, Enum):
    platform_request = "platform_request"
    profile_policy = "profile_policy"
    capability_registry = "capability_registry"
    mcp_transport = "mcp_transport"
    mcp_protocol = "mcp_protocol"
    upstream_tool = "upstream_tool"
    builtin_backend = "builtin_backend"
    internal = "internal"


class FailureOwner(str, Enum):
    platform = "platform"
    policy = "policy"
    upstream_mcp = "upstream_mcp"
    builtin_backend = "builtin_backend"
```

- [ ] **Step 4: Extend `tool_call_logs` schema**

Modify the `tool_call_logs` table definition in `src/wiki_manager/storage.py` by adding these columns after `duration_ms`:

```sql
  failure_stage TEXT,
  failure_owner TEXT,
  error_type TEXT,
  resource_type TEXT,
  resource_key TEXT,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  response_summary_json TEXT NOT NULL DEFAULT '{}',
```

Add indexes after the existing log indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_failure ON tool_call_logs(failure_owner, failure_stage, error_type);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_resource ON tool_call_logs(resource_type, resource_key);
```

- [ ] **Step 5: Add log migration helpers**

Add this helper method to `SQLiteStore` in `src/wiki_manager/storage.py`:

```python
    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
```

Add this call inside `migrate_phase2()` after `_migrate_tool_call_logs_nullable_profile(conn)`:

```python
            self._ensure_columns(
                conn,
                "tool_call_logs",
                [
                    ("failure_stage", "TEXT"),
                    ("failure_owner", "TEXT"),
                    ("error_type", "TEXT"),
                    ("resource_type", "TEXT"),
                    ("resource_key", "TEXT"),
                    ("request_summary_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("response_summary_json", "TEXT NOT NULL DEFAULT '{}'"),
                ],
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_call_logs_failure "
                "ON tool_call_logs(failure_owner, failure_stage, error_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_call_logs_resource "
                "ON tool_call_logs(resource_type, resource_key)"
            )
```

- [ ] **Step 6: Add JSON summary helper and extended log storage**

Add helper functions near `_enum_value` in `src/wiki_manager/storage.py`:

```python
def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _json_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"keys": sorted(str(key) for key in value.keys()), "bytes": _json_bytes(value)}
    if isinstance(value, list):
        return {"items": len(value), "bytes": _json_bytes(value)}
    return {"type": type(value).__name__, "bytes": _json_bytes(value)}
```

Extend `SQLiteStore.create_tool_call_log()` signature:

```python
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
```

Extend the `INSERT` column list and values:

```python
                  failure_stage,
                  failure_owner,
                  error_type,
                  resource_type,
                  resource_key,
                  request_summary_json,
                  response_summary_json
```

```python
                    failure_stage,
                    failure_owner,
                    error_type,
                    resource_type,
                    resource_key,
                    json.dumps(_json_summary({} if request is None else request), ensure_ascii=False, default=str),
                    json.dumps(_json_summary({} if response is None else response), ensure_ascii=False, default=str),
```

- [ ] **Step 7: Extend log filters**

Extend `SQLiteStore.list_tool_call_logs()` signature:

```python
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
```

Add these filters to the existing `(column, value)` loop:

```python
            ("failure_stage", failure_stage),
            ("failure_owner", failure_owner),
            ("error_type", error_type),
            ("resource_type", resource_type),
            ("resource_key", resource_key),
```

Add time filters after that loop:

```python
        if created_from is not None:
            filters.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            filters.append("created_at < ?")
            params.append(created_to)
```

- [ ] **Step 8: Run storage tests**

Run:

```bash
uv run pytest tests/test_capability_governance_storage.py tests/test_capability_log_analysis.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/wiki_manager/capabilities.py src/wiki_manager/storage.py tests/test_capability_governance_storage.py tests/test_capability_log_analysis.py
git commit -m "feat: extend capability call log classification"
```

## Task 2: Add Log Classification In Governance And Capability Execution

**Files:**
- Modify: `src/wiki_manager/capability_governance.py`
- Modify: `src/wiki_manager/capability_service.py`
- Test: `tests/test_capability_governance.py`
- Test: `tests/test_capability_service.py`
- Test: `tests/test_capability_log_analysis.py`

- [ ] **Step 1: Write failing governance validation tests**

Add to `tests/test_capability_governance.py`:

```python
def test_log_filters_validate_failure_fields(wm_paths: WikiManagerPaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid failure stage"):
        service.list_logs(actor="root", failure_stage="wrong")

    with pytest.raises(ValidationError, match="invalid failure owner"):
        service.list_logs(actor="root", failure_owner="wrong")

    with pytest.raises(ValidationError, match="invalid failure stage"):
        service.log_tool_call(
            actor="root",
            profile_key=None,
            entrypoint="metamcp_search",
            source_type=None,
            source_key=None,
            tool_name="search",
            request={},
            response={},
            status=CallLogStatus.error.value,
            error_message="bad",
            duration_ms=1,
            failure_stage="wrong",
            failure_owner=None,
            error_type="internal_error",
        )
```

- [ ] **Step 2: Write failing capability classification tests**

Add to `tests/test_capability_log_analysis.py`:

```python
import asyncio

import pytest

from wiki_manager.capabilities import FailureOwner, FailureStage, ToolType
from wiki_manager.capability_service import CapabilityService
from wiki_manager.domain import ValidationError
from wiki_manager.storage import SQLiteStore
from tests.test_capability_service import FakeMcpClient


def test_execute_classifies_profile_block(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FakeMcpClient(), admins={"root"})
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    with pytest.raises(ValidationError):
        asyncio.run(service.execute("root", "hive", "query_sql", {}, profile_key="safe-readonly"))

    log = service.governance.list_logs(actor="root", status="blocked")[0]
    assert log["failure_stage"] == FailureStage.profile_policy.value
    assert log["failure_owner"] == FailureOwner.policy.value
    assert log["error_type"] == "profile_policy_blocked"


def test_execute_classifies_mcp_transport_error(wm_paths: WikiManagerPaths) -> None:
    class FailingCallMcpClient(FakeMcpClient):
        async def call_tool(self, endpoint_url, headers, tool_name, arguments):
            raise RuntimeError("transport unavailable")

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FailingCallMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    with pytest.raises(ValidationError):
        asyncio.run(service.execute("root", "docs-api", "search_docs", {"query": "hello"}))

    log = service.governance.list_logs(actor="root", status="error")[0]
    assert log["failure_stage"] == FailureStage.mcp_transport.value
    assert log["failure_owner"] == FailureOwner.upstream_mcp.value
    assert log["error_type"] == "mcp_transport_error"
```

- [ ] **Step 3: Run failing tests**

Run:

```bash
uv run pytest tests/test_capability_governance.py tests/test_capability_log_analysis.py -v
```

Expected: FAIL because governance does not validate failure fields and capability execution does not pass them.

- [ ] **Step 4: Add validation to governance**

Modify imports in `src/wiki_manager/capability_governance.py`:

```python
from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, ProfileRuleEffect, SourceType
```

Extend `log_tool_call()` signature and call:

```python
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
```

```python
        normalized_failure_stage = self._validate_optional_failure_stage(failure_stage)
        normalized_failure_owner = self._validate_optional_failure_owner(failure_owner)
```

Pass the normalized values to `self.store.create_tool_call_log` with these keyword arguments:

```python
            failure_stage=normalized_failure_stage,
            failure_owner=normalized_failure_owner,
            error_type=error_type,
            resource_type=resource_type,
            resource_key=resource_key,
```

Extend `list_logs()` with the same filter parameters and pass them to storage after validation:

```python
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
```

Add validation helpers:

```python
    def _validate_optional_failure_stage(self, failure_stage: str | None) -> str | None:
        if failure_stage is None:
            return None
        try:
            return FailureStage(failure_stage).value
        except ValueError as exc:
            raise ValidationError("invalid failure stage") from exc

    def _validate_optional_failure_owner(self, failure_owner: str | None) -> str | None:
        if failure_owner is None:
            return None
        try:
            return FailureOwner(failure_owner).value
        except ValueError as exc:
            raise ValidationError("invalid failure owner") from exc
```

- [ ] **Step 5: Add classification helpers to capability service**

Modify imports in `src/wiki_manager/capability_service.py`:

```python
from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, McpServiceStatus, SourceType, ToolType
```

Add helpers near `_call_log_status`:

```python
def _mark_call_log_failure(
    exc: Exception,
    *,
    stage: str,
    owner: str,
    error_type: str,
) -> Exception:
    setattr(exc, "_tool_call_failure_stage", stage)
    setattr(exc, "_tool_call_failure_owner", owner)
    setattr(exc, "_tool_call_error_type", error_type)
    return exc


def _failure_stage(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_failure_stage", FailureStage.internal.value))


def _failure_owner(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_failure_owner", FailureOwner.platform.value))


def _error_type(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_error_type", "internal_error"))
```

Update the following failure sites to call `_mark_call_log_failure`:

| Failure site | Stage | Owner | Error type | Status |
| --- | --- | --- | --- | --- |
| profile blocked | `profile_policy` | `policy` | `profile_policy_blocked` | `blocked` |
| tool type unconfigured | `capability_registry` | `platform` | `capability_registry_error` | `blocked` |
| tool type non-executable | `capability_registry` | `platform` | `capability_registry_error` | `blocked` |
| service disabled or missing | `capability_registry` | `platform` | `capability_registry_error` | `error` |
| tool missing | `capability_registry` | `platform` | `capability_registry_error` | `error` |
| MCP client exception | `mcp_transport` | `upstream_mcp` | `mcp_transport_error` | `error` |

Use this code shape for profile block:

```python
                raise _mark_call_log_failure(
                    _mark_call_log_status(
                        ValidationError("source is blocked by profile policy"),
                        CallLogStatus.blocked.value,
                    ),
                    stage=FailureStage.profile_policy.value,
                    owner=FailureOwner.policy.value,
                    error_type="profile_policy_blocked",
                )
```

Example for MCP transport errors:

```python
        except Exception as exc:
            raise _mark_call_log_failure(
                ValidationError(f"MCP tool execution failed: {exc}"),
                stage=FailureStage.mcp_transport.value,
                owner=FailureOwner.upstream_mcp.value,
                error_type="mcp_transport_error",
            ) from exc
```

Update error logging calls in `search()` and `execute()`:

```python
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
```

- [ ] **Step 6: Run classification tests**

Run:

```bash
uv run pytest tests/test_capability_governance.py tests/test_capability_service.py tests/test_capability_log_analysis.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/capability_governance.py src/wiki_manager/capability_service.py tests/test_capability_governance.py tests/test_capability_service.py tests/test_capability_log_analysis.py
git commit -m "feat: classify capability call failures"
```

## Task 3: Add Log Stats Aggregation API

**Files:**
- Modify: `src/wiki_manager/storage.py`
- Modify: `src/wiki_manager/capability_governance.py`
- Modify: `src/wiki_manager/server.py`
- Test: `tests/test_capability_stats.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing storage stats test**

Create `tests/test_capability_stats.py`:

```python
from __future__ import annotations

from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, SourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.storage import SQLiteStore


def test_call_log_stats_group_by_profile_service_and_tool(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    rows = [
        ("call_1", "safe", "mysql", "query_sql", "success", None, None, None, 10),
        ("call_2", "safe", "mysql", "query_sql", "error", "mcp_transport", "upstream_mcp", "mcp_transport_error", 100),
        ("call_3", "safe", "hive", "query_sql", "blocked", "profile_policy", "policy", "profile_policy_blocked", 1),
    ]
    for log_id, profile, service, tool, status, stage, owner, error_type, duration in rows:
        store.create_tool_call_log(
            log_id=log_id,
            actor="root",
            profile_key=profile,
            entrypoint="metamcp_execute",
            source_type=SourceType.mcp_service.value,
            source_key=service,
            tool_name=tool,
            request={},
            response={},
            status=status,
            failure_stage=stage,
            failure_owner=owner,
            error_type=error_type,
            duration_ms=duration,
        )

    stats = store.aggregate_tool_call_stats(
        dimensions=["profile_key", "source_key", "tool_name"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    mysql = next(row for row in stats if row["source_key"] == "mysql")
    hive = next(row for row in stats if row["source_key"] == "hive")
    assert mysql["calls"] == 2
    assert mysql["success"] == 1
    assert mysql["error"] == 1
    assert mysql["blocked"] == 0
    assert mysql["avg_duration_ms"] == 55
    assert mysql["max_duration_ms"] == 100
    assert hive["blocked"] == 1
```

- [ ] **Step 2: Write failing API stats test**

Add to `tests/test_capability_api.py`:

```python
def test_tool_call_stats_api_groups_by_dimensions(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_tool_call_log(
        log_id="call_stats_api",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status="success",
        duration_ms=12,
    )

    response = client.get(
        "/tool-call-stats",
        params={"dimensions": "profile_key,source_key,tool_name"},
        headers={"X-Wiki-User": "root"},
    )

    assert response.status_code == 200
    assert response.json()["dimensions"] == ["profile_key", "source_key", "tool_name"]
    assert response.json()["items"][0]["profile_key"] == "safe"
    assert response.json()["items"][0]["calls"] == 1
```

- [ ] **Step 3: Run failing stats tests**

Run:

```bash
uv run pytest tests/test_capability_stats.py tests/test_capability_api.py -k "stats" -v
```

Expected: FAIL because the aggregation method and API do not exist.

- [ ] **Step 4: Add aggregation method**

Add to `SQLiteStore`:

```python
    def aggregate_tool_call_stats(
        self,
        *,
        dimensions: list[str],
        created_from: str | None,
        created_to: str | None,
        bucket: str | None,
    ) -> list[dict[str, Any]]:
        allowed_dimensions = {
            "profile_key",
            "entrypoint",
            "source_type",
            "source_key",
            "tool_name",
            "status",
            "failure_stage",
            "failure_owner",
            "error_type",
            "resource_type",
            "resource_key",
        }
        invalid = [dimension for dimension in dimensions if dimension not in allowed_dimensions]
        if invalid:
            raise ValueError(f"invalid stats dimension: {invalid[0]}")

        selected = list(dimensions)
        if bucket:
            if bucket == "hour":
                selected.insert(0, "strftime('%Y-%m-%d %H:00:00', created_at) AS bucket")
            elif bucket == "day":
                selected.insert(0, "date(created_at) AS bucket")
            else:
                raise ValueError("invalid stats bucket")

        group_columns = ["bucket"] if bucket else []
        group_columns.extend(dimensions)
        select_clause = ", ".join(selected) if selected else "'all' AS scope"
        group_clause = f"GROUP BY {', '.join(group_columns)}" if group_columns else ""
        filters: list[str] = []
        params: list[Any] = []
        if created_from is not None:
            filters.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            filters.append("created_at < ?")
            params.append(created_to)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                  {select_clause},
                  COUNT(*) AS calls,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error,
                  SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                  ROUND(AVG(COALESCE(duration_ms, 0)), 0) AS avg_duration_ms,
                  MAX(duration_ms) AS max_duration_ms
                FROM tool_call_logs
                {where_clause}
                {group_clause}
                ORDER BY calls DESC
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 5: Add governance stats API**

Add to `CapabilityGovernanceService`:

```python
    def stats(
        self,
        *,
        actor: str,
        dimensions: list[str],
        created_from: str | None = None,
        created_to: str | None = None,
        bucket: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        try:
            items = self.store.aggregate_tool_call_stats(
                dimensions=dimensions,
                created_from=created_from,
                created_to=created_to,
                bucket=bucket,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {
            "dimensions": dimensions,
            "bucket": bucket,
            "items": items,
        }
```

- [ ] **Step 6: Add FastAPI endpoint**

Add to `src/wiki_manager/server.py` after `/tool-call-logs/{log_id}`:

```python
    @app.get("/tool-call-stats")
    def tool_call_stats(
        dimensions: str = "profile_key,source_key,tool_name",
        created_from: str | None = None,
        created_to: str | None = None,
        bucket: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        parsed_dimensions = [part.strip() for part in dimensions.split(",") if part.strip()]
        return call_safely(
            lambda: service.governance.stats(
                actor=current_actor,
                dimensions=parsed_dimensions,
                created_from=created_from,
                created_to=created_to,
                bucket=bucket,
            )
        )
```

- [ ] **Step 7: Run stats tests**

Run:

```bash
uv run pytest tests/test_capability_stats.py tests/test_capability_api.py -k "stats" -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/wiki_manager/storage.py src/wiki_manager/capability_governance.py src/wiki_manager/server.py tests/test_capability_stats.py tests/test_capability_api.py
git commit -m "feat: add capability call stats aggregation"
```

## Task 4: Add Profile Resource Rules For Built-In Resources

**Files:**
- Modify: `src/wiki_manager/capabilities.py`
- Modify: `src/wiki_manager/storage.py`
- Modify: `src/wiki_manager/capability_governance.py`
- Modify: `src/wiki_manager/server.py`
- Test: `tests/test_profile_resources.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing resource rule tests**

Create `tests/test_profile_resources.py`:

```python
from __future__ import annotations

import pytest

from wiki_manager.capability_governance import CapabilityGovernanceService
from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import NotFound, ValidationError
from wiki_manager.storage import SQLiteStore


def _service(wm_paths: WikiManagerPaths) -> CapabilityGovernanceService:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityGovernanceService(store=store, admins={"root"})
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    return service


def test_profile_resource_rules_round_trip_and_filter(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    detail = service.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [
            {"resource_type": "wiki_kb", "resource_key": "frontend-docs"},
            {"resource_type": "code_repo", "resource_key": "web-app"},
        ],
    )

    assert [(rule["resource_type"], rule["resource_key"]) for rule in detail["resource_rules"]] == [
        ("code_repo", "web-app"),
        ("wiki_kb", "frontend-docs"),
    ]
    assert service.filter_resource_keys(
        actor="root",
        profile_key="safe-readonly",
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == ["frontend-docs"]
    assert service.is_resource_allowed("root", "safe-readonly", "code_repo", "web-app") is True
    assert service.is_resource_allowed("root", "safe-readonly", "code_repo", "payroll") is False


def test_profile_resource_rules_default_open_without_profile_and_closed_with_profile(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    assert service.filter_resource_keys(
        actor="root",
        profile_key=None,
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == ["frontend-docs", "payroll"]
    assert service.filter_resource_keys(
        actor="root",
        profile_key="safe-readonly",
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == []


def test_profile_resource_rules_validate_type_and_profile(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid resource type"):
        service.replace_profile_resource_rules("root", "safe-readonly", [{"resource_type": "wrong", "resource_key": "x"}])
    with pytest.raises(NotFound, match="profile not found"):
        service.replace_profile_resource_rules("root", "missing", [])
```

- [ ] **Step 2: Write failing API test**

Add to `tests/test_capability_api.py`:

```python
def test_profile_resource_rules_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Wiki-User": "root"},
    )

    saved = client.put(
        "/capability-profiles/safe-readonly/resources",
        json={"resources": [{"resource_type": "wiki_kb", "resource_key": "frontend-docs"}]},
        headers={"X-Wiki-User": "root"},
    )
    detail = client.get("/capability-profiles/safe-readonly", headers={"X-Wiki-User": "root"})

    assert saved.status_code == 200
    assert saved.json()["resource_rules"][0]["resource_key"] == "frontend-docs"
    assert detail.json()["resource_rules"][0]["resource_type"] == "wiki_kb"
```

- [ ] **Step 3: Run failing resource tests**

Run:

```bash
uv run pytest tests/test_profile_resources.py tests/test_capability_api.py -k "resource" -v
```

Expected: FAIL because resource rule storage and API do not exist.

- [ ] **Step 4: Add resource enum and schema**

Add to `src/wiki_manager/capabilities.py`:

```python
class ProfileResourceType(str, Enum):
    wiki_kb = "wiki_kb"
    code_repo = "code_repo"
```

Add schema table to `src/wiki_manager/storage.py` after `profile_source_rules`:

```sql
CREATE TABLE IF NOT EXISTS profile_resource_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  resource_type TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, resource_type, resource_key)
);
CREATE INDEX IF NOT EXISTS idx_profile_resource_rules_profile ON profile_resource_rules(profile_key);
CREATE INDEX IF NOT EXISTS idx_profile_resource_rules_resource ON profile_resource_rules(resource_type, resource_key);
```

- [ ] **Step 5: Add storage methods**

Add to `SQLiteStore`:

```python
    def replace_profile_resource_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM profile_resource_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_resource_rules (profile_key, resource_type, resource_key)
                    VALUES (?, ?, ?)
                    """,
                    (profile_key, rule["resource_type"], rule["resource_key"]),
                )

    def list_profile_resource_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_resource_rules
                WHERE profile_key = ?
                ORDER BY resource_type, resource_key
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 6: Add governance methods**

Modify imports:

```python
from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, ProfileResourceType, ProfileRuleEffect, SourceType
```

Modify `get_profile()` return:

```python
        return {
            **profile,
            "rules": self.store.list_profile_source_rules(profile_key),
            "resource_rules": self.store.list_profile_resource_rules(profile_key),
        }
```

Add methods:

```python
    def replace_profile_resource_rules(
        self,
        actor: str,
        profile_key: str,
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        normalized = [self._validate_resource_rule(rule) for rule in rules]
        self.store.replace_profile_resource_rules(profile_key, normalized)
        return self.get_profile(actor, profile_key)

    def filter_resource_keys(
        self,
        *,
        actor: str,
        profile_key: str | None,
        resource_type: str,
        resource_keys: list[str],
    ) -> list[str]:
        normalized_resource_type = self._validate_resource_type(resource_type)
        if profile_key is None:
            return resource_keys
        profile = self.store.get_project_profile(profile_key)
        if profile is None or profile.get("status") != "active":
            raise NotFound("profile not found")
        rules = self.store.list_profile_resource_rules(profile_key)
        allowed = {
            rule["resource_key"]
            for rule in rules
            if rule["resource_type"] == normalized_resource_type
        }
        return [resource_key for resource_key in resource_keys if resource_key in allowed]

    def is_resource_allowed(self, actor: str, profile_key: str | None, resource_type: str, resource_key: str) -> bool:
        return resource_key in self.filter_resource_keys(
            actor=actor,
            profile_key=profile_key,
            resource_type=resource_type,
            resource_keys=[resource_key],
        )

    def _validate_resource_rule(self, rule: dict[str, str]) -> dict[str, str]:
        resource_type = self._validate_resource_type(rule.get("resource_type"))
        resource_key = str(rule.get("resource_key") or "").strip()
        if not resource_key:
            raise ValidationError("resource_key is required")
        return {"resource_type": resource_type, "resource_key": resource_key}

    def _validate_resource_type(self, resource_type: str | None) -> str:
        try:
            return ProfileResourceType(resource_type).value
        except ValueError as exc:
            raise ValidationError("invalid resource type") from exc
```

- [ ] **Step 7: Add server models and endpoint**

Add request models to `src/wiki_manager/server.py`:

```python
class ProfileResourceRuleRequest(BaseModel):
    resource_type: str
    resource_key: str


class ProfileResourcesRequest(BaseModel):
    resources: list[ProfileResourceRuleRequest] = Field(default_factory=list)
```

Add endpoint after profile rules endpoint:

```python
    @app.put("/capability-profiles/{profile_key}/resources")
    def replace_capability_profile_resources(
        profile_key: str,
        payload: ProfileResourcesRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        resources = [resource.model_dump() for resource in payload.resources]
        return call_safely(lambda: service.governance.replace_profile_resource_rules(current_actor, profile_key, resources))
```

- [ ] **Step 8: Run resource tests**

Run:

```bash
uv run pytest tests/test_profile_resources.py tests/test_capability_api.py -k "resource or profile_api" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/wiki_manager/capabilities.py src/wiki_manager/storage.py src/wiki_manager/capability_governance.py src/wiki_manager/server.py tests/test_profile_resources.py tests/test_capability_api.py
git commit -m "feat: add profile built-in resource rules"
```

## Task 5: Add Built-In Capability Provider Interface And Wiki Provider

**Files:**
- Create: `src/wiki_manager/builtin_capabilities.py`
- Create: `src/wiki_manager/builtin_wiki.py`
- Modify: `src/wiki_manager/capability_service.py`
- Modify: `src/wiki_manager/services.py`
- Test: `tests/test_builtin_wiki.py`
- Test: `tests/test_metamcp_http_gateway.py`

- [ ] **Step 1: Write failing Wiki provider tests**

Create `tests/test_builtin_wiki.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from wiki_manager.capabilities import ProfileResourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import ValidationError
from wiki_manager.services import WikiManagerService


def _service(wm_paths: WikiManagerPaths) -> WikiManagerService:
    service = WikiManagerService.create(wm_paths, {"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "payroll", "Payroll", "")
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [{"resource_type": ProfileResourceType.wiki_kb.value, "resource_key": "frontend-docs"}],
    )
    return service


def test_metamcp_root_search_lists_wiki_builtin_with_allowed_kbs(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", None, None, profile_key="safe-readonly")

    wiki = next(item for item in result["items"] if item["service"] == "wiki")
    assert wiki["kind"] == "builtin"
    assert wiki["tool_count"] == 4
    assert wiki["resources"] == [{"resource_type": "wiki_kb", "resource_key": "frontend-docs", "name": "Frontend Docs"}]


def test_metamcp_wiki_path_lists_fixed_tools(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", "wiki", None, profile_key="safe-readonly")

    assert [item["tool"] for item in result["items"]] == ["ask", "get_document", "list_kbs", "search"]
    assert result["items"][0]["service"] == "wiki"
    assert result["items"][0]["display_tool"] == "wiki.ask"


def test_wiki_list_kbs_respects_profile_resources(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = asyncio.run(service.capabilities.execute("root", "wiki", "list_kbs", {}, profile_key="safe-readonly"))

    assert result["service"] == "wiki"
    assert result["tool"] == "list_kbs"
    assert [kb["slug"] for kb in result["result"]["kbs"]] == ["frontend-docs"]


def test_wiki_execute_blocks_unallowed_kb(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(service.capabilities.execute("root", "wiki", "search", {"kb": "payroll", "question": "salary"}, profile_key="safe-readonly"))

    log = service.governance.list_logs(actor="root", status="blocked")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "wiki"
    assert log["resource_type"] == "wiki_kb"
    assert log["resource_key"] == "payroll"
    assert log["error_type"] == "profile_policy_blocked"
```

- [ ] **Step 2: Run failing Wiki tests**

Run:

```bash
uv run pytest tests/test_builtin_wiki.py -v
```

Expected: FAIL because built-in provider dispatch does not exist.

- [ ] **Step 3: Create provider protocol**

Create `src/wiki_manager/builtin_capabilities.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BuiltinTool:
    tool: str
    name: str
    description: str
    input_schema: dict[str, Any]
    tool_type: str


class BuiltinCapabilityProvider(Protocol):
    source_key: str
    name: str
    description: str
    tags: list[str]

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        pass

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        pass

    async def execute(self, actor: str, tool: str, arguments: dict[str, Any], profile_key: str | None) -> dict[str, Any]:
        pass
```

- [ ] **Step 4: Create Wiki provider**

Create `src/wiki_manager/builtin_wiki.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wiki_manager.builtin_capabilities import BuiltinTool
from wiki_manager.capabilities import ProfileResourceType, ToolType
from wiki_manager.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from wiki_manager.services import WikiManagerService


class WikiBuiltinProvider:
    source_key = "wiki"
    name = "Wiki"
    description = "内置知识库查询能力"
    tags = ["builtin", "knowledge"]

    def __init__(self, service: "WikiManagerService") -> None:
        self.service = service

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        kbs = self.service.list_kbs(actor)
        visible = set(
            self.service.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.wiki_kb.value,
                resource_keys=[kb["slug"] for kb in kbs],
            )
        )
        return [
            {"resource_type": ProfileResourceType.wiki_kb.value, "resource_key": kb["slug"], "name": kb["name"]}
            for kb in kbs
            if kb["slug"] in visible
        ]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return [
            BuiltinTool("ask", "Wiki Ask", "Ask a question against an allowed KB.", {"type": "object"}, ToolType.search.value),
            BuiltinTool("get_document", "Wiki Document", "Read document metadata from an allowed KB.", {"type": "object"}, ToolType.detail.value),
            BuiltinTool("list_kbs", "Wiki KB List", "List allowed knowledge bases.", {"type": "object"}, ToolType.overview.value),
            BuiltinTool("search", "Wiki Search", "Search snippets in an allowed KB.", {"type": "object"}, ToolType.search.value),
        ]

    async def execute(self, actor: str, tool: str, arguments: dict[str, Any], profile_key: str | None) -> dict[str, Any]:
        if tool == "list_kbs":
            return {"kbs": self.list_resources(actor, profile_key)}
        kb_slug = str(arguments.get("kb") or arguments.get("kb_slug") or "").strip()
        if not kb_slug:
            raise ValidationError("kb is required")
        if not self.service.governance.is_resource_allowed(actor, profile_key, ProfileResourceType.wiki_kb.value, kb_slug):
            raise ValidationError("resource is blocked by profile policy")
        if tool == "search":
            question = str(arguments.get("question") or arguments.get("query") or "").strip()
            if not question:
                raise ValidationError("question is required")
            results = self.service.search(actor, kb_slug, question, top_k=int(arguments.get("top_k") or 6))
            return {"kb": kb_slug, "results": [item.model_dump() if hasattr(item, "model_dump") else item for item in results]}
        if tool == "ask":
            question = str(arguments.get("question") or "").strip()
            if not question:
                raise ValidationError("question is required")
            answer = self.service.ask(actor, kb_slug, question, session_id=arguments.get("session_id"))
            return {"kb": kb_slug, "answer": answer.model_dump() if hasattr(answer, "model_dump") else answer}
        if tool == "get_document":
            doc_slug = str(arguments.get("doc_slug") or "").strip()
            if not doc_slug:
                raise ValidationError("doc_slug is required")
            doc = self.service.get_doc(actor, doc_slug)
            if kb_slug not in doc.get("kb_slugs", []):
                raise NotFound("document not found")
            return {"document": doc}
        raise NotFound("tool not found")
```

- [ ] **Step 5: Wire providers into CapabilityService**

Modify `CapabilityService.__init__()`:

```python
        self.builtin_providers: dict[str, BuiltinCapabilityProvider] = {}
```

Import protocol under typing:

```python
from wiki_manager.builtin_capabilities import BuiltinCapabilityProvider
```

Add method:

```python
    def register_builtin_provider(self, provider: BuiltinCapabilityProvider) -> None:
        self.builtin_providers[provider.source_key] = provider
```

Add built-in root items to `_root_search_items()`:

```python
        builtin_items = [
            {
                "kind": "builtin",
                "service": provider.source_key,
                "name": provider.name,
                "description": provider.description,
                "tags": provider.tags,
                "tool_count": len(provider.list_tools(actor, profile_key)),
                "status": "enabled",
                "resources": provider.list_resources(actor, profile_key),
            }
            for provider in self.builtin_providers.values()
        ]
        external_items = [
            {
                "kind": "service",
                "service": service["service_key"],
                "name": service["name"],
                "description": service["description"],
                "tags": _json_loads(service.get("tags_json"), []),
                "tool_count": tools_by_service.get(service["service_key"], 0),
                "status": service["status"],
            }
            for service in enabled_services
        ]
        return builtin_items + external_items
```

At the top of `_search_without_log()`, before external service policy checks, handle built-in paths:

```python
        else:
            if normalized_path in self.builtin_providers:
                provider = self.builtin_providers[normalized_path]
                items = [
                    self._builtin_tool_search_item(provider.source_key, tool)
                    for tool in provider.list_tools(actor, profile_key)
                ]
                response_path = normalized_path
            else:
                if not self.governance.is_source_allowed(
                    actor,
                    profile_key,
                    SourceType.mcp_service.value,
                    normalized_path,
                ):
                    return {"path": normalized_path, "items": []}
                self._require_enabled_service(normalized_path)
                items = [self._tool_search_item(tool) for tool in self._active_tools(normalized_path)]
                response_path = normalized_path
```

Add helper:

```python
    def _builtin_tool_search_item(self, source_key: str, tool: BuiltinTool) -> dict[str, Any]:
        return {
            "kind": "tool",
            "service": source_key,
            "tool": tool.tool,
            "display_tool": f"{source_key}.{tool.tool}",
            "name": tool.name,
            "description": tool.description,
            "tags": ["builtin", source_key],
            "tool_type": tool.tool_type,
            "input_schema": tool.input_schema,
            "execute_example": {},
            "executable": True,
        }
```

At the start of `execute()`, dispatch built-ins when `service in self.builtin_providers`. Reuse the same log creation path and set `source_type=SourceType.builtin.value`.

- [ ] **Step 6: Register Wiki provider**

Modify `WikiManagerService.__init__()` in `src/wiki_manager/services.py` after `self.capabilities` is assigned:

```python
        from wiki_manager.builtin_wiki import WikiBuiltinProvider

        self.capabilities.register_builtin_provider(WikiBuiltinProvider(self))
```

- [ ] **Step 7: Run Wiki tests**

Run:

```bash
uv run pytest tests/test_builtin_wiki.py tests/test_metamcp_http_gateway.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/wiki_manager/builtin_capabilities.py src/wiki_manager/builtin_wiki.py src/wiki_manager/capability_service.py src/wiki_manager/services.py tests/test_builtin_wiki.py tests/test_metamcp_http_gateway.py
git commit -m "feat: expose wiki as built-in capability"
```

## Task 6: Add CodeGraph Repository Storage And Service

**Files:**
- Modify: `src/wiki_manager/config.py`
- Modify: `src/wiki_manager/storage.py`
- Create: `src/wiki_manager/codegraph_service.py`
- Modify: `src/wiki_manager/server.py`
- Test: `tests/test_codegraph_service.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing CodeGraph service tests**

Create `tests/test_codegraph_service.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from wiki_manager.codegraph_service import CodeGraphService
from wiki_manager.config import WikiManagerPaths
from wiki_manager.storage import SQLiteStore


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def test_codegraph_register_sync_and_search(tmp_path: Path, wm_paths: WikiManagerPaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})

    saved = service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="master",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        sync_interval_minutes=60,
        status="active",
    )
    run = service.sync_repository("root", "web-app")
    files = service.search_code("root", "web-app", query="hello")

    assert saved["repo_key"] == "web-app"
    assert run["status"] == "succeeded"
    assert files[0]["path"] == "app.py"
    assert files[0]["language"] == "python"
    assert "hello" in files[0]["snippet"]
    assert service.get_file("root", "web-app", "app.py")["content"].startswith("def hello")
```

- [ ] **Step 2: Run failing CodeGraph test**

Run:

```bash
uv run pytest tests/test_codegraph_service.py -v
```

Expected: FAIL because `CodeGraphService` and tables do not exist.

- [ ] **Step 3: Add CodeGraph paths**

Modify `WikiManagerPaths` in `src/wiki_manager/config.py` to expose:

```python
    @property
    def codegraph_dir(self) -> Path:
        return self.root / "codegraph"
```

Ensure `ensure_directories()` creates `paths.codegraph_dir`.

- [ ] **Step 4: Add CodeGraph schema**

Add to `src/wiki_manager/storage.py` schema:

```sql
CREATE TABLE IF NOT EXISTS code_repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  git_url TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  auth_ref TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  sync_interval_minutes INTEGER NOT NULL DEFAULT 60,
  status TEXT NOT NULL DEFAULT 'active',
  local_path TEXT,
  last_commit TEXT,
  last_synced_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS codegraph_sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL REFERENCES code_repositories(repo_key) ON DELETE CASCADE,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  error TEXT,
  duration_ms INTEGER,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS codegraph_index_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_key TEXT NOT NULL REFERENCES code_repositories(repo_key) ON DELETE CASCADE,
  item_type TEXT NOT NULL,
  path TEXT NOT NULL,
  symbol TEXT,
  language TEXT,
  line_start INTEGER,
  line_end INTEGER,
  content TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_codegraph_index_repo_path ON codegraph_index_items(repo_key, path);
CREATE INDEX IF NOT EXISTS idx_codegraph_index_symbol ON codegraph_index_items(repo_key, symbol);
```

- [ ] **Step 5: Add storage methods**

Add methods to `SQLiteStore`:

```python
    def upsert_code_repository(self, *, repo_key: str, name: str, git_url: str, branch: str, auth_ref: str, description: str, tags: list[str], sync_interval_minutes: int, status: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, description, tags_json, sync_interval_minutes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                  name = excluded.name,
                  git_url = excluded.git_url,
                  branch = excluded.branch,
                  auth_ref = excluded.auth_ref,
                  description = excluded.description,
                  tags_json = excluded.tags_json,
                  sync_interval_minutes = excluded.sync_interval_minutes,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (repo_key, name, git_url, branch, auth_ref, description, json.dumps(tags, ensure_ascii=False), sync_interval_minutes, status),
            )
            return dict(conn.execute("SELECT * FROM code_repositories WHERE repo_key = ?", (repo_key,)).fetchone())

    def list_code_repositories(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM code_repositories ORDER BY repo_key").fetchall()
            return [dict(row) for row in rows]

    def get_code_repository(self, repo_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM code_repositories WHERE repo_key = ?", (repo_key,)).fetchone()
            return _row_to_dict(row)

    def replace_codegraph_index(self, repo_key: str, items: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM codegraph_index_items WHERE repo_key = ?", (repo_key,))
            for item in items:
                conn.execute(
                    """
                    INSERT INTO codegraph_index_items (repo_key, item_type, path, symbol, language, line_start, line_end, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (repo_key, item["item_type"], item["path"], item.get("symbol"), item.get("language"), item.get("line_start"), item.get("line_end"), item.get("content", "")),
                )
```

Add the sync-run and index query methods to `SQLiteStore`:

```python
    def create_codegraph_sync_run(self, repo_key: str, *, status: str, stage: str) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO codegraph_sync_runs (repo_key, status, stage)
                VALUES (?, ?, ?)
                """,
                (repo_key, status, stage),
            )
            row = conn.execute("SELECT * FROM codegraph_sync_runs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def finish_codegraph_sync_run(self, run_id: int, *, status: str, stage: str, error: str | None, duration_ms: int) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE codegraph_sync_runs
                SET status = ?,
                    stage = ?,
                    error = ?,
                    duration_ms = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, stage, error, duration_ms, run_id),
            )
            row = conn.execute("SELECT * FROM codegraph_sync_runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row)

    def search_codegraph_index(self, repo_key: str, *, query: str, item_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        filters = ["repo_key = ?"]
        params: list[Any] = [repo_key]
        if item_type:
            filters.append("item_type = ?")
            params.append(item_type)
        if query:
            filters.append("(path LIKE ? OR symbol LIKE ? OR content LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM codegraph_index_items
                WHERE {' AND '.join(filters)}
                ORDER BY path, line_start
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_codegraph_file(self, repo_key: str, path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM codegraph_index_items
                WHERE repo_key = ? AND path = ? AND item_type = 'file'
                """,
                (repo_key, path),
            ).fetchone()
            return _row_to_dict(row)
```

- [ ] **Step 6: Implement CodeGraph service**

Create `src/wiki_manager/codegraph_service.py`:

```python
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import NotFound, ValidationError, require_admin_user
from wiki_manager.storage import SQLiteStore


class CodeGraphService:
    def __init__(self, *, paths: WikiManagerPaths, store: SQLiteStore, admins: set[str]) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins

    def upsert_repository(self, actor: str, repo_key: str, name: str, git_url: str, branch: str, auth_ref: str, description: str, tags: list[str], sync_interval_minutes: int, status: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if status not in {"active", "disabled"}:
            raise ValidationError("invalid repository status")
        return self.store.upsert_code_repository(
            repo_key=repo_key,
            name=name,
            git_url=git_url,
            branch=branch,
            auth_ref=auth_ref,
            description=description,
            tags=tags,
            sync_interval_minutes=sync_interval_minutes,
            status=status,
        )

    def list_repositories(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_code_repositories()

    def sync_repository(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        repo = self.store.get_code_repository(repo_key)
        if repo is None:
            raise NotFound("repository not found")
        started = time.perf_counter()
        run = self.store.create_codegraph_sync_run(repo_key, status="running", stage="sync")
        local_path = self.paths.codegraph_dir / repo_key
        try:
            self._sync_git(repo, local_path)
            items = self._index_files(repo_key, local_path)
            self.store.replace_codegraph_index(repo_key, items)
            self.store.finish_codegraph_sync_run(run["id"], status="succeeded", stage="index", error=None, duration_ms=int((time.perf_counter() - started) * 1000))
            return {"repo_key": repo_key, "status": "succeeded", "indexed": len(items)}
        except Exception as exc:
            self.store.finish_codegraph_sync_run(run["id"], status="failed", stage="sync", error=str(exc), duration_ms=int((time.perf_counter() - started) * 1000))
            raise ValidationError(f"codegraph sync failed: {exc}") from exc

    def _sync_git(self, repo: dict[str, Any], local_path: Path) -> None:
        self.paths.codegraph_dir.mkdir(parents=True, exist_ok=True)
        if local_path.exists():
            subprocess.run(["git", "fetch", "--all", "--prune"], cwd=local_path, check=True, capture_output=True, text=True)
        else:
            subprocess.run(["git", "clone", repo["git_url"], str(local_path)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "checkout", repo["branch"]], cwd=local_path, check=True, capture_output=True, text=True)

    def _index_files(self, repo_key: str, local_path: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(local_path.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue
            relative = path.relative_to(local_path).as_posix()
            content = path.read_text(encoding="utf-8", errors="ignore")
            language = "python" if path.suffix == ".py" else path.suffix.lstrip(".")
            items.append({"item_type": "file", "path": relative, "language": language, "content": content[:20000]})
            for index, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("def ") or stripped.startswith("class "):
                    symbol = stripped.split("(", 1)[0].replace("def ", "").replace("class ", "").strip()
                    items.append({"item_type": "symbol", "path": relative, "symbol": symbol, "language": language, "line_start": index, "line_end": index, "content": stripped})
        return items
```

Add these public query methods to `CodeGraphService`:

```python
    def _require_repository(self, repo_key: str) -> dict[str, Any]:
        repo = self.store.get_code_repository(repo_key)
        if repo is None or repo["status"] != "active":
            raise NotFound("repository not found")
        return repo

    def search_code(self, actor: str, repo_key: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        return self.store.search_codegraph_index(repo_key, query=query, item_type="file", limit=limit)

    def get_file(self, actor: str, repo_key: str, path: str) -> dict[str, Any]:
        self._require_repository(repo_key)
        file_row = self.store.get_codegraph_file(repo_key, path)
        if file_row is None:
            raise NotFound("file not found")
        return {
            "repo_key": repo_key,
            "path": file_row["path"],
            "language": file_row["language"],
            "content": file_row["content"],
        }

    def find_symbol(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        return self.store.search_codegraph_index(repo_key, query=symbol, item_type="symbol", limit=limit)

    def repository_overview(self, actor: str, repo_key: str) -> dict[str, Any]:
        repo = self._require_repository(repo_key)
        files = self.store.search_codegraph_index(repo_key, query="", item_type="file", limit=1000)
        symbols = self.store.search_codegraph_index(repo_key, query="", item_type="symbol", limit=1000)
        return {
            "repo_key": repo_key,
            "name": repo["name"],
            "status": repo["status"],
            "file_count": len(files),
            "symbol_count": len(symbols),
            "last_synced_at": repo.get("last_synced_at"),
        }
```

- [ ] **Step 7: Add API models and endpoints**

Add models to `src/wiki_manager/server.py`:

```python
class CodeRepositoryRequest(BaseModel):
    repo_key: str
    name: str
    git_url: str
    branch: str = "main"
    auth_ref: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    sync_interval_minutes: int = 60
    status: str = "active"
```

Create `service.codegraph` in `WikiManagerService.__init__()`:

```python
        from wiki_manager.codegraph_service import CodeGraphService

        self.codegraph = CodeGraphService(paths=paths, store=store, admins=admins)
```

Add endpoints:

```python
    @app.get("/builtin/codegraph/repositories")
    def list_code_repositories(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.list_repositories(current_actor))

    @app.post("/builtin/codegraph/repositories")
    def upsert_code_repository(payload: CodeRepositoryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.upsert_repository(current_actor, **payload.model_dump()))

    @app.post("/builtin/codegraph/repositories/{repo_key}/sync")
    def sync_code_repository(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.sync_repository(current_actor, repo_key))
```

- [ ] **Step 8: Run CodeGraph tests**

Run:

```bash
uv run pytest tests/test_codegraph_service.py tests/test_capability_api.py -k "codegraph" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/wiki_manager/config.py src/wiki_manager/storage.py src/wiki_manager/codegraph_service.py src/wiki_manager/services.py src/wiki_manager/server.py tests/test_codegraph_service.py tests/test_capability_api.py
git commit -m "feat: manage codegraph repositories"
```

## Task 7: Expose CodeGraph As A Built-In MetaMCP Provider

**Files:**
- Create: `src/wiki_manager/builtin_codegraph.py`
- Modify: `src/wiki_manager/services.py`
- Modify: `src/wiki_manager/capability_service.py`
- Test: `tests/test_builtin_codegraph.py`
- Test: `tests/test_metamcp_http_gateway.py`

- [ ] **Step 1: Write failing built-in CodeGraph tests**

Create `tests/test_builtin_codegraph.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from wiki_manager.capabilities import ProfileResourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import ValidationError
from wiki_manager.services import WikiManagerService


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text("class App:\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def test_codegraph_builtin_search_and_execute_respect_profile(tmp_path: Path, wm_paths: WikiManagerPaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    service = WikiManagerService.create(wm_paths, {"root"})
    service.init_system()
    service.codegraph.upsert_repository("root", "web-app", "Web App", str(repo), "master", "", "", ["python"], 60, "active")
    service.codegraph.sync_repository("root", "web-app")
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [{"resource_type": ProfileResourceType.code_repo.value, "resource_key": "web-app"}],
    )

    root = service.capabilities.search("root", None, None, profile_key="safe-readonly")
    tools = service.capabilities.search("root", "codegraph", None, profile_key="safe-readonly")
    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "codegraph",
            "search_code",
            {"repo": "web-app", "query": "App"},
            profile_key="safe-readonly",
        )
    )

    assert next(item for item in root["items"] if item["service"] == "codegraph")["kind"] == "builtin"
    assert [item["tool"] for item in tools["items"]] == ["find_symbol", "get_file", "list_repositories", "repository_overview", "search_code"]
    assert result["result"]["matches"][0]["path"] == "app.py"


def test_codegraph_builtin_blocks_unallowed_repo(tmp_path: Path, wm_paths: WikiManagerPaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    service = WikiManagerService.create(wm_paths, {"root"})
    service.init_system()
    service.codegraph.upsert_repository("root", "web-app", "Web App", str(repo), "master", "", "", [], 60, "active")
    service.codegraph.sync_repository("root", "web-app")
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "get_file",
                {"repo": "web-app", "path": "app.py"},
                profile_key="safe-readonly",
            )
        )
```

- [ ] **Step 2: Run failing CodeGraph provider tests**

Run:

```bash
uv run pytest tests/test_builtin_codegraph.py -v
```

Expected: FAIL because CodeGraph provider is not registered.

- [ ] **Step 3: Create CodeGraph built-in provider**

Create `src/wiki_manager/builtin_codegraph.py`:

```python
from __future__ import annotations

from typing import Any

from wiki_manager.builtin_capabilities import BuiltinTool
from wiki_manager.capabilities import ProfileResourceType, ToolType
from wiki_manager.capability_governance import CapabilityGovernanceService
from wiki_manager.codegraph_service import CodeGraphService
from wiki_manager.domain import NotFound, ValidationError


class CodeGraphBuiltinProvider:
    source_key = "codegraph"
    name = "CodeGraph"
    description = "内置代码仓库结构和代码查询能力"
    tags = ["builtin", "code"]

    def __init__(self, codegraph: CodeGraphService, governance: CapabilityGovernanceService) -> None:
        self.codegraph = codegraph
        self.governance = governance

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        repos = [repo for repo in self.codegraph.store.list_code_repositories() if repo["status"] == "active"]
        repo_keys = [repo["repo_key"] for repo in repos]
        filtered = set(
            self.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.code_repo.value,
                resource_keys=repo_keys,
            )
        )
        return [
            {"resource_type": ProfileResourceType.code_repo.value, "resource_key": repo["repo_key"], "name": repo["name"]}
            for repo in repos
            if repo["repo_key"] in filtered
        ]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return [
            BuiltinTool("find_symbol", "CodeGraph Symbol", "Find symbol definitions in an allowed repository.", {"type": "object"}, ToolType.search.value),
            BuiltinTool("get_file", "CodeGraph File", "Read a file from an allowed repository.", {"type": "object"}, ToolType.detail.value),
            BuiltinTool("list_repositories", "CodeGraph Repositories", "List allowed code repositories.", {"type": "object"}, ToolType.overview.value),
            BuiltinTool("repository_overview", "CodeGraph Repository Overview", "Show repository status and summary.", {"type": "object"}, ToolType.detail.value),
            BuiltinTool("search_code", "CodeGraph Search", "Search code in an allowed repository.", {"type": "object"}, ToolType.search.value),
        ]

    async def execute(self, actor: str, tool: str, arguments: dict[str, Any], profile_key: str | None) -> dict[str, Any]:
        if tool == "list_repositories":
            return {"repositories": self.list_resources(actor, profile_key)}
        repo_key = str(arguments.get("repo") or arguments.get("repo_key") or "").strip()
        if not repo_key:
            raise ValidationError("repo is required")
        if not self.governance.is_resource_allowed(actor, profile_key, ProfileResourceType.code_repo.value, repo_key):
            raise ValidationError("resource is blocked by profile policy")
        if tool == "search_code":
            return {"matches": self.codegraph.search_code(actor, repo_key, str(arguments.get("query") or ""))}
        if tool == "get_file":
            return self.codegraph.get_file(actor, repo_key, str(arguments.get("path") or ""))
        if tool == "find_symbol":
            return {"matches": self.codegraph.find_symbol(actor, repo_key, str(arguments.get("symbol") or ""))}
        if tool == "repository_overview":
            return self.codegraph.repository_overview(actor, repo_key)
        raise NotFound("tool not found")
```

- [ ] **Step 4: Register CodeGraph provider**

Modify `WikiManagerService.__init__()` after `self.codegraph` exists:

```python
        from wiki_manager.builtin_codegraph import CodeGraphBuiltinProvider

        self.capabilities.register_builtin_provider(CodeGraphBuiltinProvider(self.codegraph, self.governance))
```

- [ ] **Step 5: Ensure built-in execute logs resource fields**

In `CapabilityService.execute()` built-in branch, set:

```python
resource_type = {
    "wiki": "wiki_kb",
    "codegraph": "code_repo",
}.get(service)
resource_key = arguments.get("kb") or arguments.get("kb_slug") or arguments.get("repo") or arguments.get("repo_key")
```

Pass `resource_type` and `resource_key` into `log_tool_call()`.

- [ ] **Step 6: Run built-in CodeGraph tests**

Run:

```bash
uv run pytest tests/test_builtin_codegraph.py tests/test_builtin_wiki.py tests/test_metamcp_http_gateway.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/wiki_manager/builtin_codegraph.py src/wiki_manager/services.py src/wiki_manager/capability_service.py tests/test_builtin_codegraph.py tests/test_metamcp_http_gateway.py
git commit -m "feat: expose codegraph as built-in capability"
```

## Task 8: Enhance CLI MetaMCP Add Scope Selection And Confirmation

**Files:**
- Modify: `src/wiki_manager/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add to `tests/test_cli.py`:

```python
def test_metamcp_add_prompts_for_scope_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
        input="project\n",
    )

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert "written:" in result.output


def test_metamcp_add_requires_scope_in_non_interactive_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("wiki_manager.cli._stdin_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        ["metamcp", "add", "--url", "http://127.0.0.1:8765/mcp", "--profile", "safe-readonly"],
    )

    assert result.exit_code == 1
    assert "scope is required in non-interactive mode" in result.stderr


def test_metamcp_add_confirms_overwrite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"agent-capability-hub":{"url":"old"}}}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "aborted" in result.stderr
```

- [ ] **Step 2: Run failing CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -k "metamcp_add" -v
```

Expected: FAIL because `--scope` is required and overwrite confirmation is absent.

- [ ] **Step 3: Add interactive helpers**

Add to `src/wiki_manager/cli.py`:

```python
import sys
```

Add helpers:

```python
def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _resolve_metamcp_scope(scope: str | None) -> str:
    if scope:
        if scope not in {"project", "user"}:
            raise ValueError("scope must be project or user")
        return scope
    if not _stdin_is_interactive():
        raise ValueError("scope is required in non-interactive mode")
    selected = typer.prompt("选择配置范围 project/user", default="project")
    if selected not in {"project", "user"}:
        raise ValueError("scope must be project or user")
    return selected


def _confirm_overwrite(existing: dict[str, Any], yes: bool) -> None:
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict) or "agent-capability-hub" not in servers:
        return
    if yes:
        return
    if not typer.confirm("agent-capability-hub already exists, overwrite it?", default=False):
        raise RuntimeError("aborted")
```

- [ ] **Step 4: Modify CLI command signature**

Change `metamcp_add()` signature:

```python
def metamcp_add(
    url: Annotated[str, typer.Option("--url", help="MetaMCP HTTP URL.")],
    profile: Annotated[str, typer.Option("--profile", help="Project Profile key.")],
    scope: Annotated[str | None, typer.Option("--scope", help="project or user.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Overwrite existing config without confirmation.")] = False,
) -> None:
```

Change body:

```python
    try:
        resolved_scope = _resolve_metamcp_scope(scope)
        path = _claude_config_path(resolved_scope)
        existing = _load_json_file(path)
        _confirm_overwrite(existing, yes)
        path.write_text(
            json.dumps(_with_metamcp_config(existing, url, profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"metamcp config error: {exc}", err=True)
        raise typer.Exit(1) from None
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -k "metamcp_add" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/cli.py tests/test_cli.py
git commit -m "feat: improve metamcp add interaction"
```

## Task 9: Add Admin UI For Logs, Stats, Copy Command, And Built-In Resources

**Files:**
- Modify: `src/wiki_manager/web_pages.py`
- Modify: `src/wiki_manager/static/capabilities/app.js`
- Modify: `src/wiki_manager/static/capabilities/app.css`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing static UI tests**

Add to `tests/test_capability_api.py`:

```python
def test_capability_admin_page_has_phase2_views_and_modals(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert 'data-view="stats"' in response.text
    assert 'data-view="builtins"' in response.text
    assert 'id="logDetailDialog"' in response.text
    assert 'id="profileCommandDialog"' in response.text
    assert 'id="profileResourcesDialog"' in response.text


def test_capability_static_assets_support_phase2_interactions(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    js = client.get("/static/capabilities/app.js")
    css = client.get("/static/capabilities/app.css")

    assert js.status_code == 200
    assert "loadStats" in js.text
    assert "openLogDetailDialog" in js.text
    assert "copyProfileCommand" in js.text
    assert "saveProfileResources" in js.text
    assert "loadBuiltins" in js.text
    assert css.status_code == 200
    assert "stats-grid" in css.text
    assert "log-detail-modal" in css.text
    assert "json-tabs" in css.text
```

- [ ] **Step 2: Run failing UI tests**

Run:

```bash
uv run pytest tests/test_capability_api.py -k "phase2_views or phase2_interactions" -v
```

Expected: FAIL because the HTML and JS do not contain Phase 2 views.

- [ ] **Step 3: Add navigation and markup**

Modify `src/wiki_manager/web_pages.py`:

- Add nav button under 调用观测:

```html
<button class="nav-item" data-view="stats" type="button">调用统计</button>
```

- Add nav button under 能力管理:

```html
<button class="nav-item" data-view="builtins" type="button">内置能力</button>
```

- Add `view-stats` section with `statsControls`, `statsSummary`, and `statsTable`.
- Add `view-builtins` section with Wiki KB table and CodeGraph repository table.
- Add dialogs:

```html
<dialog class="modal log-detail-modal" id="logDetailDialog">
  <div class="modal-card">
    <div class="modal-header">
      <div>
        <h2 id="logDetailTitle">调用详情</h2>
        <p id="logDetailHint">查看请求、响应和错误归因。</p>
      </div>
      <button class="icon-button" id="closeLogDetailDialog" type="button" aria-label="关闭">×</button>
    </div>
    <div class="modal-body">
      <div class="json-tabs" id="logDetailTabs"></div>
      <pre class="json-panel" id="logDetailJson"></pre>
    </div>
  </div>
</dialog>
```

Add command and resource dialog markup:

```html
<dialog class="modal compact-modal" id="profileCommandDialog">
  <div class="modal-card">
    <div class="modal-header">
      <div>
        <h2 id="profileCommandTitle">复制接入命令</h2>
        <p id="profileCommandHint">复制后在目标项目或用户环境执行。</p>
      </div>
      <button class="icon-button" id="closeProfileCommandDialog" type="button" aria-label="关闭">×</button>
    </div>
    <div class="modal-body">
      <pre class="json-panel" id="profileCommandText"></pre>
    </div>
    <div class="modal-actions">
      <button id="copyProfileCommandButton" class="primary" type="button">复制命令</button>
    </div>
  </div>
</dialog>

<dialog class="modal profile-rules-modal" id="profileResourcesDialog">
  <form id="profileResourcesForm" class="modal-card" method="dialog">
    <div class="modal-header">
      <div>
        <h2 id="profileResourcesTitle">配置资源范围</h2>
        <p id="profileResourcesHint">选择此 Profile 可查阅的 Wiki KB 和 CodeGraph 仓库。</p>
      </div>
      <button class="icon-button" id="closeProfileResourcesDialog" type="button" aria-label="关闭">×</button>
    </div>
    <div class="modal-body">
      <input id="profileResourcesKey" type="hidden">
      <div class="table-wrap">
        <table class="profile-rules-table">
          <thead>
            <tr><th>资源</th><th>类型</th><th>允许</th></tr>
          </thead>
          <tbody id="profileResourcesTable">
            <tr><td colspan="3" class="empty">正在读取资源。</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="modal-actions">
      <button id="cancelProfileResourcesDialog" type="button">取消</button>
      <button class="primary" type="submit">保存资源范围</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 4: Extend view routing**

Modify `src/wiki_manager/static/capabilities/app.js`:

```javascript
let currentView = "catalog";

const VALID_VIEWS = new Set(["catalog", "services", "tools", "profiles", "logs", "stats", "builtins", "claude"]);
```

Update `setView()`:

```javascript
  } else if (view === "stats") {
    loadStats();
  } else if (view === "builtins") {
    loadBuiltins();
```

- [ ] **Step 5: Add log filters and detail modal functions**

Add functions to `app.js`:

```javascript
function logQueryParams() {
  const params = new URLSearchParams();
  ["entrypoint", "source_key", "tool_name", "profile_key", "status", "failure_owner", "failure_stage", "error_type"].forEach((id) => {
    const node = document.getElementById(`logFilter_${id}`);
    if (node && node.value) params.set(id, node.value);
  });
  return params;
}

async function openLogDetailDialog(logId) {
  const detail = await apiRequest(`/tool-call-logs/${encodeURIComponent(logId)}`, { method: "GET" });
  els.logDetailTitle.textContent = `调用详情：${logId}`;
  els.logDetailTabs.innerHTML = ["request_json", "response_json", "error_message"].map((field) => `<button type="button" data-field="${field}">${field}</button>`).join("");
  renderLogDetailField(detail, "request_json");
  showDialog(els.logDetailDialog);
}

function renderLogDetailField(detail, field) {
  let value = detail[field] || "";
  if (field.endsWith("_json")) {
    value = JSON.stringify(JSON.parse(value || "{}"), null, 2);
  }
  els.logDetailJson.textContent = value;
}
```

Update `loadLogs()` to call `/tool-call-logs?${logQueryParams()}` and render "查看请求" / "查看响应" buttons instead of embedding full JSON in the table.

- [ ] **Step 6: Add stats and built-in loading functions**

Add to `app.js`:

```javascript
async function loadStats() {
  clearMessage();
  const data = await apiRequest("/tool-call-stats?dimensions=profile_key,source_key,tool_name", { method: "GET" });
  els.statsSummary.innerHTML = `<div class="stat-card"><strong>${data.items.reduce((sum, item) => sum + Number(item.calls || 0), 0)}</strong><span>总调用</span></div>`;
  els.statsTable.innerHTML = data.items.map((item) => `
    <tr>
      <td>${escapeHtml(item.profile_key || "-")}</td>
      <td>${escapeHtml(item.source_key || "-")}</td>
      <td>${escapeHtml(item.tool_name || "-")}</td>
      <td>${Number(item.calls || 0)}</td>
      <td>${Number(item.error || 0)}</td>
      <td>${Number(item.blocked || 0)}</td>
      <td>${Number(item.avg_duration_ms || 0)}ms</td>
    </tr>
  `).join("");
}

async function loadBuiltins() {
  clearMessage();
  const [kbs, repos] = await Promise.all([
    apiRequest("/builtin/wiki/kbs", { method: "GET" }).catch(() => []),
    apiRequest("/builtin/codegraph/repositories", { method: "GET" }).catch(() => []),
  ]);
  els.builtinKbsTable.innerHTML = kbs.map((kb) => `<tr><td>${escapeHtml(kb.slug)}</td><td>${escapeHtml(kb.name)}</td><td>${escapeHtml(kb.status)}</td></tr>`).join("");
  els.codeReposTable.innerHTML = repos.map((repo) => `<tr><td>${escapeHtml(repo.repo_key)}</td><td>${escapeHtml(repo.name)}</td><td>${escapeHtml(repo.status)}</td></tr>`).join("");
}
```

- [ ] **Step 7: Add CSS**

Add to `app.css`:

```css
.stats-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  padding: 16px;
}

.stat-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}

.stat-card strong {
  display: block;
  font-size: 24px;
}

.log-detail-modal .modal-card {
  max-width: 980px;
}

.json-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
```

- [ ] **Step 8: Run UI tests**

Run:

```bash
uv run pytest tests/test_capability_api.py -k "phase2_views or phase2_interactions or capability_admin_page" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/wiki_manager/web_pages.py src/wiki_manager/static/capabilities/app.js src/wiki_manager/static/capabilities/app.css tests/test_capability_api.py
git commit -m "feat: add phase 2 capability console views"
```

## Task 10: Add Built-In Wiki And CodeGraph Admin APIs

**Files:**
- Modify: `src/wiki_manager/server.py`
- Modify: `src/wiki_manager/services.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing Wiki admin API test**

Add to `tests/test_capability_api.py`:

```python
def test_builtin_wiki_kbs_api_returns_status_summary(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "root"},
    )

    response = client.get("/builtin/wiki/kbs", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert response.json()[0]["slug"] == "frontend-docs"
    assert "backend_targets" in response.json()[0]
    assert "document_count" in response.json()[0]
```

- [ ] **Step 2: Run failing API test**

Run:

```bash
uv run pytest tests/test_capability_api.py -k "builtin_wiki_kbs_api" -v
```

Expected: FAIL because `/builtin/wiki/kbs` does not exist.

- [ ] **Step 3: Add service summary method**

Add to `WikiManagerService`:

```python
    def list_kb_status_summaries(self, actor: str) -> list[dict[str, Any]]:
        summaries = []
        for kb in self.list_kbs(actor):
            targets = self.store.list_backend_targets(kb["id"])
            docs = self.store.list_docs_for_kb(kb["id"])
            summaries.append(
                {
                    **kb,
                    "backend_targets": targets,
                    "document_count": len(docs),
                    "sync_failed_count": len([doc for doc in docs if doc.get("sync_status") == "sync_failed"]),
                }
            )
        return summaries
```

- [ ] **Step 4: Add API endpoint**

Add to `src/wiki_manager/server.py`:

```python
    @app.get("/builtin/wiki/kbs")
    def builtin_wiki_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.list_kb_status_summaries(current_actor))
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/test_capability_api.py -k "builtin_wiki_kbs_api or codegraph" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_manager/server.py src/wiki_manager/services.py tests/test_capability_api.py
git commit -m "feat: add built-in resource admin APIs"
```

## Task 11: Final MetaMCP, API, And Regression Verification

**Files:**
- Modify tests only if final integration exposes mismatched expectations.
- Test: `tests/test_metamcp_http_gateway.py`
- Test: `tests/test_e2e.py`

- [ ] **Step 1: Add final MetaMCP integration test**

Add to `tests/test_metamcp_http_gateway.py`:

```python
def test_mcp_search_lists_external_and_builtin_sources_with_profile(wm_paths) -> None:
    _register_service(wm_paths, "mysql", "MySQL")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb("frontend-docs", "Frontend Docs", "", "root")
    store.upsert_project_profile("safe-readonly", "安全只读", "", "active", "root")
    store.replace_profile_resource_rules(
        "safe-readonly",
        [{"resource_type": "wiki_kb", "resource_key": "frontend-docs"}],
    )

    svc = WikiManagerService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {}))

    services = [item["service"] for item in structured["items"]]
    assert "mysql" in services
    assert "wiki" in services
    assert "codegraph" in services
```

- [ ] **Step 2: Run targeted integration tests**

Run:

```bash
uv run pytest tests/test_metamcp_http_gateway.py tests/test_builtin_wiki.py tests/test_builtin_codegraph.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full capability suite**

Run:

```bash
uv run pytest tests/test_capability_governance_storage.py tests/test_capability_governance.py tests/test_capability_service.py tests/test_capability_api.py tests/test_capability_log_analysis.py tests/test_capability_stats.py tests/test_profile_resources.py tests/test_codegraph_service.py tests/test_builtin_wiki.py tests/test_builtin_codegraph.py tests/test_cli.py tests/test_metamcp_http_gateway.py -v
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 5: Manual browser verification**

Start the server:

```bash
uv run wiki server start
```

Open:

```text
http://127.0.0.1:8765/admin/capabilities
```

Verify:

- 左侧导航包含“调用统计”和“内置能力”。
- 调用日志支持筛选，点击请求/响应能打开详情浮窗。
- Project Profile 行包含复制命令入口，命令包含 `wiki metamcp add --url http://127.0.0.1:8765/mcp --profile safe-readonly`。
- Profile 资源配置能勾选 Wiki KB 和 CodeGraph 仓库。
- 内置能力页展示 Wiki KB 和 CodeGraph 仓库状态。
- 能力目录在没有 profile 时展示 external MCP、Wiki、CodeGraph。

- [ ] **Step 6: Commit final integration fixes**

```bash
git add src/wiki_manager tests/test_metamcp_http_gateway.py tests/test_e2e.py tests/test_builtin_wiki.py tests/test_builtin_codegraph.py
git commit -m "test: verify phase 2 capability hub flow"
```

## Self-Review Checklist

- Spec coverage:
  - 日志失败归因: Task 1 and Task 2.
  - 日志筛选和详情浮窗: Task 1, Task 2, Task 9.
  - 统计页面: Task 3 and Task 9.
  - Project Profile 复制命令和 CLI 交互: Task 8 and Task 9.
  - Wiki 固有能力和 Profile KB 范围: Task 4, Task 5, Task 10.
  - CodeGraph 仓库管理、同步、查询和 Profile repo 范围: Task 4, Task 6, Task 7.
  - MetaMCP 服务端强制裁剪: Task 5, Task 7, Task 11.
  - 回归测试: Task 11.
- Placeholder scan: passed the red-flag text scan after plan edits.
- Type consistency:
  - Source type uses `SourceType.builtin.value` for Wiki and CodeGraph logs.
  - Built-in service keys are `wiki` and `codegraph`.
  - Built-in resource types are `wiki_kb` and `code_repo`.
  - Profile resource API uses `resources`, while source allow/deny API keeps `rules`.
  - MetaMCP execute keeps the existing shape: `service_key`, `tool`, `arguments`.
