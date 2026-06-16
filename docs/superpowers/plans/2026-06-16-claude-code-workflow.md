# Claude Code Workflow Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code dynamic workflow management feature that stores workflow definitions, schedules fair repeated `claude -p` runs, exposes workflow-only MCP task tools, and stores searchable Markdown artifacts outside the KB system.

**Architecture:** Add a focused workflow domain under `agent_bridge/workflows` with storage, service, runner, scheduler, and result parsing separated. Keep Agent Bridge responsible for profile binding, task lease state, run lifecycle, MCP exposure, and artifact indexing; keep workflow business logic inside user-provided Claude Code workflow JavaScript. Frontend work extends the existing Vue capability console with workflow definition, run state, logs, and artifact browsing.

**Tech Stack:** Python 3.11, FastAPI, SQLite, APScheduler, FastMCP, pytest, Vue 3, TypeScript, existing shadcn-style UI components.

---

## Reference Spec

Read the approved design before implementation:

- `docs/superpowers/specs/2026-06-16-claude-code-workflow-design.html`

Key decisions from the spec:

- Every workflow must bind to an existing `profile_key`.
- Workflow logic is Claude Code workflow JavaScript; Agent Bridge stores it and starts `claude -p`.
- Agent Bridge renders workflow information from a manifest, not by inferring arbitrary JavaScript control flow.
- Artifacts are stored separately from KBs and searched through `artifacts_search`.
- Workflow agent MCP tools are gated by headers and are not visible to normal profile agents.
- Global scheduler starts at most two different workflows concurrently and uses round-robin fairness.
- `workflow_get_task` must lease a task atomically.

## File Map

### Backend Files To Create

- `src/agent_bridge/workflows/__init__.py`: package exports.
- `src/agent_bridge/workflows/models.py`: workflow status constants, dataclasses, parser helpers.
- `src/agent_bridge/workflows/service.py`: admin APIs, task leasing, log append, artifact ingestion/search.
- `src/agent_bridge/workflows/result_parser.py`: validate `out/result.json` and artifact files.
- `src/agent_bridge/workflows/runner.py`: fake runner protocol and real `claude -p` runner.
- `src/agent_bridge/workflows/scheduler.py`: global fair scheduler loop.
- `src/agent_bridge/storage/repositories/workflows.py`: SQLite repository for workflow tables.
- `src/agent_bridge/api/routes/workflows.py`: FastAPI routes for workflow console.
- `tests/test_workflow_storage.py`: storage and lease behavior.
- `tests/test_workflow_service.py`: service validation, task APIs, artifact search.
- `tests/test_workflow_mcp.py`: MCP visibility and tool execution.
- `tests/test_workflow_result_parser.py`: result protocol validation.
- `tests/test_workflow_scheduler.py`: fair queue and stop-window behavior.
- `tests/test_workflow_api.py`: HTTP API behavior.

### Backend Files To Modify

- `src/agent_bridge/storage/schema.py`: add workflow schema block.
- `src/agent_bridge/storage/sqlite.py`: initialize workflow repository and facade methods.
- `src/agent_bridge/knowledge/service.py`: construct `WorkflowService` and `WorkflowScheduler`.
- `src/agent_bridge/api/app.py`: start/stop workflow scheduler and register workflow routes.
- `src/agent_bridge/api/schemas.py`: add Pydantic request models.
- `src/agent_bridge/capabilities/mcp_server.py`: add workflow MCP tools and header gating.

### Frontend Files To Modify

- `frontend/capabilities/src/api/types.ts`: add workflow and artifact types.
- `frontend/capabilities/src/api/client.ts`: add workflow API methods.
- `frontend/capabilities/src/views/workflow/WorkflowView.vue`: replace placeholder with real UI.

### Test Commands

Use focused tests while developing:

```bash
uv run pytest tests/test_workflow_storage.py -v
uv run pytest tests/test_workflow_service.py -v
uv run pytest tests/test_workflow_mcp.py -v
uv run pytest tests/test_workflow_result_parser.py -v
uv run pytest tests/test_workflow_scheduler.py -v
uv run pytest tests/test_workflow_api.py -v
```

Use final backend verification:

```bash
uv run pytest tests/test_workflow_storage.py tests/test_workflow_service.py tests/test_workflow_mcp.py tests/test_workflow_result_parser.py tests/test_workflow_scheduler.py tests/test_workflow_api.py tests/test_mcp_server.py tests/test_capability_api.py -v
```

Use frontend verification after UI work:

```bash
cd frontend/capabilities
npm run build
```

---

## Task 1: Workflow Schema And Repository

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Create: `src/agent_bridge/storage/repositories/workflows.py`
- Create: `src/agent_bridge/workflows/__init__.py`
- Create: `src/agent_bridge/workflows/models.py`
- Test: `tests/test_workflow_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_workflow_storage.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_workflow_definition_requires_profile_reference(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    try:
        store.upsert_workflow_definition(
            workflow_key="page-report",
            name="Page Report",
            description="",
            profile_key="missing-profile",
            workflow_js="export const manifest = {};",
            manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            status="active",
            created_by="root",
        )
    except Exception as exc:
        assert "FOREIGN KEY" in str(exc) or "foreign key" in str(exc).lower()
    else:
        raise AssertionError("workflow definition without profile should fail")


def test_workflow_definition_round_trips_with_manifest_and_schedule(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="report-plane",
        name="Report Plane",
        description="",
        status="active",
        created_by="root",
    )

    created = store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="Nightly page report",
        profile_key="report-plane",
        workflow_js="export const manifest = { name: 'Page Report' };",
        manifest={"name": "Page Report", "nodes": [{"id": "get_task"}], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"
    assert created["manifest"]["nodes"] == [{"id": "get_task"}]
    assert created["schedule"]["stop_time"] == "07:00"

    listed = store.list_workflow_definitions()
    assert [item["workflow_key"] for item in listed] == ["page-report"]


def test_workflow_task_upsert_is_idempotent_and_does_not_replace_completed(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )

    first = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a"}}],
    )
    second = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a2"}}],
    )
    assert first == {"created": 1, "updated": 0, "skipped_completed": 0}
    assert second == {"created": 0, "updated": 1, "skipped_completed": 0}

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task is not None
    store.complete_workflow_task("page-report", "page:a", run_id="run_1")

    third = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a3"}}],
    )
    assert third == {"created": 0, "updated": 0, "skipped_completed": 1}
    assert store.get_workflow_task("page-report", "page:a")["payload"]["page"] == "a2"


def test_workflow_task_lease_is_exclusive_and_expired_leases_are_reclaimed(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {}}])

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task["task_key"] == "page:a"
    assert store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200) is None

    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.force_workflow_task_lease_expiry("page-report", "page:a", expired.isoformat())
    reclaimed = store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200)
    assert reclaimed["task_key"] == "page:a"
    assert reclaimed["lease_run_id"] == "run_2"
```

- [ ] **Step 2: Run storage tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_storage.py -v
```

Expected: FAIL because workflow repository methods and tables do not exist.

- [ ] **Step 3: Add workflow domain constants**

Create `src/agent_bridge/workflows/__init__.py`:

```python
"""Workflow orchestration domain for Agent Bridge."""
```

Create `src/agent_bridge/workflows/models.py`:

```python
from __future__ import annotations

from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    active = "active"
    disabled = "disabled"


class WorkflowRunStatus(str, Enum):
    running = "running"
    completed = "completed"
    no_task = "no_task"
    failed = "failed"
    stopped = "stopped"


class WorkflowTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    abandoned = "abandoned"


class WorkflowArtifactFormat(str, Enum):
    markdown = "markdown"


def require_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    for key in ("name", "nodes", "edges", "schemas"):
        if key not in value:
            raise ValueError(f"manifest missing required key: {key}")
    if not isinstance(value["nodes"], list):
        raise ValueError("manifest.nodes must be a list")
    if not isinstance(value["edges"], list):
        raise ValueError("manifest.edges must be a list")
    if not isinstance(value["schemas"], dict):
        raise ValueError("manifest.schemas must be an object")
    return value
```

- [ ] **Step 4: Add schema tables**

Modify `src/agent_bridge/storage/schema.py` by adding a new string constant after `CODEGRAPH_SCHEMA`:

```python
WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE RESTRICT,
  workflow_js TEXT NOT NULL DEFAULT '',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  schedule_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_profile ON workflow_definitions(profile_key);

CREATE TABLE IF NOT EXISTS workflow_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  task_key TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  lease_run_id TEXT,
  lease_expires_at TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  UNIQUE (workflow_key, task_key)
);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pick
  ON workflow_tasks(workflow_key, status, lease_expires_at, id);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  profile_key TEXT NOT NULL,
  task_key TEXT,
  status TEXT NOT NULL,
  temp_dir TEXT NOT NULL DEFAULT '',
  exit_code INTEGER,
  stdout_path TEXT,
  stderr_path TEXT,
  error TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_key, started_at DESC);

CREATE TABLE IF NOT EXISTS workflow_run_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  workflow_key TEXT NOT NULL,
  task_key TEXT,
  level TEXT NOT NULL DEFAULT 'info',
  stage TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_run_logs_run ON workflow_run_logs(run_id, id);

CREATE TABLE IF NOT EXISTS workflow_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL UNIQUE,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  profile_key TEXT NOT NULL,
  run_id TEXT NOT NULL,
  task_key TEXT,
  title TEXT NOT NULL,
  path TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  format TEXT NOT NULL DEFAULT 'markdown',
  summary TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (workflow_key, path)
);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_profile ON workflow_artifacts(profile_key);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_path ON workflow_artifacts(path);
"""
```

- [ ] **Step 5: Implement repository**

Create `src/agent_bridge/storage/repositories/workflows.py` with JSON helpers and methods used by tests:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_bridge.storage.types import row_to_dict
from agent_bridge.workflows.models import WorkflowTaskStatus, require_manifest


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    for source, target, default in [
        ("manifest_json", "manifest", {}),
        ("schedule_json", "schedule", {}),
        ("payload_json", "payload", {}),
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
    ]:
        if source in item:
            item[target] = _json_loads(item[source], default)
    return item


class WorkflowsRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def upsert_workflow_definition(
        self,
        *,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        manifest: dict[str, Any],
        schedule: dict[str, Any],
        status: str,
        created_by: str,
    ) -> dict[str, Any]:
        require_manifest(manifest)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_definitions (
                  workflow_key, name, description, profile_key, workflow_js,
                  manifest_json, schedule_json, status, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  profile_key = excluded.profile_key,
                  workflow_js = excluded.workflow_js,
                  manifest_json = excluded.manifest_json,
                  schedule_json = excluded.schedule_json,
                  status = excluded.status,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    workflow_key,
                    name,
                    description,
                    profile_key,
                    workflow_js,
                    _json_dumps(manifest),
                    _json_dumps(schedule),
                    status,
                    created_by,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workflow_definitions WHERE workflow_key = ?",
                (workflow_key,),
            ).fetchone()
            result = _row_payload(row)
            if result is None:
                raise KeyError(f"workflow not found: {workflow_key}")
            return result

    def get_workflow_definition(self, workflow_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_definitions WHERE workflow_key = ?",
                    (workflow_key,),
                ).fetchone()
            )

    def list_workflow_definitions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_definitions ORDER BY workflow_key"
            ).fetchall()
            return [item for row in rows if (item := _row_payload(row)) is not None]

    def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
        created = 0
        updated = 0
        skipped_completed = 0
        with self._connect() as conn:
            for task in tasks:
                task_key = str(task["task_key"])
                payload = task.get("payload") or {}
                existing = conn.execute(
                    "SELECT status FROM workflow_tasks WHERE workflow_key = ? AND task_key = ?",
                    (workflow_key, task_key),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO workflow_tasks (workflow_key, task_key, payload_json, status)
                        VALUES (?, ?, ?, 'pending')
                        """,
                        (workflow_key, task_key, _json_dumps(payload)),
                    )
                    created += 1
                elif existing["status"] == WorkflowTaskStatus.completed.value:
                    skipped_completed += 1
                else:
                    conn.execute(
                        """
                        UPDATE workflow_tasks
                        SET payload_json = ?, status = 'pending', lease_run_id = NULL,
                            lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE workflow_key = ? AND task_key = ?
                        """,
                        (_json_dumps(payload), workflow_key, task_key),
                    )
                    updated += 1
        return {"created": created, "updated": updated, "skipped_completed": skipped_completed}

    def get_workflow_task(self, workflow_key: str, task_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_tasks WHERE workflow_key = ? AND task_key = ?",
                    (workflow_key, task_key),
                ).fetchone()
            )

    def lease_workflow_task(self, workflow_key: str, *, run_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = _now_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_key = ?
                  AND (
                    status = 'pending'
                    OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                  )
                ORDER BY id
                LIMIT 1
                """,
                (workflow_key, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'running',
                    lease_run_id = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id, expires_at, row["id"]),
            )
            leased = conn.execute("SELECT * FROM workflow_tasks WHERE id = ?", (row["id"],)).fetchone()
            return _row_payload(leased)

    def complete_workflow_task(self, workflow_key: str, task_key: str, *, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_run_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ? AND task_key = ?
                """,
                (run_id, workflow_key, task_key),
            )

    def force_workflow_task_lease_expiry(self, workflow_key: str, task_key: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_tasks
                SET lease_expires_at = ?
                WHERE workflow_key = ? AND task_key = ?
                """,
                (expires_at, workflow_key, task_key),
            )
```

- [ ] **Step 6: Wire repository into SQLiteStore**

Modify imports in `src/agent_bridge/storage/sqlite.py`:

```python
from agent_bridge.storage.schema import CODEGRAPH_SCHEMA, SCHEMA, WORKFLOW_SCHEMA
```

Inside `SQLiteStore.__init__`, add:

```python
from agent_bridge.storage.repositories.workflows import WorkflowsRepository

self.workflows = WorkflowsRepository(db_path, self.connect)
```

Inside `init_schema`, after `conn.executescript(CODEGRAPH_SCHEMA)`, add:

```python
conn.executescript(WORKFLOW_SCHEMA)
```

Inside `migrate_phase2`, after `conn.executescript(CODEGRAPH_SCHEMA)`, add:

```python
conn.executescript(WORKFLOW_SCHEMA)
```

Add facade methods near sync config methods:

```python
def upsert_workflow_definition(self, **kwargs):
    return self.workflows.upsert_workflow_definition(**kwargs)

def get_workflow_definition(self, workflow_key: str):
    return self.workflows.get_workflow_definition(workflow_key)

def list_workflow_definitions(self):
    return self.workflows.list_workflow_definitions()

def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]):
    return self.workflows.upsert_workflow_tasks(workflow_key, tasks)

def get_workflow_task(self, workflow_key: str, task_key: str):
    return self.workflows.get_workflow_task(workflow_key, task_key)

def lease_workflow_task(self, workflow_key: str, *, run_id: str, lease_seconds: int = 7200):
    return self.workflows.lease_workflow_task(workflow_key, run_id=run_id, lease_seconds=lease_seconds)

def complete_workflow_task(self, workflow_key: str, task_key: str, *, run_id: str):
    return self.workflows.complete_workflow_task(workflow_key, task_key, run_id=run_id)

def force_workflow_task_lease_expiry(self, workflow_key: str, task_key: str, expires_at: str):
    return self.workflows.force_workflow_task_lease_expiry(workflow_key, task_key, expires_at)
```

- [ ] **Step 7: Run storage tests**

Run:

```bash
uv run pytest tests/test_workflow_storage.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit storage foundation**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/workflows.py src/agent_bridge/workflows/__init__.py src/agent_bridge/workflows/models.py tests/test_workflow_storage.py
git commit -m "feat: add workflow storage foundation"
```

---

## Task 2: Workflow Service, API, And Artifact Storage

**Files:**
- Create: `src/agent_bridge/workflows/service.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/knowledge/service.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Create: `src/agent_bridge/api/routes/workflows.py`
- Modify: `src/agent_bridge/api/app.py`
- Test: `tests/test_workflow_service.py`
- Test: `tests/test_workflow_api.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_workflow_service.py`:

```python
from __future__ import annotations


def _service(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    return svc


def test_workflow_service_creates_definition_with_existing_profile(wm_paths):
    svc = _service(wm_paths)

    created = svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="Nightly page report",
        profile_key="report-plane",
        workflow_js="export const manifest = {};",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"


def test_workflow_service_rejects_missing_profile(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)

    try:
        svc.workflows.upsert_definition(
            actor="root",
            workflow_key="page-report",
            name="Page Report",
            description="",
            profile_key="missing",
            workflow_js="",
            manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            status="active",
        )
    except ValidationError as exc:
        assert "profile not found" in exc.message
    else:
        raise AssertionError("missing profile should be rejected")


def test_workflow_service_appends_run_log(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )

    svc.workflows.append_run_log(
        workflow_key="page-report",
        run_id="run_1",
        task_key="page:a",
        level="info",
        stage="analyze",
        message="started",
        payload={"step": 1},
    )

    logs = svc.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "started"
    assert logs[0]["payload"]["step"] == 1


def test_workflow_service_saves_and_searches_artifacts(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "finance"],
        format="markdown",
        summary="Finance page report",
        content="# Page A\n\nUses table finance_orders.",
        metadata={"page_key": "page-a"},
    )

    assert saved["artifact_id"].startswith("artifact_")
    results = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query="finance_orders",
        tags=["finance"],
        path="reports/",
        workflow_key=None,
        limit=10,
    )
    assert [item["title"] for item in results["items"]] == ["Page A Report"]
    assert "finance_orders" in results["items"][0]["snippet"]
```

- [ ] **Step 2: Run service tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_service.py -v
```

Expected: FAIL because `AgentBridgeService.workflows` and artifact repository methods do not exist.

- [ ] **Step 3: Extend repository with runs, logs, artifacts, and search**

Append methods to `src/agent_bridge/storage/repositories/workflows.py`:

```python
import hashlib
import uuid


def _artifact_id() -> str:
    return f"artifact_{uuid.uuid4().hex}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

Add methods inside `WorkflowsRepository`:

```python
def create_workflow_run(
    self,
    *,
    run_id: str,
    workflow_key: str,
    profile_key: str,
    task_key: str | None,
    status: str,
    temp_dir: str,
) -> dict[str, Any]:
    with self._connect() as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_key, profile_key, task_key, status, temp_dir)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, workflow_key, profile_key, task_key, status, temp_dir),
        )
        return _row_payload(conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone())

def finish_workflow_run(
    self,
    run_id: str,
    *,
    status: str,
    exit_code: int | None,
    stdout_path: str | None,
    stderr_path: str | None,
    error: str | None,
    duration_ms: int | None,
) -> dict[str, Any]:
    with self._connect() as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET status = ?, exit_code = ?, stdout_path = ?, stderr_path = ?,
                error = ?, duration_ms = ?, finished_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (status, exit_code, stdout_path, stderr_path, error, duration_ms, run_id),
        )
        return _row_payload(conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone())

def append_workflow_run_log(
    self,
    *,
    run_id: str,
    workflow_key: str,
    task_key: str | None,
    level: str,
    stage: str,
    message: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with self._connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO workflow_run_logs (
              run_id, workflow_key, task_key, level, stage, message, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, workflow_key, task_key, level, stage, message, _json_dumps(payload)),
        )
        return _row_payload(conn.execute("SELECT * FROM workflow_run_logs WHERE id = ?", (cursor.lastrowid,)).fetchone())

def list_workflow_run_logs(self, run_id: str) -> list[dict[str, Any]]:
    with self._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_run_logs WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [item for row in rows if (item := _row_payload(row)) is not None]

def upsert_workflow_artifact(
    self,
    *,
    workflow_key: str,
    profile_key: str,
    run_id: str,
    task_key: str | None,
    title: str,
    path: str,
    tags: list[str],
    format: str,
    summary: str,
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    content_hash = _content_hash(content)
    with self._connect() as conn:
        existing = conn.execute(
            "SELECT artifact_id FROM workflow_artifacts WHERE workflow_key = ? AND path = ?",
            (workflow_key, path),
        ).fetchone()
        artifact_id = existing["artifact_id"] if existing else _artifact_id()
        conn.execute(
            """
            INSERT INTO workflow_artifacts (
              artifact_id, workflow_key, profile_key, run_id, task_key, title, path,
              tags_json, format, summary, content, content_hash, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_key, path) DO UPDATE SET
              profile_key = excluded.profile_key,
              run_id = excluded.run_id,
              task_key = excluded.task_key,
              title = excluded.title,
              tags_json = excluded.tags_json,
              format = excluded.format,
              summary = excluded.summary,
              content = excluded.content,
              content_hash = excluded.content_hash,
              metadata_json = excluded.metadata_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                artifact_id,
                workflow_key,
                profile_key,
                run_id,
                task_key,
                title,
                path,
                _json_dumps(tags),
                format,
                summary,
                content,
                content_hash,
                _json_dumps(metadata),
            ),
        )
        return _row_payload(conn.execute("SELECT * FROM workflow_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone())

def search_workflow_artifacts(
    self,
    *,
    profile_key: str | None,
    query: str | None,
    tags: list[str],
    path: str | None,
    workflow_key: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if profile_key:
        clauses.append("profile_key = ?")
        params.append(profile_key)
    if workflow_key:
        clauses.append("workflow_key = ?")
        params.append(workflow_key)
    if path:
        clauses.append("path LIKE ?")
        params.append(f"{path}%")
    if query:
        lowered = f"%{query.lower()}%"
        clauses.append("(lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(content) LIKE ? OR lower(path) LIKE ?)")
        params.extend([lowered, lowered, lowered, lowered])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with self._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM workflow_artifacts
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    items = [item for row in rows if (item := _row_payload(row)) is not None]
    if tags:
        required = set(tags)
        items = [item for item in items if required.issubset(set(item.get("tags", [])))]
    return items
```

- [ ] **Step 4: Add SQLite facade methods**

Add to `src/agent_bridge/storage/sqlite.py`:

```python
def create_workflow_run(self, **kwargs):
    return self.workflows.create_workflow_run(**kwargs)

def finish_workflow_run(self, run_id: str, **kwargs):
    return self.workflows.finish_workflow_run(run_id, **kwargs)

def append_workflow_run_log(self, **kwargs):
    return self.workflows.append_workflow_run_log(**kwargs)

def list_workflow_run_logs(self, run_id: str):
    return self.workflows.list_workflow_run_logs(run_id)

def upsert_workflow_artifact(self, **kwargs):
    return self.workflows.upsert_workflow_artifact(**kwargs)

def search_workflow_artifacts(self, **kwargs):
    return self.workflows.search_workflow_artifacts(**kwargs)
```

- [ ] **Step 5: Implement WorkflowService**

Create `src/agent_bridge/workflows/service.py`:

```python
from __future__ import annotations

from typing import Any

from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.workflows.models import WorkflowArtifactFormat, WorkflowStatus, require_manifest


def _snippet(content: str, query: str | None, size: int = 220) -> str:
    if not query:
        return content[:size]
    index = content.lower().find(query.lower())
    if index < 0:
        return content[:size]
    start = max(0, index - 60)
    end = min(len(content), index + size - 60)
    return content[start:end]


class WorkflowService:
    def __init__(self, *, store: SQLiteStore, admins: set[str]) -> None:
        self.store = store
        self.admins = admins

    def upsert_definition(
        self,
        *,
        actor: str,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        manifest: dict[str, Any],
        schedule: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise ValidationError("profile not found")
        try:
            require_manifest(manifest)
            next_status = WorkflowStatus(status).value
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return self.store.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description=description,
            profile_key=profile_key,
            workflow_js=workflow_js,
            manifest=manifest,
            schedule=schedule,
            status=next_status,
            created_by=actor,
        )

    def list_definitions(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_workflow_definitions()

    def get_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        return workflow

    def append_run_log(
        self,
        *,
        workflow_key: str,
        run_id: str,
        task_key: str | None,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.store.append_workflow_run_log(
            run_id=run_id,
            workflow_key=workflow_key,
            task_key=task_key,
            level=level,
            stage=stage,
            message=message,
            payload=payload,
        )

    def list_run_logs(self, actor: str, run_id: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_workflow_run_logs(run_id)

    def save_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            artifact_format = WorkflowArtifactFormat(format).value
        except ValueError as exc:
            raise ValidationError("unsupported artifact format") from exc
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValidationError("invalid artifact path")
        if artifact_format != WorkflowArtifactFormat.markdown.value:
            raise ValidationError("unsupported artifact format")
        return self.store.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            title=title,
            path=path,
            tags=tags,
            format=artifact_format,
            summary=summary,
            content=content,
            metadata=metadata,
        )

    def search_artifacts(
        self,
        *,
        actor: str,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        items = self.store.search_workflow_artifacts(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            limit=bounded_limit,
        )
        return {
            "items": [
                {
                    "artifact_id": item["artifact_id"],
                    "workflow_key": item["workflow_key"],
                    "profile_key": item["profile_key"],
                    "run_id": item["run_id"],
                    "task_key": item["task_key"],
                    "title": item["title"],
                    "path": item["path"],
                    "tags": item["tags"],
                    "format": item["format"],
                    "summary": item["summary"],
                    "snippet": _snippet(item["content"], query),
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in items
            ]
        }
```

- [ ] **Step 6: Attach WorkflowService to AgentBridgeService**

Modify `src/agent_bridge/knowledge/service.py`.

Add import:

```python
from agent_bridge.workflows.service import WorkflowService
```

Inside `AgentBridgeService.__init__`, after `self.codegraph_scheduler = ...`, add:

```python
self.workflows = WorkflowService(store=store, admins=admins)
```

- [ ] **Step 7: Run service tests**

Run:

```bash
uv run pytest tests/test_workflow_service.py -v
```

Expected: PASS.

- [ ] **Step 8: Write failing API tests**

Create `tests/test_workflow_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_workflow_api_creates_and_lists_workflows(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")

    app = create_app(wm_paths, {"root"})
    client = TestClient(app)
    response = client.post(
        "/workflows",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "Nightly page report",
            "profile_key": "report-plane",
            "workflow_js": "export const manifest = {};",
            "manifest": {"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            "schedule": {"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_key"] == "page-report"

    listed = client.get("/workflows", headers={"X-Agent-Bridge-User": "root"})
    assert listed.status_code == 200
    assert [item["workflow_key"] for item in listed.json()] == ["page-report"]


def test_workflow_api_lists_artifacts(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance report",
        content="# Page A",
        metadata={},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflow-artifacts?profile_key=report-plane&query=Page",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "Page A"
```

- [ ] **Step 9: Add API schemas and routes**

Modify `src/agent_bridge/api/schemas.py`:

```python
class WorkflowDefinitionRequest(BaseModel):
    workflow_key: str
    name: str
    description: str = ""
    profile_key: str
    workflow_js: str = ""
    manifest: dict[str, Any]
    schedule: dict[str, Any]
    status: str = "active"
```

Create `src/agent_bridge/api/routes/workflows.py`:

```python
"""Workflow definition, run log, and artifact endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from agent_bridge.api.schemas import WorkflowDefinitionRequest


def create_workflow_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    @router.get("/workflows")
    def list_workflows(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.list_definitions(current_actor))

    @router.post("/workflows")
    def upsert_workflow(payload: WorkflowDefinitionRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.upsert_definition(actor=current_actor, **payload.model_dump()))

    @router.get("/workflows/{workflow_key}")
    def get_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.get_definition(current_actor, workflow_key))

    @router.get("/workflow-runs/{run_id}/logs")
    def list_run_logs(run_id: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.list_run_logs(current_actor, run_id))

    @router.get("/workflow-artifacts")
    def search_artifacts(
        profile_key: str | None = None,
        query: str | None = None,
        path: str | None = None,
        workflow_key: str | None = None,
        tags: list[str] = Query(default=[]),
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.workflows.search_artifacts(
                actor=current_actor,
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                limit=limit,
            )
        )

    return router
```

Modify `src/agent_bridge/api/app.py`, route registration section:

```python
from agent_bridge.api.routes.workflows import create_workflow_routes
app.include_router(create_workflow_routes(service, actor, call_safely, ensure_capability_schema))
```

- [ ] **Step 10: Run API tests**

Run:

```bash
uv run pytest tests/test_workflow_api.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit service and API**

```bash
git add src/agent_bridge/workflows/service.py src/agent_bridge/storage/repositories/workflows.py src/agent_bridge/storage/sqlite.py src/agent_bridge/knowledge/service.py src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/workflows.py src/agent_bridge/api/app.py tests/test_workflow_service.py tests/test_workflow_api.py
git commit -m "feat: add workflow service and artifact APIs"
```

---

## Task 3: Workflow MCP Tools And Header Gating

**Files:**
- Modify: `src/agent_bridge/capabilities/mcp_server.py`
- Modify: `src/agent_bridge/workflows/service.py`
- Test: `tests/test_workflow_mcp.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing MCP tests**

Create `tests/test_workflow_mcp.py`:

```python
from __future__ import annotations

import asyncio


def test_normal_mcp_profile_sees_artifacts_search_but_not_workflow_task_tools(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")

    mcp = create_mcp_server(svc, profile_key="report-plane", workflow_context=None)
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]

    assert "artifacts_search" in names
    assert "workflow_get_task" not in names
    assert "workflow_set_task" not in names
    assert "workflow_run_log" not in names


def test_workflow_mcp_context_sees_workflow_task_tools(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]

    assert "artifacts_search" in names
    assert "workflow_get_task" in names
    assert "workflow_set_task" in names
    assert "workflow_run_log" in names


def test_workflow_mcp_set_and_get_task(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    _, set_result = asyncio.run(
        mcp.call_tool(
            "workflow_set_task",
            {"tasks": [{"task_key": "page:a", "payload": {"page": "a"}}]},
        )
    )
    assert set_result["created"] == 1

    _, get_result = asyncio.run(mcp.call_tool("workflow_get_task", {}))
    assert get_result["task"]["task_key"] == "page:a"


def test_artifacts_search_tool_returns_profile_artifacts(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance page",
        content="# Page A\nfinance_orders",
        metadata={},
    )

    mcp = create_mcp_server(svc, profile_key="report-plane", workflow_context=None)
    _, result = asyncio.run(mcp.call_tool("artifacts_search", {"query": "finance_orders", "tags": ["finance"]}))
    assert result["items"][0]["title"] == "Page A"
```

- [ ] **Step 2: Run MCP tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_mcp.py -v
```

Expected: FAIL because `workflow_context` argument and tools do not exist.

- [ ] **Step 3: Add workflow task service methods**

Add to `src/agent_bridge/workflows/service.py`:

```python
def require_workflow_context(
    self,
    *,
    profile_key: str | None,
    workflow_key: str | None,
) -> dict[str, Any]:
    if not workflow_key:
        raise ValidationError("workflow context is required")
    workflow = self.store.get_workflow_definition(workflow_key)
    if workflow is None:
        raise NotFound("workflow not found")
    if profile_key and workflow["profile_key"] != profile_key:
        raise ValidationError("workflow profile mismatch")
    return workflow

def get_task_for_agent(self, *, profile_key: str | None, workflow_key: str, run_id: str) -> dict[str, Any]:
    self.require_workflow_context(profile_key=profile_key, workflow_key=workflow_key)
    task = self.store.lease_workflow_task(workflow_key, run_id=run_id, lease_seconds=7200)
    return {"task": task}

def set_tasks_for_agent(
    self,
    *,
    profile_key: str | None,
    workflow_key: str,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    self.require_workflow_context(profile_key=profile_key, workflow_key=workflow_key)
    normalized = []
    for task in tasks:
        task_key = str(task.get("task_key") or "").strip()
        if not task_key:
            raise ValidationError("task_key is required")
        payload = task.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValidationError("task payload must be an object")
        normalized.append({"task_key": task_key, "payload": payload})
    return self.store.upsert_workflow_tasks(workflow_key, normalized)
```

- [ ] **Step 4: Register MCP tools with explicit workflow context**

Modify `src/agent_bridge/capabilities/mcp_server.py`.

Add a context var:

```python
_request_workflow_context: ContextVar[dict[str, Any] | None] = ContextVar("_request_workflow_context", default=None)
```

Change signature:

```python
def create_mcp_server(
    service: AgentBridgeService,
    profile_key: str | None = None,
    workflow_context: dict[str, Any] | None = None,
) -> FastMCP:
```

Inside `create_mcp_server`, after `execute`, add:

```python
    @mcp.tool(description="Search workflow artifacts visible to the active Agent Bridge profile.")
    def artifacts_search(
        query: str | None = None,
        tags: list[str] | None = None,
        path: str | None = None,
        workflow_key: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        return service.workflows.search_artifacts(
            actor=default_user(),
            profile_key=active_profile,
            query=query,
            tags=tags or [],
            path=path,
            workflow_key=workflow_key,
            limit=limit,
        )

    active_workflow_context = workflow_context or _request_workflow_context.get()
    if active_workflow_context and active_workflow_context.get("workflow"):

        @mcp.tool(description="Lease one pending task for the current workflow run.")
        def workflow_get_task() -> dict[str, Any]:
            active_profile = _request_profile.get() or profile_key
            current = _request_workflow_context.get() or active_workflow_context
            return service.workflows.get_task_for_agent(
                profile_key=active_profile,
                workflow_key=str(current.get("workflow_key") or ""),
                run_id=str(current.get("run_id") or ""),
            )

        @mcp.tool(description="Create or refresh pending tasks for the current workflow.")
        def workflow_set_task(tasks: list[dict[str, Any]]) -> dict[str, Any]:
            active_profile = _request_profile.get() or profile_key
            current = _request_workflow_context.get() or active_workflow_context
            return service.workflows.set_tasks_for_agent(
                profile_key=active_profile,
                workflow_key=str(current.get("workflow_key") or ""),
                tasks=tasks,
            )

        @mcp.tool(description="Append a workflow run log entry.")
        def workflow_run_log(
            level: str = "info",
            stage: str = "",
            message: str = "",
            task_key: str | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            current = _request_workflow_context.get() or active_workflow_context
            service.workflows.append_run_log(
                workflow_key=str(current.get("workflow_key") or ""),
                run_id=str(current.get("run_id") or ""),
                task_key=task_key,
                level=level,
                stage=stage,
                message=message,
                payload=payload or {},
            )
            return {"ok": True}
```

In `setup_mcp_route`, read headers:

```python
workflow_header = request.headers.get("x-agent-bridge-workflow")
workflow_key = request.headers.get("x-agent-bridge-workflow-key")
workflow_run_id = request.headers.get("x-agent-bridge-workflow-run-id")
workflow_context = {
    "workflow": workflow_header == "true",
    "workflow_key": workflow_key,
    "run_id": workflow_run_id,
} if workflow_header == "true" else None
mcp = create_mcp_server(service, profile_key=profile, workflow_context=workflow_context)
workflow_token = _request_workflow_context.set(workflow_context)
```

Reset `workflow_token` in `finally`.

- [ ] **Step 5: Update existing MCP server test expectation**

Modify `tests/test_mcp_server.py::test_mcp_server_exposes_search_and_execute_tools`:

```python
assert tool_names == ["search", "execute", "artifacts_search"]
```

Modify `test_mcp_search_tool_has_path_query_schema` and `test_mcp_execute_tool_has_service_key_tool_arguments_schema` only if tool ordering assumptions break; keep checks by name.

- [ ] **Step 6: Run MCP tests**

Run:

```bash
uv run pytest tests/test_workflow_mcp.py tests/test_mcp_server.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit MCP tools**

```bash
git add src/agent_bridge/capabilities/mcp_server.py src/agent_bridge/workflows/service.py tests/test_workflow_mcp.py tests/test_mcp_server.py
git commit -m "feat: expose workflow artifact and task MCP tools"
```

---

## Task 4: Result Parser And Artifact Ingestion

**Files:**
- Create: `src/agent_bridge/workflows/result_parser.py`
- Modify: `src/agent_bridge/workflows/service.py`
- Test: `tests/test_workflow_result_parser.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_workflow_result_parser.py`:

```python
from __future__ import annotations

import json


def test_parse_completed_result_reads_markdown_artifact(tmp_path):
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    artifact_dir = out / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.md").write_text("# Report\n\nfinance_orders", encoding="utf-8")
    (out / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_key": "page:a",
                "artifacts": [
                    {
                        "title": "Page A",
                        "path": "reports/page-a/index.md",
                        "tags": ["finance"],
                        "format": "markdown",
                        "file": "out/artifacts/report.md",
                        "summary": "Finance report",
                        "metadata": {"page_key": "page-a"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = parse_workflow_result(tmp_path)
    assert result.status == "completed"
    assert result.task_key == "page:a"
    assert result.artifacts[0].content == "# Report\n\nfinance_orders"


def test_parse_no_executable_task_result(tmp_path):
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(
        json.dumps({"status": "no_executable_task", "reason": "empty"}),
        encoding="utf-8",
    )

    result = parse_workflow_result(tmp_path)
    assert result.status == "no_executable_task"
    assert result.reason == "empty"
    assert result.artifacts == []


def test_parse_rejects_artifact_path_outside_run_dir(tmp_path):
    from agent_bridge.core.domain import ValidationError
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_key": "page:a",
                "artifacts": [
                    {
                        "title": "Bad",
                        "path": "reports/bad.md",
                        "tags": [],
                        "format": "markdown",
                        "file": "../outside.md",
                        "summary": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        parse_workflow_result(tmp_path)
    except ValidationError as exc:
        assert "artifact file escapes run directory" in exc.message
    else:
        raise AssertionError("outside path should fail")
```

- [ ] **Step 2: Run parser tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_result_parser.py -v
```

Expected: FAIL because parser does not exist.

- [ ] **Step 3: Implement result parser**

Create `src/agent_bridge/workflows/result_parser.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.core.domain import ValidationError


@dataclass(frozen=True)
class ParsedArtifact:
    title: str
    path: str
    tags: list[str]
    format: str
    summary: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedWorkflowResult:
    status: str
    task_key: str | None = None
    reason: str | None = None
    artifacts: list[ParsedArtifact] = field(default_factory=list)


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def parse_workflow_result(run_dir: Path) -> ParsedWorkflowResult:
    result_path = run_dir / "out" / "result.json"
    if not result_path.exists():
        raise ValidationError("workflow result.json not found")
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("workflow result.json is invalid JSON") from exc
    status = str(raw.get("status") or "")
    if status == "no_executable_task":
        return ParsedWorkflowResult(status=status, reason=str(raw.get("reason") or ""))
    if status != "completed":
        raise ValidationError("workflow result status is unsupported")
    task_key = str(raw.get("task_key") or "").strip()
    if not task_key:
        raise ValidationError("completed workflow result requires task_key")
    artifacts_raw = raw.get("artifacts")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise ValidationError("completed workflow result requires artifacts")
    artifacts: list[ParsedArtifact] = []
    for item in artifacts_raw:
        if not isinstance(item, dict):
            raise ValidationError("artifact must be an object")
        artifact_file = run_dir / str(item.get("file") or "")
        if not _inside(run_dir, artifact_file):
            raise ValidationError("artifact file escapes run directory")
        if not artifact_file.exists():
            raise ValidationError("artifact file not found")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValidationError("artifact tags must be a list")
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValidationError("artifact metadata must be an object")
        artifacts.append(
            ParsedArtifact(
                title=str(item.get("title") or "").strip(),
                path=str(item.get("path") or "").strip(),
                tags=[str(tag) for tag in tags],
                format=str(item.get("format") or "markdown"),
                summary=str(item.get("summary") or ""),
                content=artifact_file.read_text(encoding="utf-8"),
                metadata=metadata,
            )
        )
    return ParsedWorkflowResult(status=status, task_key=task_key, artifacts=artifacts)
```

- [ ] **Step 4: Add service ingestion helper**

Add to `src/agent_bridge/workflows/service.py`:

```python
from agent_bridge.workflows.result_parser import ParsedWorkflowResult
```

Add method:

```python
def ingest_parsed_result(
    self,
    *,
    workflow_key: str,
    profile_key: str,
    run_id: str,
    parsed: ParsedWorkflowResult,
) -> dict[str, Any]:
    if parsed.status == "no_executable_task":
        return {"status": "no_task", "artifact_count": 0}
    if parsed.status != "completed" or not parsed.task_key:
        raise ValidationError("parsed workflow result is not ingestible")
    saved = []
    for artifact in parsed.artifacts:
        saved.append(
            self.save_artifact(
                workflow_key=workflow_key,
                profile_key=profile_key,
                run_id=run_id,
                task_key=parsed.task_key,
                title=artifact.title,
                path=artifact.path,
                tags=artifact.tags,
                format=artifact.format,
                summary=artifact.summary,
                content=artifact.content,
                metadata=artifact.metadata,
            )
        )
    self.store.complete_workflow_task(workflow_key, parsed.task_key, run_id=run_id)
    return {"status": "completed", "artifact_count": len(saved), "artifacts": saved}
```

- [ ] **Step 5: Run parser tests**

Run:

```bash
uv run pytest tests/test_workflow_result_parser.py tests/test_workflow_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit result parser**

```bash
git add src/agent_bridge/workflows/result_parser.py src/agent_bridge/workflows/service.py tests/test_workflow_result_parser.py
git commit -m "feat: parse workflow result artifacts"
```

---

## Task 5: Claude Runner

**Files:**
- Create: `src/agent_bridge/workflows/runner.py`
- Modify: `src/agent_bridge/core/config.py`
- Test: `tests/test_workflow_runner.py`

- [ ] **Step 1: Write failing runner tests**

Create `tests/test_workflow_runner.py`:

```python
from __future__ import annotations


def test_runner_prepares_run_directory_with_workflow_files(tmp_path):
    from agent_bridge.workflows.runner import WorkflowRunSpec, prepare_run_directory

    spec = WorkflowRunSpec(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        workflow_js="export const manifest = {};",
        mcp_url="http://127.0.0.1:8765/mcp",
    )

    run_dir = prepare_run_directory(tmp_path, spec)

    assert (run_dir / "workflow.js").read_text(encoding="utf-8") == "export const manifest = {};"
    assert (run_dir / "out").is_dir()
    mcp_config = (run_dir / ".mcp.json").read_text(encoding="utf-8")
    assert "X-Agent-Bridge-Workflow" in mcp_config
    assert "page-report" in mcp_config
    assert "run_1" in mcp_config


def test_fake_runner_writes_no_task_result(tmp_path):
    from agent_bridge.workflows.runner import FakeWorkflowRunner, WorkflowRunSpec

    runner = FakeWorkflowRunner(status="no_executable_task")
    result = runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="page-report",
            profile_key="report-plane",
            workflow_js="",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    assert result.exit_code == 0
    assert (result.run_dir / "out" / "result.json").exists()
```

- [ ] **Step 2: Run runner tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_runner.py -v
```

Expected: FAIL because runner module does not exist.

- [ ] **Step 3: Implement runner protocol and directory preparation**

Create `src/agent_bridge/workflows/runner.py`:

```python
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class WorkflowRunSpec:
    run_id: str
    workflow_key: str
    profile_key: str
    workflow_js: str
    mcp_url: str


@dataclass(frozen=True)
class WorkflowProcessResult:
    run_dir: Path
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    duration_ms: int


class WorkflowRunner(Protocol):
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        ...


def prepare_run_directory(base_dir: Path, spec: WorkflowRunSpec) -> Path:
    run_dir = base_dir / spec.run_id
    out_dir = run_dir / "out"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "workflow.js").write_text(spec.workflow_js, encoding="utf-8")
    (run_dir / "workflow-system-prompt.md").write_text(
        "\n".join(
            [
                "You are running an Agent Bridge workflow.",
                "Use the configured MCP tools to get or create tasks, log progress, and produce output.",
                "Write the final machine-readable result to ./out/result.json.",
            ]
        ),
        encoding="utf-8",
    )
    mcp_config = {
        "mcpServers": {
            "agent-bridge": {
                "type": "http",
                "url": spec.mcp_url,
                "headers": {
                    "X-Agent-Bridge-MetaMCP-Profile": spec.profile_key,
                    "X-Agent-Bridge-Workflow": "true",
                    "X-Agent-Bridge-Workflow-Key": spec.workflow_key,
                    "X-Agent-Bridge-Workflow-Run-Id": spec.run_id,
                },
            }
        }
    }
    (run_dir / ".mcp.json").write_text(json.dumps(mcp_config, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


class ClaudeWorkflowRunner:
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        prompt = "Run the workflow defined in ./workflow.js and write the final result to ./out/result.json."
        started = time.monotonic()
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--mcp-config",
                    "./.mcp.json",
                    "--append-system-prompt-file",
                    "./workflow-system-prompt.md",
                    prompt,
                ],
                cwd=run_dir,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
            )
        return WorkflowProcessResult(
            run_dir=run_dir,
            exit_code=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class FakeWorkflowRunner:
    def __init__(self, status: str = "no_executable_task") -> None:
        self.status = status

    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        result_path = run_dir / "out" / "result.json"
        if self.status == "completed":
            artifact_dir = run_dir / "out" / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "report.md").write_text("# Report", encoding="utf-8")
            result = {
                "status": "completed",
                "task_key": "fake-task",
                "artifacts": [
                    {
                        "title": "Fake Report",
                        "path": "reports/fake/index.md",
                        "tags": ["fake"],
                        "format": "markdown",
                        "file": "out/artifacts/report.md",
                        "summary": "Fake report",
                    }
                ],
            }
        else:
            result = {"status": "no_executable_task", "reason": "fake runner"}
        result_path.write_text(json.dumps(result), encoding="utf-8")
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return WorkflowProcessResult(run_dir=run_dir, exit_code=0, stdout_path=stdout_path, stderr_path=stderr_path, duration_ms=1)
```

- [ ] **Step 4: Run runner tests**

Run:

```bash
uv run pytest tests/test_workflow_runner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit runner**

```bash
git add src/agent_bridge/workflows/runner.py tests/test_workflow_runner.py
git commit -m "feat: add claude workflow runner"
```

---

## Task 6: Fair Workflow Scheduler

**Files:**
- Create: `src/agent_bridge/workflows/scheduler.py`
- Modify: `src/agent_bridge/knowledge/service.py`
- Modify: `src/agent_bridge/api/app.py`
- Test: `tests/test_workflow_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Create `tests/test_workflow_scheduler.py`:

```python
from __future__ import annotations


def _create_workflow(store, key: str, profile_key: str = "report-plane"):
    store.upsert_workflow_definition(
        workflow_key=key,
        name=key,
        description="",
        profile_key=profile_key,
        workflow_js="",
        manifest={"name": key, "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )


def test_scheduler_selects_different_workflows_with_round_robin(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.scheduler import WorkflowScheduler
    from agent_bridge.workflows.runner import FakeWorkflowRunner

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for key in ["A", "B", "C", "D"]:
        _create_workflow(svc.store, key)

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(),
        max_concurrent_workflows=2,
    )

    first = scheduler.next_workflow_batch({"A", "B", "C", "D"}, running=set())
    second = scheduler.next_workflow_batch({"A", "B", "C", "D"}, running=set(first))

    assert first == ["A", "B"]
    assert second == ["C", "D"]


def test_scheduler_marks_no_task_workflow_finished_for_day(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.scheduler import WorkflowScheduler
    from agent_bridge.workflows.runner import FakeWorkflowRunner

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, "A")

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"),
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )

    result = scheduler.run_one_workflow("A")
    assert result["status"] == "no_task"
    assert "A" in scheduler.finished_today
```

- [ ] **Step 2: Run scheduler tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workflow_scheduler.py -v
```

Expected: FAIL because scheduler does not exist.

- [ ] **Step 3: Implement scheduler**

Create `src/agent_bridge/workflows/scheduler.py`:

```python
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.workflows.result_parser import parse_workflow_result
from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec, WorkflowRunner
from agent_bridge.workflows.service import WorkflowService

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    def __init__(
        self,
        *,
        service: WorkflowService,
        store: SQLiteStore,
        admins: set[str],
        runner: WorkflowRunner | None = None,
        base_run_dir: Path | None = None,
        mcp_url: str = "http://127.0.0.1:8765/mcp",
        max_concurrent_workflows: int = 2,
    ) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._runner = runner or ClaudeWorkflowRunner()
        self._base_run_dir = base_run_dir
        self._mcp_url = mcp_url
        self._max_concurrent = max_concurrent_workflows
        self._scheduler: BackgroundScheduler | None = None
        self._cursor = 0
        self._running: set[str] = set()
        self.finished_today: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self.tick, trigger=IntervalTrigger(seconds=30), id="workflow_tick")
        self._scheduler.start()

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "running_workflows": sorted(self._running),
            "finished_today": sorted(self.finished_today),
            "max_concurrent_workflows": self._max_concurrent,
        }

    def next_workflow_batch(self, candidates: set[str], running: set[str]) -> list[str]:
        ordered = sorted(candidates)
        if not ordered:
            return []
        selected: list[str] = []
        attempts = 0
        while len(selected) < self._max_concurrent and attempts < len(ordered):
            index = (self._cursor + attempts) % len(ordered)
            key = ordered[index]
            if key not in running and key not in selected:
                selected.append(key)
            attempts += 1
        self._cursor = (self._cursor + attempts) % len(ordered)
        return selected

    def tick(self) -> None:
        with self._lock:
            workflows = [
                item for item in self._store.list_workflow_definitions()
                if item.get("status") == "active" and (item.get("schedule") or {}).get("enabled", True)
            ]
            candidates = {item["workflow_key"] for item in workflows} - self.finished_today
            available = self._max_concurrent - len(self._running)
            if available <= 0:
                return
            batch = self.next_workflow_batch(candidates, self._running)[:available]
            for workflow_key in batch:
                self._running.add(workflow_key)
                thread = threading.Thread(target=self._run_and_release, args=(workflow_key,), daemon=True)
                thread.start()

    def _run_and_release(self, workflow_key: str) -> None:
        try:
            self.run_one_workflow(workflow_key)
        finally:
            with self._lock:
                self._running.discard(workflow_key)

    def run_one_workflow(self, workflow_key: str) -> dict[str, Any]:
        workflow = self._store.get_workflow_definition(workflow_key)
        if workflow is None:
            self.finished_today.add(workflow_key)
            return {"status": "missing"}
        run_id = f"run_{uuid.uuid4().hex}"
        base_dir = self._base_run_dir or Path("workflow-runs")
        self._store.create_workflow_run(
            run_id=run_id,
            workflow_key=workflow_key,
            profile_key=workflow["profile_key"],
            task_key=None,
            status="running",
            temp_dir=str(base_dir / run_id),
        )
        result = self._runner.run(
            base_dir,
            WorkflowRunSpec(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                workflow_js=workflow["workflow_js"],
                mcp_url=self._mcp_url,
            ),
        )
        if result.exit_code != 0:
            self._store.finish_workflow_run(
                run_id,
                status="failed",
                exit_code=result.exit_code,
                stdout_path=str(result.stdout_path),
                stderr_path=str(result.stderr_path),
                error="claude workflow runner failed",
                duration_ms=result.duration_ms,
            )
            return {"status": "failed"}
        parsed = parse_workflow_result(result.run_dir)
        ingested = self._service.ingest_parsed_result(
            workflow_key=workflow_key,
            profile_key=workflow["profile_key"],
            run_id=run_id,
            parsed=parsed,
        )
        final_status = ingested["status"]
        if final_status == "no_task":
            self.finished_today.add(workflow_key)
        self._store.finish_workflow_run(
            run_id,
            status=final_status,
            exit_code=result.exit_code,
            stdout_path=str(result.stdout_path),
            stderr_path=str(result.stderr_path),
            error=None,
            duration_ms=result.duration_ms,
        )
        return ingested
```

- [ ] **Step 4: Attach scheduler to service lifecycle**

Modify `src/agent_bridge/knowledge/service.py`.

Add import:

```python
from agent_bridge.workflows.scheduler import WorkflowScheduler
```

After `self.workflows = WorkflowService(...)`, add:

```python
self.workflow_scheduler = WorkflowScheduler(
    service=self.workflows,
    store=store,
    admins=admins,
    base_run_dir=paths.run_dir / "workflow-runs",
)
```

Modify `src/agent_bridge/api/app.py` lifespan:

```python
service.workflow_scheduler.start()
```

Add stop before other scheduler stops:

```python
service.workflow_scheduler.stop()
```

- [ ] **Step 5: Run scheduler tests**

Run:

```bash
uv run pytest tests/test_workflow_scheduler.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit scheduler**

```bash
git add src/agent_bridge/workflows/scheduler.py src/agent_bridge/knowledge/service.py src/agent_bridge/api/app.py tests/test_workflow_scheduler.py
git commit -m "feat: add fair workflow scheduler"
```

---

## Task 7: Frontend Workflow Console

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Test: `tests/test_capability_api.py`

- [ ] **Step 1: Write failing frontend source test**

Append to `tests/test_capability_api.py`:

```python
def test_frontend_workflow_view_exposes_workflow_management() -> None:
    source = Path("frontend/capabilities/src/views/workflow/WorkflowView.vue").read_text(encoding="utf-8")
    assert "workflow_key" in source
    assert "profile_key" in source
    assert "artifacts_search" in source
    assert "manifest" in source
    assert "workflow_js" in source
```

If `Path` is not imported at the top of `tests/test_capability_api.py`, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Run frontend source test and confirm failure**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_frontend_workflow_view_exposes_workflow_management -v
```

Expected: FAIL because the view is still a placeholder.

- [ ] **Step 3: Add frontend types**

Append to `frontend/capabilities/src/api/types.ts`:

```ts
export interface WorkflowDefinition {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  workflow_js: string
  manifest: {
    name: string
    nodes: { id: string; name?: string; inputs?: string[]; outputs?: string[]; description?: string }[]
    edges: [string, string][]
    schemas: Record<string, unknown>
  }
  schedule: {
    enabled?: boolean
    start_time?: string
    stop_time?: string
  }
  status: string
  created_by: string
  created_at: string
  updated_at: string
}

export interface WorkflowArtifactSearchResult {
  items: {
    artifact_id: string
    workflow_key: string
    profile_key: string
    run_id: string
    task_key: string | null
    title: string
    path: string
    tags: string[]
    format: string
    summary: string
    snippet: string
    created_at: string
    updated_at: string
  }[]
}
```

- [ ] **Step 4: Add API client methods**

Modify imports in `frontend/capabilities/src/api/client.ts` to include:

```ts
  WorkflowDefinition,
  WorkflowArtifactSearchResult,
```

Add methods inside `api`:

```ts
  // Workflows
  listWorkflows: () => get<WorkflowDefinition[]>('/workflows'),
  upsertWorkflow: (workflow: Partial<WorkflowDefinition> & { workflow_key: string; name: string; profile_key: string }) =>
    post<WorkflowDefinition>('/workflows', workflow),
  getWorkflow: (workflowKey: string) => get<WorkflowDefinition>(`/workflows/${workflowKey}`),
  searchWorkflowArtifacts: (params: { profile_key?: string; query?: string; path?: string; workflow_key?: string; tags?: string[]; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params.profile_key) qs.set('profile_key', params.profile_key)
    if (params.query) qs.set('query', params.query)
    if (params.path) qs.set('path', params.path)
    if (params.workflow_key) qs.set('workflow_key', params.workflow_key)
    if (params.limit) qs.set('limit', String(params.limit))
    ;(params.tags || []).forEach(tag => qs.append('tags', tag))
    return get<WorkflowArtifactSearchResult>(`/workflow-artifacts?${qs}`)
  },
```

- [ ] **Step 5: Replace WorkflowView placeholder**

Replace `frontend/capabilities/src/views/workflow/WorkflowView.vue` with a practical first version:

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { ProjectProfile, WorkflowDefinition, WorkflowArtifactSearchResult } from '@/api/types'

const workflows = ref<WorkflowDefinition[]>([])
const profiles = ref<ProjectProfile[]>([])
const artifacts = ref<WorkflowArtifactSearchResult['items']>([])
const selectedKey = ref('')
const query = ref('')
const tagInput = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const draft = ref({
  workflow_key: '',
  name: '',
  description: '',
  profile_key: '',
  workflow_js: 'export const manifest = {\\n  name: "页面知识沉淀",\\n  nodes: [],\\n  edges: [],\\n  schemas: {}\\n};\\n',
  manifest_text: '{\\n  "name": "页面知识沉淀",\\n  "nodes": [],\\n  "edges": [],\\n  "schemas": {}\\n}',
  schedule: { enabled: true, start_time: '22:00', stop_time: '07:00' },
  status: 'active',
})

const selectedWorkflow = computed(() => workflows.value.find(item => item.workflow_key === selectedKey.value) || null)
const selectedManifest = computed(() => selectedWorkflow.value?.manifest || null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [workflowRows, profileRows] = await Promise.all([api.listWorkflows(), api.listProfiles()])
    workflows.value = workflowRows
    profiles.value = profileRows
    if (!selectedKey.value && workflowRows[0]) selectedKey.value = workflowRows[0].workflow_key
    await searchArtifacts()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function editWorkflow(workflow: WorkflowDefinition) {
  selectedKey.value = workflow.workflow_key
  draft.value = {
    workflow_key: workflow.workflow_key,
    name: workflow.name,
    description: workflow.description,
    profile_key: workflow.profile_key,
    workflow_js: workflow.workflow_js,
    manifest_text: JSON.stringify(workflow.manifest, null, 2),
    schedule: { enabled: true, start_time: '22:00', stop_time: '07:00', ...workflow.schedule },
    status: workflow.status,
  }
}

async function saveWorkflow() {
  saving.value = true
  error.value = ''
  try {
    const manifest = JSON.parse(draft.value.manifest_text)
    await api.upsertWorkflow({
      workflow_key: draft.value.workflow_key,
      name: draft.value.name,
      description: draft.value.description,
      profile_key: draft.value.profile_key,
      workflow_js: draft.value.workflow_js,
      manifest,
      schedule: draft.value.schedule,
      status: draft.value.status,
    })
    await load()
    selectedKey.value = draft.value.workflow_key
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function searchArtifacts() {
  const tags = tagInput.value.split(',').map(item => item.trim()).filter(Boolean)
  const result = await api.searchWorkflowArtifacts({
    profile_key: selectedWorkflow.value?.profile_key,
    workflow_key: selectedKey.value || undefined,
    query: query.value || undefined,
    tags,
    limit: 20,
  })
  artifacts.value = result.items
}

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h2 class="text-xl font-semibold text-foreground">工作流管理</h2>
        <p class="text-sm text-muted-foreground">粘贴 Claude Code workflow，绑定 Profile，并查看流程结构和产出物。</p>
      </div>
      <button class="px-3 py-2 text-sm border rounded-md" :disabled="loading" @click="load">刷新</button>
    </div>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{{ error }}</div>

    <div class="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <aside class="border rounded-md bg-card">
        <div class="border-b px-4 py-3 text-sm font-medium">Workflow</div>
        <button
          v-for="workflow in workflows"
          :key="workflow.workflow_key"
          class="block w-full border-b px-4 py-3 text-left text-sm hover:bg-muted"
          :class="{ 'bg-muted': selectedKey === workflow.workflow_key }"
          @click="selectedKey = workflow.workflow_key; searchArtifacts()"
        >
          <div class="font-medium">{{ workflow.name }}</div>
          <div class="mt-1 text-xs text-muted-foreground">{{ workflow.workflow_key }} · {{ workflow.profile_key }}</div>
        </button>
      </aside>

      <section class="space-y-4">
        <div class="grid gap-4 xl:grid-cols-2">
          <div class="border rounded-md bg-card p-4">
            <h3 class="text-sm font-semibold mb-3">定义</h3>
            <div class="grid gap-3">
              <input v-model="draft.workflow_key" class="border rounded-md px-3 py-2 text-sm" placeholder="workflow_key" />
              <input v-model="draft.name" class="border rounded-md px-3 py-2 text-sm" placeholder="名称" />
              <select v-model="draft.profile_key" class="border rounded-md px-3 py-2 text-sm">
                <option value="">选择 Profile</option>
                <option v-for="profile in profiles" :key="profile.profile_key" :value="profile.profile_key">{{ profile.name }} · {{ profile.profile_key }}</option>
              </select>
              <textarea v-model="draft.description" class="min-h-16 border rounded-md px-3 py-2 text-sm" placeholder="说明" />
              <textarea v-model="draft.manifest_text" class="min-h-40 border rounded-md px-3 py-2 font-mono text-xs" placeholder="manifest JSON" />
              <textarea v-model="draft.workflow_js" class="min-h-64 border rounded-md px-3 py-2 font-mono text-xs" placeholder="workflow_js" />
              <button class="px-3 py-2 text-sm border rounded-md bg-primary text-primary-foreground" :disabled="saving" @click="saveWorkflow">保存</button>
            </div>
          </div>

          <div class="border rounded-md bg-card p-4">
            <h3 class="text-sm font-semibold mb-3">流程信息</h3>
            <div v-if="selectedManifest" class="space-y-4">
              <div>
                <div class="text-xs font-medium text-muted-foreground mb-2">节点</div>
                <div v-for="node in selectedManifest.nodes" :key="node.id" class="mb-2 rounded-md border px-3 py-2">
                  <div class="text-sm font-medium">{{ node.name || node.id }}</div>
                  <div class="text-xs text-muted-foreground">{{ node.id }}</div>
                </div>
              </div>
              <div>
                <div class="text-xs font-medium text-muted-foreground mb-2">流转</div>
                <div v-for="(edge, index) in selectedManifest.edges" :key="index" class="text-sm font-mono">{{ edge[0] }} -> {{ edge[1] }}</div>
              </div>
              <pre class="max-h-64 overflow-auto rounded-md border bg-muted p-3 text-xs">{{ selectedManifest.schemas }}</pre>
            </div>
            <div v-else class="text-sm text-muted-foreground">选择一个 workflow 查看 manifest。</div>
          </div>
        </div>

        <div class="border rounded-md bg-card p-4">
          <div class="flex items-center justify-between gap-3 mb-3">
            <h3 class="text-sm font-semibold">产出物</h3>
            <div class="flex gap-2">
              <input v-model="query" class="border rounded-md px-3 py-2 text-sm" placeholder="搜索内容" @keyup.enter="searchArtifacts" />
              <input v-model="tagInput" class="border rounded-md px-3 py-2 text-sm" placeholder="tags: finance,etl" @keyup.enter="searchArtifacts" />
              <button class="px-3 py-2 text-sm border rounded-md" @click="searchArtifacts">搜索</button>
            </div>
          </div>
          <div v-for="artifact in artifacts" :key="artifact.artifact_id" class="border-t py-3">
            <div class="text-sm font-medium">{{ artifact.title }}</div>
            <div class="mt-1 font-mono text-xs text-muted-foreground">{{ artifact.path }}</div>
            <p class="mt-2 text-sm text-muted-foreground">{{ artifact.summary || artifact.snippet }}</p>
            <div class="mt-2 flex flex-wrap gap-1">
              <span v-for="tag in artifact.tags" :key="tag" class="rounded border px-2 py-0.5 text-xs">{{ tag }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
```

- [ ] **Step 6: Run frontend source test**

Run:

```bash
uv run pytest tests/test_capability_api.py::test_frontend_workflow_view_exposes_workflow_management -v
```

Expected: PASS.

- [ ] **Step 7: Run frontend build**

Run:

```bash
cd frontend/capabilities
npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit frontend console**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/workflow/WorkflowView.vue tests/test_capability_api.py
git commit -m "feat: add workflow management console"
```

---

## Task 8: Final Integration And Regression Verification

**Files:**
- Modify: files found during prior tasks only when verification exposes integration gaps.

- [ ] **Step 1: Run focused workflow test suite**

Run:

```bash
uv run pytest tests/test_workflow_storage.py tests/test_workflow_service.py tests/test_workflow_mcp.py tests/test_workflow_result_parser.py tests/test_workflow_runner.py tests/test_workflow_scheduler.py tests/test_workflow_api.py -v
```

Expected: PASS.

- [ ] **Step 2: Run related existing backend tests**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_capability_api.py tests/test_capability_service.py tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend/capabilities
npm run build
```

Expected: PASS.

- [ ] **Step 4: Inspect git diff for accidental unrelated changes**

Run:

```bash
git status --short
git diff --stat
```

Expected: only workflow feature files are changed.

- [ ] **Step 5: Commit final fixes if any**

If Step 1, Step 2, or Step 3 required integration fixes, commit them:

```bash
git add <changed-workflow-files>
git commit -m "fix: stabilize workflow integration"
```

If no files changed after verification, skip this commit.

---

## Self-Review

Spec coverage:

- Workflow definitions with required profile binding: Task 1, Task 2, Task 7.
- Claude Code workflow JS storage and manifest rendering: Task 2, Task 7.
- Separate artifact management with tags and path: Task 2, Task 4, Task 7.
- MCP tools `workflow_set_task`, `workflow_get_task`, `workflow_run_log`, `artifacts_search`: Task 3.
- Workflow-only MCP visibility by header: Task 3.
- Task lease and idempotent task creation: Task 1, Task 3.
- `claude -p` temporary run directory and MCP config injection: Task 5.
- Result protocol and artifact ingestion: Task 4, Task 6.
- Shared scheduler, max two workflows, round-robin fairness, no-task daily ending: Task 6.
- Console page for definitions, manifest nodes/edges/schemas, artifacts: Task 7.
- Verification and regression coverage: Task 8.

Placeholder scan:

- The plan avoids open-ended implementation placeholders.
- Each task includes concrete file paths, focused tests, implementation snippets, commands, and expected results.

Type consistency:

- Backend names use `workflow_key`, `profile_key`, `run_id`, `task_key`, `manifest`, `schedule`, `workflow_js`.
- MCP names are exactly `artifacts_search`, `workflow_get_task`, `workflow_set_task`, `workflow_run_log`.
- Artifact schema uses `title`, `path`, `tags`, `format`, `summary`, `content`, `metadata`.
