# Agent 运行立即停止 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为工作流单次/批量运行、Scripts 设计 Agent 和 Workflow 设计 Agent 增加统一、幂等、可观察的立即停止能力。

**Architecture:** 在 `agent_runtime` 增加进程内 `RunControlRegistry`，用线程安全取消句柄把 API 停止请求传递到 AgentService 的异步 SDK 查询；WorkflowScheduler 用 Workflow Run 作为父级控制器，负责停止后的 Run 终态和任务租约释放；前端以 `run_id` 或调用前生成的 `run_key` 调用停止 API，批量队列保留页面级串行生命周期并补齐停止竞态保护。

**Tech Stack:** Python 3.11、FastAPI、SQLite repository、Claude Agent SDK、APScheduler、Vue 3 `<script setup>`、TypeScript、Node test runner + tsx、pytest。

---

## 文件边界

- Create: `src/agent_bridge/agent_runtime/control.py` — 线程安全 Agent/Workflow 控制器、停止请求 tombstone 和控制句柄生命周期。
- Modify: `src/agent_bridge/agent_runtime/service.py` — 接受调用方 run key、监听取消、区分 stopped 与 failed、写入停止事件。
- Modify: `src/agent_bridge/storage/repositories/agent_runs.py` — 让 Agent run 终态回写具备终态保护，避免 stop 与正常完成互相覆盖。
- Modify: `src/agent_bridge/automation/workflows/runner.py` — 传递父 Workflow Run 身份并暴露 stopped 结果。
- Modify: `src/agent_bridge/automation/workflows/scheduler.py` — 注册/停止 Workflow Run、停止收尾、释放 pending 任务、阻止后续 Agent。
- Modify: `src/agent_bridge/storage/repositories/workflows.py` — 增加仅释放指定 Run 租约且保留 attempts 的 stopped 路径。
- Modify: `src/agent_bridge/api/schemas.py` — 设计请求接受可选客户端 `run_key`。
- Modify: `src/agent_bridge/api/routes/agent_runs.py` — 增加 Agent stop API，并透传设计 run key。
- Modify: `src/agent_bridge/api/routes/workflows.py` — 增加 Workflow Run stop API。
- Test: `tests/test_agent_service.py`, `tests/test_agent_runs_api.py`, `tests/test_design_agent_api.py` — Agent 取消、状态和设计请求 key。
- Test: `tests/test_workflow_runner.py`, `tests/test_workflow_scheduler.py`, `tests/test_workflow_storage.py`, `tests/test_workflow_api.py` — Workflow stopped 传播、幂等和任务释放。
- Modify: `frontend/capabilities/src/api/client.ts`, `frontend/capabilities/src/api/types.ts`, `frontend/capabilities/src/lib/agentRunStatus.ts` — stop API、状态类型和显示文案。
- Modify: `frontend/capabilities/src/lib/workflowTasks.ts` — 批量当前 Run 停止和 execute 返回竞态。
- Modify: `frontend/capabilities/src/components/AgentRunTabs.vue` — 可选停止入口。
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue` — 单次/批量 Workflow 停止、状态展示和设计 Agent 停止。
- Modify: `frontend/capabilities/src/views/system/ScriptsView.vue` — Scripts 设计 Agent 停止。
- Test: `frontend/capabilities/tests/workflowBatchRunner.test.ts`, `frontend/capabilities/tests/agentRunStatus.test.ts` — 队列竞态和 stopped 状态。

不会修改 `/Users/kyynor/.codex/worktrees/6e40/agent-bridge` 或 `codex/lightweight-workflow-editor-design`。

## Task 1: 为统一控制中心写失败测试并实现 Agent 取消句柄

**Files:**

- Create: `src/agent_bridge/agent_runtime/control.py`
- Test: `tests/test_agent_runtime_control.py`

- [ ] **Step 1: 写控制器的失败测试**

创建 `tests/test_agent_runtime_control.py`，先锁定三个行为：注册后可以停止、停止先于注册时后续注册立即可见、终态清理后同一个 stop 请求不会留下活动句柄。

```python
from agent_bridge.agent_runtime.control import RunControlRegistry


def test_stop_before_register_is_replayed_to_later_run() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)

    assert registry.request_stop("design_script_client_key") is True
    control = registry.register("design_script_client_key")

    assert control.stop_requested.is_set()
    assert registry.is_active("design_script_client_key") is True


def test_finish_removes_active_control_and_repeated_stop_is_idempotent() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)
    registry.register("agent_run_1")

    assert registry.request_stop("agent_run_1") is True
    registry.finish("agent_run_1")

    assert registry.is_active("agent_run_1") is False
    assert registry.request_stop("agent_run_1") is True


def test_workflow_stop_cancels_all_attached_agents() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)
    registry.register_workflow("workflow_run_1")
    first = registry.register("agent_1", workflow_run_id="workflow_run_1")
    second = registry.register("agent_2", workflow_run_id="workflow_run_1")

    assert registry.request_workflow_stop("workflow_run_1") is True
    assert first.stop_requested.is_set()
    assert second.stop_requested.is_set()
```

`request_stop` 对活动 run 和已存在 tombstone 都返回 `True`；调用方是否能在数据库中找到 run 由 API 层判断，控制器本身不承担权限或 404 判断。

- [ ] **Step 2: 运行失败测试，确认失败原因正确**

```bash
pytest -q tests/test_agent_runtime_control.py
```

Expected: 收集成功但因 `agent_runtime.control` 尚不存在而失败；失败原因应是模块/类不存在，而不是测试 fixture 或导入环境错误。

- [ ] **Step 3: 实现最小线程安全控制器**

在 `control.py` 实现以下公开接口，所有共享字典都在同一个 `threading.RLock` 下读写：

- `register(run_key: str, *, workflow_run_id: str | None = None) -> RunControl`
- `register_workflow(workflow_run_id: str) -> None`
- `request_stop(run_key: str) -> bool`
- `request_workflow_stop(workflow_run_id: str) -> bool`
- `is_stop_requested(run_key: str) -> bool`
- `is_workflow_stop_requested(workflow_run_id: str) -> bool`
- `finish(run_key: str) -> None`
- `finish_workflow(workflow_run_id: str) -> None`
- `is_active(run_key: str) -> bool`

`RunControl` 是 dataclass，字段为 `run_key: str`、`workflow_run_id: str | None` 和 `stop_requested: threading.Event`。

`request_stop` 对未知 key 写入带过期时间的 tombstone；`register` 发现 tombstone 时先设置新句柄的 Event，再把 key 注册为活动运行。`request_workflow_stop` 设置 Workflow Run 的取消 Event，并设置当前已挂载 Agent 的 Event；`register` 传入已停止的父 run 时立即设置 Agent Event。默认 tombstone TTL 取 600 秒，构造函数保留显式参数便于测试。

- [ ] **Step 4: 运行控制器测试并提交**

```bash
pytest -q tests/test_agent_runtime_control.py
git add src/agent_bridge/agent_runtime/control.py tests/test_agent_runtime_control.py
git commit -m "feat: add agent run control registry"
```

Expected: 3 个控制器测试通过，提交只包含控制器和对应测试。

## Task 2: 为 AgentService 增加 stopped 结果和真实查询取消

**Files:**

- Modify: `src/agent_bridge/agent_runtime/service.py:40-260,350-390`
- Modify: `src/agent_bridge/storage/repositories/agent_runs.py:85-126`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: 写运行中停止和运行前停止的失败测试**

在 `tests/test_agent_service.py` 增加两个测试。第一个用一个永不产出结果的异步生成器，第二个在调用前设置 registry 的停止 tombstone；测试必须检查结果字段和数据库状态。

```python
def test_run_stop_cancels_query_and_persists_stopped(wm_paths, monkeypatch) -> None:
    entered = asyncio.Event()

    async def fake_query(*, prompt, options):
        entered.set()
        await asyncio.sleep(10)
        if False:
            yield _result()

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})
    run_key = "design_script_client_stop"

    async def run_and_stop():
        task = asyncio.create_task(
            bundle.agents.run(prompt="long", agent_name="design_script", run_key=run_key)
        )
        await entered.wait()
        assert bundle.agents.request_stop(run_key) is True
        return await task

    result = asyncio.run(run_and_stop())

    assert result.ok is False
    assert result.stopped is True
    assert result.error == "运行已由用户停止"
    assert bundle.store.agent_runs.get(run_key)["status"] == "stopped"


def test_run_stop_requested_before_query_does_not_call_sdk(wm_paths, monkeypatch) -> None:
    called = False

    async def fake_query(*, prompt, options):
        nonlocal called
        called = True
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})
    run_key = "workflow_design_stop_before_start"
    assert bundle.agents.request_stop(run_key) is True

    result = asyncio.run(
        bundle.agents.run(prompt="cancel", agent_name="workflow", run_key=run_key)
    )

    assert result.stopped is True
    assert called is False
    assert bundle.store.agent_runs.get(run_key)["status"] == "stopped"
```

`AgentBridgeService.create(wm_paths, {"root"}).agents` 应公开或透传一个 `request_stop(run_key)` 方法，测试通过这个公共 runtime 入口发起停止，不访问控制器私有字段。

- [ ] **Step 2: 运行失败测试，确认当前实现把取消误报为失败**

```bash
pytest -q tests/test_agent_service.py -k "stop"
```

Expected: 测试先失败；当前没有 `run_key` 参数、`stopped` 字段和停止入口，或取消被映射为普通失败。若测试在导入阶段失败，先修正测试使用的现有 fixture/导入路径再继续，不跳过红灯。

- [ ] **Step 3: 扩展 AgentRunResult 和 AgentService.run 契约**

将结果 envelope 扩展为：

```python
@dataclass
class AgentRunResult:
    ok: bool
    stopped: bool = False
    result: Any | None = None
    error: str | None = None
    run_dir: str = ""
    session_id: str | None = None
    run_key: str | None = None
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None
```

`AgentService.run` 增加 `run_key: str | None = None` 参数：有值时使用调用方 key，否则继续调用 `new_run_id`。创建/更新 Agent run 占位记录前注册控制句柄，传入 `run_id` 且 `workflow_key` 存在时将它作为 `workflow_run_id` 父级。

- [ ] **Step 4: 用控制 Event 包装 SDK 查询并处理 CancelledError**

把现有直接等待 `_drain_query(prompt, options, work_dir, on_message, events, tool_names, attribution)` 的逻辑改成一个内部协程，查询 task 与取消 watcher 二选一：

```python
async def _drain_query_with_control(
    self,
    prompt: str,
    options: Any,
    work_dir: Path | None,
    on_message: Callable[[Any], None] | None,
    events: list[dict[str, Any]],
    tool_names: dict[str, str],
    attribution: Attribution,
    stop_requested: Event,
    timeout: float,
) -> ResultMessage | None:
    query_task = asyncio.create_task(
        self._drain_query(prompt, options, work_dir, on_message, events, tool_names, attribution)
    )
    stop_task = asyncio.create_task(_wait_for_thread_event(stop_requested))
    done, _ = await asyncio.wait(
        {query_task, stop_task},
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_task in done:
        query_task.cancel()
        await _await_cancelled_query(query_task)
        raise AgentRunStopped
    if query_task in done:
        stop_task.cancel()
        return query_task.result()
    query_task.cancel()
    stop_task.cancel()
    await _await_cancelled_query(query_task)
    raise TimeoutError(f"agent timed out after {timeout}s")
```

`_wait_for_thread_event` 使用短周期 `asyncio.sleep(0.05)` 检查 `threading.Event`，不要把长时间阻塞的 `Event.wait()` 放入默认线程池。捕获 `AgentRunStopped` 时构造 `stopped=True, ok=False` 的结果，写入 `event_record("status", status="stopped", message="运行已由用户停止")` 和错误事件；捕获 SDK 普通异常时保持现有 failed 行为。所有分支都在 `finally` 调用 `registry.finish(run_key)`。

- [ ] **Step 5: 为 AgentRunsRepository 增加终态保护**

将 `finish_run` 更新语句限制为 `status = 'running'`，并让返回值表示是否真的更新了一行：

```sql
UPDATE agent_runs
SET ok = ?, status = ?, error = ?, session_id = ?, model = COALESCE(?, model),
    duration_ms = ?, cost_usd = ?, num_turns = ?, result_json = ?,
    events_json = ?, finished_at = ?
WHERE run_key = ? AND status = 'running'
```

AgentService 在返回前读取更新结果；如果 stop 和正常完成并发，只保留先成功提交的终态。后续调用不抛异常，也不覆盖终态数据。

- [ ] **Step 6: 运行 AgentService 回归测试并提交**

```bash
pytest -q tests/test_agent_runtime_control.py tests/test_agent_service.py
git diff --check
git add src/agent_bridge/agent_runtime/service.py src/agent_bridge/storage/repositories/agent_runs.py tests/test_agent_service.py
git commit -m "feat: stop agent sdk runs safely"
```

Expected: 控制器和 AgentService 测试通过，已有 success/error/timeout/workflow context 测试不回归；停止结果的 `ok` 为 false、`stopped` 为 true，数据库状态为 stopped。

## Task 3: 接入 Workflow runner、Scheduler 和任务租约释放

**Files:**

- Modify: `src/agent_bridge/automation/workflows/runner.py:20-125`
- Modify: `src/agent_bridge/automation/workflows/scheduler.py:260-520`
- Modify: `src/agent_bridge/storage/repositories/workflows.py:656-710`
- Test: `tests/test_workflow_runner.py`, `tests/test_workflow_scheduler.py`, `tests/test_workflow_storage.py`

- [ ] **Step 1: 写 stopped runner 和任务释放失败测试**

在 `tests/test_workflow_runner.py` 增加一个 Fake AgentService 返回 stopped 的测试；在 `tests/test_workflow_storage.py` 增加一个任务状态为 running、租约属于指定 run 的 fixture，验证新释放方法只改这一条任务并保留 attempt_count。

```python
def test_claude_workflow_runner_exposes_stopped_result(tmp_path):
    class Agent:
        async def run(self, **kwargs):
            return SimpleNamespace(ok=False, stopped=True, error="运行已由用户停止")

    spec = WorkflowRunSpec(
        run_id="run-stop",
        workflow_key="wf",
        profile_key="profile",
        workflow_js="",
        mcp_url="http://127.0.0.1:8765/mcp",
    )
    result = ClaudeWorkflowRunner(Agent()).run(tmp_path, spec)

    assert result.stopped is True
    assert result.exit_code == 1


def test_release_stopped_tasks_returns_running_tasks_to_pending(wm_paths):
    repo = wm_paths.store.workflows
    repo.upsert_workflow_tasks("wf", [
        {"task_key": "current", "payload": {}},
        {"task_key": "other", "payload": {}},
    ])
    current_lease = repo.lease_workflow_task("wf", run_id="run-stop", lease_seconds=7200)
    other_lease = repo.lease_workflow_task("wf", run_id="another-run", lease_seconds=7200)

    released = repo.release_tasks_for_stopped_run(
        "wf", "run-stop", error_message="运行已由用户停止"
    )

    assert released == 1
    current = repo.get_workflow_task("wf", current_lease["task_key"])
    other = repo.get_workflow_task("wf", other_lease["task_key"])
    assert current["status"] == "pending"
    assert current["lease_run_id"] is None
    assert current["attempt_count"] == current_lease["attempt_count"]
    assert other["status"] == "running"
```

根据现有 repository 的建任务测试 fixture 调整参数名；测试必须保留“不同 lease_run_id 不受影响”这一断言。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_workflow_runner.py tests/test_workflow_storage.py -k "stopped or release"
```

Expected: 新测试先因 `WorkflowProcessResult.stopped` 或 `release_tasks_for_stopped_run` 不存在而失败，既有 runner/storage 测试保持可收集。

- [ ] **Step 3: 扩展 WorkflowProcessResult 和 ClaudeWorkflowRunner**

给 `WorkflowProcessResult` 增加 `stopped: bool = False`，FakeWorkflowRunner 显式返回 `False`。ClaudeWorkflowRunner 调用 AgentService 后把 `res.stopped` 传出；`run_id=spec.run_id` 已是父 Workflow Run 标识，保持透传，不新建第二套 Agent key。

```python
return WorkflowProcessResult(
    run_dir=run_dir,
    exit_code=0 if res.ok else 1,
    stdout_path=stderr_path,
    stderr_path=stderr_path,
    duration_ms=int((time.monotonic() - started) * 1000),
    stopped=res.stopped,
)
```

`WorkflowRunner` Protocol、所有 Fake runner 和构造该 dataclass 的测试都补齐默认字段。

- [ ] **Step 4: 增加 stopped 任务释放 repository 方法**

在 `workflows.py` 增加独立方法，不复用可能产生 abandoned 的失败路径：

```python
def release_tasks_for_stopped_run(
    self, workflow_key: str, run_id: str, *, error_message: str
) -> int:
    with self._connect() as conn:
        cursor = conn.execute(
            """
            UPDATE workflow_tasks
            SET status = 'pending', lease_run_id = NULL,
                lease_expires_at = NULL, last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE workflow_key = ? AND lease_run_id = ? AND status = 'running'
            """,
            (error_message, workflow_key, run_id),
        )
        return cursor.rowcount
```

不更新 `attempt_count`，不触碰 completed/failed/abandoned，也不按 workflow_key 之外的 Run 重置。

- [ ] **Step 5: 在 Scheduler 注册、停止和收尾 Workflow Run**

在 `WorkflowScheduler` 构造函数保存 AgentService 的控制注册表。`run_workflow_now` 在创建 `workflow_runs` 行后、启动线程前调用 `register_workflow(run_id)`。

增加：

```python
def stop_workflow_run(self, run_id: str) -> dict[str, Any]:
    run = self._store.get_workflow_run(run_id)
    if run is None:
        raise NotFound("workflow run not found")
    if run["status"] != "running":
        return run
    if not self._agent_controls.request_workflow_stop(run_id):
        raise ConflictError("workflow run is not controllable")
    return {**run, "status": "stopping"}
```

在 `run_one_workflow` 中先判断 `process_result.stopped`，走 `_finish_stopped`，不进入 result parser 或 ingest。总结工作流生成 HTML 报告前后都检查父 Workflow Run 的 stop 状态；已请求停止时不再启动新的 reporter，正在运行的 reporter 由父控制器取消。

`_finish_stopped` 使用 `finish_workflow_run` 写入 `status="stopped"`、`exit_code`、stdout/stderr 路径、错误信息和 duration，然后调用 `release_tasks_for_stopped_run`。`_run_and_release` 的 `finally` 清理 `_running` 和 Workflow control。`finish_workflow_run` 必须保持现有终态保护/幂等语义，避免 stop thread 和 worker thread 二次覆盖。

- [ ] **Step 6: 写 Scheduler stop API 级别的失败测试**

在 `tests/test_workflow_scheduler.py` 增加：活动 Run stop 返回 stopping；再次 stop 不创建第二次取消；Fake runner 返回 stopped 时 Run 为 stopped 且 task 回 pending；已 completed Run stop 返回 completed。

```python
def test_stop_workflow_run_is_idempotent(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    class FakeRunControls:
        def __init__(self):
            self.requested = set()
            self.workflow_stop_calls = []

        def request_workflow_stop(self, run_id):
            if run_id not in self.requested:
                self.requested.add(run_id)
                self.workflow_stop_calls.append(run_id)
            return True

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    controls = FakeRunControls()
    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        base_run_dir=tmp_path,
        control_registry=controls,
    )
    run_id = "run-stop"
    svc.store.create_workflow_run(
        run_id=run_id,
        workflow_key="wf",
        profile_key="profile",
        task_key=None,
        status="running",
        temp_dir=str(tmp_path / run_id),
    )

    first = scheduler.stop_workflow_run(run_id)
    second = scheduler.stop_workflow_run(run_id)

    assert first["status"] == "stopping"
    assert second["status"] in {"stopping", "stopped"}
    assert controls.workflow_stop_calls == [run_id]
```

测试通过构造函数的 `control_registry` 注入 fake；不要启动真实 Claude SDK。

- [ ] **Step 7: 运行 Workflow 测试并提交**

```bash
pytest -q tests/test_workflow_runner.py tests/test_workflow_storage.py tests/test_workflow_scheduler.py
git diff --check
git add src/agent_bridge/automation/workflows/runner.py src/agent_bridge/automation/workflows/scheduler.py src/agent_bridge/storage/repositories/workflows.py tests/test_workflow_runner.py tests/test_workflow_storage.py tests/test_workflow_scheduler.py
git commit -m "feat: stop workflow runs and release leases"
```

Expected: 相关 Workflow runner/storage/scheduler 测试通过；既有失败路径仍可 abandon/release，新增 stopped 路径不改变失败语义。

## Task 4: 增加后端 stop API 和设计请求 run key

**Files:**

- Modify: `src/agent_bridge/api/schemas.py:356-365`
- Modify: `src/agent_bridge/api/routes/agent_runs.py:150-235`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Test: `tests/test_agent_runs_api.py`, `tests/test_design_agent_api.py`, `tests/test_workflow_api.py`

- [ ] **Step 1: 写 API 失败测试**

在 Agent API 测试中验证 stop 路由把 key 交给 AgentService；设计请求把 payload.run_key 传给 AgentService；Workflow API 测试验证 `POST /workflow-runs/{run_id}/stop` 返回 scheduler 结果。

```python
def test_design_script_forwards_client_run_key(wm_paths, monkeypatch):
    client = _client(wm_paths)
    service = client.app.state.agent_bridge_service
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return AgentRunResult(ok=True, result={"summary": "ok"}, run_key=kwargs["run_key"])

    monkeypatch.setattr(service.agents, "run", fake_run)
    response = client.post(
        "/agent-runs/design/script",
        json={"mode": "create", "prompt": "make script", "run_key": "client-key"},
    )

    assert response.status_code == 200
    assert captured["run_key"] == "client-key"


def test_stop_agent_run_delegates_to_runtime_control(wm_paths, monkeypatch):
    client = _client(wm_paths)
    service = client.app.state.agent_bridge_service
    calls = []
    monkeypatch.setattr(service.agents, "request_stop", lambda key: calls.append(key) or True)

    response = client.post("/agent-runs/agent-key/stop")

    assert response.status_code == 202
    assert calls == ["agent-key"]
```

使用现有 `client` fixture、app service 获取方式和 router 前缀；若项目的 TestClient 路径带 `/api`，按现有测试调用的实际 URL 保持一致。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_agent_runs_api.py tests/test_design_agent_api.py tests/test_workflow_api.py -k "stop or run_key"
```

Expected: 新增 stop 路由返回 404 或设计 key 未进入 captured kwargs；现有 API 测试可收集。

- [ ] **Step 3: 扩展 schema 与 API 响应**

在 `DesignAgentRequest` 增加：

```python
run_key: str | None = None
```

设计 Workflow/Script 调用 AgentService 时传 `run_key=payload.run_key`。增加两个 stop 路由：

```python
@router.post("/agent-runs/{run_key}/stop", status_code=202)
def stop_agent_run(run_key: str, current_actor: str = Depends(actor)):
    run = service.store.agent_runs.get(run_key)
    if run is not None and run.get("status") != "running":
        return run
    if run is None and not service.agents.has_pending_control(run_key):
        raise HTTPException(status_code=404, detail="agent run not found")
    service.agents.request_stop(run_key)
    return {"run_key": run_key, "status": "stopping"}
```

Workflow 路由调用 `service.workflow_scheduler.stop_workflow_run(run_id)`；NotFound 走现有 404 处理，Conflict 走现有 409 处理，活动 Run 返回 202/stopping，终态返回 200/原状态。保持现有 actor/admin 依赖，不创建绕过权限的新入口。

- [ ] **Step 4: 运行 API 测试并提交**

```bash
pytest -q tests/test_agent_runs_api.py tests/test_design_agent_api.py tests/test_workflow_api.py
git diff --check
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/agent_runs.py src/agent_bridge/api/routes/workflows.py tests/test_agent_runs_api.py tests/test_design_agent_api.py tests/test_workflow_api.py
git commit -m "feat: expose agent and workflow stop APIs"
```

Expected: API stop 状态码、幂等终态和 design run key 传递测试通过。

## Task 5: 为前端 API、状态显示和批量队列写测试并实现

**Files:**

- Modify: `frontend/capabilities/src/api/client.ts:379-455`
- Modify: `frontend/capabilities/src/api/types.ts:245-255,532-550`
- Modify: `frontend/capabilities/src/lib/agentRunStatus.ts`
- Modify: `frontend/capabilities/src/lib/workflowTasks.ts:120-205`
- Test: `frontend/capabilities/tests/workflowBatchRunner.test.ts`, `frontend/capabilities/tests/agentRunStatus.test.ts`

- [ ] **Step 1: 写 stopped 状态和 execute 竞态测试**

在 `workflowBatchRunner.test.ts` 增加：运行中返回 stopped 会结束队列并保留 remaining；execute 在 token 取消后才返回 run_id 时调用 `stopRun`。

```ts
test('runWorkflowTaskQueue stops after current run is stopped', async () => {
  const result = await runWorkflowTaskQueue([task('first'), task('second')], {
    canExecute: () => true,
    execute: async current => ({ run_id: `run-${current.task_key}` }),
    waitForRun: async runId => run(runId, 'stopped'),
  })

  assert.equal(result.outcomes[0].status, 'stopped')
  assert.equal(result.stopped, true)
  assert.deepEqual(result.remaining.map(item => item.task_key), ['second'])
})

test('queue stops a run that appears after page cancellation', async () => {
  let cancelled = false
  let stopCalls: string[] = []
  const result = await runWorkflowTaskQueue([task('late')], {
    canExecute: () => true,
    execute: async () => {
      cancelled = true
      return { run_id: 'run-late' }
    },
    stopRun: async runId => { stopCalls.push(runId) },
    waitForRun: async runId => run(runId, 'stopped'),
    isCancelled: () => cancelled,
  })

  assert.deepEqual(stopCalls, ['run-late'])
  assert.equal(result.outcomes[0].status, 'stopped')
})
```

在 `agentRunStatus.test.ts` 增加 `stopped` 的 label 和 badge class 断言，沿用文件现有测试风格。

- [ ] **Step 2: 运行失败测试**

```bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts tests/agentRunStatus.test.ts
```

Expected: stopped outcome 或 `stopRun` 类型不存在导致新测试失败，既有测试不因命令错误而失败。

- [ ] **Step 3: 增加前端 stop API 和状态类型**

在 `api/types.ts` 增加：

```ts
export interface RunStopResponse {
  run_key?: string
  run_id?: string
  status: 'stopping' | 'running' | 'completed' | 'failed' | 'stopped' | 'no_task' | string
}
```

在 `api/client.ts` 增加：

```ts
stopAgentRun: (runKey: string) => post<RunStopResponse>(`/agent-runs/${encodeURIComponent(runKey)}/stop`),
stopWorkflowRun: (runId: string) => post<RunStopResponse>(`/workflow-runs/${encodeURIComponent(runId)}/stop`),
```

设计方法的 body 类型增加 `run_key?: string`，WorkflowRun/AgentRun status 联合类型显式包括 `stopped`，并在 `agentRunStatus.ts` 为 stopped 返回“已停止”和中性/警示 badge class。

- [ ] **Step 4: 扩展 queue 类型和停止竞态**

将 `WorkflowTaskQueueOutcome.status` 扩展为 `'success' | 'failed' | 'skipped' | 'stopped'`，增加：

```ts
stopRun?: (runId: string) => Promise<void>
```

execute 返回后按顺序处理：先触发 `onRunStart`，再检查 `isCancelled()`；如果已经取消且有 `stopRun`，调用它并等待现有 `waitForRun` 返回终态；把当前 outcome 标为 stopped，并立即返回 `{ stopped: true, remaining: tasks.slice(index + 1) }`。普通 `waitForRun` 返回 `run.status === 'stopped'` 时同样写 stopped outcome、停止队列，不启动下一项。`onTaskFinish` 仍只触发一次。

- [ ] **Step 5: 运行前端聚焦测试并提交**

```bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts tests/agentRunStatus.test.ts
npm run typecheck
git diff --check
git add src/api/client.ts src/api/types.ts src/lib/agentRunStatus.ts src/lib/workflowTasks.ts tests/workflowBatchRunner.test.ts tests/agentRunStatus.test.ts
git commit -m "feat: model stopped runs in frontend"
```

Expected: Node 测试和 `vue-tsc` 通过，队列仍保持串行和原有 failed/skip 语义。

## Task 6: 接入 Workflow 单次运行、批量运行和共享 Agent tabs

**Files:**

- Modify: `frontend/capabilities/src/components/AgentRunTabs.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue:120-140,1130-1300,1630-1705,2280-2640`

- [ ] **Step 1: 增加共享组件的停止契约**

在 `AgentRunTabs.vue` props/emits 增加：

```ts
canStop?: boolean
stopping?: boolean
// emit: stop
```

在刷新按钮旁显示一个 destructive 风格的“立即停止”按钮：`canStop && !stopping` 时可点击；点击只 emit `stop`，不弹确认框；`stopping` 时显示“停止中…”并禁用。保持 tabs 和 sticky 行为不变，使任务页与进度页复用相同入口。

- [ ] **Step 2: 为 WorkflowView 增加 stop 状态和单次停止函数**

增加 `batchStopping`、`progressStopping` 和 `stopError` 状态。实现：

```ts
async function stopProgressRun() {
  const runId = progressRunId.value
  if (!runId || progressStopping.value) return
  progressStopping.value = true
  try {
    await api.stopWorkflowRun(runId)
    await refreshProgress()
    await pollTestRun()
  } catch (e: unknown) {
    testError.value = errorMessage(e)
  } finally {
    progressStopping.value = false
  }
}
```

把 `AgentRunTabs` 的 `can-stop` 绑定为当前 `progressRun?.status === 'running'`，`@stop="stopProgressRun"`。批量详情使用同一个 stop API，但由 `stopBatchRun` 负责同时失效 batch token。

- [ ] **Step 3: 接入批量停止和 execute 返回竞态**

实现页面函数：

```ts
async function stopBatchRun() {
  if (batchAction.value !== 'run' || batchStopping.value) return
  batchStopping.value = true
  batchToken += 1
  try {
    if (batchCurrentRunId.value) {
      await api.stopWorkflowRun(batchCurrentRunId.value)
    }
  } catch (e: unknown) {
    taskActionError.value = errorMessage(e)
  } finally {
    batchStopping.value = false
  }
}
```

在 `runSelectedTasks` 的 queue options 传入 `stopRun: runId => api.stopWorkflowRun(runId).then(() => undefined)`。不要在 stopBatchRun 中清除 `batchCurrentRunId`；等待 `waitForBatchRun` 读取 stopped 后，由 `onTaskFinish` 和队列结果保留当前详情、把剩余任务写回 `selectedTaskIds`。如果 token 失效导致 `waitForBatchRun` 过早抛出，改为让它在停止状态下继续轮询当前 run，不要把后台运行遗留成未知状态。

在顶部批量状态卡增加“停止批量”按钮，仅 `batchAction === 'run'` 时显示；批量重置不显示。统计增加 stopped 计数或在汇总中明确“当前任务已停止”，剩余任务仍显示并保留选择。

- [ ] **Step 4: 接入单次执行按钮和进度状态**

单次 `executeTask` 跳转进度页后直接复用进度页 stop；`runWorkflow`、`prepareProgress`、`pollTestRun` 对 stopped 保持终态，不把 `testing` 重新设为 running。进度头 badge 使用 `runStatusLabel`/`runBadgeClass` 的 stopped 样式，停止中期间禁用重复停止。

批量 `waitForBatchRun` 的终态集合保持 `completed/no_task/failed/stopped`，收到 stopped 时让队列停止，不调用下一项。

- [ ] **Step 5: 运行前端类型检查和队列测试**

```bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts tests/workflowRunContextLayout.test.ts
npm run typecheck
```

Expected: 共享组件 emits/props、WorkflowView 的 queue callback 和 stopped 状态均无 TypeScript 错误；批量队列测试通过。

- [ ] **Step 6: 提交 Workflow 前端接入**

```bash
git diff --check
git add frontend/capabilities/src/components/AgentRunTabs.vue frontend/capabilities/src/views/workflow/WorkflowView.vue
git commit -m "feat: stop workflow runs from progress views"
```

Expected: 只提交 Workflow 单次/批量停止和共享 Agent tabs 文件。

## Task 7: 接入 Scripts 和 Workflow 设计 Agent 停止

**Files:**

- Modify: `frontend/capabilities/src/views/system/ScriptsView.vue:55-270,830-900`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue:120-140,540-620,2720-2810`
- Test: `frontend/capabilities/tests/designAgentStopGuards.test.ts`

- [ ] **Step 1: 写设计会话的源码结构测试**

新建 `frontend/capabilities/tests/designAgentStopGuards.test.ts`，读取两个 Vue 源码并断言每个设计面板都有 key、stop API、停止按钮和结果会话保护；测试不依赖挂载整个页面。

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const scripts = readFileSync(new URL('../src/views/system/ScriptsView.vue', import.meta.url), 'utf8')
const workflows = readFileSync(new URL('../src/views/workflow/WorkflowView.vue', import.meta.url), 'utf8')

test('both design agents expose a cancellable client run key', () => {
  assert.match(scripts, /designRunKey/)
  assert.match(scripts, /stopAgentRun/)
  assert.match(scripts, /立即停止/)
  assert.match(workflows, /designRunKey/)
  assert.match(workflows, /stopAgentRun/)
  assert.match(workflows, /立即停止/)
})
```

- [ ] **Step 2: 运行失败测试**

```bash
cd frontend/capabilities
node --import tsx --test tests/designAgentStopGuards.test.ts
```

Expected: 两个页面尚无设计 stop key/API/按钮，测试失败。

- [ ] **Step 3: 为 ScriptsView 增加设计 run key 和 stop 函数**

增加：

```ts
const designRunKey = ref('')
const designStopping = ref(false)

async function stopScriptDesigner() {
  if (!designRunKey.value || designStopping.value) return
  designStopping.value = true
  try {
    await api.stopAgentRun(designRunKey.value)
    designError.value = '设计 Agent 已停止'
  } catch (e: unknown) {
    designError.value = errorMessage(e)
  } finally {
    designStopping.value = false
  }
}
```

`runScriptDesigner` 开始时生成新的 `designRunKey`、清空 `designResponse`，调用 `api.designScript({ mode: designMode.value, prompt: designPrompt.value, current: scriptDesignerCurrent(), profile_key: form.value.owner_type === 'profile' ? form.value.owner_key : undefined, run_key: designRunKey.value })`。保存当前 key 到局部常量，返回后只有 key 仍匹配且响应未被停止会话替换时才写入 `designResponse`；停止响应不会调用 `acceptScriptDesign`。生成中显示“立即停止”，停止中显示“停止中…”，关闭/取消/采纳按钮保持禁用；停止完成后生成按钮恢复可用且表单不变。

- [ ] **Step 4: 为 WorkflowView 设计面板复用同一语义**

为 Workflow designer 增加 `designRunKey` 与 `designStopping`，`runWorkflowDesigner` 透传 `run_key` 并在开始时清空旧 response；实现 `stopWorkflowDesigner` 调用 `api.stopAgentRun`。模板加入 destructive “立即停止”按钮，停止结果不影响 Workflow 编辑表单，也不触发 `saveWorkflow`。

由于 WorkflowView 同时有 Workflow Run 的 `progressRunId`，设计 stop 必须始终使用 `designRunKey`，不能误用 `progressRunId`。

- [ ] **Step 5: 运行设计面板测试和 typecheck**

```bash
cd frontend/capabilities
node --import tsx --test tests/designAgentStopGuards.test.ts
npm run typecheck
```

Expected: 两个设计面板都具备独立 key/stop API，停止不改变编辑器表单的代码路径，typecheck 通过。

- [ ] **Step 6: 提交设计 Agent 前端接入**

```bash
git diff --check
git add frontend/capabilities/src/views/system/ScriptsView.vue frontend/capabilities/src/views/workflow/WorkflowView.vue frontend/capabilities/tests/designAgentStopGuards.test.ts
git commit -m "feat: stop design agents from editors"

## Task 8: 回归验证、人工验收和整理提交

**Files:**

- Verify: all files from Tasks 1–7
- Do not add generated `frontend/capabilities/tsconfig.tsbuildinfo` or build output.

- [ ] **Step 1: 运行后端聚焦测试**

```bash
pytest -q \
  tests/test_agent_runtime_control.py \
  tests/test_agent_service.py \
  tests/test_agent_runs_api.py \
  tests/test_design_agent_api.py \
  tests/test_workflow_runner.py \
  tests/test_workflow_storage.py \
  tests/test_workflow_scheduler.py \
  tests/test_workflow_api.py
```

Expected: 全部通过；停止相关测试至少覆盖运行前、运行中、重复停止、终态竞态、任务租约精确释放和 design key 透传。

- [ ] **Step 2: 运行前端聚焦测试、typecheck 和 build**

```bash
cd frontend/capabilities
node --import tsx --test tests/workflowBatchRunner.test.ts tests/agentRunStatus.test.ts tests/designAgentStopGuards.test.ts
npm run typecheck
npm run build
```

Expected: Node 测试、Vue typecheck 和 Vite build 均通过；构建生成物不加入提交。

- [ ] **Step 3: 人工验收四条主路径**

1. 启动一个工作流，进入进度页点击“立即停止”：按钮先显示“停止中…”，最终 Run 显示“已停止”，当前任务恢复 pending 并可再次执行。
2. 选择至少三个任务批量运行，在第一个任务 Agent 有输出时点击“停止批量”：当前 Run stopped，第二/第三项不启动，剩余项仍 selected。
3. 在批量 execute 尚未返回 run_id 时点击停止：请求稍后返回的 run_id 也收到 stop，不遗留后台 running Run。
4. Scripts 设计和 Workflow 设计各发起一次并在生成过程中停止：原编辑区不变，不出现可采纳旧草稿，不触发保存，生成按钮恢复可用。

另验证刷新单次 Workflow Run 详情页不会停止后端 Run；刷新批量任务页仍遵循既有页面级队列语义。

- [ ] **Step 4: 检查工作区并提交最终整合提交**

```bash
cd /Users/kyynor/Code/agent-bridge
git diff --check
git status --short
git log --oneline -8
```

确认：没有修改 `codex/lightweight-workflow-editor-design`，没有提交 tsbuildinfo/build 产物，所有停止相关提交只在当前 `main` worktree。若存在本功能未提交文件，按职责分组后提交：

```bash
git add src frontend/capabilities/tests tests
git commit -m "feat: add immediate stop for agent runs"
```

Expected: 工作树只剩用户原有或明确保留的未跟踪/未提交内容；最终交付报告列出聚焦测试、typecheck、build 和人工验收结果。
```
