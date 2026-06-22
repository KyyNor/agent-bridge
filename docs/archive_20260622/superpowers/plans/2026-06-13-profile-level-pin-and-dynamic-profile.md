# Profile Level Pin And Dynamic Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add profile-level `service + tool_type` pinning with direct `pin_*` MCP tools, plus external dynamic profile markdown files for project/user profile guidance.

**Architecture:** Add storage and service APIs for profile pin groups, auto pin settings, auto pin preview, service+level stats, and dynamic profile markdown rendering. Keep profile allow/deny as the security boundary, reuse `CapabilityService.execute()` for direct pinned tools, and extend CLI/frontend surfaces around the existing Profile configuration flow.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Typer, FastMCP, pytest, Vue 3, TypeScript.

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-06-13-profile-level-pin-and-dynamic-profile-design.md`
- Existing MCP gateway: `src/agent_bridge/capabilities/mcp_server.py`
- Existing capability service: `src/agent_bridge/capabilities/service.py`
- Existing governance service: `src/agent_bridge/capabilities/governance.py`
- Existing SQLite schema/repositories: `src/agent_bridge/storage/schema.py`, `src/agent_bridge/storage/repositories/governance.py`, `src/agent_bridge/storage/sqlite.py`
- Existing profile CLI: `src/agent_bridge/cli/profile.py`, `src/agent_bridge/cli/app.py`
- Existing Profile UI: `frontend/capabilities/src/views/capabilities/ProfilesView.vue`

## File Structure

Create:

- `src/agent_bridge/capabilities/profile_pins.py`: profile pin computation, generated direct tool metadata, safe tool-name helpers.
- `src/agent_bridge/capabilities/profile_docs.py`: dynamic profile markdown rendering, hash computation, pointer block helpers.
- `tests/test_profile_pins.py`: storage/service tests for manual pins, auto pins, computed preview, and direct tool metadata.
- `tests/test_profile_docs.py`: profile markdown rendering/cache tests.

Modify:

- `src/agent_bridge/storage/schema.py`: add profile pin, auto pin, and profile document cache tables.
- `src/agent_bridge/storage/repositories/governance.py`: add repository methods for pin rules, auto settings, profile docs, stats with `tool_type`.
- `src/agent_bridge/storage/sqlite.py`: expose facade methods and migrations.
- `src/agent_bridge/capabilities/governance.py`: add validation and APIs for profile pin/doc management.
- `src/agent_bridge/capabilities/service.py`: add computed pin preview and direct pinned tool execution helpers.
- `src/agent_bridge/capabilities/mcp_server.py`: build profile-aware MCP tool lists with dynamic `pin_*` tools.
- `src/agent_bridge/api/schemas.py`: add request/response schema models.
- `src/agent_bridge/api/routes/governance.py`: add profile pin/doc/stats endpoints.
- `src/agent_bridge/client.py`: add HTTP client methods for CLI.
- `src/agent_bridge/cli/app.py`: rename MCP server entry to `agent-bridge`; add pointer helpers.
- `src/agent_bridge/cli/profile.py`: add `profile refresh` and `profile pins refresh`; update `profile use`.
- `frontend/capabilities/src/api/types.ts`: add pin/doc types.
- `frontend/capabilities/src/api/client.ts`: add pin/doc APIs.
- `frontend/capabilities/src/views/capabilities/ProfilesView.vue`: add Pinned Tools and profile doc sections.
- `frontend/capabilities/src/views/monitoring/StatsView.vue`: add service+level stats view.
- Tests touching existing behavior: `tests/test_cli.py`, `tests/test_metamcp_http_gateway.py`, `tests/test_capability_stats.py`, `tests/test_capability_api.py`.

## Task 1: Add Pin Storage Schema And Repository Methods

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/repositories/governance.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Test: `tests/test_profile_pins.py`

- [ ] **Step 1: Write storage tests for manual pin rules**

Create `tests/test_profile_pins.py` with these initial tests:

```python
from __future__ import annotations

from agent_bridge.capabilities.models import ProfileRuleEffect, SourceType, ToolType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def _profile(store: SQLiteStore) -> None:
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="Safe Readonly",
        description="",
        status="active",
        created_by="root",
    )


def test_profile_manual_pin_rules_round_trip(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)

    store.replace_profile_pin_rules(
        "safe-readonly",
        [
            {"service_key": "mysql", "tool_type": ToolType.search.value, "created_by": "root"},
            {"service_key": "jira", "tool_type": ToolType.detail.value, "created_by": "root"},
        ],
    )

    rows = store.list_profile_pin_rules("safe-readonly")
    assert [(row["service_key"], row["tool_type"]) for row in rows] == [
        ("jira", "detail"),
        ("mysql", "search"),
    ]


def test_profile_auto_pin_settings_round_trip(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)

    settings = store.upsert_profile_pin_settings(
        profile_key="safe-readonly",
        mode="ratio",
        ratio_percent=10,
        count=None,
        auto_cache=None,
    )

    assert settings["mode"] == "ratio"
    assert settings["ratio_percent"] == 10
    assert settings["count"] is None
    assert store.get_profile_pin_settings("safe-readonly")["mode"] == "ratio"
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_profile_manual_pin_rules_round_trip tests/test_profile_pins.py::test_profile_auto_pin_settings_round_trip -v
```

Expected: FAIL because `replace_profile_pin_rules`, `list_profile_pin_rules`, `upsert_profile_pin_settings`, and `get_profile_pin_settings` do not exist.

- [ ] **Step 3: Add schema tables**

In `src/agent_bridge/storage/schema.py`, add these tables after `profile_resource_rules`:

```sql
CREATE TABLE IF NOT EXISTS profile_pin_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  service_key TEXT NOT NULL,
  tool_type TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (profile_key, service_key, tool_type)
);
CREATE INDEX IF NOT EXISTS idx_profile_pin_rules_profile ON profile_pin_rules(profile_key);
CREATE INDEX IF NOT EXISTS idx_profile_pin_rules_service_type ON profile_pin_rules(service_key, tool_type);
CREATE TABLE IF NOT EXISTS profile_pin_settings (
  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  mode TEXT NOT NULL DEFAULT 'disabled',
  ratio_percent INTEGER,
  count INTEGER,
  auto_cache_json TEXT,
  auto_cache_computed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Add repository methods**

In `src/agent_bridge/storage/repositories/governance.py`, add:

```python
    def replace_profile_pin_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profile_pin_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_pin_rules (profile_key, service_key, tool_type, created_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (profile_key, rule["service_key"], rule["tool_type"], rule["created_by"]),
                )

    def list_profile_pin_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_pin_rules
                WHERE profile_key = ?
                ORDER BY service_key, tool_type
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_profile_pin_settings(
        self,
        *,
        profile_key: str,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
        auto_cache: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cache_json = json.dumps(auto_cache, ensure_ascii=False, default=str) if auto_cache is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_pin_settings (
                  profile_key, mode, ratio_percent, count, auto_cache_json, auto_cache_computed_at
                )
                VALUES (?, ?, ?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)
                ON CONFLICT(profile_key) DO UPDATE SET
                  mode = excluded.mode,
                  ratio_percent = excluded.ratio_percent,
                  count = excluded.count,
                  auto_cache_json = excluded.auto_cache_json,
                  auto_cache_computed_at = excluded.auto_cache_computed_at,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, mode, ratio_percent, count, cache_json, cache_json),
            )
            row = conn.execute(
                "SELECT * FROM profile_pin_settings WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            settings = row_to_dict(row)
            if settings is None:
                raise KeyError(f"profile pin settings not found: {profile_key}")
            return settings

    def get_profile_pin_settings(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profile_pin_settings WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def clear_profile_pin_auto_cache(self, profile_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE profile_pin_settings
                SET auto_cache_json = NULL,
                    auto_cache_computed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE profile_key = ?
                """,
                (profile_key,),
            )
```

- [ ] **Step 5: Add SQLiteStore facade methods**

In `src/agent_bridge/storage/sqlite.py`, add facade methods next to profile resource methods:

```python
    def replace_profile_pin_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_pin_rules(profile_key=profile_key, rules=rules)

    def list_profile_pin_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_pin_rules(profile_key=profile_key)

    def upsert_profile_pin_settings(
        self,
        *,
        profile_key: str,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
        auto_cache: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self.governance.upsert_profile_pin_settings(
            profile_key=profile_key,
            mode=mode,
            ratio_percent=ratio_percent,
            count=count,
            auto_cache=auto_cache,
        )

    def get_profile_pin_settings(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_pin_settings(profile_key=profile_key)

    def clear_profile_pin_auto_cache(self, profile_key: str) -> None:
        return self.governance.clear_profile_pin_auto_cache(profile_key=profile_key)
```

- [ ] **Step 6: Run storage tests**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_profile_manual_pin_rules_round_trip tests/test_profile_pins.py::test_profile_auto_pin_settings_round_trip -v
```

Expected: PASS.

- [ ] **Step 7: Commit storage foundation**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/governance.py src/agent_bridge/storage/sqlite.py tests/test_profile_pins.py
git commit -m "feat: store profile pin rules"
```

## Task 2: Add Governance Validation And Pin Computation Service

**Files:**
- Create: `src/agent_bridge/capabilities/profile_pins.py`
- Modify: `src/agent_bridge/capabilities/governance.py`
- Modify: `tests/test_profile_pins.py`

- [ ] **Step 1: Add failing governance tests**

Append to `tests/test_profile_pins.py`:

```python
from datetime import datetime, timedelta

import pytest

from agent_bridge.capabilities.governance import CapabilityGovernanceService
from agent_bridge.core.domain import ValidationError


def _service_with_tools(store: SQLiteStore, service_key: str = "mysql") -> None:
    store.create_mcp_service(
        service_key=service_key,
        name=service_key.upper(),
        endpoint_url=f"https://{service_key}.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status(service_key, "enabled")
    store.upsert_mcp_tool(
        service_key=service_key,
        tool_name="query_users",
        display_name="Query Users",
        description="Find users",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=[],
        examples=[],
    )
    store.upsert_mcp_tool(
        service_key=service_key,
        tool_name="delete_user",
        display_name="Delete User",
        description="Delete users",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        tool_type=ToolType.action.value,
        tags=[],
        examples=[],
    )


def test_governance_rejects_non_readonly_pin_type(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    governance = CapabilityGovernanceService(store=store, admins={"root"})

    with pytest.raises(ValidationError, match="tool_type is not pinnable"):
        governance.replace_profile_pins(
            "root",
            "safe-readonly",
            [{"service_key": "mysql", "tool_type": ToolType.action.value}],
        )


def test_compute_profile_pin_preview_filters_to_allowed_readonly_tools(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "mysql")
    _service_with_tools(store, "hive")
    store.replace_profile_source_rules(
        "safe-readonly",
        [{"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": ProfileRuleEffect.allow.value}],
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.replace_profile_pins(
        "root",
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": ToolType.search.value}],
    )

    preview = governance.profile_pin_preview("root", "safe-readonly")

    assert [(group["service_key"], group["tool_type"], group["source"]) for group in preview["groups"]] == [
        ("mysql", "search", "manual")
    ]
    assert [tool["generated_tool_name"] for tool in preview["tools"]] == ["pin_mysql_query_users"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_governance_rejects_non_readonly_pin_type tests/test_profile_pins.py::test_compute_profile_pin_preview_filters_to_allowed_readonly_tools -v
```

Expected: FAIL because governance pin methods do not exist.

- [ ] **Step 3: Create profile pin helper module**

Create `src/agent_bridge/capabilities/profile_pins.py`:

```python
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from agent_bridge.capabilities.models import ToolType

PINNABLE_TOOL_TYPES = {ToolType.overview.value, ToolType.search.value, ToolType.detail.value}
PIN_TOOL_PREFIX = "pin_"
PIN_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class PinnedGroup:
    service_key: str
    tool_type: str
    source: str
    calls: int = 0


def safe_pin_tool_name(service_key: str, tool_name: str) -> str:
    service = PIN_NAME_RE.sub("_", service_key).strip("_").lower()
    tool = PIN_NAME_RE.sub("_", tool_name).strip("_")
    return f"{PIN_TOOL_PREFIX}{service}_{tool}"


def ratio_target(candidate_count: int, ratio_percent: int) -> int:
    if candidate_count <= 0 or ratio_percent <= 0:
        return 0
    return math.ceil(candidate_count * ratio_percent / 100)


def tool_payload_to_pin_tool(service: dict[str, Any], tool: dict[str, Any], source: str) -> dict[str, Any]:
    generated_name = safe_pin_tool_name(tool["service_key"], tool["tool_name"])
    description = (
        f"Direct pinned Agent Bridge tool for service {service['name']} ({tool['service_key']}), "
        f"tool {tool['tool_name']}, level {tool['tool_type']}, source {source}. "
        f"Use search(path='{tool['service_key']}') to inspect the full service directory."
    )
    return {
        "generated_tool_name": generated_name,
        "service_key": tool["service_key"],
        "service_name": service["name"],
        "tool_name": tool["tool_name"],
        "tool_type": tool["tool_type"],
        "source": source,
        "description": description,
        "input_schema": tool.get("input_schema_json"),
    }
```

- [ ] **Step 4: Add governance pin methods**

In `src/agent_bridge/capabilities/governance.py`, import helper constants:

```python
from agent_bridge.capabilities.profile_pins import PINNABLE_TOOL_TYPES, ratio_target, safe_pin_tool_name
```

Add methods to `CapabilityGovernanceService`:

```python
    def replace_profile_pins(
        self,
        actor: str,
        profile_key: str,
        pins: list[dict[str, str]],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        normalized = []
        for pin in pins:
            service_key = str(pin.get("service_key") or "")
            tool_type = str(pin.get("tool_type") or "")
            if tool_type not in PINNABLE_TOOL_TYPES:
                raise ValidationError("tool_type is not pinnable")
            if self.store.get_mcp_service(service_key) is None:
                raise NotFound("service not found")
            normalized.append({"service_key": service_key, "tool_type": tool_type, "created_by": actor})
        self.store.replace_profile_pin_rules(profile_key, normalized)
        self.store.clear_profile_pin_auto_cache(profile_key)
        return self.profile_pin_preview(actor, profile_key)

    def update_profile_pin_settings(
        self,
        actor: str,
        profile_key: str,
        *,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        if mode not in {"disabled", "ratio", "count"}:
            raise ValidationError("invalid pin mode")
        if mode == "ratio":
            if ratio_percent is None or ratio_percent < 1 or ratio_percent > 100:
                raise ValidationError("ratio_percent must be between 1 and 100")
            count = None
        elif mode == "count":
            if count is None or count < 1:
                raise ValidationError("count must be positive")
            ratio_percent = None
        else:
            ratio_percent = None
            count = None
        self.store.upsert_profile_pin_settings(
            profile_key=profile_key,
            mode=mode,
            ratio_percent=ratio_percent,
            count=count,
            auto_cache=None,
        )
        return self.profile_pin_preview(actor, profile_key)
```

- [ ] **Step 5: Add preview computation**

Continue in `CapabilityGovernanceService`:

```python
    def profile_pin_preview(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        manual_rows = self.store.list_profile_pin_rules(profile_key)
        settings = self.store.get_profile_pin_settings(profile_key) or {
            "mode": "disabled",
            "ratio_percent": None,
            "count": None,
            "auto_cache_json": None,
            "auto_cache_computed_at": None,
        }
        allowed = set(
            self.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.mcp_service.value,
                source_keys=[service["service_key"] for service in self.store.list_mcp_services()],
            )
        )
        services = {
            service["service_key"]: service
            for service in self.store.list_mcp_services()
            if service["status"] == "enabled" and service["service_key"] in allowed
        }
        tools = [
            tool for tool in self.store.list_mcp_tools()
            if tool.get("status") == "active"
            and tool.get("service_key") in services
            and tool.get("tool_type") in PINNABLE_TOOL_TYPES
        ]
        candidate_groups = sorted({(tool["service_key"], tool["tool_type"]) for tool in tools})
        manual = [
            {"service_key": row["service_key"], "tool_type": row["tool_type"], "source": "manual", "calls": 0}
            for row in manual_rows
            if (row["service_key"], row["tool_type"]) in candidate_groups
        ]
        groups = manual
        # Task 3 extends this disabled default with auto-ranked groups.
        tool_rows = []
        for group in groups:
            for tool in tools:
                if tool["service_key"] == group["service_key"] and tool["tool_type"] == group["tool_type"]:
                    service = services[tool["service_key"]]
                    tool_rows.append(
                        {
                            "generated_tool_name": safe_pin_tool_name(tool["service_key"], tool["tool_name"]),
                            "service_key": tool["service_key"],
                            "service_name": service["name"],
                            "tool_name": tool["tool_name"],
                            "tool_type": tool["tool_type"],
                            "source": group["source"],
                            "input_schema": tool.get("input_schema_json"),
                        }
                    )
        return {"profile_key": profile_key, "settings": dict(settings), "groups": groups, "tools": tool_rows}
```

- [ ] **Step 6: Run governance preview tests**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_governance_rejects_non_readonly_pin_type tests/test_profile_pins.py::test_compute_profile_pin_preview_filters_to_allowed_readonly_tools -v
```

Expected: PASS.

- [ ] **Step 7: Commit governance pin preview**

```bash
git add src/agent_bridge/capabilities/profile_pins.py src/agent_bridge/capabilities/governance.py tests/test_profile_pins.py
git commit -m "feat: compute profile pin preview"
```

## Task 3: Implement Auto Pin Ranking And Tool-Type Stats

**Files:**
- Modify: `src/agent_bridge/storage/repositories/governance.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/capabilities/governance.py`
- Modify: `tests/test_profile_pins.py`
- Modify: `tests/test_capability_stats.py`

- [ ] **Step 1: Add failing tests for auto merge and stats**

Append to `tests/test_profile_pins.py`:

```python
def test_auto_pin_count_adds_highest_called_group_without_trimming_manual(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "mysql")
    _service_with_tools(store, "jira")
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": "allow"},
            {"source_type": SourceType.mcp_service.value, "source_key": "jira", "effect": "allow"},
        ],
    )
    store.create_tool_call_log(
        log_id="call_jira_1",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="jira",
        tool_name="query_users",
        request={},
        response={},
        status="success",
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.replace_profile_pins(
        "root",
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": ToolType.search.value}],
    )
    governance.update_profile_pin_settings("root", "safe-readonly", mode="count", ratio_percent=None, count=2)

    preview = governance.profile_pin_preview("root", "safe-readonly")

    assert [(g["service_key"], g["tool_type"], g["source"]) for g in preview["groups"]] == [
        ("mysql", "search", "manual"),
        ("jira", "search", "auto"),
    ]
    assert store.get_profile_pin_settings("safe-readonly")["auto_cache_json"] is not None
```

Append to `tests/test_capability_stats.py`:

```python
def test_call_stats_group_by_service_and_tool_type(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="",
        input_schema={},
        tool_type="search",
        tags=[],
        examples=[],
    )
    store.create_tool_call_log(
        log_id="call_stats_tool_type",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status=CallLogStatus.success.value,
    )

    stats = store.aggregate_tool_call_stats(
        dimensions=["source_key", "tool_type"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    assert stats[0]["source_key"] == "mysql"
    assert stats[0]["tool_type"] == "search"
    assert stats[0]["calls"] == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_auto_pin_count_adds_highest_called_group_without_trimming_manual tests/test_capability_stats.py::test_call_stats_group_by_service_and_tool_type -v
```

Expected: FAIL because auto ranking and `tool_type` stats are not implemented.

- [ ] **Step 3: Add tool_type stats support**

In `src/agent_bridge/storage/repositories/governance.py`, update `aggregate_tool_call_stats()`:

```python
allowed_dimensions = {
    "profile_key",
    "entrypoint",
    "source_type",
    "source_key",
    "tool_name",
    "tool_type",
    "status",
    "failure_stage",
    "failure_owner",
    "error_type",
    "resource_type",
    "resource_key",
}
```

Then build selected columns with `mcp_tools.tool_type AS tool_type` when needed, and join when any requested dimension is `tool_type`:

```python
join_clause = ""
if "tool_type" in dimensions:
    join_clause = """
    LEFT JOIN mcp_tools
      ON mcp_tools.service_key = tool_call_logs.source_key
     AND mcp_tools.tool_name = tool_call_logs.tool_name
    """
```

Use this dimension expression mapping:

```python
dimension_expr = {
    "tool_type": "mcp_tools.tool_type",
}
selected = [f"{dimension_expr.get(d, d)} AS {d}" for d in dimensions]
group_columns.extend([dimension_expr.get(d, d) for d in dimensions])
```

Place `{join_clause}` after `FROM tool_call_logs`.

- [ ] **Step 4: Add auto ranking query**

In `GovernanceRepository`, add:

```python
    def aggregate_pin_group_usage(
        self,
        *,
        profile_key: str,
        created_from: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  logs.source_key AS service_key,
                  tools.tool_type AS tool_type,
                  COUNT(*) AS calls
                FROM tool_call_logs logs
                JOIN mcp_tools tools
                  ON tools.service_key = logs.source_key
                 AND tools.tool_name = logs.tool_name
                WHERE logs.profile_key = ?
                  AND logs.entrypoint = 'metamcp_execute'
                  AND logs.status = 'success'
                  AND logs.created_at >= ?
                GROUP BY logs.source_key, tools.tool_type
                ORDER BY calls DESC, logs.source_key, tools.tool_type
                """,
                (profile_key, created_from),
            ).fetchall()
            return [dict(row) for row in rows]
```

Expose it in `SQLiteStore`:

```python
    def aggregate_pin_group_usage(self, *, profile_key: str, created_from: str) -> list[dict[str, Any]]:
        return self.governance.aggregate_pin_group_usage(profile_key=profile_key, created_from=created_from)
```

- [ ] **Step 5: Implement auto merge and 24-hour cache in governance**

In `CapabilityGovernanceService.profile_pin_preview()`, after manual groups are computed, replace the disabled default with:

```python
        groups = list(manual)
        if settings.get("mode") != "disabled":
            if settings.get("mode") == "ratio":
                target = ratio_target(len(candidate_groups), int(settings.get("ratio_percent") or 0))
            else:
                target = int(settings.get("count") or 0)
            if target > len(groups):
                import json
                from datetime import datetime, timedelta

                created_from = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                existing = {(group["service_key"], group["tool_type"]) for group in groups}
                cached_groups: list[dict[str, Any]] | None = None
                computed_at = settings.get("auto_cache_computed_at")
                if settings.get("auto_cache_json") and computed_at:
                    try:
                        computed_dt = datetime.strptime(str(computed_at), "%Y-%m-%d %H:%M:%S")
                        if datetime.utcnow() - computed_dt < timedelta(hours=24):
                            cached = json.loads(str(settings["auto_cache_json"]))
                            if isinstance(cached, dict) and isinstance(cached.get("groups"), list):
                                cached_groups = cached["groups"]
                    except (TypeError, ValueError, json.JSONDecodeError):
                        cached_groups = None
                if cached_groups is None:
                    usage = self.store.aggregate_pin_group_usage(profile_key=profile_key, created_from=created_from)
                    cached_groups = [
                        {
                            "service_key": row["service_key"],
                            "tool_type": row["tool_type"],
                            "source": "auto",
                            "calls": int(row["calls"]),
                        }
                        for row in usage
                    ]
                    self.store.upsert_profile_pin_settings(
                        profile_key=profile_key,
                        mode=str(settings.get("mode")),
                        ratio_percent=settings.get("ratio_percent"),
                        count=settings.get("count"),
                        auto_cache={"groups": cached_groups},
                    )
                for row in cached_groups:
                    key = (row["service_key"], row["tool_type"])
                    if key in existing or key not in candidate_groups:
                        continue
                    groups.append(
                        {
                            "service_key": row["service_key"],
                            "tool_type": row["tool_type"],
                            "source": "auto",
                            "calls": int(row["calls"]),
                        }
                    )
                    existing.add(key)
                    if len(groups) >= target:
                        break
```

- [ ] **Step 6: Run auto pin and stats tests**

Run:

```bash
uv run pytest tests/test_profile_pins.py::test_auto_pin_count_adds_highest_called_group_without_trimming_manual tests/test_capability_stats.py::test_call_stats_group_by_service_and_tool_type -v
```

Expected: PASS.

- [ ] **Step 7: Commit auto pin stats**

```bash
git add src/agent_bridge/storage/repositories/governance.py src/agent_bridge/storage/sqlite.py src/agent_bridge/capabilities/governance.py tests/test_profile_pins.py tests/test_capability_stats.py
git commit -m "feat: rank profile auto pins"
```

## Task 4: Add Profile Pin API Endpoints

**Files:**
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/governance.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_capability_api.py`:

```python
def test_profile_pin_api_round_trip(client, wm_paths) -> None:
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe", description="", status="active", created_by="root")
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status("mysql", "enabled")
    store.replace_profile_source_rules(
        "safe",
        [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}],
    )

    response = client.put(
        "/capability-profiles/safe/pins",
        json={"pins": [{"service_key": "mysql", "tool_type": "search"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert response.json()["groups"] == []

    settings = client.put(
        "/capability-profiles/safe/pins/settings",
        json={"mode": "count", "count": 2},
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert settings.status_code == 200
    assert settings.json()["settings"]["mode"] == "count"

    refresh = client.post(
        "/capability-profiles/safe/pins/refresh",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert refresh.status_code == 200
    assert refresh.json()["profile_key"] == "safe"
```

- [ ] **Step 2: Run API test and verify failure**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_profile_pin_api_round_trip -v
```

Expected: FAIL with 404.

- [ ] **Step 3: Add request models**

In `src/agent_bridge/api/schemas.py`, add:

```python
class ProfilePinRuleRequest(BaseModel):
    service_key: str
    tool_type: str


class ProfilePinsRequest(BaseModel):
    pins: list[ProfilePinRuleRequest] = Field(default_factory=list)


class ProfilePinSettingsRequest(BaseModel):
    mode: str
    ratio_percent: int | None = None
    count: int | None = None
```

- [ ] **Step 4: Add routes**

In `src/agent_bridge/api/routes/governance.py`, import the new schemas and add:

```python
    @router.get("/capability-profiles/{profile_key}/pins")
    def get_profile_pins(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.profile_pin_preview(current_actor, profile_key))

    @router.put("/capability-profiles/{profile_key}/pins")
    def replace_profile_pins(profile_key: str, payload: ProfilePinsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        pins = [pin.model_dump() for pin in payload.pins]
        return call_safely(lambda: service.governance.replace_profile_pins(current_actor, profile_key, pins))

    @router.put("/capability-profiles/{profile_key}/pins/settings")
    def update_profile_pin_settings(profile_key: str, payload: ProfilePinSettingsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.update_profile_pin_settings(
            current_actor,
            profile_key,
            mode=payload.mode,
            ratio_percent=payload.ratio_percent,
            count=payload.count,
        ))

    @router.post("/capability-profiles/{profile_key}/pins/refresh")
    def refresh_profile_pins(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.refresh_profile_pin_cache(current_actor, profile_key))
```

Add `refresh_profile_pin_cache()` to `CapabilityGovernanceService`:

```python
    def refresh_profile_pin_cache(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        self.store.clear_profile_pin_auto_cache(profile_key)
        return self.profile_pin_preview(actor, profile_key)
```

- [ ] **Step 5: Run API test**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_profile_pin_api_round_trip -v
```

Expected: PASS.

- [ ] **Step 6: Commit API endpoints**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/governance.py src/agent_bridge/capabilities/governance.py tests/test_capability_api.py
git commit -m "feat: expose profile pin api"
```

## Task 5: Generate Direct `pin_*` MCP Tools

**Files:**
- Modify: `src/agent_bridge/capabilities/mcp_server.py`
- Modify: `src/agent_bridge/capabilities/service.py`
- Modify: `tests/test_metamcp_http_gateway.py`

- [ ] **Step 1: Add failing MCP test**

Append to `tests/test_metamcp_http_gateway.py`:

```python
def test_mcp_exposes_profile_pinned_tools(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _register_service(wm_paths, "mysql", "MySQL")
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_users",
        display_name="Query Users",
        description="Find users",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        tool_type="search",
        tags=[],
        examples=[],
    )
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="Safe",
        description="",
        status="active",
        created_by="root",
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}],
    )
    store.replace_profile_pin_rules(
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": "search", "created_by": "root"}],
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc, profile_key="safe-readonly")
    token = _request_profile.set("safe-readonly")
    try:
        tools = asyncio.run(mcp.list_tools())
    finally:
        _request_profile.reset(token)

    pinned = next(tool for tool in tools if tool.name == "pin_mysql_query_users")
    assert pinned.parameters["properties"]["q"]["type"] == "string"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_metamcp_http_gateway.py::test_mcp_exposes_profile_pinned_tools -v
```

Expected: FAIL because only `search` and `execute` are exposed.

- [ ] **Step 3: Add a helper to build pinned tool specs**

In `src/agent_bridge/capabilities/service.py`, add:

```python
    def pinned_tool_specs(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        if profile_key is None:
            return []
        preview = self.governance.profile_pin_preview(actor, profile_key)
        specs = []
        for item in preview.get("tools", []):
            tool_payload = self.store.get_mcp_tool(item["service_key"], item["tool_name"])
            if tool_payload is None:
                continue
            specs.append(
                {
                    **item,
                    "input_schema": _json_loads(tool_payload.get("input_schema_json"), {}),
                    "description": (
                        f"Direct pinned Agent Bridge tool for service {item['service_name']} "
                        f"({item['service_key']}), tool {item['tool_name']}, level {item['tool_type']}, "
                        f"source {item['source']}. Use search(path='{item['service_key']}') "
                        "to inspect the full service directory."
                    ),
                }
            )
        return specs
```

- [ ] **Step 4: Add dynamic signature helpers**

In `src/agent_bridge/capabilities/mcp_server.py`, import `inspect` and add these helpers near `create_mcp_server()`:

```python
import inspect
```

```python
def _annotation_from_json_schema(definition: dict[str, Any]) -> Any:
    value_type = definition.get("type")
    if value_type == "string":
        return str
    if value_type == "integer":
        return int
    if value_type == "number":
        return float
    if value_type == "boolean":
        return bool
    if value_type == "array":
        return list
    if value_type == "object":
        return dict
    return Any


def _signature_from_json_schema(schema: dict[str, Any]) -> inspect.Signature:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    parameters = []
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            definition = {}
        default = inspect._empty if name in required else definition.get("default", None)
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=_annotation_from_json_schema(definition),
                default=default,
            )
        )
    return inspect.Signature(parameters=parameters)
```

- [ ] **Step 5: Make MCP server construction profile-aware**

Change the function signature in `src/agent_bridge/capabilities/mcp_server.py`:

```python
def create_mcp_server(service: AgentBridgeService, profile_key: str | None = None) -> FastMCP:
```

Inside `search` and `execute`, use the request context first and fall back to the constructor profile:

```python
active_profile = _request_profile.get() or profile_key
```

Use `active_profile` when calling `service.capabilities.search()` and `service.capabilities.execute()`.

- [ ] **Step 6: Register pinned tools in MCP server**

In `src/agent_bridge/capabilities/mcp_server.py`, create a helper inside `create_mcp_server()` after `execute`:

```python
    def register_pinned_tools() -> None:
        registered: set[str] = set()
        if profile_key is None:
            return
        for spec in service.capabilities.pinned_tool_specs(default_user(), profile_key):
            name = spec["generated_tool_name"]
            if name in registered:
                continue
            registered.add(name)

            async def pinned_tool(*, _spec=spec, **kwargs: Any) -> dict[str, Any]:
                active_profile = _request_profile.get() or profile_key
                return await service.capabilities.execute(
                    actor=default_user(),
                    service=_spec["service_key"],
                    tool=_spec["tool_name"],
                    arguments=kwargs,
                    profile_key=active_profile,
                )

            pinned_tool.__signature__ = _signature_from_json_schema(spec.get("input_schema") or {})
            mcp.tool(name=name, description=spec["description"])(pinned_tool)

    register_pinned_tools()
```

This preserves the upstream schema at the property level for primitive JSON schema types. Nested object and array values pass through as `dict` and `list`.

- [ ] **Step 7: Build the MCP server per request profile**

In `setup_mcp_route()`, remove the single shared `mcp = create_mcp_server(service)` instance and construct the server inside `handle_mcp()` after reading the profile header:

```python
    @router.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def handle_mcp(request: Request) -> Response:
        profile = request.headers.get("x-agent-bridge-metamcp-profile")
        logger.info("MCP 请求 method=%s profile=%s", request.method, profile)
        mcp = create_mcp_server(service, profile_key=profile)
        token = _request_profile.set(profile)
        try:
            response = await _dispatch_mcp(mcp, request)
            logger.info("MCP 响应 status=%d profile=%s", response.status_code, profile)
            return response
        except Exception as exc:
            logger.error("MCP 错误 profile=%s 错误=%s", profile, exc)
            raise
        finally:
            _request_profile.reset(token)
```

This favors correct, immediately profile-aware tool lists. Auto pin ranking still uses the stored 24-hour cache, so per-request server construction does not recalculate usage rankings on every call.

- [ ] **Step 8: Run MCP list_tools test**

Run:

```bash
uv run pytest tests/test_metamcp_http_gateway.py::test_mcp_exposes_profile_pinned_tools -v
```

Expected: PASS.

- [ ] **Step 9: Commit direct MCP tools**

```bash
git add src/agent_bridge/capabilities/service.py src/agent_bridge/capabilities/mcp_server.py tests/test_metamcp_http_gateway.py
git commit -m "feat: expose pinned mcp tools"
```

## Task 6: Render Dynamic Profile Markdown And Pointer Files

**Files:**
- Create: `src/agent_bridge/capabilities/profile_docs.py`
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/repositories/governance.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/capabilities/governance.py`
- Test: `tests/test_profile_docs.py`

- [ ] **Step 1: Add failing profile doc tests**

Create `tests/test_profile_docs.py`:

```python
from __future__ import annotations

from pathlib import Path

from agent_bridge.capabilities.governance import CapabilityGovernanceService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def test_render_profile_markdown_includes_usage_resources_and_manual_notes(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe Profile", description="", status="active", created_by="root")
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status("mysql", "enabled")
    store.replace_profile_source_rules("safe", [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}])
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.update_profile_manual_notes("root", "safe", "## Manual Notes\nUse read-only queries only.")

    rendered = governance.render_profile_markdown("root", "safe")

    assert "# Agent Bridge Profile: Safe Profile" in rendered["markdown"]
    assert "search" in rendered["markdown"]
    assert "execute" in rendered["markdown"]
    assert "- MySQL (`mysql`)" in rendered["markdown"]
    assert "Use read-only queries only." in rendered["markdown"]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_profile_docs.py::test_render_profile_markdown_includes_usage_resources_and_manual_notes -v
```

Expected: FAIL because profile doc APIs do not exist.

- [ ] **Step 3: Add profile document cache schema**

In `src/agent_bridge/storage/schema.py`, add:

```sql
CREATE TABLE IF NOT EXISTS profile_doc_cache (
  profile_key TEXT PRIMARY KEY REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  manual_notes TEXT NOT NULL DEFAULT '',
  auto_summary_json TEXT NOT NULL DEFAULT '{}',
  auto_summary_hash TEXT NOT NULL DEFAULT '',
  rendered_hash TEXT NOT NULL DEFAULT '',
  last_rendered_markdown TEXT NOT NULL DEFAULT '',
  last_written_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Add profile doc repository methods**

In `GovernanceRepository`, add:

```python
    def get_profile_doc_cache(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profile_doc_cache WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def upsert_profile_manual_notes(self, profile_key: str, manual_notes: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_doc_cache (profile_key, manual_notes)
                VALUES (?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                  manual_notes = excluded.manual_notes,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, manual_notes),
            )
            row = conn.execute("SELECT * FROM profile_doc_cache WHERE profile_key = ?", (profile_key,)).fetchone()
            cache = row_to_dict(row)
            if cache is None:
                raise KeyError(f"profile doc cache not found: {profile_key}")
            return cache

    def upsert_profile_rendered_doc(
        self,
        *,
        profile_key: str,
        manual_notes: str,
        auto_summary: dict[str, Any],
        auto_summary_hash: str,
        rendered_hash: str,
        markdown: str,
        mark_written: bool,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_doc_cache (
                  profile_key, manual_notes, auto_summary_json, auto_summary_hash,
                  rendered_hash, last_rendered_markdown, last_written_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT(profile_key) DO UPDATE SET
                  manual_notes = excluded.manual_notes,
                  auto_summary_json = excluded.auto_summary_json,
                  auto_summary_hash = excluded.auto_summary_hash,
                  rendered_hash = excluded.rendered_hash,
                  last_rendered_markdown = excluded.last_rendered_markdown,
                  last_written_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE profile_doc_cache.last_written_at END,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile_key,
                    manual_notes,
                    json.dumps(auto_summary, ensure_ascii=False, default=str),
                    auto_summary_hash,
                    rendered_hash,
                    markdown,
                    mark_written,
                    mark_written,
                ),
            )
            row = conn.execute("SELECT * FROM profile_doc_cache WHERE profile_key = ?", (profile_key,)).fetchone()
            cache = row_to_dict(row)
            if cache is None:
                raise KeyError(f"profile doc cache not found: {profile_key}")
            return cache
```

Expose methods in `SQLiteStore`.

- [ ] **Step 5: Create renderer module**

Create `src/agent_bridge/capabilities/profile_docs.py`:

```python
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_profile_markdown(summary: dict[str, Any], manual_notes: str) -> str:
    lines = [
        f"# Agent Bridge Profile: {summary['profile_name']}",
        "",
        "## How To Use Agent Bridge",
        "",
        "- Use `search` to discover allowed MCP services and tools.",
        "- Use `execute` to call an allowed MCP tool when no direct pinned tool exists.",
        "- Use code repository capabilities for code search, repository overview, and impact exploration.",
        "- Use knowledge base capabilities for document search and question answering.",
        "- Some high-frequency capabilities may be exposed directly as `pin_*` tools by this profile.",
        "",
        "## Available MCP Services",
        "",
    ]
    services = summary.get("services", [])
    lines.extend([f"- {svc['name']} (`{svc['service_key']}`)" for svc in services] or ["- None"])
    lines.extend(["", "## Available Code Repositories", ""])
    repos = summary.get("code_repositories", [])
    lines.extend([f"- {repo['name']} (`{repo['repo_key']}`)" for repo in repos] or ["- None"])
    lines.extend(["", "## Available Knowledge Bases", ""])
    kbs = summary.get("knowledge_bases", [])
    lines.extend([f"- {kb['name']} (`{kb['slug']}`)" for kb in kbs] or ["- None"])
    lines.extend(["", "## Manual Notes", ""])
    lines.append(manual_notes.strip() or "No manual notes.")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 6: Add governance renderer methods**

In `CapabilityGovernanceService`, add:

```python
    def update_profile_manual_notes(self, actor: str, profile_key: str, manual_notes: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        self.store.upsert_profile_manual_notes(profile_key, manual_notes)
        return self.render_profile_markdown(actor, profile_key)

    def render_profile_markdown(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        from agent_bridge.capabilities.profile_docs import render_profile_markdown, stable_hash

        allowed = set(
            self.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.mcp_service.value,
                source_keys=[svc["service_key"] for svc in self.store.list_mcp_services()],
            )
        )
        services = [
            {"service_key": svc["service_key"], "name": svc["name"]}
            for svc in self.store.list_mcp_services()
            if svc["service_key"] in allowed and svc["status"] == "enabled"
        ]
        resource_rules = self.store.list_profile_resource_rules(profile_key)
        repo_keys = {rule["resource_key"] for rule in resource_rules if rule["resource_type"] == "code_repo"}
        kb_keys = {rule["resource_key"] for rule in resource_rules if rule["resource_type"] == "wiki_kb"}
        repos = [
            {"repo_key": repo["repo_key"], "name": repo["name"]}
            for repo in self.store.list_code_repositories()
            if repo["repo_key"] in repo_keys
        ]
        kbs = [
            {"slug": kb["slug"], "name": kb["name"]}
            for kb in self.store.list_kbs()
            if kb["slug"] in kb_keys
        ]
        cache = self.store.get_profile_doc_cache(profile_key) or {}
        manual_notes = str(cache.get("manual_notes") or "")
        summary = {
            "profile_key": profile_key,
            "profile_name": profile["name"],
            "services": services,
            "code_repositories": repos,
            "knowledge_bases": kbs,
        }
        markdown = render_profile_markdown(summary, manual_notes)
        auto_hash = stable_hash(summary)
        rendered_hash = stable_hash({"summary": summary, "manual_notes": manual_notes})
        self.store.upsert_profile_rendered_doc(
            profile_key=profile_key,
            manual_notes=manual_notes,
            auto_summary=summary,
            auto_summary_hash=auto_hash,
            rendered_hash=rendered_hash,
            markdown=markdown,
            mark_written=False,
        )
        return {"profile_key": profile_key, "markdown": markdown, "rendered_hash": rendered_hash}
```

- [ ] **Step 7: Run profile doc tests**

Run:

```bash
uv run pytest tests/test_profile_docs.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit profile doc rendering**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/governance.py src/agent_bridge/storage/sqlite.py src/agent_bridge/capabilities/profile_docs.py src/agent_bridge/capabilities/governance.py tests/test_profile_docs.py
git commit -m "feat: render profile guidance docs"
```

## Task 7: Update CLI Profile Use, Refresh, And Server Name

**Files:**
- Modify: `src/agent_bridge/cli/app.py`
- Modify: `src/agent_bridge/cli/profile.py`
- Modify: `src/agent_bridge/client.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add CLI tests for server name and pointer files**

Modify `tests/test_cli.py` existing profile use tests and append:

```python
def test_profile_use_writes_agent_bridge_server_and_profile_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            return {"markdown": "# Agent Bridge Profile: Safe\n", "rendered_hash": "abc"}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp"],
    )

    assert result.exit_code == 0
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "agent-bridge" in data["mcpServers"]
    assert "agent-capability-hub" not in data["mcpServers"]
    profile_file = tmp_path / ".agent-bridge" / "profiles" / "safe.md"
    assert profile_file.read_text(encoding="utf-8") == "# Agent Bridge Profile: Safe\n"
    assert f"@{profile_file}" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert str(profile_file) in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run CLI test and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_profile_use_writes_agent_bridge_server_and_profile_files -v
```

Expected: FAIL because server name and profile files are not implemented.

- [ ] **Step 3: Add client methods**

In `src/agent_bridge/client.py`, add:

```python
    def render_profile_doc(self, profile_key: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/capability-profiles/{profile_key}/doc/render",
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()

    def refresh_profile_pin_cache(self, profile_key: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/capability-profiles/{profile_key}/pins/refresh",
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()
```

Add the matching `/doc/render` route in Task 8 if it is not yet present.

- [ ] **Step 4: Rename MCP server entry helper**

In `src/agent_bridge/cli/app.py`, change `_with_metamcp_config()` to write `"agent-bridge"`:

```python
def _with_metamcp_config(existing: dict[str, Any], url: str, profile: str) -> dict[str, Any]:
    config = dict(existing)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["agent-bridge"] = {
        "type": "http",
        "url": url,
        "headers": {"X-Agent-Bridge-MetaMCP-Profile": profile},
    }
    config["mcpServers"] = servers
    return config
```

Update `_confirm_overwrite()` to check `"agent-bridge"` and retain backwards compatibility by also warning if `"agent-capability-hub"` exists.

- [ ] **Step 5: Add pointer file helpers**

In `src/agent_bridge/cli/profile.py`, add:

```python
def _profile_doc_path(scope: str, profile: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".agent-bridge" / "profiles" / f"{profile}.md"
    if scope == "user":
        return Path.home() / ".agent-bridge" / "profiles" / f"{profile}.md"
    raise ValueError("scope must be project or user")


def _pointer_paths(scope: str) -> tuple[Path, Path]:
    if scope == "project":
        return Path.cwd() / "CLAUDE.md", Path.cwd() / "AGENTS.md"
    if scope == "user":
        return Path.home() / ".claude" / "CLAUDE.md", Path.home() / ".codex" / "AGENTS.md"
    raise ValueError("scope must be project or user")


def _replace_agent_bridge_block(path: Path, block: str) -> None:
    start = "<!-- agent-bridge:profile-pointer start -->"
    end = "<!-- agent-bridge:profile-pointer end -->"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == start:
            skipping = True
            continue
        if line.strip() == end:
            skipping = False
            continue
        if not skipping:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend([start, block.rstrip(), end])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
```

- [ ] **Step 6: Update profile use**

In `profile_use()`, after writing `.mcp.json`, call the client and write files:

```python
        from agent_bridge.cli.app import _run_client

        rendered = _run_client(lambda client: client.render_profile_doc(profile))
        profile_path = _profile_doc_path(resolved_scope, profile)
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(rendered["markdown"], encoding="utf-8")
        claude_path, agents_path = _pointer_paths(resolved_scope)
        _replace_agent_bridge_block(claude_path, f"@{profile_path.resolve()}")
        _replace_agent_bridge_block(
            agents_path,
            f"Read the active Agent Bridge profile before using agent-bridge capabilities: {profile_path.resolve()}",
        )
```

- [ ] **Step 7: Add refresh commands**

In `src/agent_bridge/cli/profile.py`, add:

```python
@profile_app.command("refresh")
def profile_refresh(
    profile: Annotated[str, typer.Argument(help="Profile 标识")],
    scope: Annotated[str, typer.Option("--scope", help="配置范围: project 或 user")] = "project",
) -> None:
    from agent_bridge.cli.app import _run_client

    rendered = _run_client(lambda client: client.render_profile_doc(profile))
    profile_path = _profile_doc_path(scope, profile)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(rendered["markdown"], encoding="utf-8")
    typer.echo(f"已刷新: {profile_path}")


pins_app = typer.Typer(help="管理 Profile Pin", no_args_is_help=True)
profile_app.add_typer(pins_app, name="pins")


@pins_app.command("refresh")
def profile_pins_refresh(profile: Annotated[str, typer.Argument(help="Profile 标识")]) -> None:
    from agent_bridge.cli.app import _run_client

    result = _run_client(lambda client: client.refresh_profile_pin_cache(profile))
    typer.echo(f"profile: {result['profile_key']} 自动 Pin 缓存已清理")
```

- [ ] **Step 8: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS after updating existing assertions from `agent-capability-hub` to `agent-bridge`.

- [ ] **Step 9: Commit CLI updates**

```bash
git add src/agent_bridge/cli/app.py src/agent_bridge/cli/profile.py src/agent_bridge/client.py tests/test_cli.py
git commit -m "feat: write profile guidance files"
```

## Task 8: Add Profile Doc API Endpoints

**Files:**
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/governance.py`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_capability_api.py`:

```python
def test_profile_doc_api_render_and_notes(client, wm_paths) -> None:
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe", description="", status="active", created_by="root")

    notes = client.put(
        "/capability-profiles/safe/doc/manual-notes",
        json={"manual_notes": "Manual policy"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert notes.status_code == 200
    assert "Manual policy" in notes.json()["markdown"]

    rendered = client.post(
        "/capability-profiles/safe/doc/render",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert rendered.status_code == 200
    assert "# Agent Bridge Profile: Safe" in rendered.json()["markdown"]
```

- [ ] **Step 2: Run API test and verify failure**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_profile_doc_api_render_and_notes -v
```

Expected: FAIL with 404.

- [ ] **Step 3: Add schema**

In `src/agent_bridge/api/schemas.py`, add:

```python
class ProfileManualNotesRequest(BaseModel):
    manual_notes: str = ""
```

- [ ] **Step 4: Add routes**

In `src/agent_bridge/api/routes/governance.py`, add:

```python
    @router.post("/capability-profiles/{profile_key}/doc/render")
    def render_profile_doc(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.render_profile_markdown(current_actor, profile_key))

    @router.put("/capability-profiles/{profile_key}/doc/manual-notes")
    def update_profile_manual_notes(profile_key: str, payload: ProfileManualNotesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.update_profile_manual_notes(current_actor, profile_key, payload.manual_notes))
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_profile_doc_api_render_and_notes -v
```

Expected: PASS.

- [ ] **Step 6: Commit doc endpoints**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/governance.py tests/test_capability_api.py
git commit -m "feat: expose profile doc api"
```

## Task 9: Update Frontend API Types And Profile Configuration UI

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/capabilities/ProfilesView.vue`

- [ ] **Step 1: Add TypeScript types**

In `frontend/capabilities/src/api/types.ts`, add:

```ts
export interface ProfilePinRule {
  service_key: string
  tool_type: string
  source?: 'manual' | 'auto'
  calls?: number
}

export interface ProfilePinSettings {
  mode: 'disabled' | 'ratio' | 'count'
  ratio_percent: number | null
  count: number | null
  auto_cache_computed_at?: string | null
}

export interface ProfilePinnedTool {
  generated_tool_name: string
  service_key: string
  service_name: string
  tool_name: string
  tool_type: string
  source: 'manual' | 'auto'
}

export interface ProfilePinPreview {
  profile_key: string
  settings: ProfilePinSettings
  groups: ProfilePinRule[]
  tools: ProfilePinnedTool[]
}

export interface ProfileDocRender {
  profile_key: string
  markdown: string
  rendered_hash: string
}
```

- [ ] **Step 2: Add frontend client methods**

In `frontend/capabilities/src/api/client.ts`, import the new types and add:

```ts
  getProfilePins: (key: string) => get<ProfilePinPreview>(`/capability-profiles/${key}/pins`),
  replaceProfilePins: (key: string, pins: ProfilePinRule[]) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins`, { pins }),
  updateProfilePinSettings: (key: string, settings: Partial<ProfilePinSettings>) =>
    put<ProfilePinPreview>(`/capability-profiles/${key}/pins/settings`, settings),
  refreshProfilePins: (key: string) =>
    post<ProfilePinPreview>(`/capability-profiles/${key}/pins/refresh`),
  renderProfileDoc: (key: string) =>
    post<ProfileDocRender>(`/capability-profiles/${key}/doc/render`),
  updateProfileManualNotes: (key: string, manual_notes: string) =>
    put<ProfileDocRender>(`/capability-profiles/${key}/doc/manual-notes`, { manual_notes }),
```

- [ ] **Step 3: Extend Profile config state**

In `ProfilesView.vue`, add refs:

```ts
const pinPreview = ref<ProfilePinPreview | null>(null)
const pendingPins = ref<ProfilePinRule[]>([])
const pinMode = ref<'disabled' | 'ratio' | 'count'>('disabled')
const pinRatio = ref(10)
const pinCount = ref(3)
const pinSaving = ref(false)
const profileMarkdown = ref('')
const manualNotes = ref('')
```

Load `api.getProfilePins(p.profile_key)` and `api.renderProfileDoc(p.profile_key)` inside `openConfig()`.

- [ ] **Step 4: Add Profile UI controls**

In the config dialog template, add a `Pinned Tools` section near service allow rules:

```vue
<div class="space-y-3">
  <h3 class="text-sm font-semibold">Pinned Tools</h3>
  <div class="grid gap-2">
    <div v-for="pin in pendingPins" :key="`${pin.service_key}:${pin.tool_type}`" class="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
      <span>{{ pin.service_key }} / {{ typeLabel(pin.tool_type) }}</span>
      <Button variant="ghost" size="sm" @click="pendingPins = pendingPins.filter(p => !(p.service_key === pin.service_key && p.tool_type === pin.tool_type))">移除</Button>
    </div>
  </div>
  <div class="flex flex-wrap gap-2">
    <Select v-model="pinMode">
      <SelectTrigger class="w-[140px]"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="disabled">关闭自动</SelectItem>
        <SelectItem value="ratio">按比例</SelectItem>
        <SelectItem value="count">按数量</SelectItem>
      </SelectContent>
    </Select>
    <Input v-if="pinMode === 'ratio'" v-model="pinRatio" type="number" class="w-[100px]" />
    <Input v-if="pinMode === 'count'" v-model="pinCount" type="number" class="w-[100px]" />
    <Button variant="outline" size="sm" @click="refreshPins">重新计算自动 Pin</Button>
  </div>
  <div class="rounded-md bg-muted p-3 text-xs text-muted-foreground">
    当前会暴露 {{ pinPreview?.tools.length || 0 }} 个 pin_* 工具。
  </div>
</div>
```

Use existing `toolTypes`, `typeLabel()`, and service lists where possible.

- [ ] **Step 5: Add save and refresh methods**

In `ProfilesView.vue`, add:

```ts
async function savePins() {
  if (!configProfile.value) return
  pinSaving.value = true
  try {
    await api.replaceProfilePins(configProfile.value.profile_key, pendingPins.value)
    pinPreview.value = await api.updateProfilePinSettings(configProfile.value.profile_key, {
      mode: pinMode.value,
      ratio_percent: pinMode.value === 'ratio' ? Number(pinRatio.value) : null,
      count: pinMode.value === 'count' ? Number(pinCount.value) : null,
    })
  } finally {
    pinSaving.value = false
  }
}

async function refreshPins() {
  if (!configProfile.value) return
  pinPreview.value = await api.refreshProfilePins(configProfile.value.profile_key)
}
```

Call `savePins()` inside existing `saveConfig()` after profile rules/resources save.

- [ ] **Step 6: Run frontend build**

Run:

```bash
cd frontend/capabilities && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit frontend profile pin UI**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/capabilities/ProfilesView.vue
git commit -m "feat: add profile pin ui"
```

## Task 10: Extend Stats UI For Service + Level Trends

**Files:**
- Modify: `frontend/capabilities/src/views/monitoring/StatsView.vue`
- Modify: `src/agent_bridge/api/routes/governance.py`
- Test: `tests/test_capability_stats.py`

- [ ] **Step 1: Add API regression test for dimensions**

Append to `tests/test_capability_stats.py`:

```python
def test_governance_stats_accepts_tool_type_dimension(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityGovernanceService(store=store, admins={"root"})

    result = service.stats(
        actor="root",
        dimensions=["source_key", "tool_type"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    assert result["dimensions"] == ["source_key", "tool_type"]
    assert result["items"] == []
```

- [ ] **Step 2: Run stats test and verify failure if needed**

Run:

```bash
uv run pytest tests/test_capability_stats.py::test_governance_stats_accepts_tool_type_dimension -v
```

Expected: PASS if Task 3 already updated stats validation; otherwise FAIL and update `_validate_stats_dimensions`.

- [ ] **Step 3: Ensure governance stats validation allows tool_type**

If failing, add `"tool_type"` to stats dimension validation in `CapabilityGovernanceService.stats()` or any helper it uses.

- [ ] **Step 4: Update StatsView dimension tabs**

In `frontend/capabilities/src/views/monitoring/StatsView.vue`, add:

```ts
const dimensions = [
  { key: 'profile_key,source_key,tool_name', label: '全部维度' },
  { key: 'source_key', label: '按服务' },
  { key: 'source_key,tool_type', label: '按服务+层级' },
  { key: 'source_key,tool_name', label: '按工具' },
  { key: 'profile_key', label: '按 Profile' },
]
```

Add label mapping:

```ts
tool_type: '层级',
```

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd frontend/capabilities && npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit stats UI**

```bash
git add frontend/capabilities/src/views/monitoring/StatsView.vue src/agent_bridge/api/routes/governance.py tests/test_capability_stats.py
git commit -m "feat: show service level stats"
```

## Task 11: Final Integration Verification

**Files:**
- Modify only if tests reveal defects.

- [ ] **Step 1: Run focused backend suite**

Run:

```bash
uv run pytest tests/test_profile_pins.py tests/test_profile_docs.py tests/test_metamcp_http_gateway.py tests/test_capability_api.py tests/test_capability_stats.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full backend suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
cd frontend/capabilities && npm run build
```

Expected: PASS.

- [ ] **Step 4: Manual smoke test with local server**

Run:

```bash
uv run agent-bridge server start
uv run agent-bridge server init
```

Open:

```text
http://127.0.0.1:8765/admin/capabilities
```

Smoke path:

1. Create or open a profile.
2. Allow an MCP service.
3. Set a tool type to `search`.
4. Add a manual pin for that service + level.
5. Verify current preview shows `pin_<service>_<tool>`.
6. Run `uv run agent-bridge profile use <profile> --scope project --url http://127.0.0.1:8765/mcp`.
7. Verify `.mcp.json` has `agent-bridge`.
8. Verify `.agent-bridge/profiles/<profile>.md`, `CLAUDE.md`, and `AGENTS.md` are written.

- [ ] **Step 5: Stop local server**

Run:

```bash
uv run agent-bridge server stop
```

Expected: server stops cleanly.

- [ ] **Step 6: Commit any integration fixes**

If fixes were needed:

```bash
git add <changed-files>
git commit -m "fix: stabilize profile pin integration"
```

If no fixes were needed, do not create an empty commit.

## Spec Coverage Checklist

- Profile-scoped manual pin rules: Tasks 1, 2, 4, 9.
- Automatic pin by ratio/count with 30-day usage: Tasks 2, 3, 4, 9.
- Direct MCP `pin_*` tools: Task 5.
- Profile UI support: Task 9.
- Stats support for service, service + level, and tool: Tasks 3, 10.
- Dynamic profile markdown and pointer files: Tasks 6, 7, 8.
- MCP server name `agent-bridge`: Task 7.
- Policy boundaries and read-only tool filtering: Tasks 2, 3, 5.
- Testing and verification: Tasks 1-11.
