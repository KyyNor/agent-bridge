# 工作流手动测试运行（Workflow Manual Test-Run）— 设计文档

- 日期：2026-06-17
- 状态：已评审（brainstorming 通过）
- 分支：`feat/workflow-test-run`

## 目标

工作流配置完成后，用户能在「工作流管理」列表点一个「测试运行」按钮，**立即跑一次**（不必等夜间调度窗口），跑完在页面上看结果：**状态 + 日志 + 产出的 markdown 产物**。

## 用户故事

在 工作流管理（`WorkflowView.vue`）选中一个工作流 → 点「测试运行」→ 该工作流立即执行一次 → 页面展示运行状态、实时日志，成功时展示产出的 markdown 产物。

## 关键决策（brainstorming 结论）

| 决策点 | 选择 |
|---|---|
| 结果展示范围 | 状态 + 日志 + 产物（复用现有「运行记录 / 日志 / 产物」面板；不做 stdout/stderr/result.json 原文查看） |
| 运行限制 | **旁路**每日时间窗（22:00–07:00）**和** active/disabled 状态——任何时间、任何状态都能测 |
| 执行模型 | 异步：触发立即返回 `run_id`；前端每 **5 秒**轮询单 run 状态端点直到终态 |
| 实现方案 | 复用 `scheduler.run_one_workflow`，接入其内存态 `_running` 守卫（`self._lock` 下）；不另起独立路径 |

## 范围

**包含（IN）**
- `scheduler.run_workflow_now(workflow_key)`
- `POST /workflows/{workflow_key}/run`、`GET /workflow-runs/{run_id}`
- 前端「测试运行」按钮 + 轮询 + 结果呈现（状态/日志/产物）
- 后端测试（pytest + `FakeWorkflowRunner`）

**不包含（OUT）**
- stdout/stderr/result.json 原文查看
- WebSocket / SSE 实时流
- 进程重启时孤儿 `running` 行的修复（既有问题，本次继承）
- 给 run 打「手动」标记（不加 flag 列）
- 前端自动化测试（仓库无前端测试基建，人工验证）

## 后端设计

### `scheduler.run_workflow_now(workflow_key) -> dict`（新增，`workflows/scheduler.py`）

```
with self._lock:
    if workflow_key in self._running:
        return {"status": "already_running"}
    run_id = f"run_{uuid.uuid4().hex}"
    # 同步建 running 行：轮询者能立刻查到
    store.create_workflow_run(run_id=run_id, workflow_key=workflow_key,
                              profile_key=<def.profile_key>, status="running",
                              temp_dir=str(base_dir/run_id), task_key=None)
    self._running.add(workflow_key)
# 起 daemon 线程，复用既有 _run_and_release → run_one_workflow
threading.Thread(target=self._run_and_release, args=(workflow_key,), daemon=True).start()
return {"status": "started", "run_id": run_id}
```

- **不查时间窗、不查 status**：这两项只在 `tick()` 里查，本方法不查 → 任何时间/状态可测。
- 需要先取定义拿 `profile_key`（`run_one_workflow` 内部也会取；为建 running 行需在此提前取一次 `profile_key`，或在 `run_one_workflow` 里建行——见下方重构）。

### `run_one_workflow(workflow_key, run_id=None)`（最小重构）

把 run_id 生成 + `create_workflow_run` 抽出，支持两种入口：
- `run_id is None`（调度路径）：内部生成 run_id + 建 running 行（维持现状）。
- `run_id` 已传入（手动路径）：跳过生成与建行（行已由 `run_workflow_now` 创建），直接进入「取定义 → 跑 runner → parse → ingest → finish」。

`_run_and_release` 不变。线程对两种入口都调 `run_one_workflow`。

### 端点（`api/routes/workflows.py`，admin-only，经 `require_admin_user`）

- `POST /workflows/{workflow_key}/run` → `scheduler.run_workflow_now(key)`：
  - 返回 `200 {"run_id": ...}`；`already_running` → `409 {"error": "already_running"}`。
- `GET /workflow-runs/{run_id}` → `store.get_workflow_run(run_id)`（已有方法，`sqlite.py`）：
  - 返回 `200` 单 run 行（status / exit_code / error / duration_ms / started_at / finished_at / stdout_path / stderr_path）；不存在 → `404`。

### 接线

- `create_workflow_routes(service, scheduler, actor, call_safely, ensure_capability_schema)`：新增 `scheduler` 参数。
- `api/app.py` 调用处（当前 `:143`）传入 scheduler——app 工厂经 knowledge service 已同时持有 `service.workflows` 与 `service.workflow_scheduler`（`knowledge/service.py:67-68`）。
- `WorkflowService` 职责不变（CRUD + 产物入库），不耦合 scheduler。

## 前端设计（`frontend/capabilities/src/views/workflow/WorkflowView.vue`）

遵循 in-SFC 约定，镜像现有 `searchArtifacts()` 的 loading 模式（`WorkflowView.vue:147-165`）。

- **按钮**：「测试运行」，放在详情头部「编辑/删除」旁（约 `:415-418`）。
  - 当该工作流存在 `status="running"` 的 run 时禁用（从 runs 列表推断 + 本地 `runningKey` ref）。
- **`runTest()` 处理**：
  1. `res = await api.runWorkflow(key)`；若 409 → 红条「已有运行在进行中」。
  2. 否则置 `runningKey = key`；每 5s 轮询 `api.getWorkflowRun(run_id)` 到终态（`completed | no_task | failed | stopped`）；每 tick 同时 `loadLogs(run_id)` 看实时日志，并自动选中本次 run。
  3. 终态后：清 `runningKey`、刷新 runs 列表与日志；`completed/no_task` → 调 `searchArtifacts()` 让新 markdown 出现在产物树 → 点开走**现有** markdown 弹窗；`failed` → status badge 显示「失败」+ 展示 `error`。
- 按钮文案运行中显示「运行中…」。
- `client.ts` 新增 `runWorkflow(key)`（POST）、`getWorkflowRun(runId)`（GET）；`types.ts` 复用已有 `WorkflowRun`。

## 并发 / 错误 / 边界

- 手动跑与调度跑共用 `_running`：同工作流一次只跑一个；并发触发 → 409 `already_running`。
- 手动跑在所有出口（success / no_task / failed）都调 `finish_workflow_run`，**不会产生手动跑孤儿**。（进程重启导致的 `running` 孤儿是既有问题，本次不修。）
- run 行 `task_key` 始终为 null（既有）；展示不需要它。
- 单次 run 可能耗时数分钟：按钮 + 轮询保持 UI 响应，无阻塞 HTTP。

## 测试（pytest + `FakeWorkflowRunner`）

- `run_workflow_now`：建出 `running` 行、返回 `run_id`；跑完到 `completed`/`no_task`；运行中再次触发返回 `already_running`。
- `POST /run` + `GET /workflow-runs/{id}`：经 API 测试客户端断言（仿 `tests/test_workflow_api.py`）。
- 旁路验证：disabled 工作流 / 窗口外时间，经 `run_workflow_now` 仍可跑。
- 前端：人工验证（按钮 → 轮询 → 产物呈现）。

## 未来（不在本次范围）

- stdout/stderr/result.json 原文查看器。
- 给 run 行加「手动」标记。
- 进程重启孤儿 `running` 行的对账。
- SSE/WebSocket 实时流。
