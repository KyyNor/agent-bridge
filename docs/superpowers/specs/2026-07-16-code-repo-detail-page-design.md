# 代码仓库详情二级页面设计

> 日期：2026-07-16  
> 范围：`frontend/capabilities` 代码知识页面  
> 状态：方案已确认

## 目标

将代码仓库详情从 `CodeRepoView.vue` 内的 Dialog 迁移为可直达、可后退的 hash 二级页面，同时保持现有详情能力和后端 API 契约不变。

## 已确认的交互

- 列表路由为 `#code-repos`。
- 详情路由为 `#code-repos/<repo_key>`。
- 列表点击“详情”进入详情路由，不再打开 Dialog。
- 详情页顶部提供返回按钮；返回后回到 `#code-repos`。
- 浏览器前进、后退和直接访问详情 hash 都能正确加载页面。
- 详情页无效或已删除的仓库显示错误状态，并提供返回列表入口。
- 详情页保留现有四个 Tab：概览、查询、探索、理解。
- Understand Dashboard 保留内嵌、启动状态、主题参数和最大化/还原行为；最大化仍使用全屏 Teleport。
- 添加/编辑代码仓库、能力平面分配继续使用现有小弹窗，它们是短表单或选择操作，不属于详情页迁移范围。

## 组件与路由边界

### `CodeRepoView.vue`

保留列表、筛选分页、添加/编辑弹窗、同步、删除和能力平面分配。组件接收 `routeKey`：

- `routeKey` 为空时渲染仓库列表。
- `routeKey` 有值时渲染 `CodeRepoDetailView`。
- 列表刷新后如果当前详情仓库已不存在，详情页显示可理解的“仓库不存在”状态。
- 路由切换由父组件统一写入 `window.location.hash`，详情组件通过事件通知返回，不直接控制列表状态。

### `CodeRepoDetailView.vue`

新建独立详情组件，接收当前仓库 key 和仓库摘要，独立管理详情加载与 Tab 内状态。组件负责：

- 加载 CodeGraph 状态、仓库概览、查询结果、Explore 结果和 Understand 数据。
- 保留当前详情 Dialog 中的请求、错误、加载、轮询和 Dashboard 生命周期。
- 发出 `back` 事件，父组件收到后切回 `#code-repos`。
- 对仓库不存在、详情加载失败和单个 Tab 请求失败分别提供可见反馈，不让异常导致空白页面。

### `App.vue` 与 `lib/navigation.ts`

- `App.vue` 将复合 hash 的 `subRoute` 传给 `CodeRepoView`。
- `shouldShowPageHeader` 将 `code-repos` 加入二级路由集合，详情页不重复显示顶层导航标题。

## 页面布局

详情页使用与知识库、Agent 运行详情一致的页面结构：

1. 顶部返回区：返回按钮、仓库名称、`repo_key` 和 Git URL/分支等摘要信息。
2. 主体内容区：状态提示、统计卡片、四个 Tab 和 Tab 内容。
3. Understand Tab 中 Dashboard 使用页面内可滚动布局；最大化时覆盖整个 viewport。

详情页不再依赖 Dialog 的 `max-h-[85vh]`。主体使用 `min-h-0` 与 `overflow-y-auto`，避免查询结果、Explore Markdown 或 Understand 摘要被弹窗裁切。

## 数据流与行为保持

- 不新增后端接口，不改变 `api.getRepoOverview`、`api.getCodeGraphStatus`、`api.queryRepo`、`api.exploreRepo`、Understand 和 Dashboard 相关 API 的参数及返回处理。
- 从列表进入详情时清理上一仓库的查询词、Explore 结果、Understand 状态和 Dashboard 定时器。
- 切换仓库或离开详情时停止 Understand Dashboard 触达轮询，避免后台定时器泄漏。
- 保存/修改仓库仍发生在原有弹窗中；详情页迁移只改变“查看详情”的承载方式。
- 列表“详情”按钮改为写入 `code-repos/<repo_key>`；详情返回改为写入 `code-repos`。

## 错误与边界

- 详情 key 为空：展示列表。
- 详情 key 不存在：展示错误提示和返回列表链接，不调用需要有效仓库的 Tab API。
- 概览或 CodeGraph 状态加载失败：显示详情页错误提示；其他 Tab 不因单个请求失败而崩溃。
- Understand Dashboard 启动或轮询失败：保留 Understand Tab 的可用内容并显示错误，不阻塞返回列表。
- 页面卸载、切换路由和关闭 Dashboard 时清理 `uaTouchTimer`。

## 测试与验收

新增或调整以下测试：

- `navigation.test.ts`：断言 `code-repos/<key>` 隐藏顶层页面标题，而 `code-repos` 保持标题。
- `codeRepoDetailLayout.test.ts`：断言 App 路由接线、列表详情入口改为 hash、详情组件存在返回事件和四个 Tab/详情布局结构，且原详情 Dialog 已移除。
- 运行现有前端测试，重点覆盖代码仓库 API 状态与结构测试。
- 运行 `npm run typecheck` 和 `npm run build`。

## 非目标

- 不迁移代码仓库添加/编辑弹窗。
- 不迁移能力平面分配弹窗。
- 不重做代码知识列表、筛选、分页或后端 API。
- 不把详情 Tab 再拆成多个独立路由；本次只保证仓库详情可通过 `repo_key` 深链访问。
