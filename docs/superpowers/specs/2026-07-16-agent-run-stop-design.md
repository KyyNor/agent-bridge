# 三类 Agent 运行立即停止设计

## 状态

- 日期：2026-07-16
- 状态：待审阅
- 范围：工作流单次运行、工作流批量运行、Scripts 设计 Agent、Workflow 设计 Agent
- 设计结论：引入统一的 Agent 运行控制中心；工作流运行和设计 Agent 共用取消机制，按各自的生命周期完成收尾

## 背景

当前页面已经能够显示单次工作流运行、批量运行和设计 Agent 的过程，但“停止”只停留在前端队列层面：批量队列取消后不会再启动下一个任务，已经启动的 Agent 仍可能继续调用 Claude；单次工作流和两个设计 Agent 没有立即停止入口。

三类运行的现状不同：

1. 工作流运行由后端 `WorkflowScheduler` 在线程中启动，可能包含主 Agent、子 Agent 和总结报告 Agent；工作流任务由后端租约管理。
2. 批量工作流目前是 `WorkflowView` 内的页面级串行队列，没有独立的后端 batch 生命周期。停止批量必须同时停止当前后端 Run 和页面队列。
3. Scripts 设计 Agent 与 Workflow 设计 Agent 使用同步 HTTP 请求，前端在请求返回前拿不到服务端生成的 `run_key`，因此需要让前端在请求开始前提供一个可取消的 key。

## 目标

1. 单次工作流运行和批量工作流的当前 Run 都提供“立即停止”。
2. Scripts 设计 Agent、Workflow 设计 Agent 都提供“立即停止”。
3. 停止请求可重复调用，能够处理“停止按钮与运行完成同时发生”的竞态。
4. 停止后的状态清晰且可重试：
   - Workflow Run 标记为 `stopped`；
   - 当前被租约占用的 Workflow Task 恢复为 `pending`；
   - `attempt_count` 保留，不回退；
   - 批量队列不再启动后续任务，但剩余选中项保留，用户可以再次运行；
   - 设计 Agent 不采纳本次草稿、不写入表单或保存结果，用户可以重新生成。
5. 尽可能复用现有 Agent run、事件流和共享 Run 展示，不引入第二套取消协议。

## 非目标

- 不把页面级批量队列改造成持久化的后端批次任务。
- 不在本次改动中支持批量任务并行执行。
- 不在前端直接按 PID 杀 Claude 进程。
- 不改变刷新页面、关闭页面或服务重启后的批量恢复策略；批量队列仍是页面级生命周期。服务重启后遗留的 `running` Run 由独立的运行恢复/清理工作处理。
- 不提供“停止后回滚已经完成的任务”；只释放仍由该 Run 租约占用的任务。

## 已确认的用户语义

### Workflow 单次运行

点击“立即停止”后：

1. 当前 Agent 进入取消流程，不再继续产生新的 Agent 工作。
2. Workflow Run 最终状态为 `stopped`。
3. 当前正在执行的 Task 回到 `pending`，保留已有尝试次数。
4. 页面停留在当前 Run 详情，显示“已停止”，允许用户再次执行。

“立即”表示立即发起取消并关闭底层 Agent 查询，不承诺在固定毫秒数内完成进程清理。取消清理期间 UI 显示“停止中…”，后端完成收尾后才显示“已停止”。

### Workflow 批量运行

点击“停止批量”后：

1. 立即取消页面级队列，不再启动下一个任务。
2. 如果当前任务已经取得 `run_id`，同时请求停止该 Workflow Run，并等待它进入终态。
3. 当前任务记为 `stopped`，后续未启动任务不记为失败，也不取消选择状态。
4. 批次汇总保留“已停止”结果和剩余数量，用户可继续使用当前选择重新运行。

如果点击停止时，当前任务的 `executeWorkflowTask` 请求还没有返回 `run_id`，先取消队列；请求稍后返回时必须再次检查取消 token，并立即对新返回的 `run_id` 发起停止请求，避免出现“界面已停止但后台刚启动”的竞态。

### Scripts / Workflow 设计 Agent

点击“立即停止”后：

1. 取消当前设计 Agent 查询。
2. 当前表单、编辑器和已有内容保持不变。
3. 本次生成结果不显示为可采纳草稿，不触发保存。
4. 设计面板显示“已停止”，生成按钮恢复可用，用户可以重试。

## 方案比较

### 方案 A：统一运行控制中心（采用）

在 Agent runtime 增加进程内的运行控制注册表。每个活动 Agent run 注册一个线程安全的控制句柄，句柄包含取消信号、运行状态和底层查询任务引用；Workflow Scheduler 额外维护 Workflow Run 到其 Agent run 的映射。

停止请求只针对控制中心发信号，不让 UI 直接接触线程、异步任务或 Claude 子进程。AgentService 负责把取消信号转换为 SDK 查询取消和标准化的 `stopped` 结果，Workflow Scheduler 负责按 Workflow 语义释放任务和完成 Run。

优点：

- 三类 Agent 共用同一条底层取消路径；
- 工作流可停止主 Agent、子 Agent 和总结 Agent；
- 前端只需要记住 `run_key` 或 `run_id`；
- 能在服务端集中处理幂等、竞态和资源清理。

代价：

- 需要改造 AgentService 的一次性查询生命周期；
- 控制注册表是进程内状态，服务重启恢复不在本次范围。

### 方案 B：把设计 Agent 改造成异步 Job API

设计请求先创建 Job，前端轮询 Job 状态，停止时取消 Job。该模型更适合长任务和断线恢复，但会改动两个设计接口、前端交互和持久化结构，明显超出本次“增加立即停止”的范围。

### 方案 C：由 API 直接查找并杀 Claude 子进程

API 根据 run 目录或进程信息查找子进程并终止。它无法可靠覆盖 SDK 内部进程、子 Agent 和总结 Agent，也容易误杀同一服务中的其他运行，因此不采用。

## 总体架构

```text
WorkflowView / ScriptsView / Workflow designer
                │
                ├── POST /workflow-runs/{run_id}/stop
                └── POST /agent-runs/{run_key}/stop
                                │
                        RunControlRegistry
                                │
                    cancel signal + active handles
                                │
                         AgentService.run
                                │
                  cancel async query / close SDK transport
                                │
                AgentRun = stopped / WorkflowRun = stopped
                                │
               release leased tasks back to pending
```

### 运行控制对象

建议在 `src/agent_bridge/agent_runtime/` 增加一个进程内控制模块，名称可在实施阶段确定，职责如下：

- `register(run_key, metadata)`：在 Agent 查询开始前注册控制句柄；
- `request_stop(run_key)`：设置取消信号，重复调用安全；
- `is_stop_requested(run_key)`：供运行开始前和查询循环检查；
- `finish(run_key)`：移除活动句柄，保留短时终态查询所需的结果；
- `register_workflow(workflow_run_id)` / `attach_agent(workflow_run_id, run_key)` / `request_workflow_stop(workflow_run_id)`：管理一个 Workflow Run 下的多个 Agent。

控制对象必须使用线程安全的数据结构。Workflow Scheduler 在线程中运行，API 请求在异步事件循环中运行，不能依赖单一事件循环内的 `asyncio.Lock` 来保护共享状态。

### AgentService 的取消实现

`AgentService.run` 接受以下新增能力：

- 可选的调用方 `run_key`；不传时继续按现有逻辑生成 key；
- 可选的父级 `workflow_run_id`；用于把 Agent run 关联到 Workflow Run 控制器；
- 运行前注册控制句柄，创建 `agent_runs` 记录后再执行查询；
- 查询期间监听线程安全取消信号。

Agent SDK 当前公开的一次性 `query()` 没有独立的 interrupt 参数，但取消内部异步查询任务会触发 SDK 的 generator `finally`，随后关闭传输层；传输层先优雅关闭，超时后 terminate/kill。实现应将这一条路径封装在 AgentService 内，不把 SDK 细节泄露给路由或页面。

取消过程要求：

1. 运行开始前发现 stop requested：不启动 Claude 查询，直接写入 `stopped`。
2. 运行中发现 stop requested：取消当前查询 task，等待 SDK transport 清理完成。
3. `asyncio.CancelledError` 单独映射为 stopped；普通异常仍映射为 failed，不能把用户停止误报为失败。
4. 无论成功、失败、停止或超时，都执行一次 `_finish_run` 和控制句柄清理。
5. 停止结果中保留 `run_key`、`run_dir`、持续时间和已有事件，错误信息使用稳定的用户可读文本，例如“运行已由用户停止”。

为处理设计请求与停止请求的先后竞态，控制中心保留短时的“stop requested” tombstone：如果停止请求先于 Agent 注册到达，后续使用相同 `run_key` 注册时立即被标记为停止；tombstone 应有有限 TTL，避免无效 key 永久占用内存。

## Workflow Run 生命周期

### Scheduler 控制

`WorkflowScheduler.run_workflow_now` 创建 `workflow_runs` 记录后，必须同步把 `workflow_run_id` 注册到控制中心，再启动后台线程。这样 API 返回 `run_id` 后，停止请求不会落在一个尚未注册的窗口里。

Workflow runner 接受 Workflow Run 的取消上下文，并将其传递给每个 AgentService 调用。一个 Workflow Run 可能顺序或嵌套产生多个 Agent run；停止 Workflow Run 时，控制中心对当前关联的全部活动 Agent run 发出取消信号。后续尚未开始的 Agent 不应再启动。

### 正常终态与停止终态

使用现有 Workflow Run 状态：

- 正常完成：`completed`；
- 无可执行任务：`no_task`；
- 业务错误或运行错误：`failed`；
- 用户立即停止：`stopped`。

当 Workflow Run 已经是终态时，停止请求不得覆盖原状态。例如 Run 在停止请求到达前已经 `completed`，接口返回 `completed`，前端显示原有结果。

停止收尾必须由 Scheduler 统一完成，并保证只执行一次：

1. 等待当前 AgentService 返回 stopped 或确认运行尚未启动；
2. 将 Workflow Run 写为 `stopped`；
3. 释放 `lease_run_id` 等于该 Run 的所有运行中任务；
4. 释放后的任务状态设为 `pending`，清空租约字段，保留 `attempt_count`；
5. 已经 `completed` 的任务保持 completed；已经失败的任务保持 failed；不把历史结果重置掉；
6. 清理 Workflow Run 控制句柄。

如果停止请求在当前 Agent 尚未完成注册时到达，Scheduler 仍需在执行入口检查 stop requested，并在不启动下一阶段 Agent 的情况下完成上述收尾。

### 停止接口

新增：

```text
POST /api/workflow-runs/{run_id}/stop
POST /api/agent-runs/{run_key}/stop
```

两者都返回统一的状态形状，具体 Pydantic 类型在实施阶段复用现有 response model 风格：

```json
{
  "run_key": "...",
  "run_id": "...",
  "status": "stopping"
}
```

`status` 允许 `stopping`、`running`、`completed`、`failed`、`stopped`、`no_task`。活动运行首次收到停止请求时返回 HTTP 202 与 `stopping`；已经完成清理时可以直接返回 HTTP 200 与 `stopped`；已存在的其他终态按 HTTP 200 原样返回。

接口幂等规则：

- 对同一个活动 run 重复停止，只重复返回 `stopping` 或最终 `stopped`，不创建第二个取消流程；
- 对已完成/失败的 run，不报错、不改状态；
- 数据库显示 `running` 但当前进程没有控制句柄时，不在本次接口中武断标记 stopped，返回明确的不可控制错误；服务重启后的孤儿 Run 由独立恢复机制处理；
- 无效的 Workflow Run ID 返回 404；无效且没有待注册 tombstone 的 Agent run key 返回 404。

## 批量队列设计

现有 `runWorkflowTaskQueue` 继续负责页面级串行调度，但取消接口从“只阻止下一个任务”扩展为“停止当前 Run + 阻止下一个任务”。

建议增加一个可选的当前运行取消回调，或等价的 queue context：

- `cancel()`：使队列 token 失效；
- `cancelCurrentRun(run_id)`：由页面调用 `stopWorkflowRun`；
- `remaining`：保留尚未启动的选中任务。

停止流程：

1. 页面把 `batchToken` 标记为 cancelled，按钮立即进入停止中；
2. 若 `batchCurrentRunId` 存在，调用 Workflow Run stop API；
3. 若 API 返回 `stopping`，继续用现有 `getWorkflowRun` 轮询到 `stopped`，不运行下一项；
4. 若停止时尚无 `run_id`，队列直接结束当前等待；`execute` 请求返回后检查 token，发现已取消则立即停止返回的 Run；
5. 队列汇总把当前任务记为 stopped，把未启动项保留为 selected/pending，不调用 execute；
6. 停止请求失败时不假装成功：保持当前 Run 可见，显示错误并禁止队列继续，用户可以重试停止。

需要避免的竞态：

```text
点击停止 ──取消 token──> execute 请求尚未返回
                              │
                              └──返回 run_id──> 检查 token ──> 立即 stop(run_id)
```

单次“执行任务”仍然只对应一个 Workflow Run，复用同一个 stop API；它不经过批量队列。

## 设计 Agent 设计

### 请求 key

扩展两个设计请求 payload，增加可选的 `run_key`：

```json
{
  "prompt": "...",
  "run_key": "client-generated-key"
}
```

服务端在调用 `AgentService.run` 时透传该 key。未提供时保留原有自动生成行为，兼容其他调用方。

前端在发起生成请求前生成 key，并把它保存在当前设计会话中。停止按钮使用该 key 调用 `POST /agent-runs/{run_key}/stop`，不需要等待设计接口返回 `run_key`。

### ScriptsView 与 WorkflowView

两个设计面板都增加独立的 `designRunKey` 与 `designStopping` 状态：

- 点击生成：清空上一次设计响应，生成新的 key，设置 designing；
- 生成过程中：显示“立即停止”，关闭面板和接受草稿按钮保持禁用；
- 点击停止：调用 agent-run stop API，按钮显示“停止中…”；
- 设计接口返回后：只有当返回 key 仍属于当前设计会话且未被停止时，才设置 `designResponse`；
- stopped 结果：显示停止提示，表单/编辑器不变，生成按钮恢复可用；
- completed 结果：沿用现有“采纳设计”流程；
- failed 结果：沿用现有错误展示。

这里的“立即停止”只停止当前设计 Agent，不影响页面中其他工作流运行或其他 Agent tab。

## 前端入口与共享组件

### Workflow Run 详情

现有 `AgentRunTabs` 是工作流任务页和进度页共享的 Agent 切换入口，应增加可选的停止控制：

- `canStop`：当前 Workflow Run 为 running，或批次当前 Run 可停止；
- `stopping`：停止请求已发出但 Run 尚未进入终态；
- `stop` 事件：由父页面决定调用哪个 API；
- 停止按钮放在 Run 控制区，不随事件列表滚动消失。

工作流进度页调用单 Run stop API；任务页批量区域显示“停止批量”，同时触发页面队列取消和当前 Run stop。若任务页只是查看某个历史 Run，则只显示该 Run 的停止按钮，终态时隐藏。

### 状态展示

统一补齐 `stopped` 的 label 和 badge：

- running：执行中；
- stopping：停止中；
- stopped：已停止；
- completed：成功；
- failed：失败。

`stopped` 使用中性或警示色，不复用失败文案；事件时间线保留停止前已经收到的内容，并在末尾显示停止事件。

## 数据与一致性

### Agent run

Agent run 的持久化状态增加明确的 `stopped` 终态，且 `_finish_run` 必须是幂等的。停止只允许从 `running` 转移到 `stopped`；如果另一个线程已经写入 completed/failed，停止流程读取现有终态并退出。

### Workflow task

释放任务时按 `lease_run_id` 精确筛选，不按 workflow key 全量重置，避免影响同一 Workflow 的其他并发运行。只释放当前仍为 running 且 lease 属于被停止 Run 的任务：

```text
running + lease_run_id = stopped_run_id
    └──> pending + lease cleared + attempt_count unchanged
```

这条释放逻辑应与失败时的 abandon 逻辑分开，不能复用会把任务置为 `abandoned` 的路径。

### 事务与顺序

停止请求发出后，先发取消信号，再等待 Agent 结束，最后在同一个收尾路径中写入 Workflow Run 终态和释放任务。不能在 Agent 仍可能写入结果时提前把任务改回 pending，否则旧 Agent 可能覆盖新一轮尝试。

## 错误处理与可观察性

- stop API 网络超时：前端进入停止中并继续查询现有 Run；用户可以再次点击停止，不重复创建控制流程。
- Agent SDK 关闭失败：记录原始异常，尽最大努力清理子进程；Run 最终仍应标记 stopped，并在事件/日志中记录 cleanup warning。
- 停止与正常完成同时发生：以先成功提交的终态为准，接口返回实际状态。
- 控制句柄泄漏：每个终态路径都必须调用 finish；可增加 debug 日志记录注册、停止、清理和句柄数量。
- 页面卸载：单次 Workflow Run 仍由后端继续运行，用户可从 Run 详情重新进入并停止；页面级批量队列仍随页面生命周期结束。

## 实施边界

预计修改边界如下，具体文件拆分在设计批准后写入实施计划：

- 后端 Agent runtime：控制中心、AgentRunResult/状态映射、SDK 查询取消和可选 run key；
- 后端 Workflow：Scheduler、runner、任务租约释放、Workflow stop API；
- 后端 Agent API：Agent stop API、两个设计请求透传 run key；
- 前端 API/types：两个 stop API 和 stopped 状态；
- 前端 Workflow：单次、批量 stop 控制，竞态保护，详情入口；
- 前端 Scripts/Workflow designer：设计会话 key、停止按钮和结果保护；
- 测试：runtime、scheduler/repository、API、队列和设计面板相关测试。

不应修改 `codex/lightweight-workflow-editor-design` worktree；本功能只在当前 `main` worktree 继续。

## 测试与验收

### 后端单元/API

1. AgentService 在运行前收到停止请求时不启动 Claude 查询，并持久化 stopped。
2. AgentService 在查询中收到停止请求时关闭查询任务/transport，结果为 stopped 而非 failed。
3. stop API 对 running、stopping、stopped、completed、failed 和不存在的 run 分别返回预期状态，并验证幂等性。
4. Workflow Scheduler 可以停止主 Agent；包含总结 Agent 的 Workflow 也能停止当前关联 Agent。
5. Workflow stop 只释放该 `lease_run_id` 的 running task，状态恢复 pending，attempt_count 不变。
6. 运行完成与 stop 同时发生时，Workflow Run、Agent run 和 task 不出现互相矛盾的终态。
7. 两个设计 API 接收并使用调用方 run key；停止后不产生可采纳结果。

### 前端单元/组件

1. 批量队列停止后不执行后续任务，remaining 与选中状态保留。
2. execute 请求在取消后才返回 run_id 时，会自动 stop 该 run，不会漏掉后台运行。
3. 单次进度页和批量当前 Run 都能展示 stopped，并在停止中禁用重复操作。
4. ScriptsView 和 WorkflowView 设计面板停止后保持原表单内容，不触发 accept/save，且可重新生成。
5. 已完成或失败的 Run 不显示可用的立即停止按钮。

### 人工验收

- 单个工作流启动后点击立即停止：看到停止中，最终显示已停止；当前任务重新可运行。
- 选中至少三个任务批量运行，在第一个任务运行中点击停止批量：第一个 Run 停止，第二、第三项不启动且仍保留选择。
- 在批量 execute 请求尚未返回时点击停止：稍后返回的 Run 也被停止。
- Scripts 设计和 Workflow 设计各运行一次并在生成过程中停止：编辑区不变，重新生成按钮可用。
- 刷新单次 Run 详情页后仍可查看并停止后端正在运行的 Run；刷新批量页面的行为保持原有页面级队列语义。

## 实施顺序

设计批准后按以下顺序拆分实施：

1. 先补 Agent runtime 控制中心和 stopped 结果路径，增加后端单元测试。
2. 接入 Workflow Scheduler、任务释放和 Workflow stop API，增加并发竞态测试。
3. 接入 Agent stop API 与两个设计请求的 run key。
4. 接入前端单次/批量 Workflow 停止入口和队列竞态保护。
5. 接入 Scripts/Workflow 设计面板停止入口。
6. 执行分层测试、类型检查和人工验收，确认不触碰指定的 lightweight workflow editor worktree。
