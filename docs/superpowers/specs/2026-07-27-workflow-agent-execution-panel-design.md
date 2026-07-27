# 工作流 Agent 输入与结果面板设计

## 目标

让工作流的批量执行详情、任务展开日志和运行进度页，都能随当前选中的 Agent 标签展示该
Agent 的输入提示词和执行结果；同时提升卡片视觉层级，并为两类内容提供统一详情查看。

## 范围

- Agent 运行详情页与工作流的三个入口共用同一输入/结果展示组件。
- 多 Agent 运行时，切换 `AgentRunTabs` 的标签后展示对应 `run_key` 的完整 `prompt` 和
  `result`。
- 输入提示词和结果均有边框更清晰的摘要卡片与“详情”按钮。
- 详情按内容类型展示：Markdown 正常渲染；JSON、HTML、Python、JavaScript 和纯文本使用
  既有只读代码查看器。
- 既有 Markdown 实体编码 URL 的安全问题已知且暂不在本需求中修复；本功能按用户明确要求
  继续使用现有 Markdown 渲染能力。

## 架构

### 运行详情数据

`AgentRun` 的列表接口只提供摘要，完整 `prompt/result` 由 `GET /agent-runs/{run_key}` 返回。
在现有工作流运行状态 composable 中增加“已选 Agent 完整详情”状态：在首次加载、切换标签
和轮询刷新时按当前 `progressAgentRunKey` 获取完整记录。`WorkflowView` 将该记录和加载状态
传给所有 `WorkflowRunDetailPanel` 实例。

任务展开日志没有 Agent 标签栏，仍对应其 `lease_run_id` 的主工作流 Agent。任务状态 composable
在首次展开时复用已取得的完整 Agent 记录并按 `lease_run_id` 缓存，直接把该记录传给同一面板。

这保持当前事件流与子 Agent 的请求逻辑不变，也让批量执行详情和运行进度页使用同一状态。
Agent 运行详情页已有完整 `detailRun`，直接传给同一展示组件。

### 可复用展示组件

新增 `AgentRunExecutionPanel`：

- 输入卡片显示 `prompt`；结果卡片显示 `result` 的格式化预览；缺少内容时不显示对应卡片。
- 卡片采用显式的 `border`、浅色背景、标题与内容区分，预览高度受限且可换行。
- 每个卡片的“详情”按钮向统一详情弹窗传入完整内容和标题。

将 `RunEventTimeline` 现有的内容类型识别、Markdown 渲染与 `PayloadCodeViewer` 弹窗能力提取
为项目级 `PayloadDetailDialog` 与类型识别 helper。时间线和 `AgentRunExecutionPanel` 都使用
该组件，避免 JSON/Markdown/代码语言判定分叉。

### 展示与交互

`WorkflowRunDetailPanel` 在 Agent 标签栏后、事件流前嵌入 `AgentRunExecutionPanel`；批量执行详情
和运行进度页因复用该面板自动获得一致行为。任务展开日志在其事件流标题后嵌入同一组件，并使用
该任务缓存的主 Agent 记录。`AgentRunsView` 删除私有的 Prompt/结果块，改用该组件。

详情弹窗标题区区分“输入提示词”和“执行结果”；Markdown 使用现有 `renderMarkdown`，其余格式
使用 `PayloadCodeViewer`。原有时间线的“查看”按钮继续使用相同弹窗，行为和语言选择不变。

## 错误与加载

- Agent 完整详情正在加载时，面板显示紧凑的加载状态，不清空上一 Agent 的卡片前先绑定
  `run_key`，防止异步返回覆盖新选择。
- 详情读取失败时保留事件流并在面板中显示可定位错误；不影响刷新、停止或子 Agent 详情。
- 没有 prompt 或未产生 result 的运行不显示空卡片。

## 验证

- 前端 helper 测试覆盖 JSON、Markdown、HTML、Python、JavaScript、纯文本的内容类型识别和
  格式化。
- 组件布局测试验证 `AgentRunExecutionPanel` 同时被 AgentRunsView 与 WorkflowRunDetailPanel
  使用，且存在两张清晰卡片及详情入口。
- 工作流状态测试验证选择 Agent 会请求对应完整 `AgentRun`，旧请求不会覆盖新选择。
- 运行 `cd frontend/capabilities && npm run check`；若既有无关的“复用节点/复用候选”断言仍失败，
  单独记录，不修改其范围外文案。
