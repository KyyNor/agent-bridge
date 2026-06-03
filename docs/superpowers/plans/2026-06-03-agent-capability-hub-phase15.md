# Agent Capability Hub Phase 1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Phase 1.5 governance loop: generic tool call logs, Project Profile service-level policy, profile-enforced MetaMCP search/execute, Chinese management pages, and Claude Code connection CLI.

**Architecture:** Keep Phase 1 MCP registry tables as the MCP asset source, and add generic governance tables that can also support OpenAPI or CLI tools later. Put policy and logging in service-layer code so pages and CLI never become the enforcement boundary. Extend the existing FastAPI app and static Chinese control console without introducing a frontend build chain.

**Tech Stack:** Python 3.11, FastAPI, SQLite, `mcp` Python SDK, `httpx`, Typer, vanilla HTML/CSS/JS, pytest, FastAPI `TestClient`.

---

## Source Documents

- Product design: `docs/agent-capability-hub-phase15-governance-design.html`
- Phase 1 implementation plan: `docs/superpowers/plans/2026-06-02-agent-capability-hub-phase1.md`
- Current Phase 1 files:
  - `src/wiki_manager/capability_service.py`
  - `src/wiki_manager/storage.py`
  - `src/wiki_manager/server.py`
  - `src/wiki_manager/mcp_server.py`
  - `src/wiki_manager/web_pages.py`
  - `src/wiki_manager/static/capabilities/app.css`
  - `src/wiki_manager/static/capabilities/app.js`
- Visual reference: `docs/design/`
  - Use the design system, page structure, colors, spacing, badges, side navigation, tables, forms, and interaction density.
  - Do not copy fictional metrics, links, or workflows from the design drafts unless they are part of this Phase 1.5 plan.

## File Structure

- Modify `src/wiki_manager/capabilities.py`
  - Add enums/dataclasses for profile rules, source types, call log status, and policy context.
- Modify `src/wiki_manager/storage.py`
  - Add generic governance tables and storage methods:
    - `tool_call_logs`
    - `project_profiles`
    - `profile_source_rules`
- Create `src/wiki_manager/capability_governance.py`
  - Profile CRUD, service-level policy evaluation, allow/deny filtering, log writing and querying.
- Modify `src/wiki_manager/capability_service.py`
  - Route `search` and `execute` through governance policy and log every MetaMCP call.
- Modify `src/wiki_manager/mcp_server.py`
  - Accept profile context, return `log_id`, and keep SDK tests working with injected fake services.
- Modify `src/wiki_manager/server.py`
  - Add governance APIs, catalog APIs, log APIs, Chinese page routes, and a small `/mcp` HTTP gateway endpoint for Claude Code connection tests.
- Modify `src/wiki_manager/web_pages.py`
  - Replace the English one-page shell with a Chinese control-console shell.
- Modify `src/wiki_manager/static/capabilities/app.css`
  - Keep the Phase 1 visual system and add dense Chinese dashboard layouts.
- Modify `src/wiki_manager/static/capabilities/app.js`
  - Add Chinese navigation, catalog, details, logs, profile, rules, and Claude Code config interactions.
- Modify `src/wiki_manager/client.py`
  - Add client methods for profile APIs and Claude Code connection helper endpoints.
- Modify `src/wiki_manager/cli.py`
  - Add `wiki metamcp ...` command group.
- Create `tests/test_capability_governance_storage.py`
  - Storage coverage for logs, profiles, and rules.
- Create `tests/test_capability_governance.py`
  - Service-level policy and logging behavior.
- Modify `tests/test_capability_service.py`
  - Search/execute log IDs and policy enforcement.
- Modify `tests/test_mcp_server.py`
  - Profile-aware MetaMCP search/execute tests.
- Modify `tests/test_capability_api.py`
  - Governance APIs, catalog APIs, logs APIs, and Chinese page assertions.
- Modify `tests/test_cli.py`
  - `wiki metamcp` commands and config-writing behavior.
- Modify `README.md`
  - Add Phase 1.5 usage for profiles, logs, and Claude Code connection.

## Task 1: Generic Governance Storage

**Files:**
- Modify: `src/wiki_manager/capabilities.py`
- Modify: `src/wiki_manager/storage.py`
- Test: `tests/test_capability_governance_storage.py`
- Test: `tests/test_capability_storage.py`

- [ ] **Step 1: Add failing storage tests**

Create `tests/test_capability_governance_storage.py`:

```python
from __future__ import annotations

import json

from wiki_manager.capabilities import CallLogStatus, ProfileRuleEffect, SourceType
from wiki_manager.storage import SQLiteStore


def test_project_profile_and_rules_round_trip(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    profile = store.upsert_project_profile(
        profile_key="safe-readonly",
        name="安全只读",
        description="只允许项目访问安全的查询服务。",
        status="active",
        created_by="root",
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "mysql",
                "effect": ProfileRuleEffect.allow.value,
            },
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "hive",
                "effect": ProfileRuleEffect.deny.value,
            },
        ],
    )

    assert profile["profile_key"] == "safe-readonly"
    assert store.get_project_profile("safe-readonly")["name"] == "安全只读"

    listed = store.list_project_profiles()
    assert [item["profile_key"] for item in listed] == ["safe-readonly"]
    assert listed[0]["allow_count"] == 1
    assert listed[0]["deny_count"] == 1

    rules = store.list_profile_source_rules("safe-readonly")
    assert [(rule["source_key"], rule["effect"]) for rule in rules] == [
        ("hive", ProfileRuleEffect.deny.value),
        ("mysql", ProfileRuleEffect.allow.value),
    ]


def test_tool_call_log_round_trip_and_filters(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    log = store.create_tool_call_log(
        log_id="call_20260603_153012_ab12",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={"service": "mysql", "tool": "query_sql", "arguments": {"sql": "select 1"}},
        response={"rows": [{"id": 1}]},
        status=CallLogStatus.success.value,
        error_message=None,
        duration_ms=42,
    )

    assert log["log_id"] == "call_20260603_153012_ab12"
    assert json.loads(log["request_json"])["arguments"]["sql"] == "select 1"

    listed = store.list_tool_call_logs(profile_key="safe-readonly", status="success")
    assert [item["log_id"] for item in listed] == ["call_20260603_153012_ab12"]
    assert listed[0]["source_key"] == "mysql"

    detail = store.get_tool_call_log("call_20260603_153012_ab12")
    assert detail is not None
    assert json.loads(detail["response_json"])["rows"][0]["id"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_governance_storage.py -v
```

Expected: FAIL with missing enums and missing `SQLiteStore` methods.

- [ ] **Step 3: Add governance enums and dataclasses**

Modify `src/wiki_manager/capabilities.py` by appending:

```python
class SourceType(str, Enum):
    mcp_service = "mcp_service"


class ProfileRuleEffect(str, Enum):
    allow = "allow"
    deny = "deny"


class CallLogStatus(str, Enum):
    success = "success"
    error = "error"
    blocked = "blocked"


@dataclass(frozen=True)
class PolicyContext:
    actor: str
    profile_key: str | None = None
    allow_sources: set[str] | None = None
    deny_sources: set[str] | None = None
    request_id: str | None = None
    entrypoint: str = "metamcp_search"
```

- [ ] **Step 4: Extend SQLite schema**

Modify `SCHEMA` in `src/wiki_manager/storage.py` by appending these tables after `mcp_tools`:

```sql
CREATE TABLE IF NOT EXISTS project_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profile_source_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  effect TEXT NOT NULL CHECK(effect IN ('allow', 'deny')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(profile_key, source_type, source_key, effect)
);

CREATE TABLE IF NOT EXISTS tool_call_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  log_id TEXT NOT NULL UNIQUE,
  actor TEXT NOT NULL,
  profile_key TEXT,
  entrypoint TEXT NOT NULL,
  source_type TEXT,
  source_key TEXT,
  tool_name TEXT,
  request_json TEXT NOT NULL DEFAULT '{}',
  response_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  error_message TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_profile ON tool_call_logs(profile_key);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_source ON tool_call_logs(source_type, source_key);
```

- [ ] **Step 5: Add profile storage methods**

Add these methods to `SQLiteStore` near the MCP methods:

```python
    def upsert_project_profile(
        self,
        *,
        profile_key: str,
        name: str,
        description: str,
        status: str,
        created_by: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_profiles (profile_key, name, description, status, created_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, name, description, status, created_by),
            )
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            profile = _row_to_dict(row)
            if profile is None:
                raise KeyError(f"profile not found: {profile_key}")
            return profile

    def get_project_profile(self, profile_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            return _row_to_dict(row)

    def list_project_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  profile.*,
                  SUM(CASE WHEN rule.effect = 'allow' THEN 1 ELSE 0 END) AS allow_count,
                  SUM(CASE WHEN rule.effect = 'deny' THEN 1 ELSE 0 END) AS deny_count
                FROM project_profiles profile
                LEFT JOIN profile_source_rules rule ON rule.profile_key = profile.profile_key
                GROUP BY profile.id
                ORDER BY profile.profile_key
                """
            ).fetchall()
            return [
                {
                    **dict(row),
                    "allow_count": int(row["allow_count"] or 0),
                    "deny_count": int(row["deny_count"] or 0),
                }
                for row in rows
            ]

    def replace_profile_source_rules(self, profile_key: str, rules: list[dict[str, str]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM profile_source_rules WHERE profile_key = ?", (profile_key,))
            for rule in rules:
                conn.execute(
                    """
                    INSERT INTO profile_source_rules (profile_key, source_type, source_key, effect)
                    VALUES (?, ?, ?, ?)
                    """,
                    (profile_key, rule["source_type"], rule["source_key"], rule["effect"]),
                )

    def list_profile_source_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM profile_source_rules
                WHERE profile_key = ?
                ORDER BY source_key, effect
                """,
                (profile_key,),
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 6: Add log storage methods**

Add these methods to `SQLiteStore`:

```python
    def create_tool_call_log(
        self,
        *,
        log_id: str,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None,
        source_key: str | None,
        tool_name: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        error_message: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_call_logs (
                  log_id, actor, profile_key, entrypoint, source_type, source_key,
                  tool_name, request_json, response_json, status, error_message, duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    actor,
                    profile_key,
                    entrypoint,
                    source_type,
                    source_key,
                    tool_name,
                    json.dumps(request, ensure_ascii=False, default=str),
                    json.dumps(response, ensure_ascii=False, default=str),
                    status,
                    error_message,
                    duration_ms,
                ),
            )
            row = conn.execute("SELECT * FROM tool_call_logs WHERE log_id = ?", (log_id,)).fetchone()
            log = _row_to_dict(row)
            if log is None:
                raise KeyError(f"tool call log not found: {log_id}")
            return log

    def list_tool_call_logs(
        self,
        *,
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in [
            ("entrypoint", entrypoint),
            ("source_type", source_type),
            ("source_key", source_key),
            ("tool_name", tool_name),
            ("profile_key", profile_key),
            ("status", status),
        ]:
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tool_call_logs
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_tool_call_log(self, log_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tool_call_logs WHERE log_id = ?", (log_id,)).fetchone()
            return _row_to_dict(row)
```

- [ ] **Step 7: Run storage tests**

Run:

```bash
uv run pytest tests/test_capability_governance_storage.py tests/test_capability_storage.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/wiki_manager/capabilities.py src/wiki_manager/storage.py tests/test_capability_governance_storage.py
git commit -m "feat: add governance storage for tool calls and profiles"
```

## Task 2: Governance Service for Policy and Logs

**Files:**
- Create: `src/wiki_manager/capability_governance.py`
- Test: `tests/test_capability_governance.py`
- Related: `src/wiki_manager/capabilities.py`

- [ ] **Step 1: Add failing governance service tests**

Create `tests/test_capability_governance.py`:

```python
from __future__ import annotations

import json

import pytest

from wiki_manager.capabilities import CallLogStatus, SourceType
from wiki_manager.capability_governance import CapabilityGovernanceService
from wiki_manager.domain import AccessDenied, NotFound, ValidationError
from wiki_manager.storage import SQLiteStore


def _service(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return CapabilityGovernanceService(store=store, admins={"root"}), store


def test_profile_crud_requires_admin_and_lists_rules(wm_paths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(AccessDenied):
        service.upsert_profile("alice", "safe-readonly", "安全只读", "", "active")

    profile = service.upsert_profile("root", "safe-readonly", "安全只读", "只读项目", "active")
    service.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
            {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
        ],
    )

    assert profile["profile_key"] == "safe-readonly"
    detail = service.get_profile("root", "safe-readonly")
    assert detail["name"] == "安全只读"
    assert [(rule["source_key"], rule["effect"]) for rule in detail["rules"]] == [
        ("hive", "deny"),
        ("mysql", "allow"),
    ]
    assert service.list_profiles("root")[0]["allow_count"] == 1


def test_policy_filters_sources_with_allow_and_deny(wm_paths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
            {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
        ],
    )

    visible = service.filter_source_keys(
        actor="root",
        profile_key="safe-readonly",
        source_type=SourceType.mcp_service.value,
        source_keys=["mysql", "hive", "wiki"],
    )

    assert visible == ["mysql"]
    assert service.is_source_allowed("root", "safe-readonly", "mcp_service", "mysql") is True
    assert service.is_source_allowed("root", "safe-readonly", "mcp_service", "hive") is False
    assert service.is_source_allowed("root", None, "mcp_service", "hive") is True


def test_unknown_profile_is_not_found(wm_paths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(NotFound, match="profile not found"):
        service.filter_source_keys(
            actor="root",
            profile_key="missing",
            source_type="mcp_service",
            source_keys=["mysql"],
        )


def test_write_and_read_tool_call_log_payloads(wm_paths) -> None:
    service, _store = _service(wm_paths)

    log = service.log_tool_call(
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_search",
        source_type=None,
        source_key=None,
        tool_name="search",
        request={"query": "mysql"},
        response={"items": []},
        status=CallLogStatus.success.value,
        error_message=None,
        duration_ms=3,
    )

    assert log["log_id"].startswith("call_")
    listed = service.list_logs(actor="root", profile_key="safe-readonly")
    assert listed[0]["log_id"] == log["log_id"]
    detail = service.get_log(actor="root", log_id=log["log_id"])
    assert json.loads(detail["request_json"]) == {"query": "mysql"}
    assert json.loads(detail["response_json"]) == {"items": []}


def test_rule_validation_rejects_unknown_effect_and_source_type(wm_paths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")

    with pytest.raises(ValidationError, match="invalid rule effect"):
        service.replace_profile_rules(
            "root",
            "safe-readonly",
            [{"source_type": "mcp_service", "source_key": "mysql", "effect": "maybe"}],
        )

    with pytest.raises(ValidationError, match="invalid source type"):
        service.replace_profile_rules(
            "root",
            "safe-readonly",
            [{"source_type": "unknown", "source_key": "mysql", "effect": "allow"}],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_governance.py -v
```

Expected: FAIL with missing `capability_governance.py`.

- [ ] **Step 3: Implement governance service**

Create `src/wiki_manager/capability_governance.py`:

```python
"""Governance service for capability profiles, policy checks, and tool call logs."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from wiki_manager.capabilities import CallLogStatus, ProfileRuleEffect, SourceType
from wiki_manager.domain import NotFound, ValidationError, require_admin_user
from wiki_manager.storage import SQLiteStore


VALID_PROFILE_STATUSES = {"active", "disabled"}


def make_log_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"call_{stamp}_{uuid.uuid4().hex[:8]}"


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


class CapabilityGovernanceService:
    def __init__(self, *, store: SQLiteStore, admins: set[str]) -> None:
        self.store = store
        self.admins = admins

    def upsert_profile(
        self,
        actor: str,
        profile_key: str,
        name: str,
        description: str,
        status: str,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if status not in VALID_PROFILE_STATUSES:
            raise ValidationError("invalid profile status")
        return self.store.upsert_project_profile(
            profile_key=profile_key,
            name=name,
            description=description,
            status=status,
            created_by=actor,
        )

    def list_profiles(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_project_profiles()

    def get_profile(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return {**profile, "rules": self.store.list_profile_source_rules(profile_key)}

    def replace_profile_rules(self, actor: str, profile_key: str, rules: list[dict[str, str]]) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        normalized = [self._validate_rule(rule) for rule in rules]
        self.store.replace_profile_source_rules(profile_key, normalized)
        return self.get_profile(actor, profile_key)

    def filter_source_keys(
        self,
        *,
        actor: str,
        profile_key: str | None,
        source_type: str,
        source_keys: list[str],
    ) -> list[str]:
        if profile_key is None:
            return source_keys
        profile = self.store.get_project_profile(profile_key)
        if profile is None or profile.get("status") != "active":
            raise NotFound("profile not found")
        rules = self.store.list_profile_source_rules(profile_key)
        relevant = [rule for rule in rules if rule["source_type"] == source_type]
        allow = {rule["source_key"] for rule in relevant if rule["effect"] == ProfileRuleEffect.allow.value}
        deny = {rule["source_key"] for rule in relevant if rule["effect"] == ProfileRuleEffect.deny.value}
        result = [source_key for source_key in source_keys if not allow or source_key in allow]
        return [source_key for source_key in result if source_key not in deny]

    def is_source_allowed(
        self,
        actor: str,
        profile_key: str | None,
        source_type: str,
        source_key: str,
    ) -> bool:
        return source_key in self.filter_source_keys(
            actor=actor,
            profile_key=profile_key,
            source_type=source_type,
            source_keys=[source_key],
        )

    def log_tool_call(
        self,
        *,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None,
        source_key: str | None,
        tool_name: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        error_message: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        return self.store.create_tool_call_log(
            log_id=make_log_id(),
            actor=actor,
            profile_key=profile_key,
            entrypoint=entrypoint,
            source_type=source_type,
            source_key=source_key,
            tool_name=tool_name,
            request=request,
            response=response,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
        )

    def list_logs(
        self,
        *,
        actor: str,
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_tool_call_logs(
            entrypoint=entrypoint,
            source_type=source_type,
            source_key=source_key,
            tool_name=tool_name,
            profile_key=profile_key,
            status=status,
            limit=limit,
            offset=offset,
        )

    def get_log(self, *, actor: str, log_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        log = self.store.get_tool_call_log(log_id)
        if log is None:
            raise NotFound("tool call log not found")
        return log

    def _validate_rule(self, rule: dict[str, str]) -> dict[str, str]:
        try:
            source_type = SourceType(rule["source_type"]).value
        except (KeyError, ValueError) as exc:
            raise ValidationError("invalid source type") from exc
        try:
            effect = ProfileRuleEffect(rule["effect"]).value
        except (KeyError, ValueError) as exc:
            raise ValidationError("invalid rule effect") from exc
        source_key = str(rule.get("source_key") or "").strip()
        if not source_key:
            raise ValidationError("source_key is required")
        return {"source_type": source_type, "source_key": source_key, "effect": effect}
```

- [ ] **Step 4: Run governance tests**

Run:

```bash
uv run pytest tests/test_capability_governance.py tests/test_capability_governance_storage.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/wiki_manager/capability_governance.py tests/test_capability_governance.py
git commit -m "feat: add capability governance service"
```

## Task 3: Profile-Aware Capability Search and Execute

**Files:**
- Modify: `src/wiki_manager/capability_service.py`
- Modify: `src/wiki_manager/services.py`
- Modify: `tests/test_capability_service.py`

- [ ] **Step 1: Add failing capability policy tests**

Append to `tests/test_capability_service.py`:

```python
def test_search_filters_services_and_tools_by_profile(wm_paths: WikiManagerPaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "mysql", "MySQL", "https://mysql.test/mcp", {}, "SQL service", ["db"])
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    asyncio.run(service.sync_tools("root", "mysql"))
    asyncio.run(service.sync_tools("root", "hive"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    root = service.search("root", None, None, profile_key="safe-readonly")
    hive_tools = service.search("root", "hive", None, profile_key="safe-readonly")
    mysql_tools = service.search("root", "mysql", None, profile_key="safe-readonly")

    assert [item["service"] for item in root["items"]] == ["mysql"]
    assert "log_id" in root
    assert hive_tools["items"] == []
    assert hive_tools["log_id"].startswith("call_")
    assert [item["service"] for item in mysql_tools["items"]] == ["mysql"]


def test_execute_blocked_by_profile_writes_log_and_does_not_call_client(wm_paths: WikiManagerPaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    asyncio.run(service.sync_tools("root", "hive"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    with pytest.raises(ValidationError, match="source is blocked by profile policy"):
        asyncio.run(service.execute("root", "hive", "query_sql", {"sql": "select 1"}, profile_key="safe-readonly"))

    assert client.calls == []
    logs = service.governance.list_logs(actor="root", status="blocked")
    assert logs[0]["source_key"] == "hive"
    assert logs[0]["tool_name"] == "query_sql"


def test_execute_success_returns_log_id(wm_paths: WikiManagerPaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "mysql", "MySQL", "https://mysql.test/mcp", {}, "SQL service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    client.call_result = {"structured": {"rows": [{"id": 1}]}, "is_error": False, "content": []}
    asyncio.run(service.sync_tools("root", "mysql"))

    result = asyncio.run(service.execute("root", "mysql", "query_sql", {"sql": "select 1"}))

    assert result["success"] is True
    assert result["log_id"].startswith("call_")
    detail = service.governance.get_log(actor="root", log_id=result["log_id"])
    assert detail["entrypoint"] == "metamcp_execute"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_service.py::test_search_filters_services_and_tools_by_profile tests/test_capability_service.py::test_execute_blocked_by_profile_writes_log_and_does_not_call_client tests/test_capability_service.py::test_execute_success_returns_log_id -v
```

Expected: FAIL because `CapabilityService` does not accept `profile_key` and has no governance service.

- [ ] **Step 3: Wire governance into `WikiManagerService`**

Modify imports in `src/wiki_manager/services.py`:

```python
from wiki_manager.capability_governance import CapabilityGovernanceService
```

Modify `WikiManagerService.__init__`:

```python
        self.governance = CapabilityGovernanceService(store=store, admins=admins)
        self.capabilities = CapabilityService(store=store, admins=admins, governance=self.governance)
```

- [ ] **Step 4: Update CapabilityService constructor**

Modify `src/wiki_manager/capability_service.py` imports:

```python
from wiki_manager.capabilities import CallLogStatus, McpServiceStatus, SourceType, ToolType
from wiki_manager.capability_governance import CapabilityGovernanceService, monotonic_ms
```

Modify constructor:

```python
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        mcp_client: McpHttpClient | None = None,
        governance: CapabilityGovernanceService | None = None,
    ) -> None:
        self.store = store
        self.admins = admins
        self.mcp_client = mcp_client or McpHttpClient()
        self.governance = governance or CapabilityGovernanceService(store=store, admins=admins)
```

- [ ] **Step 5: Make search profile-aware and logged**

Replace `CapabilityService.search` with:

```python
    def search(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int = 20,
        profile_key: str | None = None,
    ) -> dict[str, Any]:
        started = monotonic_ms()
        request = {"path": path, "query": query, "limit": limit, "profile_key": profile_key}
        try:
            result = self._search_without_log(actor, path, query, limit, profile_key)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=(path or "").strip("/") or None,
                tool_name="search",
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=(path or "").strip("/") or None,
                tool_name="search",
                request=request,
                response={"error": str(exc)},
                status=CallLogStatus.error.value,
                error_message=str(exc),
                duration_ms=monotonic_ms() - started,
            )
            if hasattr(exc, "message"):
                exc.message = f"{exc.message} (log_id: {log['log_id']})"
            raise

    def _search_without_log(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int,
        profile_key: str | None,
    ) -> dict[str, Any]:
        normalized_path = (path or "").strip("/")
        if normalized_path == "":
            items = self._root_search_items(profile_key=profile_key, actor=actor)
            response_path = "/"
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

        if query:
            needle = query.lower()
            items = [item for item in items if needle in _json_text(item)]
        return {"path": response_path, "items": items[:limit]}
```

Then change `_root_search_items` signature and body:

```python
    def _root_search_items(self, *, profile_key: str | None = None, actor: str = "root") -> list[dict[str, Any]]:
        enabled_services = [
            service
            for service in self.store.list_mcp_services()
            if service["status"] == McpServiceStatus.enabled.value
        ]
        visible_keys = set(
            self.governance.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.mcp_service.value,
                source_keys=[service["service_key"] for service in enabled_services],
            )
        )
        enabled_services = [service for service in enabled_services if service["service_key"] in visible_keys]
        ...
```

Keep the existing tool-count logic after this filter.

- [ ] **Step 6: Make execute profile-aware and logged**

Replace `CapabilityService.execute` with:

```python
    async def execute(
        self,
        actor: str,
        service: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None = None,
    ) -> dict[str, Any]:
        started = monotonic_ms()
        request = {"service": service, "tool": tool, "arguments": arguments, "profile_key": profile_key}
        try:
            if not self.governance.is_source_allowed(actor, profile_key, SourceType.mcp_service.value, service):
                raise ValidationError("source is blocked by profile policy")
            result = await self._execute_without_log(actor, service, tool, arguments)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=SourceType.mcp_service.value,
                source_key=service,
                tool_name=tool,
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            status = CallLogStatus.blocked.value if "blocked by profile policy" in str(exc) or "action tools" in str(exc) else CallLogStatus.error.value
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=SourceType.mcp_service.value,
                source_key=service,
                tool_name=tool,
                request=request,
                response={"error": str(exc)},
                status=status,
                error_message=str(exc),
                duration_ms=monotonic_ms() - started,
            )
            if hasattr(exc, "message"):
                exc.message = f"{exc.message} (log_id: {log['log_id']})"
            raise

    async def _execute_without_log(
        self,
        actor: str,
        service: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        service_payload = self._require_enabled_service(service)
        tool_payload = self.store.get_mcp_tool(service, tool)
        if tool_payload is None or tool_payload.get("status") != "active":
            raise NotFound("tool not found")
        if tool_payload["tool_type"] not in READONLY_TOOL_TYPES:
            raise ValidationError("action tools are not executable in phase 1")

        headers = _json_loads(service_payload.get("headers_json"), {})
        try:
            result = await self.mcp_client.call_tool(
                service_payload["endpoint_url"],
                headers,
                tool,
                arguments,
            )
        except Exception as exc:
            raise ValidationError(f"MCP tool execution failed: {exc}") from exc
        return {
            "service": service,
            "tool": tool,
            "success": not bool(result.get("is_error")) if isinstance(result, dict) else True,
            "result": result,
        }
```

- [ ] **Step 7: Run capability tests**

Run:

```bash
uv run pytest tests/test_capability_service.py tests/test_capability_governance.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/wiki_manager/capability_service.py src/wiki_manager/services.py tests/test_capability_service.py
git commit -m "feat: enforce profiles in capability service"
```

## Task 4: Profile-Aware MetaMCP Gateway and HTTP Entrypoint

**Files:**
- Modify: `src/wiki_manager/mcp_server.py`
- Modify: `src/wiki_manager/server.py`
- Modify: `tests/test_mcp_server.py`
- Test: `tests/test_metamcp_http_gateway.py`

- [ ] **Step 1: Add failing MCP server tests**

Append to `tests/test_mcp_server.py`:

```python
def test_mcp_search_passes_profile_to_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    class FakeCapabilities:
        def search(self, *, actor, path, query, limit, profile_key=None):
            assert actor == "root"
            assert profile_key == "safe-readonly"
            return {"path": "/", "items": [], "log_id": "call_1"}

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root", profile_key="safe-readonly")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(handler(CallToolRequest(params=CallToolRequestParams(name="search", arguments={}))))

    assert result.root.structuredContent["log_id"] == "call_1"


def test_mcp_execute_passes_profile_to_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    class FakeCapabilities:
        async def execute(self, *, actor, service, tool, arguments, profile_key=None):
            assert profile_key == "safe-readonly"
            return {"service": service, "tool": tool, "success": True, "result": {}, "log_id": "call_2"}

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root", profile_key="safe-readonly")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(
        handler(
            CallToolRequest(
                params=CallToolRequestParams(
                    name="execute",
                    arguments={"service": "mysql", "tool": "query_sql", "arguments": {}},
                )
            )
        )
    )

    assert result.root.structuredContent["log_id"] == "call_2"
```

- [ ] **Step 2: Add failing HTTP gateway tests**

Create `tests/test_metamcp_http_gateway.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def test_metamcp_http_search_uses_profile_header(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "hive", "name": "Hive", "endpoint_url": "https://hive.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Wiki-User": "root"},
    )
    client.put(
        "/capability-profiles/safe-readonly/rules",
        json={"rules": [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}]},
        headers={"X-Wiki-User": "root"},
    )

    response = client.post(
        "/mcp/search",
        json={},
        headers={"X-Wiki-User": "root", "X-Wiki-MetaMCP-Profile": "safe-readonly"},
    )

    assert response.status_code == 200
    data = response.json()
    assert [item["service"] for item in data["items"]] == ["mysql"]
    assert data["log_id"].startswith("call_")
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_metamcp_http_gateway.py -v
```

Expected: FAIL because `create_mcp_server` does not accept `profile_key`, and `/mcp/search` does not exist.

- [ ] **Step 4: Update MCP server profile parameter**

Modify `create_mcp_server` signature in `src/wiki_manager/mcp_server.py`:

```python
def create_mcp_server(
    *,
    service: WikiManagerService | None = None,
    actor: str = "root",
    profile_key: str | None = None,
    paths: WikiManagerPaths | None = None,
    admins: set[str] | None = None,
) -> Server:
```

Modify call handlers:

```python
        if name == "search":
            return svc.capabilities.search(
                actor=actor,
                path=arguments.get("path"),
                query=arguments.get("query"),
                limit=int(arguments.get("limit", 20)),
                profile_key=profile_key,
            )
        if name == "execute":
            return await svc.capabilities.execute(
                actor=actor,
                service=arguments["service"],
                tool=arguments["tool"],
                arguments=arguments.get("arguments") or {},
                profile_key=profile_key,
            )
```

- [ ] **Step 5: Add HTTP gateway request models**

Modify `src/wiki_manager/server.py` request models:

```python
class MetaMcpSearchRequest(BaseModel):
    path: str | None = None
    query: str | None = None
    limit: int = 20


class MetaMcpExecuteRequest(BaseModel):
    service: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 6: Add profile header dependency**

Add inside `create_app` after `actor`:

```python
    def metamcp_profile(x_wiki_metamcp_profile: str | None = Header(default=None, alias="X-Wiki-MetaMCP-Profile")) -> str | None:
        return x_wiki_metamcp_profile
```

- [ ] **Step 7: Add HTTP MetaMCP routes**

Add routes before `return app`:

```python
    @app.post("/mcp/search")
    def metamcp_search(
        payload: MetaMcpSearchRequest,
        current_actor: str = Depends(actor),
        profile_key: str | None = Depends(metamcp_profile),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.capabilities.search(
                current_actor,
                payload.path,
                payload.query,
                payload.limit,
                profile_key=profile_key,
            )
        )

    @app.post("/mcp/execute")
    async def metamcp_execute(
        payload: MetaMcpExecuteRequest,
        current_actor: str = Depends(actor),
        profile_key: str | None = Depends(metamcp_profile),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(
            lambda: service.capabilities.execute(
                current_actor,
                payload.service,
                payload.tool,
                payload.arguments,
                profile_key=profile_key,
            )
        )
```

- [ ] **Step 8: Run gateway tests**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_metamcp_http_gateway.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/wiki_manager/mcp_server.py src/wiki_manager/server.py tests/test_mcp_server.py tests/test_metamcp_http_gateway.py
git commit -m "feat: add profile-aware MetaMCP gateway"
```

## Task 5: Governance, Catalog, and Log APIs

**Files:**
- Modify: `src/wiki_manager/capability_service.py`
- Modify: `src/wiki_manager/server.py`
- Modify: `tests/test_capability_api.py`

- [ ] **Step 1: Add failing API tests**

Append to `tests/test_capability_api.py`:

```python
def test_profile_api_and_catalog_preview(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "hive", "name": "Hive", "endpoint_url": "https://hive.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )

    created = client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Wiki-User": "root"},
    )
    rules = client.put(
        "/capability-profiles/safe-readonly/rules",
        json={"rules": [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}]},
        headers={"X-Wiki-User": "root"},
    )
    catalog = client.get(
        "/capability-catalog",
        params={"profile_key": "safe-readonly"},
        headers={"X-Wiki-User": "root"},
    )

    assert created.status_code == 200
    assert rules.status_code == 200
    assert [item["source_key"] for item in catalog.json()["sources"]] == ["mysql"]


def test_tool_call_log_api_returns_full_payload(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    response = client.post(
        "/mcp/search",
        json={"query": "mysql"},
        headers={"X-Wiki-User": "root"},
    )
    log_id = response.json()["log_id"]

    listed = client.get("/tool-call-logs", headers={"X-Wiki-User": "root"})
    detail = client.get(f"/tool-call-logs/{log_id}", headers={"X-Wiki-User": "root"})

    assert listed.status_code == 200
    assert listed.json()[0]["log_id"] == log_id
    assert detail.status_code == 200
    assert '"query": "mysql"' in detail.json()["request_json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_profile_api_and_catalog_preview tests/test_capability_api.py::test_tool_call_log_api_returns_full_payload -v
```

Expected: FAIL because API routes do not exist.

- [ ] **Step 3: Add public service detail helper**

Add this method to `CapabilityService` in `src/wiki_manager/capability_service.py` after `list_services`:

```python
    def get_service(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("service not found")
        return self._service_payload(service, redact_headers=True)
```

- [ ] **Step 4: Add API models**

Modify `src/wiki_manager/server.py`:

```python
class ProjectProfileRequest(BaseModel):
    profile_key: str
    name: str
    description: str = ""
    status: str = "active"


class ProfileSourceRuleRequest(BaseModel):
    source_type: str
    source_key: str
    effect: str


class ProfileRulesRequest(BaseModel):
    rules: list[ProfileSourceRuleRequest] = Field(default_factory=list)
```

- [ ] **Step 5: Add profile API routes**

Add routes inside `create_app`:

```python
    @app.post("/capability-profiles")
    def upsert_capability_profile(
        payload: ProjectProfileRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.governance.upsert_profile(
                current_actor,
                payload.profile_key,
                payload.name,
                payload.description,
                payload.status,
            )
        )

    @app.get("/capability-profiles")
    def list_capability_profiles(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.list_profiles(current_actor))

    @app.get("/capability-profiles/{profile_key}")
    def get_capability_profile(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_profile(current_actor, profile_key))

    @app.put("/capability-profiles/{profile_key}/rules")
    def replace_capability_profile_rules(
        profile_key: str,
        payload: ProfileRulesRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        rules = [rule.model_dump() for rule in payload.rules]
        return call_safely(lambda: service.governance.replace_profile_rules(current_actor, profile_key, rules))
```

- [ ] **Step 6: Add log API routes**

Add routes:

```python
    @app.get("/tool-call-logs")
    def list_tool_call_logs(
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.governance.list_logs(
                actor=current_actor,
                entrypoint=entrypoint,
                source_type=source_type,
                source_key=source_key,
                tool_name=tool_name,
                profile_key=profile_key,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/tool-call-logs/{log_id}")
    def get_tool_call_log(log_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_log(actor=current_actor, log_id=log_id))
```

- [ ] **Step 7: Add catalog routes**

Add helper inside `create_app`:

```python
    def catalog_sources(current_actor: str, profile_key: str | None, query: str | None) -> list[dict[str, Any]]:
        sources = []
        allowed_keys = set(
            service.governance.filter_source_keys(
                actor=current_actor,
                profile_key=profile_key,
                source_type="mcp_service",
                source_keys=[item["service_key"] for item in service.store.list_mcp_services()],
            )
        )
        for item in service.capabilities.list_services(current_actor):
            if item["service_key"] not in allowed_keys:
                continue
            text = f"{item['service_key']} {item['name']} {item.get('description', '')} {' '.join(item.get('tags', []))}".lower()
            if query and query.lower() not in text:
                continue
            sources.append(
                {
                    "source_type": "mcp_service",
                    "source_key": item["service_key"],
                    "name": item["name"],
                    "description": item["description"],
                    "status": item["status"],
                    "tags": item["tags"],
                }
            )
        return sources
```

Add routes:

```python
    @app.get("/capability-catalog")
    def capability_catalog(
        profile_key: str | None = None,
        query: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"sources": catalog_sources(current_actor, profile_key, query)})

    @app.get("/capability-catalog/sources/{source_type}/{source_key}")
    def capability_source_detail(source_type: str, source_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="source not found")
        service_payload = call_safely(lambda: service.capabilities.get_service(current_actor, source_key))
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        return {"source_type": source_type, "source": service_payload, "tools": tools}

    @app.get("/capability-catalog/sources/{source_type}/{source_key}/tools/{tool_name}")
    def capability_tool_detail(source_type: str, source_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="tool not found")
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        for tool in tools:
            if tool["tool"] == tool_name:
                logs = service.governance.list_logs(
                    actor=current_actor,
                    source_type=source_type,
                    source_key=source_key,
                    tool_name=tool_name,
                    limit=10,
                )
                return {"source_type": source_type, "source_key": source_key, "tool": tool, "logs": logs}
        raise HTTPException(status_code=404, detail="tool not found")
```

- [ ] **Step 8: Run API tests**

Run:

```bash
uv run pytest tests/test_capability_api.py tests/test_metamcp_http_gateway.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 5**

```bash
git add src/wiki_manager/capability_service.py src/wiki_manager/server.py tests/test_capability_api.py
git commit -m "feat: add governance APIs and capability catalog"
```

## Task 6: Chinese Control Console Pages

**Files:**
- Modify: `src/wiki_manager/web_pages.py`
- Modify: `src/wiki_manager/static/capabilities/app.css`
- Modify: `src/wiki_manager/static/capabilities/app.js`
- Modify: `tests/test_capability_api.py`

- [ ] **Step 1: Add failing Chinese page tests**

Append to `tests/test_capability_api.py`:

```python
def test_capability_admin_page_is_chinese_control_console(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert "能力治理控制台" in response.text
    assert "能力目录" in response.text
    assert "调用日志" in response.text
    assert "Project Profile" in response.text
    assert "Claude Code 接入" in response.text
    assert "MCP Services" not in response.text
    assert "Register Service" not in response.text


def test_capability_static_assets_use_chinese_labels(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    js = client.get("/static/capabilities/app.js")

    assert js.status_code == 200
    assert "加载服务" not in js.text
    assert "登记服务" in js.text
    assert "同步工具" in js.text
    assert "调用日志" in js.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_capability_admin_page_is_chinese_control_console tests/test_capability_api.py::test_capability_static_assets_use_chinese_labels -v
```

Expected: FAIL because current page and JS contain English labels.

- [ ] **Step 3: Replace HTML shell with Chinese console**

Modify `src/wiki_manager/web_pages.py` so `capability_admin_page()` returns a Chinese shell with these required IDs:

```html
<aside class="sidebar" aria-label="能力治理导航">
  <div class="sidebar-header">
    <div class="sidebar-logo">
      <div class="sidebar-logo-icon">A</div>
      <div class="sidebar-logo-text">能力治理控制台<span>Agent Capability Hub</span></div>
    </div>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-group-label">能力管理</div>
    <button class="nav-item active" data-view="catalog" type="button">能力目录</button>
    <button class="nav-item" data-view="services" type="button">MCP 服务</button>
    <button class="nav-item" data-view="tools" type="button">工具清单</button>
    <div class="nav-group-label">治理策略</div>
    <button class="nav-item" data-view="profiles" type="button">Project Profile</button>
    <div class="nav-group-label">调用观测</div>
    <button class="nav-item" data-view="logs" type="button">调用日志</button>
    <div class="nav-group-label">接入配置</div>
    <button class="nav-item" data-view="claude" type="button">Claude Code 接入</button>
  </nav>
</aside>
```

Keep links to `/static/capabilities/app.css` and `/static/capabilities/app.js`.

- [ ] **Step 4: Update CSS without visual drift**

Modify `src/wiki_manager/static/capabilities/app.css`:

```css
.main {
  margin-left: var(--sidebar-width);
  min-width: 0;
  padding: 24px;
  width: calc(100% - var(--sidebar-width));
}

.view {
  display: none;
}

.view.active {
  display: block;
}

.detail-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr) 380px;
}

.json-panel {
  background: #111827;
  border-radius: 8px;
  color: #e5e7eb;
  font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  max-height: 360px;
  overflow: auto;
  padding: 14px;
  white-space: pre-wrap;
}
```

Do not add gradients, decorative orbs, or a new color palette.

- [ ] **Step 5: Rewrite JS labels and add views**

Modify `src/wiki_manager/static/capabilities/app.js` to use Chinese labels and the new APIs. Minimum required functions:

```javascript
async function loadCatalog() {
  const data = await apiRequest("/capability-catalog", { method: "GET" });
  // Render 能力目录 rows.
}

async function loadProfiles() {
  const profiles = await apiRequest("/capability-profiles", { method: "GET" });
  // Render Project Profile rows.
}

async function loadLogs() {
  const logs = await apiRequest("/tool-call-logs", { method: "GET" });
  // Render 调用日志 rows.
}

function renderClaudeConfig() {
  // Render `wiki metamcp add ...` command preview.
}
```

Existing service registration functions should keep working, but change user-facing strings:

```javascript
showMessage(`已保存服务 ${payload.service_key}。`);
showMessage(`${serviceKey} 已切换为 ${status}。`);
showMessage(`已从 ${serviceKey} 同步 ${result.tool_count} 个工具。`);
```

- [ ] **Step 6: Run page tests**

Run:

```bash
uv run pytest tests/test_capability_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add src/wiki_manager/web_pages.py src/wiki_manager/static/capabilities/app.css src/wiki_manager/static/capabilities/app.js tests/test_capability_api.py
git commit -m "feat: add Chinese capability governance console"
```

## Task 7: CLI for Project Profiles and Claude Code Connection

**Files:**
- Modify: `src/wiki_manager/client.py`
- Modify: `src/wiki_manager/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Add failing CLI tests**

Modify imports in `tests/test_cli.py`:

```python
import json
```

Append to `tests/test_cli.py`:

```python
def test_metamcp_profile_create_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def upsert_profile(self, profile_key, name, description, status):
            calls.append((profile_key, name, description, status))
            return {"profile_key": profile_key, "name": name}

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["metamcp", "profile", "create", "safe-readonly", "--name", "安全只读"])

    assert result.exit_code == 0
    assert "safe-readonly" in result.stdout
    assert calls == [("safe-readonly", "安全只读", "", "active")]


def test_metamcp_profile_rules_calls_client(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def replace_profile_rules(self, profile_key, rules):
            captured["profile_key"] = profile_key
            captured["rules"] = rules
            return {"profile_key": profile_key, "rules": rules}

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["metamcp", "profile", "rules", "safe-readonly", "--allow", "mysql", "--deny", "hive"],
    )

    assert result.exit_code == 0
    assert captured["rules"] == [
        {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
        {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
    ]


def test_metamcp_add_writes_project_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
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
    )

    config = tmp_path / ".mcp.json"
    assert result.exit_code == 0
    assert config.exists()
    assert "safe-readonly" in config.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py::test_metamcp_profile_create_calls_client tests/test_cli.py::test_metamcp_profile_rules_calls_client tests/test_cli.py::test_metamcp_add_writes_project_config -v
```

Expected: FAIL because `metamcp` command group and client methods do not exist.

- [ ] **Step 3: Add client methods**

Modify `src/wiki_manager/client.py`:

```python
    def upsert_profile(self, profile_key: str, name: str, description: str, status: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/capability-profiles",
            json={"profile_key": profile_key, "name": name, "description": description, "status": status},
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()

    def list_profiles(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/capability-profiles", headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def get_profile(self, profile_key: str) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/capability-profiles/{profile_key}", headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def replace_profile_rules(self, profile_key: str, rules: list[dict[str, str]]) -> dict[str, Any]:
        response = httpx.put(
            f"{self.base_url}/capability-profiles/{profile_key}/rules",
            json={"rules": rules},
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()
```

- [ ] **Step 4: Add CLI command groups**

Modify `src/wiki_manager/cli.py`:

```python
import json
```

Add Typer apps:

```python
metamcp_app = typer.Typer(help="Manage MetaMCP profiles and Claude Code connection.", no_args_is_help=True)
metamcp_profile_app = typer.Typer(help="Manage Project Profiles.", no_args_is_help=True)
app.add_typer(metamcp_app, name="metamcp")
metamcp_app.add_typer(metamcp_profile_app, name="profile")
```

- [ ] **Step 5: Add profile CLI commands**

Add to `src/wiki_manager/cli.py`:

```python
@metamcp_profile_app.command("create")
def metamcp_profile_create(
    profile_key: Annotated[str, typer.Argument(help="Project Profile key.")],
    name: Annotated[str, typer.Option("--name", help="Display name.")],
    description: Annotated[str, typer.Option("--description", help="Description.")] = "",
    status: Annotated[str, typer.Option("--status", help="Profile status.")] = "active",
) -> None:
    profile = _run_client(lambda client: client.upsert_profile(profile_key, name, description, status))
    _echo_mapping(profile, ("profile_key", "name", "status"))


@metamcp_profile_app.command("list")
def metamcp_profile_list() -> None:
    profiles = _run_client(lambda client: client.list_profiles())
    for profile in profiles:
        typer.echo(
            f"{profile['profile_key']} | allow: {profile.get('allow_count', 0)} | deny: {profile.get('deny_count', 0)}"
        )


@metamcp_profile_app.command("show")
def metamcp_profile_show(profile_key: Annotated[str, typer.Argument(help="Project Profile key.")]) -> None:
    profile = _run_client(lambda client: client.get_profile(profile_key))
    _echo_mapping(profile, ("profile_key", "name", "status"))
    for rule in profile.get("rules", []):
        typer.echo(f"  {rule['effect']} {rule['source_type']}:{rule['source_key']}")


@metamcp_profile_app.command("rules")
def metamcp_profile_rules(
    profile_key: Annotated[str, typer.Argument(help="Project Profile key.")],
    allow: Annotated[list[str], typer.Option("--allow", help="Allowed MCP service key.")] = [],
    deny: Annotated[list[str], typer.Option("--deny", help="Denied MCP service key.")] = [],
) -> None:
    rules = [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "allow"}
        for source_key in allow
    ] + [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "deny"}
        for source_key in deny
    ]
    profile = _run_client(lambda client: client.replace_profile_rules(profile_key, rules))
    typer.echo(f"profile: {profile['profile_key']} rules: {len(profile.get('rules', []))}")
```

- [ ] **Step 6: Add Claude Code config helpers**

Add to `src/wiki_manager/cli.py`:

```python
def _claude_config_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".mcp.json"
    if scope == "user":
        return Path.home() / ".mcp.json"
    raise ValueError("scope must be project or user")


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON config: {path}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"config must be a JSON object: {path}")
    return loaded


def _with_metamcp_config(existing: dict[str, Any], url: str, profile: str) -> dict[str, Any]:
    config = dict(existing)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["agent-capability-hub"] = {
        "url": url,
        "headers": {"X-Wiki-MetaMCP-Profile": profile},
    }
    config["mcpServers"] = servers
    return config
```

Add commands:

```python
@metamcp_app.command("add")
def metamcp_add(
    scope: Annotated[str, typer.Option("--scope", help="project or user.")],
    url: Annotated[str, typer.Option("--url", help="MetaMCP HTTP URL.")],
    profile: Annotated[str, typer.Option("--profile", help="Project Profile key.")],
) -> None:
    path = _claude_config_path(scope)
    existing = _load_json_file(path)
    path.write_text(
        json.dumps(_with_metamcp_config(existing, url, profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(f"written: {path}")


@metamcp_app.command("config")
def metamcp_config(scope: Annotated[str, typer.Option("--scope", help="project or user.")] = "project") -> None:
    path = _claude_config_path(scope)
    if not path.exists():
        typer.echo(f"missing: {path}")
        return
    typer.echo(path.read_text(encoding="utf-8"))
```

Add this extra CLI test:

```python
def test_metamcp_add_preserves_existing_servers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node"}}}),
        encoding="utf-8",
    )

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
    )

    data = json.loads(config.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "existing" in data["mcpServers"]
    assert "agent-capability-hub" in data["mcpServers"]
```

- [ ] **Step 7: Update README**

Append to `README.md`:

```markdown
## Phase 1.5 Governance Usage

```bash
uv run wiki metamcp profile create safe-readonly --name "安全只读"
uv run wiki metamcp profile rules safe-readonly --allow mysql --deny hive
uv run wiki metamcp add --scope project --url http://127.0.0.1:8765/mcp --profile safe-readonly
uv run wiki metamcp config --scope project
```
```

- [ ] **Step 8: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 7**

```bash
git add src/wiki_manager/client.py src/wiki_manager/cli.py tests/test_cli.py README.md
git commit -m "feat: add MetaMCP profile CLI"
```

## Task 8: Final Verification and Cleanup

**Files:**
- Modify only if verification finds defects.

- [ ] **Step 1: Run focused Phase 1.5 tests**

Run:

```bash
uv run pytest tests/test_capability_governance_storage.py tests/test_capability_governance.py tests/test_capability_service.py tests/test_mcp_server.py tests/test_metamcp_http_gateway.py tests/test_capability_api.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 2: Run existing Phase 1 capability tests**

Run:

```bash
uv run pytest tests/test_capability_storage.py tests/test_mcp_http_client.py -v
```

Expected: PASS.

- [ ] **Step 3: Run non-live full suite**

Run:

```bash
uv run pytest -v -m "not ragflow and not weknora"
```

Expected: PASS.

- [ ] **Step 4: Verify external integration behavior**

Run:

```bash
uv run pytest -v
```

Expected: PASS if live RagFlow and Weknora services are healthy. If failures are limited to live `ragflow` or `weknora` tests with 502/empty response, record that external services were unavailable and rerun Step 3 as the authoritative non-live verification.

- [ ] **Step 5: Inspect git status**

Run:

```bash
git status --short
```

Expected: no tracked changes. Pre-existing untracked files such as `docs/design/` and `.DS_Store` may remain untouched.

## Final Acceptance Checklist

- [ ] `tool_call_logs` stores full request/response payloads for MetaMCP calls.
- [ ] `project_profiles` and `profile_source_rules` support service-level allow/deny.
- [ ] No profile means all enabled MCP services are visible.
- [ ] Deny hides services in `search` and blocks direct `execute`.
- [ ] Allow restricts visibility to listed services.
- [ ] Allow plus deny applies allow first, then deny.
- [ ] Unknown profile returns `profile not found` and writes a log.
- [ ] `search` returns `log_id`.
- [ ] `execute` success returns `log_id`.
- [ ] Blocked `execute` does not call the target MCP client.
- [ ] `/tool-call-logs` and `/tool-call-logs/{log_id}` return list/detail data.
- [ ] `/capability-catalog` previews profile-filtered capabilities.
- [ ] `/admin/capabilities` is Chinese and contains 能力目录、调用日志、Project Profile、Claude Code 接入.
- [ ] Existing service registration/sync/status UI still works.
- [ ] `wiki metamcp profile create/list/show/rules` works.
- [ ] `wiki metamcp add --scope project` writes `.mcp.json` with `X-Wiki-MetaMCP-Profile`.
- [ ] `uv run pytest -v -m "not ragflow and not weknora"` passes.

## Plan Self-Review

- Spec coverage: This plan covers generic tool logs, Project Profiles, service-level rules, profile filtering, service-side enforcement, log IDs, capability catalog/details, Chinese console, and Claude Code CLI.
- Scope boundary: It intentionally does not add action execution, approval, dry-run, tool-level allow/deny, React/Vite, or default log redaction.
- Type consistency: The plan consistently uses `tool_call_logs`, `project_profiles`, `profile_source_rules`, `source_type`, `source_key`, `profile_key`, and `log_id`.
