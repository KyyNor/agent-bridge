# 工作流单窗口运行次数上限（Workflow Per-Window Run Limit）— 设计文档

- 日期：2026-06-18
- 状态：已评审（brainstorming 通过）
- 分支：`feat/workflow-run-limit`

## 目标

在系统配置页新增一个全局上限 N（`workflow_max_runs`）：每个 active 工作流在一个运行窗口内**最多被自动调度 N 次**。例如设为 10，则工作流 A、B、C 在今晚各自的窗口内各最多自动跑 10 次（各自独立，非合计）。N=0（默认）表示不限，完全保持现状。

## 背景

当前 `WorkflowScheduler.tick()`（`workflows/scheduler.py`）每 60 秒在运行窗口内（默认 22:00→07:00）反复拉起所有 `active` 且本窗口尚未返回 `no_task` 的工作流，**窗口内不限运行次数**。唯一的"停止"信号是工作流返回 `no_task`（进入 `finished_today`）或被置为 `disabled`。本功能在不改变窗口机制的前提下，新增一个按工作流计的运行次数硬上限，用于控制成本/运行量。

## 关键决策（brainstorming 结论）

| 决策点 | 选择 |
|---|---|
| 配置粒度 | **全局一个 N**，应用到每个工作流各自（A、B、C 各 N 次，非合计） |
| 计数语义 | 每次自动调度拉起算 1 次（成功 / 失败 / no_task 都算） |
| 手动触发（`run_workflow_now`） | **不计入配额、也不受上限约束**（沿用其绕过窗口/状态的既有语义） |
| 计数存储 | **内存**（`dict[str,int]`），不改 DB schema；窗口打开时清零，与 `finished_today` 同机制 |
| 重置时机 | 运行窗口打开时清零；`always-on`（未配窗口）按日历日 0 点 |
| 超出后行为 | 该工作流本窗口不再被自动调度，下个窗口自动恢复 |
| 默认值 | `0` = 不限，保持现状（回归保障） |

## 范围

**包含（IN）**
- 系统配置项 `workflow_max_runs`（建表列 + migration + get/save + Pydantic + service 透传）
- `WorkflowScheduler`：内存计数 `run_counts`、tick 筛选/计数/重置、`_load_window` 读配置、`get_status` 暴露
- 前端：配置输入框 + 校验 + 状态区显示运行计数进度
- 后端测试（pytest + `FakeWorkflowRunner`，沿用现有测试结构）

**不包含（OUT）**
- per-workflow 各自独立的上限值（YAGNI，全局 N 已满足需求）
- DB 持久化计数 / 给 `workflow_runs` 加 `trigger` 列（已选内存方案；重启丢计数的权衡被接受）
- 进程重启后从历史恢复计数（接受重启清零，同 `finished_today` 既有局限）
- 前端自动化测试（仓库无前端测试基建，人工验证）

## 后端设计

### 计数数据结构（`WorkflowScheduler.__init__`，`scheduler.py:38-55`）

```python
self._max_runs: int = 0                # 0 = unlimited
self.run_counts: dict[str, int] = {}   # 本窗口每个 workflow 已自动运行次数
```

### `tick()` 改动（`scheduler.py:165-191`，全部在已有 `self._lock` 内，无需额外同步）

**1. 窗口重置**（紧随 `self.finished_today.clear()`，约 :174）：
```python
self.finished_today.clear()
self.run_counts.clear()
self._window_marker = anchor
```

**2. 候选筛选**（:183），排除已达上限的工作流：
```python
candidates = {item["workflow_key"] for item in workflows} - self.finished_today
if self._max_runs > 0:
    candidates = {k for k in candidates if self.run_counts.get(k, 0) < self._max_runs}
```

**3. 拉起时计数**（:188-189，紧随 `self._running.add(workflow_key)`）：
```python
for workflow_key in batch:
    self._running.add(workflow_key)
    if self._max_runs > 0:
        self.run_counts[workflow_key] = self.run_counts.get(workflow_key, 0) + 1
    thread = threading.Thread(target=self._run_and_release, args=(workflow_key,), daemon=True)
    thread.start()
```

### `_load_window()` 读配置（`scheduler.py:108-113`）

```python
config = self._store.get_sync_config()
self._start_time_str = config.get("workflow_start_time") or _DEFAULT_START_TIME
self._stop_time_str = config.get("workflow_stop_time") or _DEFAULT_STOP_TIME
self._start_time = _parse_hhmm(self._start_time_str)
self._stop_time = _parse_hhmm(self._stop_time_str)
self._max_runs = int(config.get("workflow_max_runs") or 0)
```

`save_sync_config` 末尾已调用 `self.workflow_scheduler.refresh()`（`knowledge/service.py:375`），配置改动即时生效。

### 为什么"手动不计入"天然成立

自动调度经 `tick()` → `_run_and_release` → `run_one_workflow(workflow_key, run_id=None)`；手动经 `run_workflow_now` → 预先传入 `run_id` → `run_one_workflow(workflow_key, run_id=...)`。计数 +1 只发生在 `tick()`（仅自动调度经过），手动路径完全不碰 `run_counts`，因此无需给 run 行打标记。`run_workflow_now` 本就绕过窗口/状态，自然也不受 `_max_runs` 限制。

### `get_status()` 暴露（`scheduler.py:86-102`）

```python
"max_runs": self._max_runs,
"run_counts": dict(self.run_counts),
```

## 配置链路（新增 `workflow_max_runs`，共 6 处改动 + 1 处无需改）

| # | 文件:行 | 改动 |
|---|---|---|
| 1 | `storage/schema.py:289` | `knowledge_sync_config` 建表加列 `workflow_max_runs INTEGER NOT NULL DEFAULT 0` |
| 2 | `storage/sqlite.py:106-116` | migration `_ensure_columns` 加 `"workflow_max_runs": "INTEGER NOT NULL DEFAULT 0"` |
| 3 | `storage/repositories/codegraph.py:320-347` | `get_sync_config`：defaults 加 `"workflow_max_runs": 0`、SELECT 加列、result 投影 `workflow_max_runs: int(row[6] or 0)` |
| 4 | `storage/repositories/codegraph.py:349-382` | `save_sync_config`：加参数 `workflow_max_runs: int = 0`、INSERT/ON CONFLICT 加列与占位符、return 加 |
| 5 | `api/schemas.py:126-132` | `KnowledgeSyncConfigRequest` 加 `workflow_max_runs: int = 0` |
| 6 | `knowledge/service.py:352-376` | `save_sync_config` 加参数并透传给 `store.save_sync_config` |
| 7 | `api/routes/builtins.py:157-159` | **无需改**（`**payload.model_dump()` 自动透传新字段） |

## 前端设计（`frontend/capabilities/src/views/knowledge/KnowledgeProcessingConfigView.vue`）

- **类型**（`api/types.ts:430-437`）：`KnowledgeSyncConfig` 加 `workflow_max_runs: number`。
- **状态初值**（`syncConfig` ref，:16-23）：加 `workflow_max_runs: 0`。
- **输入框**：在「工作流调度」时间行（:276-285）下方新增一行：
  - 标签「单工作流最大运行次数/窗口」，`<Input v-model.number="syncConfig.workflow_max_runs" type="number" min="0" />`
  - 提示文字「0 = 不限；每个工作流在一个调度窗口内最多自动运行该次数（手动测试运行不受限）」
- **校验**：新增 `maxRunsValid` computed（非负整数），并入 `cronValid`（:107-112），决定保存按钮禁用。
- **`client.ts:238`**：无需改（整体 `post(config)`）。
- **状态区进度**（:408-411 块）：`schedulerStatus.workflow` 现含 `max_runs`/`run_counts`，新增一行展示，如「运行计数：A 3/10、B 7/10」（`max_runs=0` 时显示「不限」）。

## 并发 / 错误 / 边界

- **线程安全**：所有 `run_counts` 读写均在 `tick()` 的 `self._lock` 内，与 `_running`/`finished_today` 同锁，无竞态。
- **计数增长速率**：受 tick 60s 间隔 + `_running` 守卫约束，单工作流每分钟最多 +1；N=10 最快约 10 分钟达上限。
- **与 `finished_today` 互不干扰**：两者独立排除候选，取并集；一个工作流可能因 `no_task` 提前进 `finished_today`（此时 `run_counts` 可能未达 N），也可能因达 N 而停（此时未必进 `finished_today`）。
- **重启**：`run_counts` 为内存态，进程重启清零 → 当窗口可能超跑几次。这是选用内存方案的已接受权衡（同 `finished_today`）。
- **`max_runs=0`**：筛选与计数分支均被 `if self._max_runs > 0` 短路，行为与现状完全一致。
- **配置即时生效**：`save_sync_config` 末尾的 `refresh()` 立即重读 `_max_runs`，下个 tick（≤60s）按新值评估。调大 N 可让被限工作流恢复调度；调为 0 解除所有限制；调小 N 立即收紧。

## 测试（pytest + `FakeWorkflowRunner`，沿用 `tests/` 现有结构）

- `max_runs=0`：多次 tick 工作流持续被调度（直到 `no_task`），`run_counts` 不增长、不参与筛选 —— 回归不变。
- `max_runs=2`：前两次 tick 调度该工作流并 `run_counts=2`，第三次 tick 该工作流被排除。
- 手动 `run_workflow_now`：不增加 `run_counts`，且在已达上限时仍可触发。
- 窗口重置：`_window_marker` 变化时 `run_counts` 被清空。
- 配置往返：`save_sync_config(workflow_max_runs=10)` 后 `get_sync_config()` 返回 `workflow_max_runs=10`；migration 对既有库补列默认 0。

## 未来（不在本次范围）

- per-workflow 独立上限（覆盖全局 N）。
- DB 持久化计数 / `workflow_runs.trigger` 列，以扛重启。
- 前端运行计数的实时图表。
