# Agent Run 进展 SSE 改造计划

## 结论与范围

将正在运行的 Agent timeline 从 1.5 秒全量轮询改为可靠的 SSE 增量推送。保留现有 REST
详情与历史事件接口作为首次快照、断线恢复和兼容路径；不实施 `?after=cursor` 的增量轮询
过渡方案。

首期只负责 `agent_runs` 的事件和终态。工作流的排队、DAG 节点状态、批量任务、HTML
报告生成以及某个 workflow run 新增了另一个 agent run，均属于 workflow orchestration
状态，不能假定它们能从某一个 Agent 的事件流中推导。它们继续使用现有刷新机制，待有
明确体验诉求时再设计独立的 workflow SSE。

目标页面：

1. Agent 运行监控详情页：替换 `AgentRunsView.vue` 的 `detailEventsPoll`。
2. 工作流进度页中已选 Agent 的时间轴：替换 `useWorkflowRunProgress.ts` 对
   `getAgentRunEvents()` 的周期刷新。

非目标：WebSocket、重写持久化格式、给每种 Coding Agent adapter 单独实现流协议、以 SSE
替代停止 API、在首期改变 workflow scheduler 的状态发布模型。

## 现状与问题

运行期已经具备适合 SSE 的事实来源：`AgentService` 在每个语义事件产生后写入
`run/agent-runs/<run_key>/events.jsonl`，结束后再将完整事件写入 SQLite。

但 API 的 `GET /agent-runs/{run_key}/events` 每次都会读取整个 JSONL 并返回整个数组。前端
运行详情页每 1.5 秒调用它，随后还会请求一次 Agent run 详情；工作流进度轮询还会并行刷新
workflow run、agent run 列表、Agent 详情和选中 Agent 的事件。运行越久、事件越多，重复
读取和传输越多。

不应仅将这个全量读取接口包进一个“每秒检查文件”的 SSE generator：那会把客户端轮询移到
服务端，并没有消除重复 I/O。SSE 的发布源必须与现有的“事件写入”路径相连；JSONL/SQLite
继续提供可重放的持久来源。

## 已确认的技术约束

- 当前服务启动方式没有指定 uvicorn `--workers`，首期是单一服务进程。因此进程内发布器在
  当前部署模型下可用；以后横向扩容时必须替换为跨进程 broker 或按文件可靠 tail 的实现。
- 页面身份通过 `X-Agent-Bridge-User` 自定义 Header 传递。浏览器原生 `EventSource` 不能
  发送该 Header，因此前端不能直接使用它。
- 前端应以 `fetch` + `ReadableStream` 读取 `text/event-stream`，保留该 Header，并自己实现
  SSE 帧解析、`Last-Event-ID`、指数退避重连和 `AbortController` 取消。
- `sse-starlette` 目前只是间接依赖，不应作为新功能的隐式依赖。首期以 FastAPI/Starlette
  的 `StreamingResponse` 输出规范 SSE 文本，或若选择其功能则显式写入项目依赖。
- SSE 连接不替代授权：路由仍使用现有 `Depends(actor)`。不能把用户名直接拼入 query string
  来迁就原生 `EventSource`。

## 事件协议

新增端点：

`GET /agent-runs/{run_key}/events/stream`

请求头支持 `Last-Event-ID`。响应为 `Content-Type: text/event-stream`、`Cache-Control: no-cache`
和禁用代理缓冲的响应头。

服务端事件：

| SSE event | id | data | 客户端动作 |
| --- | --- | --- | --- |
| `agent_event` | 单 run 递增事件号 | 一个规范化 `WorkflowRunEvent` | 按 id 去重后追加至时间轴 |
| `run_terminal` | 最后事件号 | `{run_key, status, ok, error}` | 补拉一次详情、停止连接、触发一次已展开子 Agent 刷新 |
| `heartbeat` | 无 | 空或时间戳 | 仅保活 |
| `resync_required` | 无 | 原因 | 关闭流并调用现有 REST 快照后重新连接（如仍在运行） |

`agent_event` 的 id 必须同时写入 `events.jsonl`（建议字段名 `event_id`），而不是只存在于
内存队列。这样重连、页面刷新和服务重启后都能从持久事件中精确恢复；旧事件没有 id 时按
文件顺序投影 id，仅用于兼容历史详情。

连接建立顺序必须避免“快照与订阅之间丢事件”：先注册订阅并记录边界，再从持久日志重放
`Last-Event-ID` 之后的记录，最后消费队列中超过该边界的记录。重放和实时记录按 id 去重。
单订阅者队列必须有上限；溢出后发送 `resync_required` 而非无限积压内存。

## 后端实施步骤

### 1. 提取运行事件日志与发布器

新增 `agent_runtime` 内的共享组件（建议名 `live_events.py`），负责：

- 为 run 初始化和恢复下一个 `event_id`；
- 持久追加 JSONL、flush，并返回带 id 的记录；
- 维护按 `run_key` 划分的、有界订阅者队列；
- 订阅、取消订阅、心跳和 run terminal 通知；
- 从 JSONL 或完成后的 SQLite events 构建重放记录。

`AgentService._drain_agent()` 当前直接持有 `events.jsonl` 文件句柄，而
`_append_live_event()` 又单独打开文件写入终态事件。两条写入路径都改为调用该组件，确保
“已耐久写入”先于“向订阅者发布”。不得在 API 路由中自行读写或监视文件。

保持 `events` 内存列表和 `_finish_run()` 的 SQLite 回填语义不变，只是其中的记录会带
`event_id`。`_finish_run()` 执行完成后发布一次 `run_terminal`；即使持久回填失败，也要发布
可关闭流的终态通知并记录清晰中文错误日志。

### 2. 增加流式 API

在 `api/routes/agent_runs.py` 中新增异步路由。它先复用现有 run 存在性检查和 actor
依赖，再将共享组件的 async iterator 传给 `StreamingResponse`。

保留原 `GET /events`，不改变它的响应格式或历史调用方。流端点应只为 `running` run 保持
长连接；若连接时已完成，重放遗漏事件、发送 `run_terminal` 并结束，避免长期占用连接。

检测 `Request.is_disconnected()` 并在 generator 的 `finally` 中注销订阅。心跳建议 20 秒；
客户端重连延迟为 1、2、4、8 秒，上限 15 秒，且在页面隐藏或导航离开时主动中止。

### 3. 前端建立可复用的 SSE-over-fetch composable

新增小型 `useAgentRunEventStream`（或等价的 API helper），不把 SSE 解析和生命周期塞进
两个页面。职责为：

- 使用既有请求 Header 打开 fetch 流；
- 正确处理跨 chunk 的 SSE 帧、`event`、`id`、多行 `data`；
- 维护最后确认的 event id，重连时放到 `Last-Event-ID`；
- 暴露 `start(runKey, handlers)`、`stop()` 与连接/降级状态；
- 对网络失败自动重连；对 `401/403/404`、终态和显式 stop 不重连；
- 队列溢出或协议错误时通过现有 `getAgentRunEvents()` 做一次完整快照恢复。

保留 REST 初始加载：先读取 Agent run 详情和完整 events，渲染后打开流。由于后端按
`Last-Event-ID` 去重，初始快照与连接建立之间的事件不会遗漏或重复。

### 4. 接入两个消费者

`AgentRunsView.vue`：

- 删除 `detailEventsPoll`、`stopDetailEventsPolling()` 和 1.5 秒 interval；
- 详情加载完成且 run 为 `running` 时启动 stream，切换路由/返回列表/卸载时取消；
- 收到 `agent_event` 仅追加新事件，不再每次重建整个 timeline；
- 收到终态后仅补拉一次 `getAgentRun()`，并对已展开的 subagent detail 刷新一次。

`useWorkflowRunProgress.ts`：

- 将选中 Agent 的 `runEvents` 接到同一 stream；切换 selected agent run 时先停止旧流；
- `pollTestRun()` 不再调用 `loadProgressAgentEvents({quiet: true})`；
- workflow 的 run 状态、批量 task 状态、agent run 发现和报告产物仍走现有 workflow 轮询，
  因其并不由单个 agent event 推导；
- 选中 Agent 已结束时不建立连接，仍通过 REST 显示历史。

子 Agent transcript 详情的来源是 `messages.jsonl`，不是 timeline 的 `events.jsonl`。首期不
为它另开一条流：当关联 task 的事件到达时，仅刷新用户已展开且相关的详情；无法确认 task
关联时，保留当前一次性加载，避免每个展开面板创建额外网络连接。

### 5. 部署、文档和观测

- README/CLAUDE 增加接口、单进程限制、反向代理要求：关闭响应缓冲、读取超时必须大于最长
  Agent run、保留 keepalive；
- 生命周期日志以中文记录订阅建立/断开、run_key、最后事件号、重连/溢出原因和持续时间，
  不记录 prompt 或工具 payload；
- 可选轻量计数器：活跃订阅数、重放事件数、重连数、队列溢出数。先写日志，避免为首期引入
  指标系统依赖。

## 验收与测试

后端（`tests/test_agent_runs_api.py` 及新的运行期组件测试）：

1. 正在运行的 run 能在事件写入后立即收到一次 `agent_event`，事件先落盘。
2. `Last-Event-ID` 能只重放后续事件；首次连接、断线重连、服务重启后重放均不重复不丢失。
3. 连接时 run 已完成，能重放遗漏记录、收到 terminal 后关闭。
4. 慢订阅者队列溢出收到 `resync_required`，资源被释放。
5. 客户端断开和未知 run 不泄漏订阅；actor 依赖仍有效。
6. 原 `/events` 的 live JSONL 优先与 SQLite 终态回退回归测试继续通过。

前端：

1. 增加 SSE parser 单测：分块帧、多行 data、id、终态、异常重连和 Abort。
2. 在两个消费者的测试中断言运行中使用 stream、离开或切换时停止、终态只补拉一次详情。
3. 运行 `cd frontend/capabilities && npm run check`。

发布前增加一个带真实 uvicorn 的 `process` 标记测试（或最小手工 smoke）：curl/流式 fetch
连接后追加两条事件，验证逐条即时到达、代理环境不缓存。后端相关测试至少运行
`uv run pytest tests/test_agent_runs_api.py -q`。

## 分期与工作量

1. 事件日志/发布器、SSE endpoint 和后端测试：约 1–2 人日。
2. fetch 流客户端、两个页面接入和前端测试：约 1–2 人日。
3. 部署验证、断线与慢消费者压测、文档：约 0.5–1 人日。

总计约 2.5–5 人日。若将 workflow scheduler 也改为推送，则应另立设计：它需要工作流状态
版本、多个 agent run 的聚合顺序和报告产物通知，预计另加 2–4 人日，不纳入本计划。

## 决策点

- 首期假设部署维持单 Uvicorn worker。若发布前计划改为多 worker/多实例，必须暂停进程内
  hub 实现，先选 Redis pub/sub、消息队列或共享日志 tail 方案。
- SSE 只对可见的运行详情建立，一页一个选中 run 一条连接；列表页不订阅所有运行。
- 不采用 query string 传递用户身份，也不引入 WebSocket。
