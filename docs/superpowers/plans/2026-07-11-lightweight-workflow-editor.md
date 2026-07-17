# 轻量工作流编辑器与执行器实现计划

> **给执行本计划的 Agent：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，严格按任务顺序执行。每个步骤使用复选框跟踪；每项任务完成测试后单独提交。

**目标：** 用结构化 JSON 工作流定义、Vue Flow 编辑器和轻量 Python DAG 执行器，替换当前 Claude Code 专用的 `workflow.js` 执行链路。

**架构：** 前端直接编辑并保存结构化 DAG；后端用 Pydantic 校验定义，用 `asyncio` 调度就绪节点。节点处理器复用现有任务租约、`AgentService`、`ScriptService`、技能管理和产物服务；现有 `WorkflowScheduler` 只负责触发，不再启动 Claude 工作流 Agent。

**技术栈：** Python 3.11、Pydantic、FastAPI、SQLite、asyncio、Vue 3、TypeScript、Vue Flow、Dagre、Node 内置测试运行器、pytest。

## 全局约束

- 第一版只有 `get_task`、`agent`、`script`、`output` 四类节点。
- 支持无环图、并行和单条件边；不支持循环、审批、重试、失败继续、条件组或自由表达式。
- 工作流绑定一个 Profile；Agent 节点只能控制是否启用该 Profile 的 MCP。
- 技能正文按节点配置顺序拼接在用户提示词前，不调用后端原生 Skills。
- 脚本节点只能引用脚本管理中已启用的托管 Python 脚本，不允许内联代码。
- 普通节点快速失败；HTML 输出失败是唯一警告例外。
- 总结型工作流必须且只能包含一组受保护的 Markdown 输出和 HTML 输出节点。
- 保存覆盖当前定义；不实现草稿、发布和版本历史，但每次运行保存定义快照。
- 继续使用现有全局调度窗口、运行上限和手动运行入口，不增加单工作流 cron。
- `workflow_js` 仅用于兼容历史数据；新执行链路不得读取或执行它。

---

## 文件职责与依赖关系

**后端新文件**

- `src/agent_bridge/automation/workflows/definition.py`：Pydantic 图定义、节点配置、条件和默认总结图。
- `src/agent_bridge/automation/workflows/validation.py`：纯图校验、祖先引用校验、资源存在性校验和可定位错误。
- `src/agent_bridge/automation/workflows/references.py`：路径读取、模板渲染和条件求值。
- `src/agent_bridge/automation/workflows/handlers.py`：获取任务、Agent、托管脚本处理器及统一执行上下文/结果。
- `src/agent_bridge/automation/workflows/output_handler.py`：Markdown/HTML 输出、产物保存和 HTML 警告语义。
- `src/agent_bridge/automation/workflows/executor.py`：轻量 DAG 调度、节点状态持久化、快速失败和运行输出汇总。

**后端修改文件**

- `src/agent_bridge/storage/schema.py`：定义快照、输入输出和节点运行表。
- `src/agent_bridge/storage/sqlite.py`：向既有数据库补列和建表。
- `src/agent_bridge/storage/repositories/workflows.py`：工作流定义、运行、节点状态和任务失败存储接口。
- `src/agent_bridge/automation/workflows/service.py`：保存定义时的资源校验、节点详情查询和产物保存入口。
- `src/agent_bridge/automation/workflows/scheduler.py`：改为调用 `WorkflowDagExecutor`。
- `src/agent_bridge/api/schemas.py`：工作流定义和手动运行请求模型。
- `src/agent_bridge/api/routes/workflows.py`：结构化保存、带输入运行和节点运行详情。
- `src/agent_bridge/api/app.py`：输出结构化工作流校验错误。
- `src/agent_bridge/app/service.py`：装配验证器、处理器和执行器。

**前端新文件**

- `frontend/capabilities/src/views/workflow/workflowDefinition.ts`：图类型、默认图、Vue Flow 转换和前端基础校验。
- `frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue`：画布、连线、节点选择和删除。
- `frontend/capabilities/src/views/workflow/WorkflowNodePalette.vue`：四类节点入口。
- `frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`：四类节点配置表单。
- `frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue`：结构化条件配置。
- `frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue`：定义快照上的节点运行状态展示。
- `frontend/capabilities/tests/workflowDefinition.test.ts`：纯 TypeScript 图逻辑测试。

**前端修改文件**

- `frontend/capabilities/src/api/types.ts`：工作流定义、节点、边和节点运行类型。
- `frontend/capabilities/src/api/client.ts`：结构化保存、带输入运行和运行详情。
- `frontend/capabilities/src/views/workflow/WorkflowView.vue`：接入编辑器和运行图，移除 `workflow.js` 编辑及静态解析入口。

---

### 任务 1：建立结构化工作流定义与纯图校验

**文件：**

- 新建：`src/agent_bridge/automation/workflows/definition.py`
- 新建：`src/agent_bridge/automation/workflows/validation.py`
- 修改：`src/agent_bridge/automation/workflows/models.py`
- 新建测试：`tests/test_workflow_definition.py`

**接口：**

- 产出：`WorkflowGraph`、四种节点模型、`WorkflowEdge`、`EdgeCondition`。
- 产出：`default_workflow_graph(workflow_type: WorkflowType, default_backend: str) -> WorkflowGraph`。
- 产出：`validate_graph(graph: WorkflowGraph, workflow_type: WorkflowType) -> None`。
- 产出：`WorkflowValidationIssue` 和 `WorkflowDefinitionValidationError`。
- 后续任务只接收已经通过 `validate_graph()` 的 `WorkflowGraph`。

- [ ] **步骤 1：先写定义解析和图约束失败测试**

```python
from agent_bridge.automation.workflows.definition import WorkflowGraph, default_workflow_graph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import (
    WorkflowDefinitionValidationError,
    validate_graph,
)


def test_summary_default_graph_contains_locked_output_pair():
    graph = default_workflow_graph(WorkflowType.summary, "codex")
    assert [node.type for node in graph.nodes] == ["output", "output"]
    assert graph.nodes[0].config.format == "markdown"
    assert graph.nodes[1].config.format == "html"
    assert [node.config.backend_key for node in graph.nodes] == ["codex", "codex"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [
        (graph.nodes[0].id, graph.nodes[1].id)
    ]


def test_validate_graph_rejects_cycle():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0},
             "config": {"prompt": "a", "backend_key": "claude"}},
            {"id": "b", "type": "agent", "name": "B", "position": {"x": 200, "y": 0},
             "config": {"prompt": "b", "backend_key": "claude"}},
        ],
        "edges": [
            {"id": "ab", "source": "a", "target": "b"},
            {"id": "ba", "source": "b", "target": "a"},
        ],
    })
    try:
        validate_graph(graph, WorkflowType.operation)
    except WorkflowDefinitionValidationError as exc:
        assert any(issue.message == "工作流不能包含环" for issue in exc.issues)
    else:
        raise AssertionError("expected WorkflowDefinitionValidationError")
```

- [ ] **步骤 2：运行测试并确认失败原因是模块尚不存在**

运行：

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_definition.py -q
```

预期：收集失败，提示 `agent_bridge.automation.workflows.definition` 不存在。

- [ ] **步骤 3：实现类型模型和默认总结图**

`definition.py` 必须使用可辨识联合类型，核心定义如下：

```python
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


class EdgeCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    operator: Literal["equals", "not_equals", "exists", "not_exists", "contains"]
    value: Any | None = None


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: str
    target: str
    condition: EdgeCondition | None = None


class GetTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str
    backend_key: str
    mcp_enabled: bool = True
    skill_names: list[str] = Field(default_factory=list)
    result_mode: Literal["text", "json"] = "text"
    output_schema: dict[str, Any] | None = None


class ScriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_key: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["markdown", "html"]
    title: str
    path: str
    tags: list[str] = Field(default_factory=list)
    prompt: str
    backend_key: str
    mcp_enabled: bool = False
    skill_names: list[str] = Field(default_factory=list)


class BaseNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    position: NodePosition


class GetTaskNode(BaseNode):
    type: Literal["get_task"]
    config: GetTaskConfig = Field(default_factory=GetTaskConfig)


class AgentNode(BaseNode):
    type: Literal["agent"]
    config: AgentConfig


class ScriptNode(BaseNode):
    type: Literal["script"]
    config: ScriptConfig


class OutputNode(BaseNode):
    type: Literal["output"]
    config: OutputConfig


WorkflowNode = Annotated[
    GetTaskNode | AgentNode | ScriptNode | OutputNode,
    Field(discriminator="type"),
]


class WorkflowGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
```

`default_workflow_graph(WorkflowType.summary, default_backend)` 必须生成稳定 ID：`markdown-output`、`html-output`、`markdown-to-html`。两个输出节点使用传入的系统默认后端。Markdown 默认提示词固定为“根据全部上游节点输出生成结构清晰的 Markdown 主报告；返回 title、summary、content，content 必须是完整 Markdown。”；HTML 默认提示词固定为“只根据 Markdown 主产物生成完整 HTML 文档；返回 title、summary、content，content 必须包含 html 或 body 标签、内联 CSS、无外链脚本。”。操作型工作流返回空图。

- [ ] **步骤 4：实现纯图校验**

`validation.py` 至少检查：节点/边 ID 唯一、边端点存在、无自环、无环、`get_task` 数量和起点约束、引用节点必须为祖先、JSON 输出模式必须有 schema、总结输出对的数量/顺序/末端约束。

```python
@dataclass(frozen=True)
class WorkflowValidationIssue:
    scope: Literal["workflow", "node", "edge"]
    id: str | None
    field: str | None
    message: str


class WorkflowDefinitionValidationError(ValidationError):
    def __init__(self, issues: list[WorkflowValidationIssue]) -> None:
        super().__init__("工作流定义校验失败")
        self.issues = issues


def validate_graph(graph: WorkflowGraph, workflow_type: WorkflowType) -> None:
    issues = collect_graph_issues(graph, workflow_type)
    if issues:
        raise WorkflowDefinitionValidationError(issues)
```

- [ ] **步骤 5：运行定义测试**

运行：

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_definition.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交任务 1**

```bash
git add src/agent_bridge/automation/workflows/definition.py \
  src/agent_bridge/automation/workflows/validation.py \
  src/agent_bridge/automation/workflows/models.py \
  tests/test_workflow_definition.py
git commit -m "feat(workflows): define structured workflow graph"
```

---

### 任务 2：迁移 SQLite 定义、运行快照和节点运行存储

**文件：**

- 修改：`src/agent_bridge/storage/schema.py`
- 修改：`src/agent_bridge/storage/sqlite.py`
- 修改：`src/agent_bridge/storage/repositories/workflows.py`
- 修改测试：`tests/test_workflow_storage.py`

**接口：**

- `upsert_workflow_definition(*, workflow_key: str, name: str, description: str, profile_key: str, definition: dict[str, Any], status: str, created_by: str, workflow_type: str = "operation", workflow_js: str = "")`。
- `create_workflow_run(*, run_id: str, workflow_key: str, profile_key: str, task_key: str | None, status: str, temp_dir: str, definition_snapshot: dict[str, Any], input_data: dict[str, Any])`。
- `finish_workflow_run(run_id: str, *, status: str, exit_code: int | None, stdout_path: str | None, stderr_path: str | None, error: str | None, duration_ms: int | None, output: dict[str, Any])`。
- 新增节点运行的创建、开始、结束、查询方法；`start_workflow_node_run(run_id, node_id, condition_results)` 在启动节点时保存入边判断记录。
- 新增 `fail_workflow_task_for_run(workflow_key, run_id, error_message) -> bool`。

- [ ] **步骤 1：写存储迁移和节点状态失败测试**

```python
def test_workflow_definition_and_run_snapshot_round_trip(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="report-plane", name="Report Plane", created_by="root"
    )
    definition = {"nodes": [], "edges": []}
    saved = store.workflows.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition=definition,
        status="active",
        created_by="root",
    )
    run = store.workflows.create_workflow_run(
        run_id="run_1",
        workflow_key=saved["workflow_key"],
        profile_key=saved["profile_key"],
        task_key=None,
        status="running",
        temp_dir="",
        definition_snapshot=definition,
        input_data={"topic": "x"},
    )
    assert saved["definition"] == definition
    assert run["definition_snapshot"] == definition
    assert run["input"] == {"topic": "x"}


def test_workflow_node_run_lifecycle(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="report-plane", name="Report Plane", created_by="root"
    )
    store.workflows.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
        created_by="root",
    )
    store.workflows.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="",
        definition_snapshot={"nodes": [], "edges": []},
        input_data={},
    )
    store.workflows.create_workflow_node_runs("run_1", [
        {"node_id": "a", "node_type": "agent"},
    ])
    store.workflows.start_workflow_node_run("run_1", "a")
    store.workflows.finish_workflow_node_run(
        "run_1", "a", status="completed", output={"text": "ok"}
    )
    node = store.workflows.list_workflow_node_runs("run_1")[0]
    assert node["status"] == "completed"
    assert node["output"] == {"text": "ok"}
```

- [ ] **步骤 2：运行测试并确认因新参数或方法缺失而失败**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_storage.py -q
```

预期：失败信息指向 `definition` 参数或节点运行方法不存在。

- [ ] **步骤 3：扩展表结构和兼容迁移**

在 `workflow_definitions` 增加可空的 `definition_json TEXT`，保留 `workflow_js`。迁移时不得回填空图；历史行保持 `NULL`，由列表和运行入口识别为“需要迁移”。

在 `workflow_runs` 增加：

```sql
definition_snapshot_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
input_json TEXT NOT NULL DEFAULT '{}',
output_json TEXT NOT NULL DEFAULT '{}'
```

新增：

```sql
CREATE TABLE IF NOT EXISTS workflow_node_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  condition_results_json TEXT NOT NULL DEFAULT '[]',
  output_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  agent_run_key TEXT,
  script_run_id TEXT,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run
  ON workflow_node_runs(run_id, id);
```

`SQLiteStore.init_schema()` 必须通过 `_ensure_columns()` 给既有数据库补列。

- [ ] **步骤 4：实现仓库读写和 JSON 投影**

扩展 `_row_payload()`，把 `definition_json`、`definition_snapshot_json`、`input_json`、`output_json`、`condition_results_json` 转成对应无 `_json` 后缀的对象字段。`definition_json IS NULL` 时返回 `definition=None`，不要替换为空图。

节点完成接口固定为：

```python
def finish_workflow_node_run(
    self,
    run_id: str,
    node_id: str,
    *,
    status: str,
    condition_results: list[dict[str, Any]] | None = None,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    agent_run_key: str | None = None,
    script_run_id: str | None = None,
) -> dict[str, Any]:
```

任务失败接口只更新本次运行持有的租约：

```sql
UPDATE workflow_tasks
SET status = 'failed', last_error = ?, lease_expires_at = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE workflow_key = ? AND lease_run_id = ? AND status = 'running'
```

- [ ] **步骤 5：运行存储测试和数据库回归测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_storage.py tests/test_storage.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交任务 2**

```bash
git add src/agent_bridge/storage/schema.py \
  src/agent_bridge/storage/sqlite.py \
  src/agent_bridge/storage/repositories/workflows.py \
  tests/test_workflow_storage.py
git commit -m "feat(workflows): persist graph and node runs"
```

---

### 任务 3：实现引用渲染和结构化条件求值

**文件：**

- 新建：`src/agent_bridge/automation/workflows/references.py`
- 新建测试：`tests/test_workflow_references.py`

**接口：**

- `resolve_path(context: dict[str, Any], path: str) -> Any`。
- `render_text(template: str, context: dict[str, Any]) -> str`。
- `render_value(value: Any, context: dict[str, Any]) -> Any`。
- `evaluate_condition(condition: EdgeCondition | None, context: dict[str, Any]) -> ConditionResult`。

- [ ] **步骤 1：写类型保持、缺失引用和五种条件测试**

```python
import pytest

from agent_bridge.automation.workflows.definition import EdgeCondition
from agent_bridge.automation.workflows.references import (
    MissingReferenceError,
    evaluate_condition,
    render_text,
    render_value,
)


CONTEXT = {
    "input": {"limit": 20},
    "task": {"payload": {"repo": "acme/demo"}},
    "nodes": {"classify": {"output": {"category": "bug", "tags": ["ui"]}}},
}


def test_whole_reference_preserves_json_type():
    assert render_value("{{ input.limit }}", CONTEXT) == 20


def test_embedded_reference_becomes_text():
    assert render_text("repo={{ task.payload.repo }}", CONTEXT) == "repo=acme/demo"


def test_missing_prompt_reference_fails():
    with pytest.raises(MissingReferenceError):
        render_text("{{ task.payload.missing }}", CONTEXT)


def test_missing_not_equals_is_false():
    result = evaluate_condition(
        EdgeCondition(field="nodes.classify.output.missing", operator="not_equals", value="x"),
        CONTEXT,
    )
    assert result.matched is False
    assert result.actual is None


def test_null_condition_is_always_active():
    result = evaluate_condition(None, CONTEXT)
    assert result.matched is True
    assert result.actual is None
```

- [ ] **步骤 2：运行测试并确认模块缺失**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_references.py -q
```

预期：收集失败，提示 `references` 模块不存在。

- [ ] **步骤 3：实现无表达式的路径和模板解析**

只识别完整的 `{{ dotted.path }}`，不使用 `eval()`、Jinja 或 JSONPath。

```python
REFERENCE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")


@dataclass(frozen=True)
class ConditionResult:
    matched: bool
    actual: Any


class MissingReferenceError(ValueError):
    def __init__(self, path: str) -> None:
        super().__init__(f"引用字段不存在: {path}")
        self.path = path
```

`render_value()` 必须递归处理字典和列表；整个字符串只有一个引用时返回原类型，否则调用 `render_text()`。

`contains` 只接受字符串、列表或字典；字段缺失时按照 spec 固定返回，不抛异常。

- [ ] **步骤 4：运行测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_references.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交任务 3**

```bash
git add src/agent_bridge/automation/workflows/references.py tests/test_workflow_references.py
git commit -m "feat(workflows): resolve node references and conditions"
```

---

### 任务 4：为托管脚本增加输入 Schema

**文件：**

- 修改：`pyproject.toml`
- 修改：`uv.lock`
- 修改：`src/agent_bridge/storage/schema.py`
- 修改：`src/agent_bridge/storage/sqlite.py`
- 修改：`src/agent_bridge/storage/repositories/scripts.py`
- 修改：`src/agent_bridge/system_config/scripts/service.py`
- 修改：`src/agent_bridge/api/schemas.py`
- 修改：`src/agent_bridge/api/routes/agent_runs.py`
- 修改：`src/agent_bridge/system_config/skills/defaults/design_script.md`
- 修改：`frontend/capabilities/src/api/types.ts`
- 修改：`frontend/capabilities/src/api/client.ts`
- 修改：`frontend/capabilities/src/views/system/ScriptsView.vue`
- 修改测试：`tests/test_scripts.py`
- 修改测试：`tests/test_design_agent_api.py`
- 修改测试：`tests/test_capability_api.py`

**接口：**

- 托管脚本新增 `input_schema: dict[str, Any]`。
- `ScriptService.upsert_script()` 必须接收并校验 `input_schema`。
- `ScriptService.run_script()` 在创建运行目录前校验 `script_params`。
- 运行时校验失败继续使用 `ValidationError`，消息包含字段路径，并在消息末尾附加紧凑 schema JSON。

- [ ] **步骤 1：先写脚本 Schema 保存和运行校验失败测试**

```python
import pytest

from agent_bridge.core.domain import ValidationError


SCRIPT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "repo": {"type": "string", "description": "仓库标识"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["repo"],
}


def test_script_input_schema_round_trip_and_validation(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    scripts = AgentBridgeService.create(wm_paths, {"root"}).scripts
    saved = scripts.upsert_script(
        actor="root", script_key="collect", name="Collect", description="",
        language="python", code="def main(envelope):\n    return {}\n",
        input_schema=SCRIPT_INPUT_SCHEMA,
        status="active", owner_type="system", owner_key="",
    )
    assert saved["input_schema"] == SCRIPT_INPUT_SCHEMA

    with pytest.raises(ValidationError) as exc:
        scripts.test_script(
            actor="root", script_key="collect",
            script_params={"repo": 123}, timeout_seconds=10,
        )
    assert "repo" in str(exc.value)
    assert "expected_schema=" in str(exc.value)


def test_script_rejects_invalid_input_schema(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    scripts = AgentBridgeService.create(wm_paths, {"root"}).scripts
    with pytest.raises(ValidationError, match="input_schema"):
        scripts.upsert_script(
            actor="root", script_key="bad", name="Bad", description="",
            language="python", code="def main(envelope):\n    return {}\n",
            input_schema={"type": "string"},
            status="active", owner_type="system", owner_key="",
        )
```

- [ ] **步骤 2：运行脚本测试并确认新参数缺失**

```bash
PYTHONPATH=. uv run pytest tests/test_scripts.py -q
```

预期：失败信息指向 `input_schema` 参数不存在或返回值没有该字段。

- [ ] **步骤 3：增加直接依赖和数据库字段**

在 `pyproject.toml` 增加 `jsonschema>=4.23.0,<5` 并执行：

```bash
uv lock
```

`scripts` 表增加：

```sql
input_schema_json TEXT NOT NULL DEFAULT '{"type":"object","properties":{},"additionalProperties":true}'
```

该默认值只用于升级前历史脚本的兼容模式；新建或重新保存的脚本必须从 API 提交 schema。`SQLiteStore.init_schema()` 通过 `_ensure_columns()` 补列。

`ScriptsRepository` 的 upsert 新增 `input_schema` 参数并读写 `input_schema_json`；list/get 返回解析后的 `input_schema`，不得把原始 JSON 字符串暴露给 API。

- [ ] **步骤 4：实现 schema 自身校验和运行参数校验**

`ScriptService` 使用标准库接口：

```python
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


def _validate_input_schema(self, input_schema: dict[str, Any]) -> dict[str, Any]:
    if input_schema.get("type") != "object":
        raise ValidationError("input_schema 根类型必须为 object")
    try:
        Draft202012Validator.check_schema(input_schema)
    except SchemaError as exc:
        raise ValidationError(f"input_schema 非法: {exc.message}") from exc
    return input_schema


def _validate_script_params(
    self, script_key: str, input_schema: dict[str, Any], script_params: dict[str, Any]
) -> None:
    errors = sorted(
        Draft202012Validator(input_schema).iter_errors(script_params),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "<root>"
    expected = json.dumps(input_schema, ensure_ascii=False, separators=(",", ":"))
    raise ValidationError(
        f"script params invalid script={script_key} field={path}: "
        f"{first.message}; expected_schema={expected}"
    )
```

在 `run_script()` 中先加载并校验 params，再创建 `run_id` 和运行目录；非法输入不得生成 script run 记录。

- [ ] **步骤 5：更新脚本 API、设计 Agent 和脚本管理页**

- `ScriptRequest` 增加必填 `input_schema: dict[str, Any]`。
- `ManagedScript` 和 `ScriptDesignResult.script` 增加 `input_schema`。
- `SCRIPT_DESIGN_SCHEMA` 将 `input_schema` 加入 required，并限制根类型为对象 schema。
- `design_script.md` 要求设计 Agent 同时输出字段名、类型、描述和 required。
- `ScriptsView.vue` 增加输入字段表格：字段名输入框、类型下拉框、必填复选框、描述输入框、删除图标和“添加字段”按钮。
- UI 保存时生成 `{"type":"object","properties":...,"required":...,"additionalProperties":false}`。
- 历史兼容 schema（`additionalProperties=true` 且无 properties）显示“兼容模式：未声明字段”，用户保存时可继续保留或改成明确字段。

- [ ] **步骤 6：运行脚本、设计 Agent 和前端回归**

```bash
PYTHONPATH=. uv run pytest tests/test_scripts.py tests/test_design_agent_api.py \
  tests/test_capability_api.py -q
cd frontend/capabilities
npm run typecheck
npm run build
```

预期：全部通过。

- [ ] **步骤 7：提交任务 4**

```bash
git add pyproject.toml uv.lock src/agent_bridge/storage/schema.py \
  src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/scripts.py \
  src/agent_bridge/system_config/scripts/service.py src/agent_bridge/api/schemas.py \
  src/agent_bridge/api/routes/agent_runs.py \
  src/agent_bridge/system_config/skills/defaults/design_script.md \
  frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts \
  frontend/capabilities/src/views/system/ScriptsView.vue \
  tests/test_scripts.py tests/test_design_agent_api.py tests/test_capability_api.py
git commit -m "feat(scripts): declare and validate input schema"
```

---

### 任务 5：实现获取任务、Agent 和托管脚本处理器

**文件：**

- 新建：`src/agent_bridge/automation/workflows/handlers.py`
- 新建测试：`tests/test_workflow_handlers.py`
- 修改：`src/agent_bridge/automation/workflows/service.py`

**接口：**

- `NodeExecutionContext`：运行 ID、工作流、输入、任务、节点输出和 actor。
- `NodeExecutionResult`：统一输出和关联运行 ID。
- `WorkflowNodeHandlers.execute(node, context) -> NodeExecutionResult`。

- [ ] **步骤 1：写任务获取、Agent 技能拼接和脚本参数测试**

```python
from types import SimpleNamespace

import pytest

from agent_bridge.agent_runtime.service import AgentRunResult
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.handlers import NodeExecutionContext, WorkflowNodeHandlers


class FakeAgentService:
    def __init__(self) -> None:
        self.calls = []
        self.result = AgentRunResult(ok=True, result="agent result", run_key="agent_run_1")

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeScriptService:
    def __init__(self) -> None:
        self.calls = []

    def run_script(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "script_run_1", "result": {"ok": True}}


class FakeSkillService:
    def get_skill(self, actor, skill_name):
        return {"skill_name": skill_name, "prompt": f"{skill_name} prompt"}


@pytest.fixture
def fake_services():
    return SimpleNamespace(
        agent=FakeAgentService(), scripts=FakeScriptService(),
        skills=FakeSkillService(),
    )


def make_handlers(services):
    return WorkflowNodeHandlers(
        agent_service=services.agent, scripts=services.scripts,
        skill_service=services.skills,
    )


def context():
    return NodeExecutionContext(
        actor="root", workflow={"workflow_key": "w", "profile_key": "p"},
        run_id="run_1", input={"limit": 20},
        task={"task_key": "t", "payload": {"repo": "acme/demo"}}, nodes={},
    )


def node(node_type, config):
    return WorkflowGraph.model_validate({
        "nodes": [{"id": "n", "type": node_type, "name": "N",
                   "position": {"x": 0, "y": 0}, "config": config}],
        "edges": [],
    }).nodes[0]


def agent_node():
    return node("agent", {"prompt": "review {{ task.payload.repo }}",
                          "backend_key": "claude", "skill_names": ["review"]})


def script_node():
    return node("script", {"script_key": "collect",
                            "params": {"repo": "{{ task.payload.repo }}",
                                       "count": "{{ input.limit }}"},
                            "timeout_seconds": 60})


@pytest.mark.asyncio
async def test_agent_handler_prepends_skills_and_wraps_text(fake_services):
    handlers = make_handlers(fake_services)
    result = await handlers.execute(agent_node(), context())
    prompt = fake_services.agent.calls[0]["prompt"]
    assert prompt.index("[技能：review]") < prompt.index("[任务指令]")
    assert result.output == {"text": "agent result"}
    assert result.agent_run_key == "agent_run_1"


@pytest.mark.asyncio
async def test_script_handler_passes_only_rendered_params(fake_services):
    handlers = make_handlers(fake_services)
    result = await handlers.execute(script_node(), context())
    assert fake_services.scripts.calls[0]["script_params"] == {
        "repo": "acme/demo",
        "count": 20,
    }
    assert result.output == {"ok": True}
```

- [ ] **步骤 2：运行测试并确认处理器尚不存在**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_handlers.py -q
```

预期：收集失败或导入失败。

- [ ] **步骤 3：实现统一上下文和结果**

```python
@dataclass
class NodeExecutionContext:
    actor: str
    workflow: dict[str, Any]
    run_id: str
    input: dict[str, Any]
    task: dict[str, Any] | None
    nodes: dict[str, dict[str, Any]]

    def template_context(self) -> dict[str, Any]:
        return {"input": self.input, "task": self.task, "nodes": self.nodes}


@dataclass(frozen=True)
class NodeExecutionResult:
    status: Literal["completed"]
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    agent_run_key: str | None = None
    script_run_id: str | None = None
```

- [ ] **步骤 4：实现三类处理器**

关键调用约束：

```python
agent_result = await self.agent_service.run(
    prompt=rendered_prompt,
    agent_name=f"workflow_{node.id}",
    profile=workflow["profile_key"] if config.mcp_enabled else None,
    workflow_key=workflow["workflow_key"],
    run_id=context.run_id,
    output_schema=output_schema,
    backend_key=config.backend_key,
    skills=None,
)
```

脚本必须经 `asyncio.to_thread()` 调用现有阻塞接口：

```python
script_run = await asyncio.to_thread(
    self.scripts.run_script,
    actor=context.actor,
    script_key=config.script_key,
    script_params=render_value(config.params, context.template_context()),
    timeout_seconds=config.timeout_seconds,
    profile_key=context.workflow["profile_key"],
    workflow_context={
        "workflow": True,
        "workflow_key": context.workflow["workflow_key"],
        "run_id": context.run_id,
    },
    run_type="mcp",
)
```

`GetTaskHandler` 直接调用 `WorkflowService.get_task_for_agent()`；`AgentHandler` 调用 `AgentService.run()`；`ScriptHandler` 调用 `self.scripts.run_script()`。三者失败都抛 `NodeExecutionError`。

- [ ] **步骤 5：运行处理器测试和现有 Agent/脚本测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_handlers.py \
  tests/test_scripts.py tests/test_agent_service.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交任务 5**

```bash
git add src/agent_bridge/automation/workflows/handlers.py \
  src/agent_bridge/automation/workflows/service.py \
  tests/test_workflow_handlers.py
git commit -m "feat(workflows): execute task agent and script nodes"
```

---

### 任务 6：实现独立输出结果处理器

**文件：**

- 新建：`src/agent_bridge/automation/workflows/output_handler.py`
- 修改：`src/agent_bridge/automation/workflows/handlers.py`
- 修改：`src/agent_bridge/automation/workflows/service.py`
- 新建测试：`tests/test_workflow_output_handler.py`

**接口：**

- `OutputHandler.execute(node: OutputNode, context: NodeExecutionContext) -> NodeExecutionResult`。
- 扩展 `NodeExecutionResult.status` 为 `Literal["completed", "warning"]`，增加 `artifact_ids: list[str]`。
- `WorkflowNodeHandlers` 在 `node.type == "output"` 时委托 `OutputHandler`。

- [ ] **步骤 1：先写 Markdown 保存、HTML 输入和警告语义测试**

```python
from types import SimpleNamespace

import pytest

from agent_bridge.agent_runtime.service import AgentRunResult
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.handlers import NodeExecutionContext
from agent_bridge.automation.workflows.output_handler import OutputHandler


class FakeOutputAgent:
    def __init__(self):
        self.calls = []
        self.result = AgentRunResult(
            ok=True,
            result={"title": "T", "summary": "S", "content": "# Report"},
            run_key="agent_output_1",
        )

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeOutputSkills:
    def get_skill(self, actor, skill_name):
        return {"skill_name": skill_name, "prompt": f"{skill_name} prompt"}


class FakeOutputWorkflows:
    def save_artifact(self, **kwargs):
        return {"artifact_id": f"artifact_{kwargs['format']}"}


def output_node(node_id, output_format):
    return WorkflowGraph.model_validate({
        "nodes": [{
            "id": node_id, "type": "output", "name": node_id,
            "position": {"x": 0, "y": 0},
            "config": {
                "format": output_format,
                "title": node_id,
                "path": f"reports/t/index.{ 'md' if output_format == 'markdown' else 'html' }",
                "tags": ["summary"], "prompt": "render", "backend_key": "claude",
            },
        }],
        "edges": [],
    }).nodes[0]


@pytest.fixture
def output_fixture():
    agent = FakeOutputAgent()
    handler = OutputHandler(
        agent_service=agent,
        skill_service=FakeOutputSkills(),
        workflow_service=FakeOutputWorkflows(),
    )

    def context(*, nodes):
        return NodeExecutionContext(
            actor="root", workflow={"workflow_key": "w", "profile_key": "p"},
            run_id="run_1", input={}, task={"task_key": "t", "payload": {}},
            nodes=nodes,
        )

    return SimpleNamespace(
        agent=agent,
        handler=handler,
        context=context,
        markdown_node=output_node("markdown-output", "markdown"),
        html_node=output_node("html-output", "html"),
    )


@pytest.mark.asyncio
async def test_markdown_output_injects_ancestors_and_saves_artifact(output_fixture):
    result = await output_fixture.handler.execute(
        output_fixture.markdown_node,
        output_fixture.context(nodes={
            "analyze": {"status": "completed", "output": {"summary": "S"}},
        }),
    )
    prompt = output_fixture.agent.calls[0]["prompt"]
    assert "[上游节点输出]" in prompt
    assert '"analyze"' in prompt
    assert result.status == "completed"
    assert result.artifact_ids == ["artifact_markdown"]


@pytest.mark.asyncio
async def test_html_output_only_injects_markdown_artifact(output_fixture):
    output_fixture.agent.result = AgentRunResult(
        ok=True,
        result={"title": "T", "summary": "S", "content": "<html><body>Report</body></html>"},
        run_key="agent_output_2",
    )
    context = output_fixture.context(nodes={
        "markdown-output": {
            "status": "completed",
            "output": {
                "title": "T", "summary": "S", "content": "# Report",
                "artifact_ids": ["artifact_markdown"],
            },
        },
        "raw-analysis": {"status": "completed", "output": {"secret": "raw"}},
    })
    await output_fixture.handler.execute(output_fixture.html_node, context)
    prompt = output_fixture.agent.calls[0]["prompt"]
    assert "[Markdown 主产物]" in prompt
    assert "# Report" in prompt
    assert "secret" not in prompt


@pytest.mark.asyncio
async def test_html_agent_or_format_failure_returns_warning(output_fixture):
    output_fixture.agent.result = AgentRunResult(ok=False, error="bad html")
    result = await output_fixture.handler.execute(
        output_fixture.html_node,
        output_fixture.context(nodes={"markdown-output": {
            "status": "completed",
            "output": {"title": "T", "summary": "S", "content": "# Report",
                       "artifact_ids": ["artifact_markdown"]},
        }}),
    )
    assert result.status == "warning"
    assert result.error == "bad html"
    assert result.artifact_ids == []
```

- [ ] **步骤 2：运行测试并确认输出处理器不存在**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_output_handler.py -q
```

预期：收集失败，提示 `output_handler` 模块不存在。

- [ ] **步骤 3：实现固定输出 schema 和提示词输入**

```python
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "content"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "content": {"type": "string"},
    },
}
HTML_MAX_BYTES = 5 * 1024 * 1024
```

- Markdown：按节点 ID 排序序列化所有祖先节点输出，追加到 `[上游节点输出]`。
- HTML：只读取直接上游 Markdown 输出的标题、摘要、正文和产物 ID，追加到 `[Markdown 主产物]`。
- 两类输出都通过 `AgentService.run(..., output_schema=OUTPUT_SCHEMA, backend_key=config.backend_key)` 调用 Coding Agent。
- 技能注入和 MCP 开关复用任务 5 的公共提示词组装函数，不复制实现。

- [ ] **步骤 4：实现格式校验、产物保存和 HTML 警告**

- Markdown `content.strip()` 为空时抛 `NodeExecutionError`。
- HTML 必须包含 `<html` 或 `<body`，UTF-8 字节数不得超过 5 MiB。
- 调用 `WorkflowService.save_artifact()` 保存 title、path、tags、format、summary、content 和 `metadata={"node_id": node.id}`。
- Markdown 的 Agent、schema、格式或保存错误抛 `NodeExecutionError`。
- HTML 的上述错误返回 `NodeExecutionResult(status="warning", error=...)`，不得保存空产物。
- 成功输出中保留 `title`、`summary`、`content`、`artifact_ids`，供 HTML 和运行结果汇总使用。

- [ ] **步骤 5：运行输出处理器和现有 HTML 报告测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_output_handler.py \
  tests/test_workflow_html_report.py -q
```

预期：全部通过；现有 HTML 测试应改为调用 `OutputHandler`，不再直接测试调度器后处理。

- [ ] **步骤 6：提交任务 6**

```bash
git add src/agent_bridge/automation/workflows/output_handler.py \
  src/agent_bridge/automation/workflows/handlers.py \
  src/agent_bridge/automation/workflows/service.py \
  tests/test_workflow_output_handler.py tests/test_workflow_html_report.py
git commit -m "feat(workflows): generate and persist workflow outputs"
```

---

### 任务 7：实现 DAG 执行器和快速失败

**文件：**

- 新建：`src/agent_bridge/automation/workflows/executor.py`
- 新建测试：`tests/test_workflow_executor.py`

**接口：**

- `WorkflowExecutionResult(status, output, task, error, warnings)`。
- `WorkflowDagExecutor.run(workflow, run_id, input_data, actor) -> WorkflowExecutionResult`。
- 消费任务 1 的图模型、任务 2 的存储接口、任务 3 的条件求值，以及任务 5/6 的节点处理器。

- [ ] **步骤 1：写串行、并行、分支、汇合、跳过和快速失败测试**

```python
import asyncio

import pytest

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.handlers import NodeExecutionError, NodeExecutionResult


def agent(node_id):
    return {"id": node_id, "type": "agent", "name": node_id,
            "position": {"x": 0, "y": 0},
            "config": {"prompt": node_id, "backend_key": "claude"}}


def workflow(nodes, edges):
    return {"workflow_key": "w", "profile_key": "p", "workflow_type": "operation",
            "definition": WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})}


class RecordingStore:
    def __init__(self):
        self.statuses = {}

    def create_workflow_node_runs(self, run_id, nodes):
        self.statuses.update({item["node_id"]: "pending" for item in nodes})

    def start_workflow_node_run(self, run_id, node_id):
        self.statuses[node_id] = "running"

    def finish_workflow_node_run(self, run_id, node_id, *, status, **kwargs):
        self.statuses[node_id] = status


class RecordingHandlers:
    def __init__(self, outputs=None, failing_node=None, blocking_node=None):
        self.outputs = outputs or {}
        self.failing_node = failing_node
        self.blocking_node = blocking_node
        self.order = []
        self.parallel_batch = []
        self.cancelled = set()
        self.block = asyncio.Event()

    async def execute(self, node, context):
        self.order.append(node.id)
        if node.id in {"left", "right"}:
            self.parallel_batch.append(node.id)
        if node.id == self.failing_node:
            raise NodeExecutionError(node.id, "boom")
        if node.id == self.blocking_node:
            try:
                await self.block.wait()
            except asyncio.CancelledError:
                self.cancelled.add(node.id)
                raise
        return NodeExecutionResult(
            status="completed", output=self.outputs.get(node.id, {"text": node.id})
        )


def executor(handlers):
    return WorkflowDagExecutor(store=RecordingStore(), handlers=handlers)


def workflow_with_parallel_join():
    return workflow(
        [agent("start"), agent("left"), agent("right"), agent("join")],
        [
            {"id": "s-l", "source": "start", "target": "left"},
            {"id": "s-r", "source": "start", "target": "right"},
            {"id": "l-j", "source": "left", "target": "join"},
            {"id": "r-j", "source": "right", "target": "join"},
        ],
    )


def conditional_workflow():
    return workflow(
        [agent("classify"), agent("fix"), agent("document")],
        [
            {"id": "to-fix", "source": "classify", "target": "fix",
             "condition": {"field": "nodes.classify.output.category",
                           "operator": "equals", "value": "bug"}},
            {"id": "to-doc", "source": "classify", "target": "document",
             "condition": {"field": "nodes.classify.output.category",
                           "operator": "equals", "value": "feature"}},
        ],
    )


def parallel_failure_workflow():
    return workflow(
        [agent("start"), agent("fast-fail"), agent("slow-agent")],
        [
            {"id": "to-fail", "source": "start", "target": "fast-fail"},
            {"id": "to-slow", "source": "start", "target": "slow-agent"},
        ],
    )


def conditional_join_skip_workflow():
    return workflow(
        [agent("classify"), agent("a"), agent("b"), agent("join")],
        [
            {"id": "to-a", "source": "classify", "target": "a"},
            {"id": "to-b", "source": "classify", "target": "b",
             "condition": {"field": "nodes.classify.output.category",
                           "operator": "equals", "value": "feature"}},
            {"id": "a-join", "source": "a", "target": "join",
             "condition": {"field": "nodes.classify.output.category",
                           "operator": "equals", "value": "feature"}},
            {"id": "b-join", "source": "b", "target": "join"},
        ],
    )


@pytest.mark.asyncio
async def test_executor_runs_parallel_nodes_then_join():
    handlers = RecordingHandlers()
    result = await executor(handlers).run(
        workflow=workflow_with_parallel_join(),
        run_id="run_1",
        input_data={},
        actor="root",
    )
    assert result.status == "completed"
    assert set(handlers.parallel_batch) == {"left", "right"}
    assert handlers.order[-1] == "join"


@pytest.mark.asyncio
async def test_executor_skips_false_branch_and_runs_true_branch():
    handlers = RecordingHandlers(outputs={"classify": {"category": "bug"}})
    result = await executor(handlers).run(
        workflow=conditional_workflow(), run_id="run_1", input_data={}, actor="root"
    )
    assert result.node_statuses["fix"] == "completed"
    assert result.node_statuses["document"] == "skipped"


@pytest.mark.asyncio
async def test_join_skips_when_completed_edge_is_false_and_other_source_is_skipped():
    handlers = RecordingHandlers(outputs={"classify": {"category": "bug"}})
    result = await executor(handlers).run(
        workflow=conditional_join_skip_workflow(),
        run_id="run_1", input_data={}, actor="root",
    )
    assert result.node_statuses["a"] == "completed"
    assert result.node_statuses["b"] == "skipped"
    assert result.node_statuses["join"] == "skipped"


@pytest.mark.asyncio
async def test_executor_cancels_running_nodes_on_failure():
    handlers = RecordingHandlers(failing_node="fast-fail", blocking_node="slow-agent")
    result = await executor(handlers).run(
        workflow=parallel_failure_workflow(), run_id="run_1", input_data={}, actor="root"
    )
    assert result.status == "failed"
    assert handlers.cancelled == {"slow-agent"}
```

- [ ] **步骤 2：运行测试并确认执行器不存在**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_executor.py -q
```

预期：收集失败或导入失败。

- [ ] **步骤 3：实现调度循环**

执行器必须维护 `pending` 集合和 `asyncio.Task` 映射；只有所有直接上游进入终态后才判断节点。

```python
TERMINAL_NODE_STATUSES = {"completed", "skipped", "failed", "cancelled", "warning"}


@dataclass(frozen=True)
class WorkflowExecutionResult:
    status: Literal["completed", "no_task", "failed"]
    output: dict[str, Any]
    task: dict[str, Any] | None
    error: str | None
    warnings: list[str]
    node_statuses: dict[str, str]
```

调度规则必须精确实现：

1. 根节点直接运行。
2. 非根节点等待全部直接上游终态。
3. 来源节点为 `completed` 或 `warning` 且边条件成立，边才激活。
4. 至少一条入边激活则运行；全部不激活则跳过。
5. 一轮中所有就绪节点一起 `asyncio.create_task()`。
6. 普通节点失败时取消其余任务并等待取消完成；被取消的运行中节点持久化为 `cancelled`。
7. `get_task` 输出的 `task=None` 立即结束为 `no_task`。
8. HTML warning 继续调度并计入 warnings。
9. 最终输出只收集实际完成的末端节点；总结型额外包含产物 ID。

每个节点开始或跳过前，将全部入边求值结果写入 `condition_results_json`；记录字段包括 `edge_id`、`field`、`operator`、`expected`、`actual`、`matched`。

- [ ] **步骤 4：运行执行器测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_executor.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交任务 7**

```bash
git add src/agent_bridge/automation/workflows/executor.py tests/test_workflow_executor.py
git commit -m "feat(workflows): add lightweight DAG executor"
```

---

### 任务 8：接入服务、调度器和任务生命周期

**文件：**

- 修改：`src/agent_bridge/automation/workflows/service.py`
- 修改：`src/agent_bridge/automation/workflows/scheduler.py`
- 修改：`src/agent_bridge/app/service.py`
- 修改测试：`tests/test_workflow_runner.py`
- 修改测试：`tests/test_workflow_scheduler.py`
- 修改测试：`tests/test_workflow_html_report.py`

**接口：**

- `WorkflowService.upsert_definition(*, actor: str, workflow_key: str, name: str, description: str, profile_key: str, definition: dict[str, Any], status: str, workflow_type: str = "operation")` 保存前完成图和资源校验。
- `WorkflowScheduler.run_workflow_now(workflow_key: str, input_data: dict[str, Any] | None = None, actor: str | None = None)`。
- `WorkflowScheduler.run_one_workflow(workflow_key: str, run_id: str | None = None, input_data: dict[str, Any] | None = None, actor: str | None = None)` 调用 DAG 执行器。

- [ ] **步骤 1：把调度测试改写为新执行器契约并确认失败**

测试必须覆盖：调度窗口保持不变、手动输入进入运行快照、`no_task` 加入 `finished_today`、失败任务变为 `failed`、HTML warning 不改变主运行成功。

另加两条迁移保护测试：自动调度候选不包含 `definition_json IS NULL` 的历史工作流；手动运行历史工作流抛出“工作流需要通过新编辑器迁移”的校验错误。

```python
import time


def test_manual_run_passes_input_to_executor(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.upsert_project_profile(
        profile_key="report-plane", name="Report Plane", created_by="root"
    )
    service.workflows.upsert_definition(
        actor="root", workflow_key="manual-report", name="Manual Report",
        description="", profile_key="report-plane",
        definition={"nodes": [], "edges": []}, status="active",
    )
    started = service.workflow_scheduler.run_workflow_now(
        "manual-report", input_data={"topic": "release"}, actor="root"
    )
    deadline = time.time() + 5
    run = service.workflows.get_run("root", started["run_id"])
    while run["status"] == "running" and time.time() < deadline:
        time.sleep(0.02)
        run = service.workflows.get_run("root", started["run_id"])
    assert run["input"] == {"topic": "release"}
    assert run["status"] == "completed"
```

运行：

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_scheduler.py \
  tests/test_workflow_runner.py tests/test_workflow_html_report.py -q
```

预期：旧 Runner 断言或新参数断言失败。

- [ ] **步骤 2：在 `AgentBridgeService` 装配新执行链路**

装配顺序固定为：`WorkflowService`、`SkillService`、`ScriptService`、`WorkflowNodeHandlers`、`WorkflowDagExecutor`、`WorkflowScheduler`。将 `agent_service`、`skills`、`scripts` 和后端 registry 显式传入，移除后置属性写入。

- [ ] **步骤 3：在保存定义时校验外部资源**

`WorkflowService.upsert_definition()` 在写库前依次执行：

1. `WorkflowGraph.model_validate(definition)` 和 `validate_graph()`。
2. 查询 `profile_key`，不存在则产生 workflow 级错误。
3. 对每个 Agent/输出节点调用 `agent_service.coding_agents.get(backend_key)`，未知后端产生 node 级 `config.backend_key` 错误。
4. 对每个技能调用 `skills.get_skill(actor, skill_name)`，不存在产生 node 级 `config.skill_names` 错误。
5. 对脚本节点查询 `store.scripts.get_script(script_key)`，不存在或非 `active` 产生 node 级 `config.script_key` 错误。
6. 脚本 schema 中每个 required 字段都必须出现在节点 `config.params`；缺失时错误定位到 `config.params.<field>`。
7. 当多个脚本参数把同一个 `input.<path>` 映射为不同 schema 类型时，拒绝保存并报告冲突节点。
8. 输出路径不得以 `/` 开头，路径段不得包含 `..`；错误定位到 `config.path`。
9. 汇总全部问题后一次性抛 `WorkflowDefinitionValidationError`，不要遇到首个问题立即返回。

- [ ] **步骤 4：改造调度器**

`run_workflow_now()` 同步创建运行行时必须保存 `definition_snapshot` 和 `input_data`。后台线程只接收 `workflow_key`、`run_id`、输入和执行 actor。

`tick()` 只能调度 `definition_json IS NOT NULL` 的工作流。`run_workflow_now()` 在创建运行行之前检查 `workflow["definition"]`，为 `None` 时抛 `ValidationError("工作流需要通过新编辑器迁移")`。

手动运行由路由传入当前管理员 `actor`；自动调度使用 `sorted(self._admins)[0]` 作为系统执行 actor，并在构造器中拒绝空管理员集合。该 actor 只用于读取技能、运行托管脚本和记录审计信息。

```python
execution = asyncio.run(
    self._executor.run(
        workflow=workflow,
        run_id=run_id,
        input_data=input_data or {},
        actor=actor or sorted(self._admins)[0],
    )
)
```

收尾映射：

- `completed`：完成当前租约任务并保存 `output_json`。
- `no_task`：运行状态 `no_task` 并加入 `finished_today`。
- `failed`：调用 `fail_workflow_task_for_run()`，不调用 `release_or_abandon_tasks_for_run()`。
- HTML warning：运行仍为 `completed`，warning 已在节点记录和输出中保存。

- [ ] **步骤 5：运行调度和总结回归测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_scheduler.py \
  tests/test_workflow_runner.py tests/test_workflow_html_report.py -q
```

预期：全部通过；测试命名可以保留，但断言必须针对 DAG 执行器，不再执行 `workflow.js`。

- [ ] **步骤 6：提交任务 8**

```bash
git add src/agent_bridge/automation/workflows/service.py \
  src/agent_bridge/automation/workflows/scheduler.py \
  src/agent_bridge/app/service.py \
  tests/test_workflow_scheduler.py tests/test_workflow_runner.py \
  tests/test_workflow_html_report.py
git commit -m "refactor(workflows): run structured DAG workflows"
```

---

### 任务 9：更新工作流 API 和结构化错误

**文件：**

- 修改：`src/agent_bridge/api/schemas.py`
- 修改：`src/agent_bridge/api/routes/workflows.py`
- 修改：`src/agent_bridge/api/app.py`
- 修改测试：`tests/test_workflow_api.py`

**接口：**

- `WorkflowDefinitionRequest.definition: WorkflowGraph`。
- `WorkflowRunRequest.input: dict[str, Any]`。
- `GET /workflow-runs/{run_id}` 返回 `node_runs`。
- 校验错误返回 `{detail, errors}`。

- [ ] **步骤 1：先写 API 失败测试**

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workflow_api(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.upsert_project_profile(
        profile_key="report-plane", name="Report Plane", created_by="root"
    )
    return TestClient(create_app(wm_paths, {"root"})), {"X-Agent-Bridge-User": "root"}


def test_workflow_api_saves_structured_definition(workflow_api):
    client, headers = workflow_api
    response = client.post("/workflows", headers=headers, json={
        "workflow_key": "page-report",
        "name": "Page Report",
        "profile_key": "report-plane",
        "workflow_type": "operation",
        "status": "active",
        "definition": {"nodes": [], "edges": []},
    })
    assert response.status_code == 200
    assert response.json()["definition"] == {"nodes": [], "edges": []}
    assert "workflow_js" not in response.json()


def test_workflow_api_returns_locatable_validation_errors(workflow_api):
    client, headers = workflow_api
    payload = {
        "workflow_key": "invalid",
        "name": "Invalid",
        "profile_key": "report-plane",
        "workflow_type": "operation",
        "status": "active",
        "definition": {
            "nodes": [
                {"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0},
                 "config": {"prompt": "a", "backend_key": "claude"}}
            ],
            "edges": [{"id": "missing-target", "source": "a", "target": "no-such-node"}],
        },
    }
    response = client.post("/workflows", headers=headers, json=payload)
    assert response.status_code == 400
    assert response.json()["errors"][0] == {
        "scope": "edge",
        "id": "missing-target",
        "field": "target",
        "message": "目标节点不存在: no-such-node",
    }


def test_manual_workflow_run_accepts_input(workflow_api):
    client, headers = workflow_api
    created = client.post("/workflows", headers=headers, json={
        "workflow_key": "manual",
        "name": "Manual",
        "profile_key": "report-plane",
        "workflow_type": "operation",
        "status": "active",
        "definition": {"nodes": [], "edges": []},
    })
    assert created.status_code == 200
    response = client.post(
        "/workflows/manual/run", headers=headers, json={"input": {"topic": "release"}}
    )
    assert response.status_code == 200
```

- [ ] **步骤 2：运行 API 测试并确认旧 schema 失败**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_api.py -q
```

预期：失败信息指向 `definition` 未保存、`workflow_js` 仍存在或 run 请求体不接受。

- [ ] **步骤 3：实现请求模型和路由**

```python
class WorkflowDefinitionRequest(BaseModel):
    workflow_key: str
    name: str
    description: str = ""
    profile_key: str
    definition: WorkflowGraph
    status: str = "active"
    workflow_type: str = "operation"


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
```

运行路由调用：

```python
return service.workflow_scheduler.run_workflow_now(
    workflow_key,
    input_data=payload.input,
    actor=current_actor,
)
```

`GET /workflow-runs/{run_id}` 在 `WorkflowService.get_run()` 中合并 `list_workflow_node_runs(run_id)`，不要在路由层直接访问仓库。

`WorkflowService` 的列表、详情和保存返回统一通过 `_definition_payload()` 投影：保留解析后的 `definition`，删除 `definition_json` 和 `workflow_js`。历史工作流返回 `definition: null`。

`api/app.py` 对 `WorkflowDefinitionValidationError` 单独注册处理器：

```python
@app.exception_handler(WorkflowDefinitionValidationError)
async def _handle_workflow_validation_error(request, exc):
    return JSONResponse(
        status_code=400,
        content={
            "detail": exc.message,
            "errors": [asdict(issue) for issue in exc.issues],
        },
    )
```

- [ ] **步骤 4：运行 API 和设计 Agent 回归测试**

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_api.py tests/test_design_agent_api.py -q
```

预期：工作流 API 全部通过；设计 Agent API 可继续保留旧接口作为未展示的兼容入口，但新工作流页面不得调用它。

- [ ] **步骤 5：提交任务 9**

```bash
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/workflows.py \
  src/agent_bridge/api/app.py tests/test_workflow_api.py
git commit -m "feat(workflows): expose structured workflow API"
```

---

### 任务 10：建立前端工作流图类型和纯逻辑

**文件：**

- 修改：`frontend/capabilities/src/api/types.ts`
- 修改：`frontend/capabilities/src/api/client.ts`
- 新建：`frontend/capabilities/src/views/workflow/workflowDefinition.ts`
- 新建测试：`frontend/capabilities/tests/workflowDefinition.test.ts`

**接口：**

- 前端 `WorkflowGraph` 与后端字段同构。
- `createDefaultGraph(type, defaultBackend)` 生成空操作图或使用系统默认后端的固定总结输出对。
- `toVueFlowElements(graph)` 和 `fromVueFlowElements(nodes, edges)`。
- `isProtectedSummaryNode()`、`isProtectedSummaryEdge()`。
- `deriveManualInputFields(graph, scripts) -> ManualInputField[]`，只从脚本参数中的完整 `{{ input.path }}` 引用推导字段。

- [ ] **步骤 1：写默认总结图和保护规则测试**

```typescript
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createDefaultGraph,
  deriveManualInputFields,
  isProtectedSummaryEdge,
  isProtectedSummaryNode,
} from '../src/views/workflow/workflowDefinition.ts'

test('summary graph creates protected markdown and html pair', () => {
  const graph = createDefaultGraph('summary', 'codex')
  assert.deepEqual(graph.nodes.map(node => node.id), ['markdown-output', 'html-output'])
  assert.equal(isProtectedSummaryNode(graph.nodes[0], 'summary'), true)
  assert.equal(isProtectedSummaryEdge(graph.edges[0], 'summary'), true)
  assert.deepEqual(graph.nodes.map(node => node.config.backend_key), ['codex', 'codex'])
})

test('manual input fields are derived from selected script schemas', () => {
  const graph = graphWithScriptParams({
    repo: '{{ input.repo }}',
    limit: '{{ input.limit }}',
  })
  const scripts = [managedScript('collect', {
    type: 'object',
    additionalProperties: false,
    properties: {
      repo: { type: 'string' },
      limit: { type: 'integer' },
    },
    required: ['repo'],
  })]
  assert.deepEqual(deriveManualInputFields(graph, scripts), [
    { path: 'input.limit', type: 'integer', required: false, description: '' },
    { path: 'input.repo', type: 'string', required: true, description: '' },
  ])
})
```

`graphWithScriptParams()` 和 `managedScript()` 在测试文件中返回完整的 `WorkflowGraph` 和 `ManagedScript` 测试对象；推导结果按 `input.path` 排序，保证表单顺序稳定。

- [ ] **步骤 2：运行测试并确认模块缺失**

```bash
cd frontend/capabilities
node --test tests/workflowDefinition.test.ts
```

预期：失败，提示模块不存在。

- [ ] **步骤 3：实现类型和 API 客户端**

核心类型：

```typescript
export type WorkflowNodeType = 'get_task' | 'agent' | 'script' | 'output'
export type WorkflowType = 'operation' | 'summary'
export type ConditionOperator = 'equals' | 'not_equals' | 'exists' | 'not_exists' | 'contains'

export interface WorkflowGraph {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface WorkflowDefinition {
  workflow_key: string
  name: string
  description: string
  profile_key: string
  definition: WorkflowGraph | null
  status: string
  workflow_type: WorkflowType
  created_by: string
  created_at: string
  updated_at: string
}

export interface WorkflowNodeRun {
  node_id: string
  node_type: WorkflowNodeType
  status: 'pending' | 'running' | 'completed' | 'skipped' | 'failed' | 'cancelled' | 'warning'
  condition_results: Array<{
    edge_id: string
    field: string | null
    operator: ConditionOperator | null
    expected: unknown
    actual: unknown
    matched: boolean
  }>
  output: Record<string, unknown>
  error: string | null
  agent_run_key: string | null
  script_run_id: string | null
  started_at: string | null
  finished_at: string | null
}
```

客户端签名：

```typescript
runWorkflow: (key: string, input: Record<string, unknown> = {}) =>
  post<{ status: string; run_id?: string }>(`/workflows/${key}/run`, { input })
```

- [ ] **步骤 4：实现纯图逻辑并运行测试、类型检查**

```bash
cd frontend/capabilities
node --test tests/workflowDefinition.test.ts
npm run typecheck
```

预期：测试和类型检查通过。

- [ ] **步骤 5：提交任务 10**

```bash
git add frontend/capabilities/src/api/types.ts \
  frontend/capabilities/src/api/client.ts \
  frontend/capabilities/src/views/workflow/workflowDefinition.ts \
  frontend/capabilities/tests/workflowDefinition.test.ts
git commit -m "feat(workflows): add frontend graph model"
```

---

### 任务 11：实现 Vue Flow 编辑器和配置面板

**文件：**

- 新建：`frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue`
- 新建：`frontend/capabilities/src/views/workflow/WorkflowNodePalette.vue`
- 新建：`frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`
- 新建：`frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue`
- 修改：`frontend/capabilities/src/views/workflow/WorkflowView.vue`
- 修改测试：`tests/test_capability_api.py`

**接口：**

- `WorkflowEditorCanvas` 使用 `v-model:graph`，发出 `select-node`、`select-edge`。
- 配置面板只编辑传入节点/边并发出完整替换对象，不直接调用 API。
- `WorkflowView` 是保存和运行的唯一编排层。

- [ ] **步骤 1：写页面结构失败测试**

```python
def test_workflow_editor_uses_structured_canvas_and_no_inline_code():
    view = Path("frontend/capabilities/src/views/workflow/WorkflowView.vue").read_text()
    canvas = Path("frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue").read_text()
    assert "WorkflowEditorCanvas" in view
    assert "WorkflowNodeConfigPanel" in view
    assert "workflow_js" not in view
    assert "parseWorkflowDag" not in view
    assert "WorkflowDagGraph" not in view
    assert "@vue-flow/core" in canvas
```

- [ ] **步骤 2：运行测试并确认组件缺失**

```bash
PYTHONPATH=. uv run pytest tests/test_capability_api.py::test_workflow_editor_uses_structured_canvas_and_no_inline_code -q
```

预期：失败，提示组件文件不存在或断言不成立。

- [ ] **步骤 3：实现节点栏和画布**

节点栏固定四项，不提供搜索和插件入口。画布必须：

- 支持拖入节点、连线、选择、删除和适应视图。
- 节点尺寸稳定，显示名称、类型、后端或脚本标识。
- 禁止删除总结型强制节点和固定连线。
- 删除普通节点时同步删除关联边。
- 不在节点内部放长提示词或功能说明。

`WorkflowEditorCanvas` 的公开接口：

```typescript
const graph = defineModel<WorkflowGraph>('graph', { required: true })
const props = defineProps<{ workflowType: WorkflowType; errors: WorkflowValidationError[] }>()
const emit = defineEmits<{
  selectNode: [nodeId: string]
  selectEdge: [edgeId: string]
}>()
```

- [ ] **步骤 4：实现节点和边配置面板**

Agent 配置必须包含：提示词、后端三选一、Profile MCP 开关、技能有序列表、文本/JSON 输出模式和 JSON Schema 编辑区。技能选项只来自 `api.listSkills()`；选择后可用上移、下移和删除图标调整注入顺序，不允许手输技能名。

脚本配置必须从 `api.listScripts()` 返回的启用脚本中选择，不允许手输脚本标识。选中后根据脚本 `input_schema.properties` 生成参数映射行，根据 `required` 标记必填字段，并允许编辑超时。

输出配置必须包含格式只读、标题、路径、标签、提示词、后端、MCP 和技能；总结型格式不可改。

边配置只显示五个操作符；`exists/not_exists` 时隐藏 value。

- [ ] **步骤 5：接入 `WorkflowView` 保存和测试运行**

- 新建/编辑表单使用 `definition`，移除 `workflow_js` 文本框、静态解析图和工作流设计 Agent 按钮。
- 新建总结型工作流时从现有 Agent Runtime 配置读取 `default_backend`，并传给 `createDefaultGraph('summary', defaultBackend)`。
- 历史工作流 `definition=null` 时在详情和编辑页显示“需要迁移”；进入编辑页后按工作流类型创建默认图，只有用户显式保存后才写入新定义。
- dirty 状态沿用现有逻辑。
- 有未保存修改时点击测试运行，先提示保存。
- 有 `get_task` 节点时直接使用任务队列，不显示手动输入表单。
- 无 `get_task` 节点时调用 `deriveManualInputFields()` 展示可推导字段，并提供“高级 JSON”区域补充无法推导的输入；同一路径以字段表单值覆盖高级 JSON 值。JSON 解析失败或必填字段为空时不发请求。
- 后端 `{errors}` 映射到画布节点或边。

- [ ] **步骤 6：运行前端类型检查、构建和页面结构测试**

```bash
cd frontend/capabilities
npm run typecheck
npm run build
cd ../..
PYTHONPATH=. uv run pytest tests/test_capability_api.py -q
```

预期：全部通过。

- [ ] **步骤 7：提交任务 11**

```bash
git add frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue \
  frontend/capabilities/src/views/workflow/WorkflowNodePalette.vue \
  frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue \
  frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue \
  frontend/capabilities/src/views/workflow/WorkflowView.vue \
  tests/test_capability_api.py
git commit -m "feat(workflows): add lightweight visual editor"
```

---

### 任务 12：在运行详情中显示 DAG 节点状态

**文件：**

- 新建：`frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue`
- 修改：`frontend/capabilities/src/views/workflow/WorkflowView.vue`
- 修改：`frontend/capabilities/src/api/types.ts`
- 修改测试：`tests/test_capability_api.py`

**接口：**

- `WorkflowRunGraph` 接收 `definitionSnapshot` 和 `nodeRuns`。
- 点击 Agent 节点使用 `agent_run_key` 打开现有 Agent Run 详情。
- 点击脚本节点使用 `script_run_id` 打开现有脚本运行详情。

- [ ] **步骤 1：写状态映射页面测试**

```python
def test_workflow_run_graph_links_agent_and_script_details():
    source = Path("frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue").read_text()
    assert "agent_run_key" in source
    assert "script_run_id" in source
    assert "warning" in source
    assert "skipped" in source
    assert "cancelled" in source
```

- [ ] **步骤 2：运行测试并确认组件不存在**

```bash
PYTHONPATH=. uv run pytest tests/test_capability_api.py::test_workflow_run_graph_links_agent_and_script_details -q
```

预期：失败，提示文件不存在。

- [ ] **步骤 3：实现只读运行图**

状态颜色必须复用现有语义：等待为中性灰、运行中为蓝、完成为绿、跳过为浅灰、失败为红、已取消为深灰、警告为黄色。条件边显示实际值和匹配结果；不要让状态文字改变节点尺寸。

组件公开接口：

```typescript
const props = defineProps<{
  definitionSnapshot: WorkflowGraph
  nodeRuns: WorkflowNodeRun[]
}>()

const emit = defineEmits<{
  openAgentRun: [runKey: string]
  openScriptRun: [runId: string]
}>()
```

- [ ] **步骤 4：接入运行进度页和轮询**

继续沿用现有 workflow run 轮询；每次 `getWorkflowRun()` 返回节点列表后更新图。终态为 `completed/no_task/failed/stopped` 时停止轮询，HTML warning 不视为运行失败。

- [ ] **步骤 5：运行前端构建和页面测试**

```bash
cd frontend/capabilities
npm run typecheck
npm run build
cd ../..
PYTHONPATH=. uv run pytest tests/test_capability_api.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交任务 12**

```bash
git add frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue \
  frontend/capabilities/src/views/workflow/WorkflowView.vue \
  frontend/capabilities/src/api/types.ts tests/test_capability_api.py
git commit -m "feat(workflows): show DAG node run status"
```

---

### 任务 13：移除新主路径中的 Claude 工作流兼容代码并完成端到端回归

**文件：**

- 删除：`src/agent_bridge/automation/workflows/runner.py`
- 删除：`src/agent_bridge/automation/workflows/result_parser.py`
- 删除：`src/agent_bridge/automation/workflows/reporter.py`
- 删除：`frontend/capabilities/src/views/workflow/workflowDag.ts`
- 删除：`frontend/capabilities/src/views/workflow/WorkflowDagGraph.vue`
- 删除：`tests/test_workflow_runner.py`，其有效执行断言已迁入 `tests/test_workflow_executor.py` 和 `tests/test_workflow_scheduler.py`
- 删除：`tests/test_workflow_result_parser.py`，其产物结构断言已迁入 `tests/test_workflow_handlers.py`
- 修改：`tests/test_workflow_html_report.py`，只保留 Markdown 必需、HTML 警告和产物历史的集成断言
- 修改：`docs/multi-agent-adapter-research.md`
- 修改：`docs/TODO.md`

**接口：**

- 主运行路径中不得出现 `ClaudeWorkflowRunner`、`WORKFLOW_PROMPT`、`workflow.js` 执行或调度后 HTML reporter。
- 历史数据库中的 `workflow_js` 字段仍保留，不再执行。

- [ ] **步骤 1：写主路径不再引用旧 Runner 的保护测试**

```python
def test_scheduler_no_longer_uses_claude_workflow_runner():
    scheduler = Path("src/agent_bridge/automation/workflows/scheduler.py").read_text()
    service = Path("src/agent_bridge/app/service.py").read_text()
    assert "ClaudeWorkflowRunner" not in scheduler
    assert "ClaudeWorkflowRunner" not in service
    assert "workflow.js" not in scheduler
```

- [ ] **步骤 2：删除无引用代码和过时前端解析器**

先运行：

```bash
rg -n "ClaudeWorkflowRunner|parse_workflow_result|build_report_prompt|parseWorkflowDag|WorkflowDagGraph" src frontend tests
```

只删除已经没有生产引用的文件。测试需要保留的行为必须迁移到定义、处理器、执行器或调度器测试，不能简单丢弃断言。

- [ ] **步骤 3：记录日志聚合待办并更新研究结论**

在 `docs/multi-agent-adapter-research.md` 增加结论：未采用 Archon/Node-RED；采用 Vue Flow + 结构化 JSON + 自研轻量执行器；列出四类节点和明确不做范围。不要复制完整 spec，只链接到设计文档。

在 `docs/TODO.md` 增加未排期项：“工作流执行日志聚合：一个 workflow run 作为顶层记录，内部关联多个 Agent Run；普通 Agent Run 继续独立展示。第一版先由 workflow_node_runs 保存关联关系。”本计划不得实现新的聚合表或日志页面。

- [ ] **步骤 4：运行后端完整本地测试基线**

```bash
PYTHONPATH=. uv run pytest -q -m 'not ragflow and not weknora'
```

预期：全部通过；基线参考为 `795 passed, 8 deselected`，实际通过数应因新增测试而增加。

- [ ] **步骤 5：运行前端全部纯逻辑测试、类型检查和构建**

```bash
cd frontend/capabilities
node --test tests/*.test.ts
npm run typecheck
npm run build
```

预期：全部通过。

- [ ] **步骤 6：启动本地版本并做浏览器验收**

使用仓库既有启动命令启动 Agent Bridge，验证桌面宽度下：

1. 创建操作型工作流，添加 Agent 和脚本节点，保存并手动运行。
2. 创建总结型工作流，确认 Markdown/HTML 节点和固定连线不可删除。
3. 配置一条条件边，验证未命中分支显示跳过。
4. 验证 Agent 节点能选择 Claude、OpenCode、Codex。
5. 验证脚本节点只能选择启用脚本。
6. 验证运行图实时显示节点状态，详情链接可打开。
7. 模拟 HTML 输出失败，确认 Markdown 和任务仍成功且 HTML 节点为警告。
8. 检查节点、工具栏和右侧面板没有文字溢出或重叠。

- [ ] **步骤 7：提交任务 13**

```bash
git add -A src/agent_bridge/automation/workflows \
  frontend/capabilities/src/views/workflow tests \
  docs/multi-agent-adapter-research.md docs/TODO.md
git commit -m "refactor(workflows): retire Claude-only workflow runtime"
```

---

## 最终交付检查

- [ ] `git status --short` 为空。
- [ ] 后端本地测试（排除 RagFlow/WeKnora 外部集成标记）全部通过。
- [ ] 前端 Node 测试、类型检查和生产构建全部通过。
- [ ] 新工作流 API 不再返回 `workflow_js`。
- [ ] 全局调度器和手动测试都进入同一个 `WorkflowDagExecutor`。
- [ ] 运行详情可以定位到每个 Agent Run 和 Script Run。
- [ ] 托管脚本保存 input schema，脚本管理页和工作流节点均从 schema 生成参数字段，运行前校验参数。
- [ ] 总结型 Markdown 必需、HTML 最佳努力语义通过自动测试和浏览器验证。
- [ ] 工作流日志聚合只记录到 `docs/TODO.md`，本阶段没有新增聚合实现。
- [ ] 没有新增循环、审批、自动重试、版本、cron、内联代码或动态节点机制。
