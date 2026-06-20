# Script Runtime Context And Return Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace managed script `stdout JSON` parsing with a `main(envelope) -> dict` protocol, while making `profile` and `workflow` execution context trusted, inherited, and available through runtime helpers.

**Architecture:** Keep scripts isolated in subprocesses, but stop using `stdout` as the result transport. Instead, run each script through a generated bootstrap runner that imports the script, calls `main(envelope)`, writes the returned `dict` to a result file, and still captures `stdout/stderr` as logs. Add trusted runtime context parsing at the API boundary, plumb it through `CapabilityService.execute(...)` for built-in `run_script`, and expose dedicated runtime workflow helper endpoints that reuse the existing workflow service validation.

**Tech Stack:** FastAPI, Python subprocess execution, Pydantic schemas, existing Agent Bridge capability/workflow services, pytest

---

## File Structure

- Create: `src/agent_bridge/api/runtime_context.py`
  Parses trusted `profile` and `workflow` headers from `Request` and returns normalized context dictionaries.

- Create: `src/agent_bridge/api/routes/script_runtime.py`
  Adds internal HTTP endpoints for `workflow_get_task`, `workflow_set_task`, and `workflow_run_log` that read trusted context from headers.

- Create: `src/agent_bridge/system_config/scripts/runtime_support.py`
  Owns the generated bootstrap runner and runtime helper source strings so `ScriptService` no longer embeds large Python source blobs inline.

- Modify: `src/agent_bridge/api/app.py`
  Registers the new script runtime router.

- Modify: `src/agent_bridge/api/routes/builtins.py`
  Reads trusted runtime headers for `/scripts/{script_key}/test` and passes them into `ScriptService.test_script(...)`.

- Modify: `src/agent_bridge/api/routes/capabilities.py`
  Reads trusted runtime headers for `/capabilities/execute` and passes workflow context into `CapabilityService.execute(...)`.

- Modify: `src/agent_bridge/api/schemas.py`
  Removes `profile_key` from `ScriptTestRunRequest` and adds request bodies for runtime workflow endpoints.

- Modify: `src/agent_bridge/capability_hub/service.py`
  Extends `execute(...)` and `_execute_builtin(...)` to accept trusted `workflow_context`.

- Modify: `src/agent_bridge/capability_hub/gateway/metamcp.py`
  Forwards current workflow context into `CapabilityService.execute(...)` when top-level `execute` runs.

- Modify: `src/agent_bridge/capability_hub/sources/builtin/base.py`
  Extends the built-in provider protocol to accept trusted `workflow_context`.

- Modify: `src/agent_bridge/capability_hub/sources/builtin/platform.py`
  Forwards trusted `workflow_context` to `ScriptService.run_script(...)`.

- Modify: `src/agent_bridge/capability_hub/sources/builtin/wiki.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/codegraph.py`
  Signature-only updates so all built-in providers conform to the new protocol.

- Modify: `src/agent_bridge/system_config/scripts/service.py`
  Reworks the script execution flow to build a richer envelope, set workflow env vars, invoke the bootstrap runner, and read result files instead of parsing `stdout`.

- Modify: `tests/test_scripts.py`
  Covers `main(envelope)` return semantics, helper API changes, and script-level workflow behavior.

- Create: `tests/test_script_runtime_api.py`
  Covers `/scripts/.../test` header injection and the new runtime workflow HTTP endpoints.

- Modify: `tests/test_workflow_mcp.py`
  Keeps MCP workflow top-level tool behavior aligned while the script runtime grows its own helper entrypoints.

---

### Task 1: Add Trusted Runtime Context Parsing And Workflow Runtime Routes

**Files:**
- Create: `src/agent_bridge/api/runtime_context.py`
- Create: `src/agent_bridge/api/routes/script_runtime.py`
- Modify: `src/agent_bridge/api/app.py`
- Modify: `src/agent_bridge/api/routes/builtins.py`
- Modify: `src/agent_bridge/api/routes/capabilities.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/capability_hub/service.py`
- Modify: `src/agent_bridge/capability_hub/gateway/metamcp.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/base.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/platform.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/wiki.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/codegraph.py`
- Create: `tests/test_script_runtime_api.py`

- [ ] **Step 1: Write the failing API tests for trusted runtime headers and workflow runtime routes**

```python
from __future__ import annotations

from fastapi.testclient import TestClient


SCRIPT_CODE = """
def main(envelope):
    return {
        "profile_key": envelope["profile_key"],
        "workflow": envelope["workflow"],
    }
"""


def _create_client(wm_paths):
    from agent_bridge.api.app import create_app

    app = create_app(wm_paths, {"root"})
    return TestClient(app)


def _register_script(client: TestClient) -> None:
    response = client.post(
        "/scripts",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "script_key": "system.ctx_echo",
            "name": "Context Echo",
            "description": "",
            "language": "python",
            "code": SCRIPT_CODE,
            "status": "active",
            "owner_type": "system",
            "owner_key": "",
        },
    )
    assert response.status_code == 200


def test_script_test_route_injects_profile_and_workflow_headers(wm_paths):
    client = _create_client(wm_paths)
    _register_script(client)

    response = client.post(
        "/scripts/system.ctx_echo/test",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={"script_params": {"limit": 3}, "timeout_seconds": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["profile_key"] == "report-plane"
    assert payload["result"]["workflow"] == {
        "enabled": True,
        "workflow_key": "page-report",
        "run_id": "run_1",
    }


def test_runtime_workflow_routes_require_complete_headers(wm_paths):
    client = _create_client(wm_paths)

    response = client.post(
        "/runtime/workflow/get-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
        },
        json={},
    )

    assert response.status_code == 400
    assert "workflow context is required" in response.text


def test_runtime_workflow_routes_use_trusted_header_context(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    client = _create_client(wm_paths)
    svc: AgentBridgeService = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )

    set_response = client.post(
        "/runtime/workflow/set-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={"tasks": [{"task_key": "page:a", "payload": {"page": "a"}}]},
    )
    assert set_response.status_code == 200
    assert set_response.json()["created"] == 1

    get_response = client.post(
        "/runtime/workflow/get-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={},
    )
    assert get_response.status_code == 200
    assert get_response.json()["task"]["lease_run_id"] == "run_1"
```

- [ ] **Step 2: Run the new API tests to verify they fail**

Run: `uv run pytest tests/test_script_runtime_api.py -v`

Expected: FAIL because the route module does not exist, `/runtime/workflow/*` is unregistered, and `/scripts/{script_key}/test` does not yet read runtime headers.

- [ ] **Step 3: Add a shared runtime context parser and runtime workflow request schemas**

```python
# src/agent_bridge/api/runtime_context.py
from __future__ import annotations

from typing import Any

from fastapi import Request


def profile_from_headers(request: Request) -> str | None:
    value = request.headers.get("x-agent-bridge-metamcp-profile", "").strip()
    return value or None


def workflow_context_from_headers(request: Request) -> dict[str, Any] | None:
    workflow_enabled = request.headers.get("x-agent-bridge-workflow", "").strip().lower() == "true"
    workflow_key = request.headers.get("x-agent-bridge-workflow-key", "").strip()
    run_id = request.headers.get("x-agent-bridge-workflow-run-id", "").strip()
    if not workflow_enabled:
        return None
    if not workflow_key or not run_id:
        return {"workflow": True, "workflow_key": workflow_key or None, "run_id": run_id or None}
    return {"workflow": True, "workflow_key": workflow_key, "run_id": run_id}
```

```python
# src/agent_bridge/api/schemas.py
class ScriptTestRunRequest(BaseModel):
    script_params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None


class RuntimeWorkflowSetTaskRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeWorkflowRunLogRequest(BaseModel):
    level: str = "info"
    stage: str = ""
    message: str = ""
    task_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Implement runtime workflow routes and plumb trusted context through execute/test-script paths**

```python
# src/agent_bridge/api/routes/script_runtime.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_bridge.api.runtime_context import profile_from_headers, workflow_context_from_headers
from agent_bridge.api.schemas import RuntimeWorkflowRunLogRequest, RuntimeWorkflowSetTaskRequest


def create_script_runtime_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    def require_runtime_context(request: Request) -> tuple[str | None, dict[str, str]]:
        profile_key = profile_from_headers(request)
        workflow_context = workflow_context_from_headers(request)
        if not workflow_context or not workflow_context.get("workflow_key") or not workflow_context.get("run_id"):
            raise HTTPException(status_code=400, detail="workflow context is required")
        return profile_key, {
            "workflow_key": str(workflow_context["workflow_key"]),
            "run_id": str(workflow_context["run_id"]),
        }

    @router.post("/runtime/workflow/get-task")
    def runtime_workflow_get_task(request: Request, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        profile_key, current = require_runtime_context(request)
        return call_safely(
            lambda: service.workflows.get_task_for_agent(
                profile_key=profile_key,
                workflow_key=current["workflow_key"],
                run_id=current["run_id"],
            )
        )

    @router.post("/runtime/workflow/set-task")
    def runtime_workflow_set_task(
        payload: RuntimeWorkflowSetTaskRequest,
        request: Request,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        profile_key, current = require_runtime_context(request)
        return call_safely(
            lambda: service.workflows.set_tasks_for_agent(
                profile_key=profile_key,
                workflow_key=current["workflow_key"],
                run_id=current["run_id"],
                tasks=payload.tasks,
            )
        )
```

```python
# src/agent_bridge/capability_hub/service.py
async def execute(
    self,
    actor: str,
    service: str,
    tool_name: str,
    params: dict[str, Any],
    profile_key: str | None = None,
    workflow_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
    result = await self._execute_builtin(actor, service, tool_name, params, profile_key, workflow_context)


async def _execute_builtin(
    self,
    actor: str,
    service: str,
    tool_name: str,
    params: dict[str, Any],
    profile_key: str | None,
    workflow_context: dict[str, Any] | None,
) -> dict[str, Any]:
    result = await provider.execute(actor, tool_name, params, profile_key, workflow_context)
```

```python
# src/agent_bridge/capability_hub/sources/builtin/platform.py
async def execute(
    self,
    actor: str,
    tool: str,
    arguments: dict[str, Any],
    profile_key: str | None,
    workflow_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
    return self.service.scripts.run_script(
        actor=actor,
        script_key=script_key,
        script_params=script_params,
        timeout_seconds=arguments.get("timeout_seconds"),
        profile_key=profile_key,
        workflow_context=workflow_context,
        run_type="mcp",
    )
```

```python
# src/agent_bridge/api/routes/builtins.py
@router.post("/scripts/{script_key}/test")
def test_script(
    script_key: str,
    payload: ScriptTestRunRequest,
    request: Request,
    current_actor: str = Depends(actor),
) -> dict[str, Any]:
    ensure_capability_schema()
    return call_safely(
        lambda: service.scripts.test_script(
            actor=current_actor,
            script_key=script_key,
            script_params=payload.script_params,
            timeout_seconds=payload.timeout_seconds,
            profile_key=profile_from_headers(request),
            workflow_context=workflow_context_from_headers(request),
        )
    )
```

- [ ] **Step 5: Run the API tests and existing workflow MCP tests**

Run: `uv run pytest tests/test_script_runtime_api.py tests/test_workflow_mcp.py -v`

Expected: PASS. `tests/test_workflow_mcp.py` continues proving that MCP top-level workflow tools remain dynamic and unchanged, while the new runtime workflow routes work only with trusted headers.

- [ ] **Step 6: Commit the trusted runtime context plumbing**

```bash
git add \
  src/agent_bridge/api/runtime_context.py \
  src/agent_bridge/api/routes/script_runtime.py \
  src/agent_bridge/api/app.py \
  src/agent_bridge/api/routes/builtins.py \
  src/agent_bridge/api/routes/capabilities.py \
  src/agent_bridge/api/schemas.py \
  src/agent_bridge/capability_hub/service.py \
  src/agent_bridge/capability_hub/gateway/metamcp.py \
  src/agent_bridge/capability_hub/sources/builtin/base.py \
  src/agent_bridge/capability_hub/sources/builtin/platform.py \
  src/agent_bridge/capability_hub/sources/builtin/wiki.py \
  src/agent_bridge/capability_hub/sources/builtin/codegraph.py \
  tests/test_script_runtime_api.py
git commit -m "feat: add trusted script runtime context"
```

---

### Task 2: Replace Stdout JSON Parsing With `main(envelope) -> dict`

**Files:**
- Create: `src/agent_bridge/system_config/scripts/runtime_support.py`
- Modify: `src/agent_bridge/system_config/scripts/service.py`
- Modify: `tests/test_scripts.py`

- [ ] **Step 1: Replace the old stdout-based script tests with failing tests for the new protocol**

```python
MAIN_SCRIPT = """
def main(envelope):
    print("hello from stdout")
    return {
        "run_type": envelope["run_type"],
        "script_key": envelope["script_key"],
        "params": envelope["script_params"],
    }
"""


def test_script_service_runs_main_and_persists_return_value(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="workflow.import_tasks",
        name="Import tasks",
        description="Create workflow tasks from upstream data",
        language="python",
        code=MAIN_SCRIPT,
        status="active",
        owner_type="system",
        owner_key="",
    )

    result = service.scripts.test_script(
        actor="root",
        script_key="workflow.import_tasks",
        script_params={"limit": 3},
        timeout_seconds=10,
    )

    assert result["status"] == "success"
    assert result["result"] == {
        "run_type": "test",
        "script_key": "workflow.import_tasks",
        "params": {"limit": 3},
    }
    assert "hello from stdout" in result["stdout"]


def test_script_run_requires_main_function(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.no_main",
        name="No Main",
        description="",
        language="python",
        code="print('still only logs')",
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="missing script main\\(envelope\\)"):
        service.scripts.test_script(
            actor="root",
            script_key="system.no_main",
            script_params={},
            timeout_seconds=10,
        )


def test_script_run_requires_main_to_return_object(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.bad_return",
        name="Bad Return",
        description="",
        language="python",
        code="def main(envelope):\\n    return ['not', 'a', 'dict']\\n",
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="script main\\(envelope\\) must return a JSON object"):
        service.scripts.test_script(
            actor="root",
            script_key="system.bad_return",
            script_params={},
            timeout_seconds=10,
        )
```

- [ ] **Step 2: Run the script tests to verify they fail under the current stdout-only implementation**

Run: `uv run pytest tests/test_scripts.py -v`

Expected: FAIL because the service still parses `stdout` as the result channel and does not know how to import and call `main(envelope)`.

- [ ] **Step 3: Add bootstrap runner support and switch ScriptService to result-file reading**

```python
# src/agent_bridge/system_config/scripts/runtime_support.py
from __future__ import annotations


def render_runner() -> str:
    return """from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("managed_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    run_dir = Path.cwd()
    envelope = json.loads((run_dir / "envelope.json").read_text(encoding="utf-8"))
    module = _load_module(run_dir / "script.py")
    entry = getattr(module, "main", None)
    if entry is None or not callable(entry):
        raise RuntimeError("missing script main(envelope)")
    result = entry(envelope)
    if not isinstance(result, dict):
        raise RuntimeError("script main(envelope) must return a JSON object")
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""
```

```python
# src/agent_bridge/system_config/scripts/service.py
def test_script(
    self,
    *,
    actor: str,
    script_key: str,
    script_params: dict[str, Any] | None,
    timeout_seconds: int | None,
    profile_key: str | None = None,
    workflow_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return self.run_script(
        actor=actor,
        script_key=script_key,
        script_params=script_params or {},
        timeout_seconds=timeout_seconds,
        profile_key=profile_key,
        workflow_context=workflow_context,
        run_type="test",
    )


def run_script(
    self,
    *,
    actor: str,
    script_key: str,
    script_params: dict[str, Any],
    timeout_seconds: int | None,
    profile_key: str | None,
    workflow_context: dict[str, Any] | None,
    run_type: str = "mcp",
) -> dict[str, Any]:
    envelope = {
        "run_id": run_id,
        "run_type": run_type,
        "script_key": script["script_key"],
        "script_params": script_params,
        "profile_key": profile_key,
        "workflow": {
            "enabled": bool(workflow_context and workflow_context.get("workflow")),
            "workflow_key": (workflow_context or {}).get("workflow_key"),
            "run_id": (workflow_context or {}).get("run_id"),
        },
    }
```

```python
# src/agent_bridge/system_config/scripts/service.py
from agent_bridge.system_config.scripts.runtime_support import render_runner, render_runtime_helper


def _run_python(...):
    run_dir = self.base_run_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "script.py").write_text(str(script["code"]), encoding="utf-8")
    (run_dir / "envelope.json").write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    (run_dir / "script_runner.py").write_text(render_runner(), encoding="utf-8")
    (run_dir / "agent_bridge_runtime.py").write_text(render_runtime_helper(), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(run_dir / "script_runner.py")],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        cwd=run_dir,
        env=self._runtime_env(actor, envelope),
        check=False,
    )
```

```python
# src/agent_bridge/system_config/scripts/service.py
def run_script(...):
    ...
    result_path = self.base_run_dir / run_id / "result.json"
    if process.timed_out:
        ...
    elif process.exit_code != 0:
        stderr_text = process.stderr.strip()
        stdout_text = process.stdout.strip()
        detail = stderr_text or stdout_text or f"script exited with code {process.exit_code}"
        error_message = detail if "main(envelope)" in detail else f"script exited with code {process.exit_code}"
    elif not result_path.is_file():
        status = "failed"
        error_message = "script main(envelope) did not produce result.json"
    else:
        parsed = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            status = "failed"
            error_message = "script main(envelope) must return a JSON object"
        else:
            result = parsed
```

- [ ] **Step 4: Run the script tests and package layout smoke test**

Run: `uv run pytest tests/test_scripts.py tests/test_package_layout.py -v`

Expected: PASS. Scripts now succeed when `main(envelope)` returns a `dict`, preserve `stdout` as logs, and fail clearly for missing `main` or invalid return types.

- [ ] **Step 5: Commit the new script execution protocol**

```bash
git add \
  src/agent_bridge/system_config/scripts/runtime_support.py \
  src/agent_bridge/system_config/scripts/service.py \
  tests/test_scripts.py
git commit -m "feat: switch scripts to main return protocol"
```

---

### Task 3: Expose Safe Runtime Helpers And Verify End-To-End Workflow Behavior

**Files:**
- Modify: `src/agent_bridge/system_config/scripts/runtime_support.py`
- Modify: `src/agent_bridge/system_config/scripts/service.py`
- Modify: `tests/test_scripts.py`

- [ ] **Step 1: Add failing tests for helper API cleanup and workflow helpers**

```python
WORKFLOW_SCRIPT = """
from agent_bridge_runtime import workflow_get_task, workflow_set_task, workflow_run_log


def main(envelope):
    workflow_set_task([{"task_key": "page:a", "payload": {"page": "a"}}])
    leased = workflow_get_task()
    workflow_run_log(level="info", stage="lease", message="leased task", task_key=leased["task"]["task_key"])
    return {
        "task": leased["task"]["task_key"],
        "workflow": envelope["workflow"],
    }
"""


def test_runtime_helper_execute_signature_rejects_profile_override(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.bad_helper_signature",
        name="Bad Helper Signature",
        description="",
        language="python",
        code="from agent_bridge_runtime import execute\\n\\ndef main(envelope):\\n    execute('built-in', 'load_skill', {}, profile_key='other')\\n    return {}\\n",
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="takes 3 positional arguments but 4 were given|unexpected keyword argument"):
        service.scripts.test_script(
            actor="root",
            script_key="system.bad_helper_signature",
            script_params={},
            timeout_seconds=10,
        )


def test_workflow_helpers_require_runtime_context(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.workflow_missing_context",
        name="Workflow Missing Context",
        description="",
        language="python",
        code="from agent_bridge_runtime import workflow_get_task\\n\\ndef main(envelope):\\n    workflow_get_task()\\n    return {}\\n",
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="workflow context is required"):
        service.scripts.test_script(
            actor="root",
            script_key="system.workflow_missing_context",
            script_params={},
            timeout_seconds=10,
        )
```

- [ ] **Step 2: Run the helper-focused tests to verify they fail**

Run: `uv run pytest tests/test_scripts.py -k "helper or workflow" -v`

Expected: FAIL because the generated runtime helper still exposes `profile_key`, does not implement `workflow_*` helpers, and does not pass workflow headers anywhere.

- [ ] **Step 3: Update the generated runtime helper to remove profile overrides and add workflow helper calls**

```python
# src/agent_bridge/system_config/scripts/runtime_support.py
def render_runtime_helper() -> str:
    return '''from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _base_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Bridge-User": os.environ.get("AGENT_BRIDGE_USER", "root"),
    }
    profile = os.environ.get("AGENT_BRIDGE_PROFILE", "").strip()
    if profile:
        headers["X-Agent-Bridge-MetaMCP-Profile"] = profile
    if os.environ.get("AGENT_BRIDGE_WORKFLOW", "").strip().lower() == "true":
        headers["X-Agent-Bridge-Workflow"] = "true"
        headers["X-Agent-Bridge-Workflow-Key"] = os.environ.get("AGENT_BRIDGE_WORKFLOW_KEY", "")
        headers["X-Agent-Bridge-Workflow-Run-Id"] = os.environ.get("AGENT_BRIDGE_WORKFLOW_RUN_ID", "")
    return headers


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("AGENT_BRIDGE_API_BASE", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AGENT_BRIDGE_API_BASE is not configured")
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=_base_headers(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def execute(service: str, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _post_json("/capabilities/execute", {
        "service": service,
        "tool_name": tool_name,
        "params": params or {},
    })


def workflow_get_task() -> dict[str, Any]:
    return _post_json("/runtime/workflow/get-task", {})


def workflow_set_task(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return _post_json("/runtime/workflow/set-task", {"tasks": tasks})


def workflow_run_log(
    level: str = "info",
    stage: str = "",
    message: str = "",
    task_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _post_json("/runtime/workflow/run-log", {
        "level": level,
        "stage": stage,
        "message": message,
        "task_key": task_key,
        "payload": payload or {},
    })
'''
```

```python
# src/agent_bridge/system_config/scripts/service.py
def _runtime_env(self, actor: str, envelope: dict[str, Any]) -> dict[str, str]:
    workflow = envelope.get("workflow") if isinstance(envelope.get("workflow"), dict) else {}
    return {
        "AGENT_BRIDGE_API_BASE": base_url,
        "AGENT_BRIDGE_USER": actor,
        "AGENT_BRIDGE_PROFILE": str(envelope.get("profile_key") or ""),
        "AGENT_BRIDGE_WORKFLOW": "true" if workflow.get("enabled") else "false",
        "AGENT_BRIDGE_WORKFLOW_KEY": str(workflow.get("workflow_key") or ""),
        "AGENT_BRIDGE_WORKFLOW_RUN_ID": str(workflow.get("run_id") or ""),
        "PYTHONIOENCODING": "utf-8",
    }
```

- [ ] **Step 4: Add one end-to-end workflow helper success test and run the full script/runtime suite**

```python
def test_script_workflow_helpers_round_trip_tasks_and_logs(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    service.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    service.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )
    service.scripts.upsert_script(
        actor="root",
        script_key="system.workflow_roundtrip",
        name="Workflow Roundtrip",
        description="",
        language="python",
        code=WORKFLOW_SCRIPT,
        status="active",
        owner_type="system",
        owner_key="",
    )

    result = service.scripts.test_script(
        actor="root",
        script_key="system.workflow_roundtrip",
        script_params={},
        timeout_seconds=10,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )

    assert result["result"]["task"] == "page:a"
    logs = service.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "leased task"
```

Run: `uv run pytest tests/test_scripts.py tests/test_script_runtime_api.py tests/test_workflow_mcp.py -v`

Expected: PASS. The generated runtime helper now exposes safe workflow helpers, ordinary `execute()` no longer accepts profile overrides, and workflow helpers fail fast without trusted runtime context.

- [ ] **Step 5: Commit the runtime helper API changes**

```bash
git add \
  src/agent_bridge/system_config/scripts/runtime_support.py \
  src/agent_bridge/system_config/scripts/service.py \
  tests/test_scripts.py
git commit -m "feat: add trusted script runtime helpers"
```

---

## Self-Review

- **Spec coverage:** The plan covers all approved requirements from the spec: hard switch to `main(envelope) -> dict`, removal of helper-level `profile_key` overrides, test-run header injection, workflow context propagation, dedicated workflow helpers, and trusted workflow route validation.
- **Placeholder scan:** No `TBD`, `TODO`, “handle later”, or ambiguous “add tests” steps remain. Every task includes concrete files, test cases, commands, and implementation snippets.
- **Type consistency:** The plan consistently uses `workflow_context` as the trusted server-side context object, keeps `profile_key` out of helper signatures, and uses the same runtime helper names across routes, tests, and bootstrap code.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-20-script-runtime-context-and-return.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
