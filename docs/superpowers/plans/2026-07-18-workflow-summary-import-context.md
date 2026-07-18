# 工作流总结导入与任务上下文 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工作流节点获得明确任务上下文，并让总结类工作流导入时自动规范化末尾两个系统输出节点，同时适配现有 Schema 展示。

**Architecture:** 在 Python 工作流处理器中集中生成任务上下文说明块，在导入服务中于校验前规范化 summary 图，避免普通输出节点绕过系统约束。前端复用现有输出模式和 Schema 展示区域，通过已选脚本及固定契约补齐展示和引用数据。

**Tech Stack:** Python、Pydantic、pytest、Vue 3、TypeScript、Node test。

---

### Task 1: 任务上下文注入

**Files:**
- Modify: `src/agent_bridge/automation/workflows/handlers.py`
- Modify: `src/agent_bridge/automation/workflows/output_handler.py`
- Test: `tests/test_workflow_executor.py`
- Test: `tests/test_workflow_output_handler.py`

- [ ] **Step 1: Write failing tests**

增加两个断言：Agent 提示词包含 `task_key`、`task_version`、`cpt_file_path`；Markdown Output 提示词同时包含任务说明和 `[上游节点输出]`。

- [ ] **Step 2: Run tests and verify failure**

运行 `pytest tests/test_workflow_executor.py tests/test_workflow_output_handler.py -q`，确认新增断言因任务说明尚未注入而失败。

- [ ] **Step 3: Implement minimal context block**

在 `handlers.py` 增加 `build_task_context_block(task)`，由 `build_agent_prompt` 在任务指令前追加格式化后的任务上下文；`task is None` 时不追加。所有 Agent 和 Output 调用继续复用 `build_agent_prompt`。

- [ ] **Step 4: Run tests**

运行同一组 pytest，确认上下文注入及原有上游输出行为均通过。

### Task 2: 总结类导入规范化

**Files:**
- Modify: `src/agent_bridge/automation/workflows/definition.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Test: `tests/test_workflow_import.py`
- Test: `tests/test_workflow_definition.py`

- [ ] **Step 1: Write failing tests**

覆盖三种输入：一个普通 Markdown 输出、无输出节点、已有合法系统输出对。断言规范化后的最后两个节点分别具有 `summary_markdown`、`summary_html`，且存在唯一系统边。

- [ ] **Step 2: Run tests and verify failure**

运行 `pytest tests/test_workflow_import.py tests/test_workflow_definition.py -q`，确认当前导入会因 summary 输出数量或顺序校验失败。

- [ ] **Step 3: Implement normalization**

在 definition 模块增加纯函数，移除旧总结系统节点及相关边，识别唯一普通 Markdown/HTML 输出并转换；没有时使用默认系统节点；将业务末端节点连接到 Markdown，最后补充 Markdown → HTML 系统边。导入服务在 `require_valid` 前调用该函数，并把规范化后的 definition 存入预览快照。

- [ ] **Step 4: Run tests**

运行导入和定义测试，确认规范化结果通过现有 summary 校验且普通 operation 图行为不变。

### Task 3: 适配现有 Schema 展示与示例工作流

**Files:**
- Modify: `frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`
- Modify: `frontend/capabilities/src/lib/workflowReferences.ts`
- Modify: `frontend/capabilities/tests/workflowConfigDrawer.test.ts`
- Modify: `frontend/capabilities/tests/workflowReferences.test.ts`
- Modify: `examples/workflows/fine-report-analysis/workflow.json`

- [ ] **Step 1: Write failing tests**

增加源码级断言：Script 面板引用选中脚本的 `output_schema`，get_task 和 Output 有固定契约，示例工作流类型为 `summary` 并包含两个系统输出节点。

- [ ] **Step 2: Run tests and verify failure**

运行 `npm test -- --runInBand`（前端工作区实际脚本）或对应 workflow 测试文件，确认当前 Script 和固定节点没有输出契约展示/示例仍是 operation。

- [ ] **Step 3: Implement adaptation**

复用现有面板中的 Schema 展示样式：Agent 保持可编辑 JSON Schema；Script 显示托管脚本输出 Schema；固定节点显示只读固定契约。引用派生逻辑与展示使用同一契约。把示例工作流的末端改成 summary Markdown/HTML 系统节点。

- [ ] **Step 4: Run frontend checks**

运行前端 workflow 测试和 typecheck，确认类型、Schema 展示和引用选择器通过。

### Task 4: 集成验证与提交

**Files:**
- No new production files.

- [ ] **Step 1: Run focused backend and frontend tests**

运行任务上下文、导入、定义、输出处理相关 pytest，以及前端 workflow 测试和 typecheck。

- [ ] **Step 2: Perform one integrated review**

检查导入预览使用的是规范化后的 definition，确认没有把既有 `frontend/capabilities/tsconfig.tsbuildinfo` 纳入提交。

- [ ] **Step 3: Commit on main**

使用提交信息 `fix: normalize summary workflow imports and inject task context`。
