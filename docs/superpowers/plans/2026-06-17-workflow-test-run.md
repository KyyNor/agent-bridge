# Workflow Manual Test-Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a「测试运行」button to the workflow list that runs a workflow once on demand (bypassing the nightly window and active/disabled status) and shows status + logs + produced artifact on the page.

**Architecture:** Reuse the existing scheduler run path. Add `WorkflowScheduler.run_workflow_now(key)` which creates a `running` row, registers in the in-memory `_running` guard, and spawns the same worker thread as `tick()`. Two new admin-only HTTP endpoints: `POST /workflows/{key}/run` (trigger, returns `run_id` or 409) and `GET /workflow-runs/{run_id}` (poll single run). The frontend adds a button + a 5s poll loop that drives the existing runs/logs/artifact panels.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (backend); Vue 3 `<script setup lang="ts">` / shadcn-vue / Tailwind / Vite (frontend). Tests: pytest (+`FakeWorkflowRunner`).

**Spec:** `docs/superpowers/specs/2026-06-17-workflow-test-run-design.md`

---

## File Structure

**Backend (create/modify):**
- Modify `src/agent_bridge/workflows/scheduler.py` — add `run_workflow_now()`; refactor `run_one_workflow()` to accept an optional pre-created `run_id`; thread `run_id` through `_run_and_release()`. Add domain-error import.
- Modify `src/agent_bridge/workflows/service.py` — add `WorkflowService.get_run(actor, run_id)`.
- Modify `src/agent_bridge/api/routes/workflows.py` — add `POST /workflows/{key}/run` and `GET /workflow-runs/{run_id}`. No signature change (the router already receives the `AgentBridgeService`, which exposes `.workflow_scheduler` and `.workflows`).
- Modify `tests/test_workflow_scheduler.py` — tests for `run_workflow_now`.
- Modify `tests/test_workflow_api.py` — tests for the two endpoints.

**Frontend (modify):**
- Modify `frontend/capabilities/src/api/client.ts` — add `runWorkflow()` and `getWorkflowRun()`.
- Modify `frontend/capabilities/src/views/workflow/WorkflowView.vue` — add「测试运行」button, polling state/logic, and result wiring into existing panels.

No new types needed (`WorkflowRun` already exists in `types.ts`). No DB migration (uses existing `workflow_runs` columns).

---

## Task 1: Scheduler on-demand run + `run_one_workflow` refactor

**Files:**
- Modify: `src/agent_bridge/workflows/scheduler.py`
- Test: `tests/test_workflow_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workflow_scheduler.py`:

```python
def test_run_workflow_now_runs_once_and_creates_run_row(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

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

    result = scheduler.run_workflow_now("A")
    assert result["status"] == "started"
    assert result["run_id"].startswith("run_")

    _wait_runs_done(scheduler)
    run = svc.store.get_workflow_run(result["run_id"])
    assert run is not None
    assert run["status"] == "no_task"


def test_run_workflow_now_rejects_when_already_running(wm_paths, tmp_path):
    import pytest
    from agent_bridge.core.domain import ConflictError
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, "A")

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(),
        base_run_dir=tmp_path,
    )
    scheduler._running.add("A")  # simulate an in-flight run

    with pytest.raises(ConflictError):
        scheduler.run_workflow_now("A")


def test_run_workflow_now_missing_workflow_raises_not_found(wm_paths, tmp_path):
    import pytest
    from agent_bridge.core.domain import NotFound
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"}, base_run_dir=tmp_path,
    )

    with pytest.raises(NotFound):
        scheduler.run_workflow_now("does-not-exist")


def test_run_workflow_now_bypasses_disabled_status(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.store.upsert_workflow_definition(
        workflow_key="A", name="A", description="", profile_key="report-plane",
        workflow_js="", manifest={"name": "A", "nodes": [], "edges": [], "schemas": {}},
        status="disabled", created_by="root",
    )

    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"), base_run_dir=tmp_path,
    )

    result = scheduler.run_workflow_now("A")  # disabled, yet runnable for a test
    assert result["status"] == "started"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workflow_scheduler.py -k run_workflow_now -v`
Expected: FAIL — `AttributeError: 'WorkflowScheduler' object has no attribute 'run_workflow_now'`.

- [ ] **Step 3: Add the domain-error import to the scheduler**

In `src/agent_bridge/workflows/scheduler.py`, add to the import block near the top (after the existing `from agent_bridge.workflows.runner import ...` line):

```python
from agent_bridge.core.domain import ConflictError, NotFound
```

- [ ] **Step 4: Refactor `_run_and_release` to thread `run_id`**

Replace the existing `_run_and_release` method (currently lines 192–199) with:

```python
    def _run_and_release(self, workflow_key: str, run_id: str | None = None) -> None:
        try:
            self.run_one_workflow(workflow_key, run_id=run_id)
        except Exception:
            logger.exception("Workflow 执行异常 workflow=%s", workflow_key)
        finally:
            with self._lock:
                self._running.discard(workflow_key)
```

- [ ] **Step 5: Add `run_workflow_now`**

Insert this new method immediately before `run_one_workflow`:

```python
    def run_workflow_now(self, workflow_key: str) -> dict[str, Any]:
        """Launch a single on-demand run immediately — a "test run".

        Bypasses the daily window and the active/disabled status check (those
        live only in tick()). Shares the in-memory _running guard with the
        scheduler so a workflow cannot run twice concurrently. The run row is
        created synchronously so callers can poll it immediately.
        """
        with self._lock:
            if workflow_key in self._running:
                raise ConflictError("workflow is already running")
            workflow = self._store.get_workflow_definition(workflow_key)
            if workflow is None:
                raise NotFound("workflow not found")
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
            self._running.add(workflow_key)
        thread = threading.Thread(
            target=self._run_and_release, args=(workflow_key, run_id), daemon=True
        )
        thread.start()
        return {"status": "started", "run_id": run_id}
```

- [ ] **Step 6: Refactor `run_one_workflow` to accept an optional pre-created `run_id`**

Replace the whole existing `run_one_workflow` method (currently lines 201–266) with:

```python
    def run_one_workflow(self, workflow_key: str, run_id: str | None = None) -> dict[str, Any]:
        workflow = self._store.get_workflow_definition(workflow_key)
        if workflow is None:
            self.finished_today.add(workflow_key)
            return {"status": "missing"}

        base_dir = self._base_run_dir or Path("workflow-runs")
        if run_id is None:
            run_id = f"run_{uuid.uuid4().hex}"
            self._store.create_workflow_run(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                task_key=None,
                status="running",
                temp_dir=str(base_dir / run_id),
            )
        process_result = None
        try:
            process_result = self._runner.run(
                base_dir,
                WorkflowRunSpec(
                    run_id=run_id,
                    workflow_key=workflow_key,
                    profile_key=workflow["profile_key"],
                    workflow_js=workflow["workflow_js"],
                    mcp_url=self._mcp_url,
                ),
            )
            if process_result.exit_code != 0:
                return self._finish_failed(workflow_key, run_id, process_result, "claude workflow runner failed")
            parsed = parse_workflow_result(process_result.run_dir)
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
                exit_code=process_result.exit_code,
                stdout_path=str(process_result.stdout_path),
                stderr_path=str(process_result.stderr_path),
                error=None,
                duration_ms=process_result.duration_ms,
            )
            return ingested
        except Exception as exc:
            logger.exception("Workflow 执行失败 workflow=%s run=%s", workflow_key, run_id)
            stdout_path = str(process_result.stdout_path) if process_result else None
            stderr_path = str(process_result.stderr_path) if process_result else None
            duration_ms = process_result.duration_ms if process_result else None
            self._store.finish_workflow_run(
                run_id,
                status="failed",
                exit_code=process_result.exit_code if process_result else None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error=str(exc),
                duration_ms=duration_ms,
            )
            self._release_leased_tasks(workflow_key, run_id, str(exc))
            return {"status": "failed", "error": str(exc)}
```

(The only behavioral changes vs. the original: the signature gains `run_id: str | None = None`; the `create_workflow_run` call is guarded by `if run_id is None` so a manually-pre-created row is not recreated; the `except` block uses the local `run_id` instead of the former `run["run_id"]`. The scheduled path — `tick` → `_run_and_release(workflow_key)` with no `run_id` — is unchanged.)

- [ ] **Step 7: Run the new tests + the full scheduler suite**

Run: `.venv/bin/python -m pytest tests/test_workflow_scheduler.py -v`
Expected: PASS — all existing tests still pass (the `run_one_workflow("A")` single-arg calls still work) and the four new `run_workflow_now` tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_bridge/workflows/scheduler.py tests/test_workflow_scheduler.py
git commit -m "feat: add workflow on-demand run_workflow_now (bypasses window/status)"
```

---

## Task 2: `WorkflowService.get_run` + the two HTTP endpoints

**Files:**
- Modify: `src/agent_bridge/workflows/service.py`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Test: `tests/test_workflow_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workflow_api.py`:

```python
def test_workflow_api_get_run_returns_single_run(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="completed", temp_dir="/tmp/run_1",
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflow-runs/run_1", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "run_1"
    assert response.json()["status"] == "completed"


def test_workflow_api_get_run_404_for_unknown(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    AgentBridgeService.create(wm_paths, {"root"}).store.init_schema()
    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflow-runs/run_nope", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 404


def test_workflow_api_run_returns_409_when_already_running(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)

    app = create_app(wm_paths, {"root"})
    # Simulate an in-flight run on the app's own scheduler.
    app.state.agent_bridge_service.workflow_scheduler._running.add("page-report")
    client = TestClient(app)

    response = client.post("/workflows/page-report/run", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 409


def test_workflow_api_run_triggers_and_completes(wm_paths, tmp_path):
    import time
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)

    app = create_app(wm_paths, {"root"})
    # Swap the app's runner for an instant fake so the test does not shell out to claude.
    app.state.agent_bridge_service.workflow_scheduler._runner = FakeWorkflowRunner(status="no_executable_task")
    client = TestClient(app)

    started = client.post("/workflows/page-report/run", headers={"X-Agent-Bridge-User": "root"})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]

    status = "running"
    deadline = time.time() + 5
    while time.time() < deadline:
        r = client.get(f"/workflow-runs/{run_id}", headers={"X-Agent-Bridge-User": "root"})
        assert r.status_code == 200
        status = r.json()["status"]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "no_task"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workflow_api.py -k "get_run or run_returns_409 or run_triggers" -v`
Expected: FAIL — 404 on `GET /workflow-runs/run_1` (route not registered) and on `POST .../run`.

- [ ] **Step 3: Add `WorkflowService.get_run`**

In `src/agent_bridge/workflows/service.py`, add this method immediately after `list_runs` (right after line 79):

```python
    def get_run(self, actor: str, run_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        run = self.store.get_workflow_run(run_id)
        if run is None:
            raise NotFound("workflow run not found")
        return run
```

(`NotFound` is already imported at the top of `service.py`.)

- [ ] **Step 4: Add the two endpoints**

In `src/agent_bridge/api/routes/workflows.py`, inside `create_workflow_routes(...)`, add these two handlers immediately before `return router` (after the existing `get_artifact` handler):

```python
    @router.post("/workflows/{workflow_key}/run")
    def run_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflow_scheduler.run_workflow_now(workflow_key))

    @router.get("/workflow-runs/{run_id}")
    def get_run(run_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.get_run(current_actor, run_id))
```

(`service` is the `AgentBridgeService`, which already exposes `.workflow_scheduler` and `.workflows` — no change to `create_workflow_routes`'s signature or to `app.py`.)

- [ ] **Step 5: Run the API tests + full workflow API suite**

Run: `.venv/bin/python -m pytest tests/test_workflow_api.py -v`
Expected: PASS — new tests pass and existing ones still pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_bridge/workflows/service.py src/agent_bridge/api/routes/workflows.py tests/test_workflow_api.py
git commit -m "feat: add POST /workflows/{key}/run and GET /workflow-runs/{id}"
```

---

## Task 3: Frontend — button + polling + result wiring

**Files:**
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`

No automated frontend tests exist in this repo; this task is implement + typecheck/build.

- [ ] **Step 1: Add the two API client functions**

In `frontend/capabilities/src/api/client.ts`, in the `// Workflows` section, add after the `getWorkflowArtifact` function (after line 157):

```ts
  runWorkflow: (key: string) => post<{ status: string; run_id?: string }>(`/workflows/${key}/run`),
  getWorkflowRun: (runId: string) => get<WorkflowRun>(`/workflow-runs/${runId}`),
```

- [ ] **Step 2: Add polling state and lifecycle imports to `WorkflowView.vue`**

In `frontend/capabilities/src/views/workflow/WorkflowView.vue`:

Change the Vue import on line 2 from:
```ts
import { computed, onMounted, ref } from 'vue'
```
to:
```ts
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
```

After the existing `logsLoading` ref (line 35), add:
```ts
const testing = ref(false)
const testingRunId = ref('')
const testError = ref('')
let testPoll: ReturnType<typeof setInterval> | null = null
```

- [ ] **Step 3: Add the `hasRunningRun` computed + test-run functions**

After the `selectRun` function (after line 347), add:

```ts
const hasRunningRun = computed(() => runs.value.some(r => r.status === 'running'))

function stopTestPolling() {
  if (testPoll) {
    clearInterval(testPoll)
    testPoll = null
  }
}

async function runTest() {
  const wf = selectedWorkflow.value
  if (!wf || testing.value) return
  testError.value = ''
  testing.value = true
  try {
    const res = await api.runWorkflow(wf.workflow_key)
    if (res.status === 'started' && res.run_id) {
      testingRunId.value = res.run_id
      selectedRunId.value = res.run_id
      await loadRuns()
      await loadLogs()
      stopTestPolling()
      testPoll = setInterval(pollTestRun, 5000)
    } else {
      testing.value = false
    }
  } catch (e: unknown) {
    testing.value = false
    testError.value = errorMessage(e)
  }
}

async function pollTestRun() {
  const runId = testingRunId.value
  if (!runId) return
  try {
    const run = await api.getWorkflowRun(runId)
    await loadLogs()
    if (['completed', 'no_task', 'failed', 'stopped'].includes(run.status)) {
      stopTestPolling()
      testing.value = false
      testingRunId.value = ''
      await loadRuns()
      if (run.status === 'completed' || run.status === 'no_task') {
        await searchArtifacts()
      }
    }
  } catch {
    // transient poll error: keep polling
  }
}
```

- [ ] **Step 4: Add cleanup on workflow switch and unmount**

After the `onMounted(async () => { await loadAll() })` block (after line 126), add:

```ts
watch(selectedKey, () => {
  stopTestPolling()
  testing.value = false
  testingRunId.value = ''
  testError.value = ''
})

onUnmounted(() => stopTestPolling())
```

- [ ] **Step 5: Add the「测试运行」button to the detail header**

In the template, replace the header action block (currently lines 415–418):

```vue
              <div class="flex gap-2">
                <Button variant="outline" @click="openEdit(selectedWorkflow)">编辑</Button>
                <Button variant="ghost" class="text-destructive" @click="deleteCurrent">删除</Button>
              </div>
```

with:

```vue
              <div class="flex flex-col items-end gap-1">
                <div class="flex gap-2">
                  <Button variant="outline" :disabled="testing || hasRunningRun" @click="runTest">{{ testing ? '运行中…' : '测试运行' }}</Button>
                  <Button variant="outline" @click="openEdit(selectedWorkflow)">编辑</Button>
                  <Button variant="ghost" class="text-destructive" @click="deleteCurrent">删除</Button>
                </div>
                <div v-if="testError" class="text-xs text-destructive">{{ testError }}</div>
              </div>
```

- [ ] **Step 6: Typecheck / build**

Run: `cd frontend/capabilities && npm run build`
Expected: build succeeds (vue-tsc typecheck + vite build) with no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/workflow/WorkflowView.vue
git commit -m "ui: add workflow test-run button with 5s polling"
```

---

## Task 4: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite (sanity)**

Run: `.venv/bin/python -m pytest tests/test_workflow_scheduler.py tests/test_workflow_api.py tests/test_workflow_runner.py tests/test_workflow_service.py -v`
Expected: all PASS. (Known unrelated env failures — `test_ragflow_integration.py`, `test_weknora_integration.py` — are outside this scope.)

- [ ] **Step 2: Start the app**

From the project root, start the server the way the project normally does (e.g. `uv run python -m agent_bridge` or the documented run command). Open the admin UI (`/admin/capabilities`) and navigate to 工作流管理.

- [ ] **Step 3: Verify the happy path**

Select a workflow whose `workflow_js` produces output (e.g. the `hellogithub-summary` example, or a trivial test workflow that writes `out/result.json`). Click「测试运行」:
- Button text becomes「运行中…」and is disabled.
- The new run appears at the top of 运行与日志, auto-selected; its status badge is 执行中.
- Logs stream into the right pane as the agent emits them.
- On completion the badge flips to 成功/无任务; for 成功/无任务 the 产物 tree refreshes and the new markdown is openable via 查看.

- [ ] **Step 4: Verify the already-running guard**

While a run is in 执行中 (or by triggering a second click before the first finishes on a longer workflow), clicking again is disabled; if reached via the API it returns 409 and the button area shows the error.

- [ ] **Step 5: Verify bypass**

Set the workflow to 停用 (disabled) in the editor and save. Click「测试运行」— it still runs (status bypass confirmed). It also runs during the day regardless of the 22:00–07:00 window.

- [ ] **Step 6: (No commit unless verification surfaced fixes.)**

---

## Self-Review (completed during authoring)

- **Spec coverage:** spec §后端 `run_workflow_now` → Task 1; `run_one_workflow` refactor → Task 1 Step 6; `POST /run` + `GET /workflow-runs/{id}` → Task 2; 接线 (no signature change needed — confirmed `service` is the `AgentBridgeService`) → Task 2 Step 4; spec §前端 button + 5s poll + result wiring → Task 3; spec §测试 backend pytest + `FakeWorkflowRunner` → Tasks 1–2; manual FE verification → Task 4. All spec sections mapped.
- **Placeholder scan:** none — every code step contains complete, runnable code; commit messages and test commands are concrete.
- **Type/name consistency:** `run_workflow_now` (scheduler) ↔ `service.workflow_scheduler.run_workflow_now` (route) ↔ `api.runWorkflow` (client) consistent. `get_run` (service) ↔ `service.workflows.get_run` (route) ↔ `api.getWorkflowRun` (client) consistent. `run_id` parameter threaded consistently through `_run_and_release` → `run_one_workflow`. Frontend state names (`testing`, `testingRunId`, `testPoll`, `pollTestRun`) used consistently. Terminal-status set `{completed, no_task, failed, stopped}` matches `WorkflowRunStatus` enum.
- **Scope:** single focused feature, one plan, no decomposition needed.
