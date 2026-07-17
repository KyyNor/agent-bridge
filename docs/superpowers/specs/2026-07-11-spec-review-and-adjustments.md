# Spec/Plan 审阅意见与需求调整

日期：2026-07-11
对应文档：`2026-07-11-lightweight-workflow-editor-design.md`（Spec）、`2026-07-11-lightweight-workflow-editor.md`（Plan）

## 一、总体结论

Spec 设计方向正确，Plan 任务拆分和 TDD 节奏合理。以下为代码库验证后发现的问题和新增需求，需在实现前对齐。

---

## 二、需修正的代码库假设

以下问题已在实际代码中验证，执行前必须修正：

**1. `AgentService.run()` 的 `backend_key` 参数（核对后无需调整）**

原审阅意见认为实际签名不含 `backend_key`。再次核对当前分支后确认，
`agent_runtime/service.py` 的 `AgentService.run()` 已提供
`backend_key: str | None = None`，因此 per-call backend 可直接沿用，
Spec 4.2 的 Claude/OpenCode/Codex 节点级选择保持不变。

**2. `definition_json` 投影默认值应为 `None`（中）**

`_row_payload`（`storage/repositories/workflows.py:46`，模块级自由函数）扩展时，
`("definition_json", "definition", None)` 必须用 `None` 而非 `{}`，
否则历史工作流的空定义会被误当空图执行。

**3. ScriptService 命名修正（中）**

全文 `script_service` → `scripts`；路径为 `system_config/scripts/service.py`。

**4. CodeMirror 断言修正（中）**

Plan 原断言 `"CodeMirror" not in view`——WorkflowView.vue 本来就没有 CodeMirror。
改为断言 `workflow_js` 文本框、`parseWorkflowDag`、`WorkflowDagGraph` 被移除。

**5. 补充 join + 条件组合测试（低）**

Plan 的 DAG 执行器任务补一个用例：上游 A 完成但条件不满足 + 上游 B 被跳过 → Join 节点应为 skipped。
同时在 Spec 5.2 显式声明 `condition: null` 等于"始终激活"。

---

## 三、设计层调整

**6. OutputHandler 复杂度（中）**

Output 节点承载固定 schema 校验、HTML 格式检查、产物保存、上游注入、warning 语义，
是 4 个 handler 里最重的。建议从 Plan 任务 4 拆出为独立子任务。

---

## 四、新增需求

### 需求 A：脚本输入 Schema 声明

**现状：** ScriptService 不做输入结构限定，脚本从输入中猜测字段。

**改为：** 每个托管脚本必须声明输入 schema（字段名、类型、必填/非必填）。
框架在调用前校验输入，不符合时明确报错并返回期望 schema。

**影响：**
- ScriptService 和脚本管理模型需增加 `input_schema` 字段。
- 工作流引用脚本时，编辑器可从 schema 推导出需要用户输入哪些内容（联动需求 B）。
- 手动输入型工作流的输入参数可从所引用脚本的 schema 汇总而来。

### 需求 B：脚本与技能从下拉框选择

Spec/Plan 示例中的硬编码名称（`code-review`、`collect_pages`）仅为示意。
实际交互为：编辑器从系统中已添加的脚本/技能列表填充下拉框，用户选择即可。
当前技能仅有 3 个（`design_script`、`design_workflow`、`design_html_report`），
脚本数量取决于系统配置——这些都是正常的数据来源，不影响设计。

### 需求 C：工作流执行日志聚合（记为待办）

**现状：** 日志以 agent 为单位，workflow 一次执行产生 N 条互不关联的 agent 记录。

**目标：** workflow 一次执行记录为一条顶层记录，内部关联各 agent；
普通 agent（UA、脚本设计等）仍直接记录一条。

**处理：** 当前阶段不实现，记录为待办。第一版维持现有 agent 日志 + `workflow_node_runs` 表的节点级关联。

---

## 五、Plan 修正检查清单

- [x] 核对 per-call backend：当前 `AgentService.run()` 已支持，无需改动运行时接口
- [x] Plan 任务 2：`_row_payload` 默认值改 `None`
- [x] Plan 处理器和装配任务：`script_service` → `scripts`
- [x] Plan 任务 11：CodeMirror 断言替换为 workflow_js/parseWorkflowDag/WorkflowDagGraph
- [x] Plan 任务 7：补 join+条件组合测试
- [x] Spec 5.2：声明 `condition: null` = 始终激活
- [x] Plan 任务 6：OutputHandler 拆为独立子任务
- [x] Plan 任务 4：增加脚本 `input_schema` 定义和校验（需求 A）
- [x] Plan 任务 11：确认脚本/技能下拉框从系统列表加载（需求 B）
- [x] Plan 新增待办项：工作流执行日志聚合（需求 C）

---

## 六、核对结果

- 第 1 条经核对不成立：`AgentService.run()` 已在 `agent_runtime/service.py` 提供 `backend_key: str | None = None`，节点级后端选择保持不变。
- 第 2 条已采用：历史 `definition_json` 保持 `NULL`，不会投影为空对象或空图。
- 第 3、4、5 条已同步修正 Plan，并补充 `condition: null` 的 Spec 语义。
- 第 6 条已采用：输出结果处理器拆为独立文件和独立实现任务。
- 需求 A、B 已加入 Spec 和 Plan：托管脚本输入 Schema、脚本/技能下拉选择和手动输入字段推导。
- 需求 C 仅记录到 `docs/TODO.md` 的实现任务中，本阶段不实现日志聚合。
