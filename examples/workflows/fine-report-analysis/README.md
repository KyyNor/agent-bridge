# 帆软报表三维分析

`workflow.json` 是从 `/Users/kyynor/Downloads/wf.js` 转换后的当前结构化 DAG 工作流导入包，可直接使用“导入工作流”功能导入。

## 运行分支

首次 `get_task` 没有任务时，节点以 `on_empty=continue` 输出 `task: null`，条件边进入 `seed_fine_report_tasks`，随后由第二个 `get_task` 重试领取。第二个节点保持默认的 `on_empty=terminate`，因此任务仍为空时运行以 `no_task` 结束。

拿到任务后，访问量查询、报表内容分析和数据血缘追踪并行执行，最后由输出节点生成 Markdown 报告。

## 外部资源

工作流假定系统中已经注册以下托管脚本：

- `seed_fine_report_tasks`：扫描或准备 `data_center` 下待分析的帆软报表任务。
- `query_visit_stats`：根据 `cpt_file_path` 和 `report_id` 查询访问量数据。

内容分析和血缘追踪使用 `report-plane` 能力平面的 MCP；对应的 `fine_search`、`ds_search` 等能力按当前环境配置。

## 与旧 JS 的差异

- 旧 JS 中的任务领取、补任务和重试被显式建模为节点和条件边。
- 三个分析分支由当前 DAG 执行器并行调度，结果通过节点 `output_json` 传给综合输出节点。
- 旧版 `result.json` 由当前运行实例的节点输出和产物记录替代；Markdown 路径使用 `reports/<report_id>.md` 模板。
- 原脚本中的运行时临时目录、手写日志和路径清理不再由工作流定义承担，由脚本运行器、运行记录和产物服务负责。
