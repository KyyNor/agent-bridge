# 工作流增量执行与历史产物复用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏历史运行和产物的前提下，为工作流任务增加 `stale` 状态、增量执行预览、节点级历史 `output_json` 复用和完整的来源追踪；任务版本变化仍然强制全量执行，工作流版本变化则按 DAG 影响范围增量执行。

**Architecture:** 以 `workflow_tasks.task_version` 作为任务版本边界，以工作流定义 revision/content hash 作为工作流版本边界。新增纯函数式节点指纹与增量规划器，规划器以一个完整历史运行作为 baseline，不跨运行拼接节点。调度器在创建新的 `workflow_runs` 后生成不可变执行计划；执行器按计划对节点执行或复用，并把复用的 `output_json` 注入当前运行的节点上下文。历史运行、历史物理产物均只读，当前运行通过节点运行记录和产物关联表建立来源链路。

**Tech Stack:** Python 3、FastAPI、SQLite、Pydantic、asyncio、Vue 3、TypeScript、现有 `pytest` 与前端 `node:test`/TypeScript 构建链。

## Global Constraints

- `workflow_tasks.task_version` 是任务版本的唯一来源；任务版本发生变化时，`execution_mode=incremental` 也必须退化为全量执行。
- 不增加人工维护的节点版本号。节点版本由规范化后的节点类型、配置、执行资源指纹自动计算；节点 `position` 不进入指纹。
- 节点复用必须同时通过节点指纹、入边/条件指纹、上游复用状态、任务版本、运行环境/脚本/技能指纹、历史节点成功状态、`output_json` 完整性和产物有效性校验。
- 影响集合从直接修改节点、增删节点、入边/条件变化开始，沿 DAG 下游传播；节点位置变化但执行语义未变化不产生影响。
- 每次执行创建新 `workflow_runs`。一个增量运行只选择一个同工作流、同 profile、同 task version 的成功历史运行作为 baseline，不在多个运行之间混合节点来源。
- baseline 按成功时间/运行编号取最新可用运行；若该运行的某个节点产物失效，不从其他运行补该节点，而是从该节点开始重算并继续向下游传播。
- `stale` 只允许在任务 upsert/import 时由当前定义 revision 与最新成功运行比较后产生；调度器不会因为时间流逝自动启动执行。按同一 `workflow_key + task_key` 的 `set_at DESC, id DESC` 只处理最新任务版本，旧版本维持原状态。
- `stale` 与 `pending` 都可领取；领取顺序为一次性优先标记、`pending`、`stale`、其他可回收运行，不能让旧 `stale` 抢占新 `pending`。
- `stale` 运行失败时恢复为 `stale`；`pending` 运行失败时恢复为 `pending`。成功完成后才将任务设为 `completed`。
- `normal` 表示现有普通执行路径；`incremental` 使用规划器；`force_full` 绕过所有复用判断。当前 revision 已有成功完成结果的任务，默认不自动重跑；页面的运行按钮可以显式发起 `force_full`。
- 复用的 `output_json` 必须作为当前运行的节点输出进入后续模板上下文；如果输出引用 artifact，当前运行记录其来源关联，不能把历史 artifact 内容改写为当前运行内容。
- 不使用跨任务、跨 workflow、跨 task version 的缓存；外部 MCP 的实时资源没有稳定快照/指纹时判定为不可复用。
- 旧运行、旧节点运行和旧物理 artifact 不更新、不删除；并发运行只写自己的运行记录和产物关联，不能用全局 `is_current` 更新覆盖其他正在运行的结果。
- 所有后端行为先通过失败测试锁定，再实现；每个任务完成后提交一个独立 commit。实现过程禁止引入未完成标记、占位实现或静默放宽复用条件。

---

## Task 1: 增加 stale 状态、运行元数据和数据库迁移

**Files:**

- Modify: `src/agent_bridge/automation/workflows/models.py`
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Test: `tests/test_workflow_storage.py`

**Interfaces and data contract:**

- `WorkflowTaskStatus.stale = "stale"`。
- `workflow_runs` 新增：`workflow_revision_no INTEGER`、`workflow_content_hash TEXT`、`task_version TEXT`、`execution_mode TEXT`、`execution_plan_json TEXT`、`source_run_id TEXT`。
- `workflow_node_runs` 新增：`node_fingerprint TEXT`、`action TEXT`、`reuse_reason TEXT`、`source_run_id TEXT`、`source_node_id TEXT`、`source_node_fingerprint TEXT`、`artifact_ids_json TEXT`。
- `workflow_tasks` 新增：`lease_origin_status TEXT`，用于失败释放时恢复 `pending` 或 `stale`。
- 新表 `workflow_run_artifacts`：`run_id`、`node_id`、`artifact_id`、`source_run_id`、`source_node_id`、`created_at`，主键为 `(run_id, node_id, artifact_id)`，用于把复用的历史 artifact 映射到当前运行。
- 所有新增字段都由 `_ensure_columns`/`CREATE TABLE IF NOT EXISTS` 兼容已有数据库，新增索引覆盖 `(workflow_key, task_key, set_at DESC, id DESC)`、`(workflow_key, task_version, status)`、`workflow_run_artifacts(run_id, node_id)`。

**Implementation steps:**

- [ ] 先在存储测试中增加失败用例：旧数据库初始化后能读到新增列；状态值可序列化为 `stale`；同一 `workflow_key + task_key` 的多个版本只允许最新 `set_at` 版本被标记/领取；`stale` 领取时写入 `lease_origin_status=stale`；失败释放恢复为 `stale`；成功完成清理来源状态。
- [ ] 在 schema 和 SQLite migration 中加入上述字段、表和索引，保持已有数据库可启动，迁移重复执行不报错。
- [ ] 更新 repository 的 `_row_payload`，把新增 JSON 字段解析为对象/数组，把 `is_current` 和新增 action 字段保持可读类型。
- [ ] 把 `_workflow_task_action` 的过期 completed 逻辑保留为按 `set_at` 判断，但输出动作只针对该 task key 最新版本；非最新历史版本不得被 reopened/stale。
- [ ] 修改 `lease_workflow_task`：查询 `pending`/`stale`，先按 `priority_flag`，再按 `status` 优先级 `pending=0, stale=1`，最后按 id；更新时保存 `lease_origin_status` 并同步 `workflow_runs.task_key/task_version`。
- [ ] 修改 reset、complete、release/abandon 路径：reset 统一回 `pending`；complete 清除 `lease_origin_status`；失败释放恢复 `lease_origin_status or pending`，abandon 清空它。
- [ ] 为 run/node/artifact association 提供 repository round-trip 方法：创建 run 时保存运行元数据，创建节点运行时保存 fingerprint/action/source 字段，完成节点时保存 `output_json`、artifact ids 和来源；关联表只允许追加当前 run 的关联。
- [ ] 运行 `pytest -q tests/test_workflow_storage.py`，确认新增测试全绿且原有 task lease、30 天 `set_at` 规则、node run round-trip 不回归。
- [ ] 提交：`git add src/agent_bridge/automation/workflows/models.py src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/workflows.py tests/test_workflow_storage.py && git commit -m "feat: add stale workflow task state and run metadata"`。

## Task 2: 实现节点指纹与增量执行规划器

**Files:**

- Create: `src/agent_bridge/automation/workflows/incremental.py`
- Modify: `src/agent_bridge/automation/workflows/definition.py`（仅补充稳定的配置序列化/类型辅助，不改变现有 API）
- Modify: `src/agent_bridge/automation/workflows/validator.py`（补充资源指纹解析入口）
- Test: `tests/test_workflow_incremental.py`

**Interfaces and data contract:**

- 定义 `ExecutionMode = Literal["normal", "incremental", "force_full"]`。
- 定义不可变数据类 `NodePlan(node_id, action, reason, node_fingerprint, source_run_id, source_node_id, source_node_fingerprint, output_json, artifact_ids, condition_results)`，其中 `action` 只有 `"execute"` 或 `"reuse"`。
- 定义不可变数据类 `IncrementalPlan(workflow_key, workflow_revision_no, workflow_content_hash, task_version, mode, baseline_run_id, nodes, affected_node_ids, reusable_node_ids, reasons, warnings)`。
- 定义 `WorkflowIncrementalPlanner.build(*, workflow, current_revision, task, mode, baseline_run, baseline_node_runs, baseline_artifacts, runtime_fingerprint) -> IncrementalPlan`。
- 节点 fingerprint 规范化规则：保留 node id/type/config、执行资源版本、必要的 profile/runtime 标识；忽略 `position`、纯展示 metadata；字典按 key 排序、数组保持语义顺序后 SHA-256。
- 边 fingerprint 规范化规则：保留 edge id/source/target/condition；只要 target 的入边集合或条件签名变化，就把 target 及下游加入影响集合。

**Implementation steps:**

- [ ] 先写失败测试覆盖：a→b→c→d 仅 c 修改得到 `reuse(a,b), execute(c,d)`；修改 b、d 的传播；配置/type/edge/condition/新增/删除节点；仅 position 变化全部可复用；task version 变化全量；`force_full` 全量；历史 node run 失败、缺 output、artifact 缺失/失效从该点开始执行；同一 task version 的 v2/v3 baseline 只选一个最新成功 run；外部实时 MCP 无稳定指纹时不可复用。
- [ ] 实现规范化 JSON、node/edge/resource fingerprint 和 DAG 下游闭包。删除节点不出现在当前计划，但其后继由于入边变化必须进入影响集合。
- [ ] 实现 baseline 校验：workflow/profile/task version 必须相同；baseline run 必须 `completed`；候选按最新成功运行选择，候选不可用时按历史顺序选择下一个完整运行；不得为单个节点跨 run 补来源。
- [ ] 实现保守复用判定：直接变化节点、上游 action 为 execute、入边/条件变化、节点运行非 completed、output_json 非对象/缺失、artifact 不存在/过期/不可复用、资源指纹不匹配都返回 execute 及明确 reason。
- [ ] 将正常模式表示为全部 execute；将 force_full 表示为全部 execute 且 reason=`force_full`；将 incremental 在无 baseline 时表示为全部 execute 且 reason=`no_usable_baseline`。
- [ ] 对复用节点保留原始 output_json，不在 planner 内修改业务字段；artifact ids 和来源交给执行器写入当前 run 的关联表。
- [ ] 运行 `pytest -q tests/test_workflow_incremental.py`，确认每个节点都能得到可供 API 展示的判断原因。
- [ ] 提交：`git add src/agent_bridge/automation/workflows/incremental.py src/agent_bridge/automation/workflows/definition.py src/agent_bridge/automation/workflows/validator.py tests/test_workflow_incremental.py && git commit -m "feat: plan workflow node reuse by fingerprints"`。

## Task 3: 让 DAG 执行器真正复用 output_json 和 artifact 来源

**Files:**

- Modify: `src/agent_bridge/automation/workflows/executor.py`
- Modify: `src/agent_bridge/automation/workflows/handlers.py`
- Modify: `src/agent_bridge/automation/workflows/output_handler.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `tests/test_workflow_executor.py`
- Modify: `tests/test_workflow_output_handler.py`
- Test: `tests/test_workflow_incremental_executor.py`

**Interfaces and data contract:**

- `WorkflowDagExecutor.run(..., plan: IncrementalPlan | None = None)`；`plan=None` 保持现有全量行为。
- `NodeExecutionContext` 新增 `execution_mode` 和可选 `reused_sources`，`template_context()` 继续以 `nodes[node_id]` 暴露 `output_json`，因此新节点无需区分历史还是当前产物。
- `WorkflowService.save_artifact(..., producer_node_id, producer_node_fingerprint, run_id, ...)` 在新增物理 artifact 时写入当前 run，并通过 `workflow_run_artifacts` 建立当前节点关联；复用 artifact 只写关联，不改历史行。
- 当前节点运行最终状态仍使用 `completed`/`warning` 等现有状态，另用 `action=reuse` 标明没有调用 handler；`output_json` 在当前 `workflow_node_runs` 中完整保存。

**Implementation steps:**

- [ ] 先写失败测试：handler 计数器证明 a/b 没有再次调用；c 使用复用 b 的 output_json；c 完成后 d 使用新 c 输出；复用 get_task 时恢复 context.task；复用 output 节点时保留 artifact ids 和 source run；force_full 每个 handler 都被调用；历史 output/artifact 无效时 handler 从最近节点重新开始。
- [ ] 在 executor 初始化节点计划并为每个当前节点创建 node run；ready 节点若 action=`reuse`，直接从 `NodePlan.output_json` 构造 `NodeExecutionResult`，不创建 asyncio handler task。
- [ ] 复用节点调用统一的 `_persist_result`，但写入 `action`, `reuse_reason`, `source_run_id/source_node_id`, `source_node_fingerprint` 和 artifact ids；更新 `outputs[node.id]`，若是 get_task 则同步 `task`。
- [ ] 新执行节点继续走现有 handler；写入当前节点 fingerprint/action=`execute`；output handler 产生的 artifact 用当前 run 关联。
- [ ] 增加 executor 级别的 source artifact 校验和关联写入，确保复用 output_json 中引用的 artifact 已存在、未过期、未标记不可复用；校验失败转换为 execute 计划结果，而不是异常地使用无效缓存。
- [ ] 保持条件节点行为：条件计算使用当前 `outputs`，复用节点和新执行节点对下游完全等价；被条件跳过的节点 action=`execute`、status=`skipped`，并记录 `condition_not_matched`。
- [ ] 更新输出聚合、workflow run output 和 node run API payload，使复用节点和新执行节点的字段结构一致。
- [ ] 运行 `pytest -q tests/test_workflow_executor.py tests/test_workflow_output_handler.py tests/test_workflow_incremental_executor.py`，确认并发 ready 节点、异常取消、no_task 和条件分支不回归。
- [ ] 提交：`git add src/agent_bridge/automation/workflows/executor.py src/agent_bridge/automation/workflows/handlers.py src/agent_bridge/automation/workflows/output_handler.py src/agent_bridge/automation/workflows/service.py tests/test_workflow_executor.py tests/test_workflow_output_handler.py tests/test_workflow_incremental_executor.py && git commit -m "feat: reuse historical workflow node outputs"`。

## Task 4: 接入任务 upsert、调度器和运行生命周期

**Files:**

- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `src/agent_bridge/automation/workflows/scheduler.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Modify: `tests/test_workflow_service.py`
- Modify: `tests/test_workflow_scheduler_review.py`
- Test: `tests/test_workflow_incremental_runs.py`

**Interfaces and data contract:**

- `WorkflowScheduler.run_workflow_now(workflow_key, input_data=None, actor=None, *, task_key=None, task_version=None, execution_mode="normal") -> dict`。
- 新增 service 层 `preview_incremental_run(...)` 和 `build_incremental_plan(...)`，scheduler 只负责运行实例和线程/锁生命周期，规划规则集中在 planner。
- `create_workflow_run` 必须在执行前保存当前 definition snapshot、revision no/content hash、task version、execution mode 和 serialized execution plan；计划生成失败不得启动 handler。
- 运行结束时写入当前 run 的最终 output/status；仅当 run 成功且仍对应当前 workflow revision/task version 时完成 task。定义在执行中变化时，当前 run 保留结果但不错误地把新 revision 标成 completed。

**Implementation steps:**

- [ ] 先写失败测试：v1 完成后修改 c，upsert 只把最新 set_at task 标为 stale；v2/v3 都 stale 时 v4 运行只选择最新可用完整 baseline；修改 b/d 的复用矩阵；task version 变化全量；旧 task version complete 不被 stale；stale 失败后仍可领取；并发 run 的 run_id/output/artifact 互不覆盖。
- [ ] 在 `upsert_definition` 完成 revision 写入后调用一个事务内的 `mark_latest_task_stale_if_needed(workflow_key, revision_no, content_hash)`：按 `set_at DESC, id DESC` 取每个 task key 最新行，只对当前 task version 的最近成功 run 与新 revision 不一致的任务更新为 stale；pending/running/failed 不被无条件覆盖。
- [ ] 让 task execute 的 leasability 接受 `pending` 和 `stale`；对当前 revision 已 completed 的任务，保留服务层拒绝默认执行的保护，显式 `force_full` 路径才能运行，避免普通刷新导致重复任务。
- [ ] scheduler 启动 run 时解析 task 目标：task execute 路径传入明确 task_key/task_version，普通 workflow run 沿用 get_task lease。拿到任务后重新校验 task version，并生成 plan；计划中的 baseline、原因和节点动作写入 run。
- [ ] 在运行结束/异常/停止时统一调用 release/complete：失败按 `lease_origin_status` 回到 stale 或 pending，历史 run 不变；完成时只更新本 run 的记录，当前任务版本与 revision 仍匹配才写 completed。
- [ ] 为 same task key 多版本查询增加显式排序和过滤 helper，避免 `task_version=None` 时随机选到旧版本；所有执行、预览、artifact 查找都传递 task version。
- [ ] 运行 `pytest -q tests/test_workflow_service.py tests/test_workflow_scheduler_review.py tests/test_workflow_incremental_runs.py`，确认 scheduler 的 active workflow tick、手动运行、停止和异常日志行为不回归。
- [ ] 提交：`git add src/agent_bridge/automation/workflows/service.py src/agent_bridge/automation/workflows/scheduler.py src/agent_bridge/storage/repositories/workflows.py tests/test_workflow_service.py tests/test_workflow_scheduler_review.py tests/test_workflow_incremental_runs.py && git commit -m "feat: schedule stale tasks with incremental plans"`。

## Task 5: 提供增量预览和强制全量执行 API

**Files:**

- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Test: `tests/test_workflow_api.py`
- Test: `tests/test_workflow_incremental_api.py`
- Modify: `tests/test_workflow_task_execute_reset_api.py`

**Interfaces and data contract:**

- `WorkflowRunRequest` 新增 `task_key: str | None`、`task_version: str | None`、`execution_mode: Literal["normal", "incremental", "force_full"] = "normal"`；保留 `input`。
- 新增 `WorkflowRunPreviewRequest`，字段与 run 请求一致但不创建运行、不领取任务、不写 artifact。
- 新增 `POST /workflows/{workflow_key}/run/preview`，返回 `IncrementalPlan` 的 JSON：`mode`、`baseline_run_id`、`affected_node_ids`、`reusable_node_ids`、`nodes[{node_id, action, reason, source_run_id, source_node_id, node_fingerprint}]`、`warnings`。
- `POST /workflows/{workflow_key}/run` 和 task execute 支持 `execution_mode`; stale task 默认 incremental；completed current revision 只有显式 force_full 允许重跑。
- API 返回新 run 的 `run_id`、`run_status`、`execution_mode` 和计划摘要；已有客户端字段保持兼容。

**Implementation steps:**

- [ ] 先写失败 API 测试：预览不产生 run/task lease；仅修改 c 返回 a/b reuse、c/d execute 及理由；任务版本变化返回全量；stale task execute 可成功启动；completed 当前版本未指定 force_full 返回 409；force_full 能启动；task version 缺省时只选最新 set_at 版本。
- [ ] 在 Pydantic schema 中限制 execution mode 枚举，拒绝未知值；对 task_key path 参数和 request task_key 冲突返回 400/422，不静默覆盖。
- [ ] 路由调用 service preview/run，不直接拼装 planner 数据；对预览使用与真实运行完全相同的 baseline、资源指纹和有效性校验。
- [ ] 保持历史 execute/reset 测试的其余行为，并把“completed 必须先 reset”的旧断言改为“当前 completed 默认拒绝，force_full 明确允许；stale 直接可领取”。
- [ ] 运行 `pytest -q tests/test_workflow_api.py tests/test_workflow_incremental_api.py tests/test_workflow_task_execute_reset_api.py`，确认响应字段、错误码和权限校验完整。
- [ ] 提交：`git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/workflows.py src/agent_bridge/automation/workflows/service.py tests/test_workflow_api.py tests/test_workflow_incremental_api.py tests/test_workflow_task_execute_reset_api.py && git commit -m "feat: expose incremental preview and run modes"`。

## Task 6: 完善 artifact 来源查询、有效性校验和并发隔离

**Files:**

- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Modify: `src/agent_bridge/automation/workflows/output_handler.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `tests/test_workflow_artifact_full.py`
- Test: `tests/test_workflow_artifact_reuse.py`

**Interfaces and data contract:**

- artifact 有效性由存在性、content/content_hash 校验、过期标识、不可复用标识和所属 workflow/profile/task_version 共同决定；新增 `reuse_allowed INTEGER DEFAULT 1`、`invalid_reason TEXT`、`producer_node_id TEXT`、`producer_node_fingerprint TEXT`。
- 增加 `list_artifacts_for_run(run_id, include_reused=True)`，通过 `workflow_run_artifacts` 返回当前 run 的新产物与复用产物，并带 `source_run_id/source_node_id`。
- `save_artifact` 只写当前 run 的 artifact；source artifact 不做 `is_current`/content 更新。当前指针只在成功 run 完成事务内更新，运行中和失败 run 不修改其他 run 的 current 状态。

**Implementation steps:**

- [ ] 先写失败测试：复用节点能看到历史 artifact；历史 artifact 删除、hash 不一致、`reuse_allowed=0`、task version/workflow/profile 不同均阻止复用；两个并发 run 写 artifact 后都能查到自己的关联且内容不互串；历史 API 的 include_history/full 行为保持不变。
- [ ] 扩展 artifact schema/repository 写入 producer 元数据和有效性字段；新增关联表的插入、查询、删除仅限定当前 run。
- [ ] 修改 artifact search/detail/history 查询：`run_id=current` 能查到当前 run 关联的复用 artifact，默认列表仍按逻辑 current 结果返回；保留原有物理 artifact history。
- [ ] 在 `OutputHandler` 保存产物时传入 node id/fingerprint 并创建关联；executor 的 reuse 分支复制关联，不复制或修改 source artifact。
- [ ] 对 output_json 中的 artifact ids 执行来源校验并保留原 ids；通过当前 run 的关联查询把它们解释为当前上下文的输入，避免跨 workflow/task version 误用。
- [ ] 运行 `pytest -q tests/test_workflow_artifact_full.py tests/test_workflow_artifact_reuse.py`，确认内容缓存、全文查询、历史版本和并发隔离均通过。
- [ ] 提交：`git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/workflows.py src/agent_bridge/automation/workflows/output_handler.py src/agent_bridge/automation/workflows/service.py tests/test_workflow_artifact_full.py tests/test_workflow_artifact_reuse.py && git commit -m "feat: track reusable artifact lineage per workflow run"`。

## Task 7: 前端支持 stale、运行模式和复用预览

**Files:**

- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/lib/workflowTasks.ts`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue`
- Modify: `frontend/capabilities/tests/workflowTasks.test.ts`
- Modify: `frontend/capabilities/tests/workflowBatchRunner.test.ts`
- Test: `frontend/capabilities/tests/workflowIncrementalRun.test.ts`

**Interfaces and data contract:**

- TypeScript 增加 `WorkflowTaskStatus`、`WorkflowExecutionMode`、`WorkflowNodeAction`、`WorkflowNodePlan`、`WorkflowExecutionPlan` 类型；`WorkflowRun` 增加 revision/task version/mode/plan/source 字段；`WorkflowNodeRun` 增加 action/reason/source/fingerprint/artifact ids。
- client 增加 `previewWorkflowRun`，扩展 `runWorkflow`/`executeWorkflowTask` 的 mode/task version 参数；请求参数与后端 schema 一一对应。
- `workflowTasks.ts` 的状态顺序为 `running, pending, stale, failed, abandoned, completed`；增加 stale 标签“待增量执行”和 `canExecuteTask` 纯函数，stale 与 pending 都可执行，completed 当前版本只有 force-full action。

**Implementation steps:**

- [ ] 先写前端失败测试：stale label/order/filter/stats；stale 可进入批量执行队列；preview response 能正确渲染 reuse/execute；completed current 不显示默认增量执行而显示“全量运行”入口。
- [ ] 更新 API 类型和 client，保持现有调用默认 `normal` 的兼容行为；所有 task run 明确传 `task_version`，避免同 key 多版本误选。
- [ ] 在 WorkflowView 任务表加入 stale badge、复用/执行统计和“运行”按钮：stale 默认 preview 后 incremental run；completed 有产物或当前结果时允许显式 force_full；运行按钮不自动确认、不新增后台自动执行。
- [ ] 增加预览面板/弹窗，展示 baseline run、复用节点、重新执行节点和每节点 reason；预览关闭不改变任务状态。
- [ ] 在 artifact 列表或任务产物区域提供运行入口，使存在历史产物的任务也能手动发起运行；对 stale 使用增量，对 completed 使用 force_full。
- [ ] 更新 WorkflowRunGraph：节点显示 `reuse`/`execute`、来源运行和未复用原因；当前运行轮询期间不把复用节点误显示成 waiting。
- [ ] 运行 `cd frontend/capabilities && npm test`（若项目脚本映射为 node:test，则运行对应脚本）、`npm run typecheck`、`npm run build`，确认没有类型和模板编译错误。
- [ ] 提交：`git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/lib/workflowTasks.ts frontend/capabilities/src/views/workflow/WorkflowView.vue frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue frontend/capabilities/tests/workflowTasks.test.ts frontend/capabilities/tests/workflowBatchRunner.test.ts frontend/capabilities/tests/workflowIncrementalRun.test.ts && git commit -m "feat: add stale task and incremental run controls"`。

## Task 8: 完成十项验收场景和回归验证

**Files:**

- Create: `tests/test_workflow_incremental_acceptance.py`
- Modify: `tests/test_versioning_workflows.py`
- Modify: `tests/test_workflow_api.py`
- Modify: `frontend/capabilities/tests/workflowIncrementalRun.test.ts`
- Modify: `docs/superpowers/specs/2026-07-18-workflow-incremental-execution-design.md`（同步最终接口名和迁移字段）
- Modify: `docs/superpowers/specs/2026-07-18-workflow-incremental-execution-design.html`（同步最终接口名和迁移字段）

**Acceptance matrix:**

- [ ] 仅修改 c：复用 a、b，执行 c、d。
- [ ] 修改 b：复用 a，执行 b、c、d。
- [ ] 修改 d：复用 a、b、c，只执行 d。
- [ ] 修改 `workflow_tasks.task_version`：a、b、c、d 全量执行。
- [ ] 修改节点配置：该节点和全部下游执行。
- [ ] 只改 node position：节点仍可复用。
- [ ] 修改边或条件：从受影响 target 开始重新计算下游。
- [ ] 新增/删除节点：新节点执行，删除节点不污染当前图，受影响后继重新执行。
- [ ] 历史产物缺失/失效：从最近不可复用节点重新执行，后续依赖继续执行。
- [ ] 预览返回每个节点的 reuse/execute action、baseline/source 和判断原因；运行详情与 artifact 查询展示相同来源。

**Implementation steps:**

- [ ] 先将上述十项写成端到端 service/API 测试，使用固定 handler/script stub 和可控时间，断言调用次数、节点 action、task status、run lineage 和 output_json 内容，而不是只断言最终 status。
- [ ] 增加 v2/v3/v4 场景：v2、v3 都 complete/stale 时只选择最新可用成功运行作为 baseline；若最新 baseline 某节点无效，不能从 v2 偷取单个节点。
- [ ] 增加同 task key 多版本场景：旧版本 complete、新版本 complete 时只有最新 `set_at` 版本在新 revision 变化后 stale；旧版本可查询但不被默认 execute。
- [ ] 增加增量运行失败、强制全量、并发运行、运行中定义变化和数据库迁移回归测试。
- [ ] 更新设计文档中的最终字段/端点命名，确保文档、API response、数据库迁移和前端类型一致。
- [ ] 运行后端完整测试：`pytest -q`；运行前端：`cd frontend/capabilities && npm test && npm run typecheck && npm run build`。
- [ ] 运行 `git diff --check`、检查工作区没有未追踪的临时数据库/构建产物，并提交：`git add tests/test_workflow_incremental_acceptance.py tests/test_versioning_workflows.py tests/test_workflow_api.py frontend/capabilities/tests/workflowIncrementalRun.test.ts docs/superpowers/specs/2026-07-18-workflow-incremental-execution-design.md docs/superpowers/specs/2026-07-18-workflow-incremental-execution-design.html && git commit -m "test: cover workflow incremental execution acceptance"`。

## Definition of Done

- `stale` 能按最新 `set_at` 任务版本生成、领取、失败恢复和完成清理。
- planner 能输出可解释的逐节点计划，位置变化不影响复用，任务版本变化强制全量，影响沿 DAG 下游传播。
- executor 不调用复用节点 handler，能将历史完整 `output_json` 注入当前上下文，并记录 source run/node/fingerprint/reason。
- artifact 内容和来源可校验、可查询、不可跨任务版本混用，并发运行之间没有覆盖或污染。
- API 支持预览、增量、强制全量；页面支持 stale/产物任务手动运行并展示每节点原因。
- 十项验收场景、后端全量测试、前端测试、typecheck 和 build 全部通过。
