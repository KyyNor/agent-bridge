# 工作流编写体验与统一校验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为结构化 DAG 工作流补齐统一校验、built-in scripts、前置数据提示、Schema 字段编辑器和覆盖式配置抽屉，并按新契约重写 `design_workflow` skill。

**Architecture:** 后端新增 `WorkflowValidator` 作为解析、图规则和资源规则的唯一入口，保存、执行前复检、校验 API 与 `system.validate_workflow` 都调用它。脚本管理沿用 built-in skills 的默认文件加数据库覆盖模型，并为脚本增加可选输出 Schema。前端用共享数据推导器、引用选择器和 Schema 字段编辑器增强工作流抽屉，保留高级 JSON 作为复杂结构的兜底。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、`jsonschema`、pytest、Vue 3、TypeScript、Vue Flow、Vitest/Node test、Tailwind utility classes。

## Global Constraints

- 工作流继续使用结构化 `definition` JSON DAG；不执行 `workflow_js`。
- 保存、测试运行、正式执行、校验 API 和内置校验脚本必须共用 `WorkflowValidator`。
- 校验结果必须带稳定的 `scope`、`id`、`field`、`code`、`message`；前端不得解析中文错误文案。
- `system.validate_workflow` 只做薄脚本适配，不复制校验规则。
- 内置脚本固定 key、归属、名称和状态，只允许数据库覆盖或恢复默认，不允许删除或停用。
- 普通脚本没有 `output_schema` 时保持可运行，但下游引用只能展示输出根路径。
- 引用提示只展示当前节点的祖先数据；连线条件只展示来源节点及其祖先。
- Schema 字段列表只覆盖顶层 object 和六种基础类型；复杂 Schema 保留高级 JSON。
- 配置抽屉默认覆盖 DAG 右侧，支持扩展为覆盖整个 DAG 编辑区域；页面级保存和测试操作保持可见。
- 每个逻辑任务必须有后端测试或前端测试；最后运行现有相关测试、前端类型检查和构建。

---

## 文件与职责地图

后端：

- Create `src/agent_bridge/automation/workflows/validator.py`: 统一工作流解析、图规则、资源规则和结构化结果。
- Modify `src/agent_bridge/automation/workflows/validation.py`: 保留纯图规则兼容入口，补充稳定错误代码或让其由 validator 转换。
- Modify `src/agent_bridge/automation/workflows/service.py`: 用 `WorkflowValidator` 替代保存路径中的私有资源校验。
- Modify `src/agent_bridge/automation/workflows/executor.py`: 执行前接收并调用统一 validator，避免只做图结构校验。
- Modify `src/agent_bridge/automation/workflows/scheduler.py`: 在创建 run 之前校验保存的定义和当前资源。
- Modify `src/agent_bridge/app/service.py`: 装配 validator，并把它注入 WorkflowService、executor 和 scheduler。
- Modify `src/agent_bridge/api/routes/workflows.py`: 增加 `POST /workflows/validate`，确保路由位于动态 workflow key 路由之前。
- Modify `src/agent_bridge/api/schemas.py`: 扩展脚本请求的 `output_schema`，增加工作流校验请求模型。
- Modify `src/agent_bridge/capability_hub/sources/builtin/platform.py`: 增加 `validate_workflow` built-in tool。
- Modify `src/agent_bridge/system_config/scripts/service.py`: 增加内置脚本注册表、覆盖/恢复默认、输出 Schema 校验和资源合并读取。
- Create `src/agent_bridge/system_config/scripts/defaults/system_validate_workflow.py`: 内置校验脚本薄适配。
- Modify `src/agent_bridge/storage/schema.py`: 为 scripts 表增加 `output_schema_json`。
- Modify `src/agent_bridge/storage/sqlite.py`: 兼容已有数据库的列迁移和新表初始化。
- Modify `src/agent_bridge/storage/repositories/scripts.py`: 读写 output Schema，并支持内置覆盖查询。

前端：

- Modify `frontend/capabilities/src/api/types.ts`: 增加脚本输出 Schema、source/built-in 字段和 validation code 类型。
- Modify `frontend/capabilities/src/api/client.ts`: 增加工作流校验、脚本恢复默认和扩展脚本保存 payload。
- Create `frontend/capabilities/src/lib/schemaFields.ts`: Schema 字段列表、兼容性检测和转换纯函数。
- Create `frontend/capabilities/src/components/SchemaFieldEditor.vue`: 共享字段列表/高级 JSON 编辑器。
- Modify `frontend/capabilities/src/views/system/ScriptsView.vue`: 使用共享 Schema 编辑器维护输入/输出 Schema，并处理内置脚本恢复默认。
- Create `frontend/capabilities/src/lib/workflowReferences.ts`: 祖先节点与输出契约推导。
- Create `frontend/capabilities/src/components/workflow/WorkflowReferencePicker.vue`: 搜索、展示和插入可用引用。
- Modify `frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`: 接入抽屉内配置、引用选择器和 Schema 编辑器。
- Modify `frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue`: 接入连线引用选择器和结构化错误定位。
- Create `frontend/capabilities/src/views/workflow/WorkflowConfigDrawer.vue`: 右侧覆盖抽屉与全屏配置容器。
- Modify `frontend/capabilities/src/views/workflow/WorkflowView.vue`: 替换固定第三列、管理抽屉状态、草稿校验和错误定位。
- Modify `frontend/capabilities/src/views/workflow/workflowDefinition.ts`: 扩展手动输入推导和节点输出契约。
- Modify `frontend/capabilities/src/components/ui/input/Input.vue` 和 `Textarea.vue`: 暴露焦点、选择区间和插入文本所需的轻量 ref API。

测试：

- Modify `tests/test_workflow_definition.py`、`tests/test_workflow_service.py`、`tests/test_workflow_executor.py`、`tests/test_workflow_api.py`、`tests/test_scripts.py`。
- Create `tests/test_workflow_validator.py`：统一 validator 的解析、图、资源和稳定错误代码。
- Create/modify `frontend/capabilities/tests/workflowReferences.test.ts`、`schemaFields.test.ts`、`workflowConfigDrawer.test.ts`、`workflowDefinition.test.ts`。

---

### Task 1: 建立统一 WorkflowValidator

**Files:**
- Create: `src/agent_bridge/automation/workflows/validator.py`
- Modify: `src/agent_bridge/automation/workflows/validation.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `src/agent_bridge/app/service.py`
- Test: `tests/test_workflow_validator.py`

**Interfaces:**
- Consumes: `WorkflowDefinitionRequest` 形状的 dict、当前 actor、`WorkflowGraph`、AgentService、SkillService、ScriptService。
- Produces: `WorkflowValidationResult` 和 `WorkflowValidationIssue`，供保存、执行、API 和 built-in tool 使用。

- [ ] **Step 1: 写失败测试，固定 validator 的公开结果结构。**

```python
def test_validator_returns_stable_code_for_invalid_ancestor_reference(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    result = service.workflows.validator.validate(
        actor="root",
        workflow={
            "workflow_key": "bad-ref",
            "profile_key": "default",
            "workflow_type": "operation",
            "definition": {
                "nodes": [
                    {"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0}, "config": {"prompt": "", "backend_key": "codex"}},
                    {"id": "b", "type": "agent", "name": "B", "position": {"x": 1, "y": 0}, "config": {"prompt": "{{ nodes.c.output.text }}", "backend_key": "codex"}},
                ],
                "edges": [{"id": "a-b", "source": "a", "target": "b", "condition": None}],
            },
        },
    )
    assert result.valid is False
    assert result.errors[0].code == "invalid_reference"
    assert result.errors[0].scope == "node"
    assert result.errors[0].id == "b"
```

- [ ] **Step 2: 运行失败测试，确认统一入口尚不存在。**

Run: `PYTHONPATH=. uv run pytest tests/test_workflow_validator.py::test_validator_returns_stable_code_for_invalid_ancestor_reference -q`

Expected: FAIL because `WorkflowService.validator` or `WorkflowValidationResult` is not implemented.

- [ ] **Step 3: 实现结果模型和 validator 分层。**

在 `validator.py` 中定义：

```python
@dataclass(frozen=True)
class WorkflowValidationIssue:
    scope: Literal["workflow", "node", "edge"]
    id: str | None
    field: str | None
    code: str
    message: str

@dataclass(frozen=True)
class WorkflowValidationResult:
    valid: bool
    errors: list[WorkflowValidationIssue]
    warnings: list[WorkflowValidationIssue]

class WorkflowValidator:
    def validate(self, *, actor: str, workflow: dict[str, Any]) -> WorkflowValidationResult:
        raise NotImplementedError

    def require_valid(self, *, actor: str, workflow: dict[str, Any]) -> WorkflowGraph:
        raise NotImplementedError
```

`validate` 依次完成 Pydantic 解析、调用现有 `collect_graph_issues`、Profile/后端/技能/脚本资源校验，并把旧 issue 转换为稳定 `code`。`require_valid` 在有错误时抛出包含完整 issues 的 `WorkflowDefinitionValidationError`。

- [ ] **Step 4: 将 WorkflowService 保存流程切换到 validator。**

保留 `upsert_definition` 的历史 `workflow_js` 兼容参数，但只把 `definition` 送入 validator；删除或停止使用 `_resource_issues` 的保存路径。将 validator 作为 `self.validator` 暴露给其他装配对象。

- [ ] **Step 5: 运行 validator 和现有保存测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_workflow_validator.py tests/test_workflow_definition.py tests/test_workflow_service.py -q`

Expected: 新测试和已有保存/图规则测试全部 PASS。

- [ ] **Step 6: Commit。**

```bash
git add src/agent_bridge/automation/workflows/validator.py src/agent_bridge/automation/workflows/validation.py src/agent_bridge/automation/workflows/service.py src/agent_bridge/app/service.py tests/test_workflow_validator.py tests/test_workflow_definition.py tests/test_workflow_service.py
git commit -m "refactor(workflows): unify definition validation"
```

### Task 2: 扩展脚本契约与 SQLite 存储

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/repositories/scripts.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/system_config/scripts/service.py`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: 现有 `scripts` 表和 `Draft202012Validator`。
- Produces: `ManagedScript.output_schema: dict[str, Any] | None`，运行时对声明的输出进行 JSON Schema 校验。

- [ ] **Step 1: 写失败测试，验证 output Schema 存储、返回值校验和历史兼容。**

```python
def test_script_output_schema_round_trip_and_validation(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    output_schema = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array"}}}
    service.scripts.upsert_script(
        actor="root", script_key="schema.output", name="Output", description="", language="python",
        code="def main(envelope):\n    return {'items': []}\n", input_schema=PERMISSIVE_INPUT_SCHEMA,
        output_schema=output_schema, status="active", owner_type="system", owner_key="",
    )
    script = service.scripts.get_script("root", "schema.output")
    assert script["output_schema"] == output_schema

    service.scripts.upsert_script(
        actor="root", script_key="schema.bad-output", name="Bad", description="", language="python",
        code="def main(envelope):\n    return {'items': 'bad'}\n", input_schema=PERMISSIVE_INPUT_SCHEMA,
        output_schema=output_schema, status="active", owner_type="system", owner_key="",
    )
    with pytest.raises(ValidationError, match="output_schema"):
        service.scripts.test_script(actor="root", script_key="schema.bad-output", script_params={}, timeout_seconds=10)
```

- [ ] **Step 2: 运行测试确认当前 API 不接受 output Schema。**

Run: `PYTHONPATH=. uv run pytest tests/test_scripts.py::test_script_output_schema_round_trip_and_validation -q`

Expected: FAIL because the request/service/repository chain has no `output_schema` field.

- [ ] **Step 3: 添加数据库列和兼容迁移。**

在 `schema.py` 与 `sqlite.py` 的 scripts 建表定义中加入：

```sql
output_schema_json TEXT
```

在 `SQLiteStore.init_schema()` 中对旧表执行一次 `PRAGMA table_info(scripts)` 检查，缺失时执行 `ALTER TABLE scripts ADD COLUMN output_schema_json TEXT`。Repository 的 insert/update/select/payload 统一把空值映射为 `None`。

- [ ] **Step 4: 扩展 API 和 ScriptService 的 schema 校验。**

给 `ScriptRequest` 与 `ScriptService.upsert_script` 增加 `output_schema: dict[str, Any] | None = None`。复用 `_validate_input_schema` 的 Draft 2020-12 检查，但错误前缀区分 `input_schema` 和 `output_schema`。运行脚本成功解析 JSON 对象后，若存在 output Schema，使用 `Draft202012Validator(output_schema).iter_errors(result)` 校验并以 `ValidationError` 结束运行；运行记录必须保存失败状态和首个错误。

- [ ] **Step 5: 运行脚本测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_scripts.py -q`

Expected: 全部 PASS，历史没有 output Schema 的脚本仍能成功运行。

- [ ] **Step 6: Commit。**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/scripts.py src/agent_bridge/api/schemas.py src/agent_bridge/system_config/scripts/service.py tests/test_scripts.py
git commit -m "feat(scripts): add optional output schemas"
```

### Task 3: 实现 built-in scripts 与工作流校验入口

**Files:**
- Modify: `src/agent_bridge/system_config/scripts/service.py`
- Create: `src/agent_bridge/system_config/scripts/defaults/system_validate_workflow.py`
- Modify: `src/agent_bridge/capability_hub/sources/builtin/platform.py`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/app/service.py`
- Test: `tests/test_scripts.py`
- Test: `tests/test_workflow_api.py`

**Interfaces:**
- Consumes: `WorkflowValidator.validate()`、`ScriptService.run_script()`、脚本运行时的 `execute()`。
- Produces: `system.validate_workflow`、`built-in.validate_workflow`、`POST /workflows/validate`。

- [ ] **Step 1: 写失败测试，覆盖内置脚本默认、覆盖、恢复和校验工具。**

```python
def test_builtin_workflow_validator_script_can_be_overridden_and_reset(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    default = service.scripts.get_script("root", "system.validate_workflow")
    assert default["source"] == "default"
    assert default["status"] == "active"
    with pytest.raises(ValidationError, match="cannot delete built-in"):
        service.scripts.delete_script("root", "system.validate_workflow")

    service.scripts.upsert_script(
        actor="root", script_key="system.validate_workflow", name="ignored", description="ignored",
        language="python", code=default["code"], input_schema=default["input_schema"],
        output_schema=default["output_schema"], status="active", owner_type="system", owner_key="",
    )
    assert service.scripts.get_script("root", "system.validate_workflow")["source"] == "database"
    restored = service.scripts.reset_script("root", "system.validate_workflow")
    assert restored["source"] == "default"

def test_validate_workflow_endpoint_returns_structured_result(client):
    response = client.post("/workflows/validate", json={"workflow": {"workflow_type": "operation", "definition": {"nodes": [], "edges": []}}})
    assert response.status_code == 200
    assert response.json() == {"valid": True, "errors": [], "warnings": []}
```

- [ ] **Step 2: 运行测试确认 built-in registry 和入口尚不存在。**

Run: `PYTHONPATH=. uv run pytest tests/test_scripts.py::test_builtin_workflow_validator_script_can_be_overridden_and_reset tests/test_workflow_api.py::test_validate_workflow_endpoint_returns_structured_result -q`

Expected: FAIL because the built-in script, reset method和校验 route 尚未实现。

- [ ] **Step 3: 添加 built-in script 定义和默认文件加载。**

在 `ScriptService` 中增加 `BuiltInScriptDefinition`，注册 `system.validate_workflow` 的默认路径、固定描述、输入 Schema 和输出 Schema。`list_scripts`/`get_script` 先读取数据库覆盖，没有覆盖时从默认文件构造 payload；覆盖保存时禁止修改 key/name/owner/status；新增：

```python
def reset_script(self, actor: str, script_key: str) -> dict[str, Any]:
    raise NotImplementedError
```

`delete_script` 对内置 key 抛出 `ValidationError("cannot delete built-in script")`。普通脚本逻辑不改变。`_require_script` 和运行路径必须能够直接执行默认文件内容，无需先写入 SQLite。

- [ ] **Step 4: 写默认校验脚本并增加 built-in capability。**

默认脚本的核心实现保持薄：

```python
from agent_bridge_runtime import execute

def main(envelope):
    response = execute("built-in", "validate_workflow", {"workflow": envelope["script_params"]["workflow"]})
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        raise RuntimeError("validate_workflow returned an invalid response")
    return response["result"]
```

在 `PlatformBuiltinProvider.list_tools` 增加 `validate_workflow` 的 object 输入 Schema，执行时调用 `self.service.workflows.validator.validate(actor=actor, workflow=arguments["workflow"])` 并返回 `model_dump`/dataclass payload。`run_script` 仍可通过 HTTP 运行 `system.validate_workflow`。

- [ ] **Step 5: 增加校验 API。**

在 `api/schemas.py` 增加：

```python
class WorkflowValidationRequest(BaseModel):
    workflow: dict[str, Any]
```

在 `workflows.py` 中把 `POST /workflows/validate` 放在 `/workflows/{workflow_key}` 之前，调用 `service.workflows.validator.validate(actor=current_actor, workflow=payload.workflow)`，返回统一 JSON。该端点只校验，不保存、不创建运行记录。

- [ ] **Step 6: 运行后端入口测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_scripts.py tests/test_workflow_api.py -q`

Expected: 内置脚本、built-in tool、恢复默认和校验 endpoint 全部 PASS。

- [ ] **Step 7: Commit。**

```bash
git add src/agent_bridge/system_config/scripts/service.py src/agent_bridge/system_config/scripts/defaults/system_validate_workflow.py src/agent_bridge/capability_hub/sources/builtin/platform.py src/agent_bridge/api/routes/workflows.py src/agent_bridge/api/schemas.py src/agent_bridge/app/service.py tests/test_scripts.py tests/test_workflow_api.py
git commit -m "feat(workflows): add built-in validation script"
```

### Task 4: 让执行前复检与保存共用 validator

**Files:**
- Modify: `src/agent_bridge/automation/workflows/executor.py`
- Modify: `src/agent_bridge/automation/workflows/scheduler.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `tests/test_workflow_executor.py`
- Modify: `tests/test_workflow_service.py`

**Interfaces:**
- Consumes: Task 1 的 `WorkflowValidator.require_valid()`。
- Produces: 保存后资源失效时，正式/手动运行在创建或启动执行前得到一致 validation issue。

- [ ] **Step 1: 写失败测试，模拟脚本保存后被停用。**

```python
def test_run_workflow_revalidates_disabled_script_before_execution(wm_paths):
    service = make_service_with_valid_script_workflow(wm_paths)
    script = service.scripts.get_script("root", "workflow.collect")
    service.scripts.upsert_script(
        actor="root", script_key=script["script_key"], name=script["name"], description=script["description"],
        language=script["language"], code=script["code"], input_schema=script["input_schema"],
        output_schema=script.get("output_schema"), status="disabled", owner_type=script["owner_type"], owner_key=script["owner_key"],
    )
    with pytest.raises(WorkflowDefinitionValidationError) as exc_info:
        service.workflow_scheduler.run_workflow_now("workflow-key", actor="root")
    assert any(issue.code == "script_unavailable" for issue in exc_info.value.issues)
```

- [ ] **Step 2: 运行失败测试确认 scheduler 只依赖保存时状态。**

Run: `PYTHONPATH=. uv run pytest tests/test_workflow_executor.py::test_run_workflow_revalidates_disabled_script_before_execution -q`

Expected: FAIL because scheduler/executor当前没有资源复检。

- [ ] **Step 3: 在 scheduler 创建 run 前调用 validator。**

`run_workflow_now` 和后台 `run_one_workflow` 读取 definition 后调用 `require_valid(actor=actor or sorted(self._admins)[0], workflow=workflow_payload)`。校验失败时不创建新的 run，也不启动线程；后台调度将错误写入已有调度日志并返回 failed 状态，手动调用保留结构化异常响应。

- [ ] **Step 4: 移除 executor 的重复图校验入口。**

executor 接收 scheduler 已经复检的 workflow，但仍保留 Pydantic 转换作为防御性解析；把 `validate_graph` 替换为注入的 validator 或明确只在直接单元调用时执行结构校验，确保正式路径不会出现不同资源规则。更新 `AgentBridgeService` 装配，使 scheduler 和 executor 都持有同一个 validator 实例。

- [ ] **Step 5: 运行工作流全套后端测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_workflow_executor.py tests/test_workflow_service.py tests/test_workflow_api.py tests/test_workflow_scheduler_review.py -q`

Expected: 所有既有 DAG、手动运行、调度和 API 测试 PASS，且新增资源失效复检测试 PASS。

- [ ] **Step 6: Commit。**

```bash
git add src/agent_bridge/automation/workflows/executor.py src/agent_bridge/automation/workflows/scheduler.py src/agent_bridge/app/service.py tests/test_workflow_executor.py tests/test_workflow_service.py
git commit -m "fix(workflows): revalidate resources before execution"
```

### Task 5: 重写 design_workflow skill

**Files:**
- Modify: `src/agent_bridge/system_config/skills/defaults/design_workflow.md`
- Modify: `src/agent_bridge/system_config/skills/service.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: 新的 `definition` DAG 契约和 `system.validate_workflow` 调用方式。
- Produces: 面向 Agent 的中文设计指南，要求生成后运行校验脚本并根据 `code` 修正。

- [ ] **Step 1: 写 skill 内容断言测试。**

```python
def test_design_workflow_skill_mentions_structured_validation_and_omits_history_list(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    prompt = service.skills.get_skill("root", "design_workflow")["prompt"]
    assert "system.validate_workflow" in prompt
    assert "definition" in prompt
    assert "workflow_js" not in prompt
    assert "第一版明确不做" not in prompt
```

- [ ] **Step 2: 运行测试确认旧 skill 仍包含历史内容。**

Run: `PYTHONPATH=. uv run pytest tests/test_skills.py::test_design_workflow_skill_mentions_structured_validation_and_omits_history_list -q`

Expected: FAIL because当前默认 skill仍有 `workflow_js`/“第一版明确不做”内容。

- [ ] **Step 3: 重写 skill。**

保留当前四种节点、引用、条件边、summary Markdown→HTML 和 JSON 输出契约，但组织为“输入信息 → 选择节点 → 设计数据契约 → 生成 JSON → 调用校验脚本 → 修正并交付”。要求输出无 Markdown fence 的完整 JSON；使用真实后端、技能和脚本 key；引用只来自 `input`、`task` 和祖先节点；生成后执行：

```text
execute service='built-in' tool_name='run_script'
params={"script_key":"system.validate_workflow","script_params":{"workflow":<完整对象>}}
```

不再单列实现历史或“不做事项”。

- [ ] **Step 4: 更新 skill 元数据描述。**

把 `SkillService` 中 “编写 Agent Bridge workflow.js 的提示词” 改成“设计 Agent Bridge 结构化 DAG 工作流的提示词”。

- [ ] **Step 5: 运行 skill 测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_skills.py tests/test_design_agent_api.py -q`

Expected: skill 内容和已有技能 API 测试 PASS。

- [ ] **Step 6: Commit。**

```bash
git add src/agent_bridge/system_config/skills/defaults/design_workflow.md src/agent_bridge/system_config/skills/service.py tests/test_skills.py tests/test_design_agent_api.py
git commit -m "docs(skills): refine structured workflow design guidance"
```

### Task 6: 抽取 Schema 字段编辑器并扩展脚本管理

**Files:**
- Create: `frontend/capabilities/src/lib/schemaFields.ts`
- Create: `frontend/capabilities/src/components/SchemaFieldEditor.vue`
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/system/ScriptsView.vue`
- Test: `frontend/capabilities/tests/schemaFields.test.ts`

**Interfaces:**
- Consumes: `Record<string, unknown>` JSON Schema。
- Produces: `SchemaField[]`、`schemaToFields()`、`fieldsToSchema()`、`isSimpleObjectSchema()` 和可复用 Vue 组件。

- [ ] **Step 1: 写纯函数失败测试。**

```ts
test('simple object schema round trips through field rows', () => {
  const schema = {
    type: 'object',
    properties: { summary: { type: 'string', description: '说明' }, count: { type: 'integer' } },
    required: ['summary'],
    additionalProperties: false,
  }
  const fields = schemaToFields(schema)
  assert.deepEqual(fields, [
    { name: 'summary', type: 'string', required: true, description: '说明' },
    { name: 'count', type: 'integer', required: false, description: '' },
  ])
  assert.deepEqual(fieldsToSchema(fields), schema)
})

test('nested schema stays in advanced mode without dropping data', () => {
  const schema = { type: 'object', properties: { result: { type: 'object', properties: { value: { type: 'string' } } } } }
  assert.equal(isSimpleObjectSchema(schema), false)
})
```

- [ ] **Step 2: 运行测试确认工具函数不存在。**

Run: `cd frontend/capabilities && node --test tests/schemaFields.test.ts`

Expected: FAIL because schema utility module does not exist。

- [ ] **Step 3: 实现 Schema 转换纯函数。**

只支持顶层 `object` 和 `string/number/integer/boolean/array/object` 行类型。空字段名或重复字段名由组件提交前拒绝；高级 Schema 中存在嵌套、组合关键字、非默认额外属性设置等内容时标记为兼容模式，原 JSON 必须原样保留。

- [ ] **Step 4: 实现 SchemaFieldEditor。**

组件 props 使用 `modelValue: Record<string, unknown> | null` 和 `label`，emit `update:modelValue`。默认显示字段列表和新增/删除/类型/必填/说明控件；兼容模式显示高级 JSON 文本框。JSON 解析错误显示在组件内，成功解析简单 Schema 后允许切换回字段列表。

- [ ] **Step 5: 将 ScriptsView 的输入 Schema 改为共享组件并增加输出 Schema。**

脚本表单新增 `output_schema`；保存 payload、设计 Agent 当前草稿、详情回填都带该字段。输入和输出各自使用 `SchemaFieldEditor`。内置脚本显示来源 Badge，禁止状态和删除编辑，提供 `api.resetScript()`。

- [ ] **Step 6: 运行前端纯函数与类型检查。**

Run: `cd frontend/capabilities && node --test tests/schemaFields.test.ts && npm run typecheck`

Expected: Schema utility tests PASS，TypeScript 无错误。

- [ ] **Step 7: Commit。**

```bash
git add frontend/capabilities/src/lib/schemaFields.ts frontend/capabilities/src/components/SchemaFieldEditor.vue frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/system/ScriptsView.vue frontend/capabilities/tests/schemaFields.test.ts
git commit -m "feat(capabilities): add reusable schema field editor"
```

### Task 7: 实现工作流前置数据推导与引用插入

**Files:**
- Create: `frontend/capabilities/src/lib/workflowReferences.ts`
- Create: `frontend/capabilities/src/components/workflow/WorkflowReferencePicker.vue`
- Modify: `frontend/capabilities/src/views/workflow/workflowDefinition.ts`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue`
- Modify: `frontend/capabilities/src/api/types.ts`
- Test: `frontend/capabilities/tests/workflowReferences.test.ts`

**Interfaces:**
- Consumes: `WorkflowGraph`、`ManagedScript.output_schema`、Agent `output_schema`、当前编辑目标。
- Produces: `deriveAvailableData(graph, target, scripts)` 和引用选择器的 `insert` event。

- [ ] **Step 1: 写失败测试固定祖先过滤和输出字段展开。**

```ts
test('node target exposes input, task, and ancestor output fields only', () => {
  const result = deriveAvailableData(graphWithAgentAndScript, { kind: 'node', id: 'report' }, scripts)
  assert.deepEqual(result.map(item => item.path), [
    'input.repo',
    'task.task_key',
    'task.payload',
    'nodes.enrich.output.summary',
    'nodes.collect.output.pages',
  ])
  assert.equal(result.some(item => item.path.includes('parallel')), false)
})

test('edge target exposes source lineage and returns raw condition paths', () => {
  const result = deriveAvailableData(graphWithClassifierEdge, { kind: 'edge', id: 'classify-handle' }, scripts)
  assert.equal(result.some(item => item.path === 'nodes.classify.output.category'), true)
  assert.equal(result.some(item => item.path === 'nodes.sibling.output.value'), false)
})
```

- [ ] **Step 2: 运行测试确认推导函数不存在。**

Run: `cd frontend/capabilities && node --test tests/workflowReferences.test.ts`

Expected: FAIL because `deriveAvailableData` 尚未实现。

- [ ] **Step 3: 实现 lineage 和输出契约推导。**

定义：

```ts
export interface WorkflowReferenceItem {
  path: string
  label: string
  type: string
  description: string
  sourceNodeId?: string
}

export type WorkflowReferenceTarget =
  | { kind: 'node'; id: string }
  | { kind: 'edge'; id: string }
```

函数签名为 `deriveAvailableData(graph: WorkflowGraph, target: WorkflowReferenceTarget, scripts: ManagedScript[]): WorkflowReferenceItem[]`。

按逆向 incoming edges 计算祖先；Agent JSON 展开 schema properties；文本 Agent 添加 `text`；脚本 output Schema 展开，否则添加根路径；output 节点添加 `title/summary/content/artifact_ids`。排序固定为 input、task、祖先节点拓扑顺序。

- [ ] **Step 4: 实现 picker 和插入事件。**

`WorkflowReferencePicker` 接收 `items`、`mode: 'template' | 'condition'`，提供搜索和列表；点击后 emit `insert`：模板模式为 `{{ path }}`，条件模式为 `path`。没有活动输入框的 fallback 由父组件执行 `navigator.clipboard.writeText(path)`。

- [ ] **Step 5: 让 Input/Textarea 暴露插入所需 ref API。**

在两个基础控件内部使用 `ref<HTMLInputElement | HTMLTextAreaElement>()`，通过 `defineExpose` 暴露：

```ts
focus(): void
insertText(value: string): void
```

`insertText` 使用 `selectionStart/selectionEnd` 替换当前选区、更新 model value、恢复光标到插入文本后。

- [ ] **Step 6: 接入节点和连线面板。**

节点面板对提示词、脚本参数、输出标题和路径提供 picker；连线面板对 `condition.field` 提供 `mode='condition'` picker。面板从 WorkflowView 接收当前 graph、target 和 scripts，不自行计算全图。

- [ ] **Step 7: 运行前端引用测试。**

Run: `cd frontend/capabilities && node --test tests/workflowReferences.test.ts tests/workflowDefinition.test.ts && npm run typecheck`

Expected: 祖先过滤、Schema 展开、插入格式和类型检查 PASS。

- [ ] **Step 8: Commit。**

```bash
git add frontend/capabilities/src/lib/workflowReferences.ts frontend/capabilities/src/components/workflow/WorkflowReferencePicker.vue frontend/capabilities/src/views/workflow/workflowDefinition.ts frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue frontend/capabilities/src/api/types.ts frontend/capabilities/src/components/ui/input/Input.vue frontend/capabilities/src/components/ui/textarea/Textarea.vue frontend/capabilities/tests/workflowReferences.test.ts frontend/capabilities/tests/workflowDefinition.test.ts
git commit -m "feat(workflows): add discoverable reference picker"
```

### Task 8: 把配置面板改为覆盖式抽屉和全屏模式

**Files:**
- Create: `frontend/capabilities/src/views/workflow/WorkflowConfigDrawer.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue`
- Test: `frontend/capabilities/tests/workflowConfigDrawer.test.ts`

**Interfaces:**
- Consumes: Task 6 的 SchemaFieldEditor 和 Task 7 的 ReferencePicker。
- Produces: 节点/连线配置抽屉、全屏切换、错误定位和响应式布局。

- [ ] **Step 1: 写失败测试固定默认/全屏状态。**

```ts
test('drawer starts as overlay and can expand to full editor area', () => {
  const state = createWorkflowDrawerState()
  assert.equal(state.mode, 'overlay')
  toggleDrawerFullscreen(state)
  assert.equal(state.mode, 'fullscreen')
  closeDrawer(state)
  assert.equal(state.open, false)
})
```

- [ ] **Step 2: 运行测试确认抽屉状态模块不存在。**

Run: `cd frontend/capabilities && node --test tests/workflowConfigDrawer.test.ts`

Expected: FAIL because drawer state and component do not exist。

- [ ] **Step 3: 实现 WorkflowConfigDrawer。**

组件 props：`open`、`mode`、`title`；events：`update:open`、`update:mode`。overlay 模式使用绝对定位覆盖 DAG 右侧，宽度 `min(560px, 52vw)`；fullscreen 模式覆盖 `.workflow-editor-region`，顶部保留关闭/全屏按钮。`@media (max-width: 1024px)` 直接使用 fullscreen。

- [ ] **Step 4: 修改 WorkflowView 的编辑布局。**

移除 `xl:grid-cols-[132px_minmax(0,1fr)_340px]` 的固定第三列。画布区变为 `relative`，节点/连线面板放进 drawer；页面级保存、测试按钮和工作流元信息不进入 drawer。选择目标时 drawer 保持打开并替换内容，关闭只清除显示状态，不清除 `selectedNodeId/selectedEdgeId` 的草稿数据。

- [ ] **Step 5: 接入结构化错误定位。**

扩展前端 `WorkflowValidationError` 增加 `code`。保存、校验 API 返回的 issues 写入 `graphErrors`；节点/边错误映射沿用 `scope + id`，抽屉打开时根据 `field` 给具体控件增加 `aria-invalid` 和错误提示。画布仍显示节点/连线错误边框。

- [ ] **Step 6: 运行抽屉测试和构建。**

Run: `cd frontend/capabilities && node --test tests/workflowConfigDrawer.test.ts && npm run typecheck && npm run build`

Expected: 抽屉状态、全屏 CSS、类型检查和生产构建 PASS。

- [ ] **Step 7: Commit。**

```bash
git add frontend/capabilities/src/views/workflow/WorkflowConfigDrawer.vue frontend/capabilities/src/views/workflow/WorkflowView.vue frontend/capabilities/src/views/workflow/WorkflowNodeConfigPanel.vue frontend/capabilities/src/views/workflow/WorkflowEdgeConfigPanel.vue frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue frontend/capabilities/tests/workflowConfigDrawer.test.ts frontend/capabilities/src/api/types.ts
git commit -m "feat(workflows): use overlay configuration drawer"
```

### Task 9: 接入草稿校验与 design workflow 交互

**Files:**
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `tests/test_workflow_api.py`
- Test: `frontend/capabilities/tests/workflowDefinition.test.ts`

**Interfaces:**
- Consumes: `POST /workflows/validate` 和统一 validation result。
- Produces: 编辑器保存前的草稿预检、准确错误显示，以及与新 `definition` 契约一致的前端请求。

- [ ] **Step 1: 写 API 和前端请求失败测试。**

后端测试断言 `/workflows/validate` 不写入数据库；前端纯函数测试断言一个校验 issue 能定位到 node/edge field。

- [ ] **Step 2: 实现 client 类型和方法。**

增加：

```ts
export interface WorkflowValidationIssue {
  scope: 'workflow' | 'node' | 'edge'
  id: string | null
  field: string | null
  code: string
  message: string
}

validateWorkflow: (workflow: WorkflowDraft) => post<WorkflowValidationResult>('/workflows/validate', { workflow })
```

- [ ] **Step 3: 在保存和测试运行前调用草稿校验。**

`saveWorkflow` 先调用 `api.validateWorkflow(form.value)`，有 error 时更新 `graphErrors/formError` 并保持当前页面；通过后再调用保存 API。`runEditedWorkflow` 使用相同预检，避免把明显非法草稿送入后台。正式保存后的运行仍由后端再次复检。

- [ ] **Step 4: 运行 API 和前端测试。**

Run: `PYTHONPATH=. uv run pytest tests/test_workflow_api.py -q && cd frontend/capabilities && node --test tests/workflowDefinition.test.ts && npm run typecheck`

Expected: 草稿校验不持久化、保存/测试阻止非法定义、类型检查 PASS。

- [ ] **Step 5: Commit。**

```bash
git add frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/workflow/WorkflowView.vue frontend/capabilities/src/api/types.ts tests/test_workflow_api.py frontend/capabilities/tests/workflowDefinition.test.ts
git commit -m "feat(workflows): validate drafts before save and test run"
```

### Task 10: 集成验证与交付检查

**Files:**
- Modify only files needed to fix test findings from Tasks 1-9。
- Test: `tests/` and `frontend/capabilities/tests/`。

**Interfaces:**
- Consumes: 全部前后端实现。
- Produces: 可运行的工作流编辑与校验闭环。

- [ ] **Step 1: 运行后端核心测试。**

Run: `PYTHONPATH=. uv run pytest -q -m 'not ragflow and not weknora'`

Expected: 所有现有后端测试 PASS；若环境依赖导致 deselect，输出必须明确列出 deselected 项。

- [ ] **Step 2: 运行前端完整测试、类型检查和构建。**

Run: `cd frontend/capabilities && node --test tests/*.test.ts && npm run typecheck && npm run build`

Expected: 所有前端测试 PASS，typecheck 无错误，build 成功。

- [ ] **Step 3: 手动验证内置脚本闭环。**

使用本地管理员账号执行：

1. 打开脚本管理，确认 `system.validate_workflow` 显示“内置”。
2. 修改其默认代码形成数据库覆盖，确认来源变为 database。
3. 点击恢复默认，确认代码和来源恢复。
4. 通过 Agent capability 执行 `run_script`，传入一个合法和一个非法工作流，确认返回 `{valid, errors, warnings}`。
5. 在工作流编辑器中选择 Agent 节点，打开引用提示，确认只出现 input/task/祖先输出。
6. 点击引用，确认内容插入光标位置；选择连线条件时确认插入裸路径。
7. 在 Agent JSON 输出和脚本输入/输出中使用字段列表编辑，再切换高级 JSON，确认复杂字段没有丢失。
8. 检查配置抽屉默认覆盖 DAG，点击全屏后仍能看到页面级保存和测试入口。

- [ ] **Step 4: 检查变更和工作区。**

Run: `git diff --check && git status --short`

Expected: 无空白错误；只包含本计划相关文件或已明确的测试修正。

- [ ] **Step 5: Commit 集成修正。**

```bash
git add src frontend tests
git commit -m "test(workflows): verify authoring and validation integration"
```

---

## 依赖顺序

Task 1 是后端核心，Task 2 可并行但 Task 3 依赖 Task 1 和 Task 2。Task 4 依赖 Task 1。Task 5 依赖 Task 3 的脚本调用契约。Task 6 可与后端任务并行，Task 7 依赖 Task 2 的脚本输出 Schema 类型，Task 8 依赖 Task 6 和 Task 7，Task 9 依赖 Task 3 与 Task 8。Task 10 在全部任务完成后执行。

推荐实现批次：

1. Task 1-4：先完成统一校验与内置脚本闭环。
2. Task 5：更新 Agent 设计 skill，使生成流程可以使用校验脚本。
3. Task 6-8：完成前端数据发现、Schema 编辑和抽屉布局。
4. Task 9-10：接入草稿校验并做全量验证。

## 计划自检

- 规格中的统一 validator、稳定错误码、执行前复检对应 Task 1、Task 4。
- built-in scripts、默认/覆盖/恢复、不可删除停用和 output Schema 对应 Task 2、Task 3、Task 6。
- `design_workflow` 重写和校验脚本调用对应 Task 5。
- 前置数据祖先过滤、输出契约和插入行为对应 Task 7。
- Schema 字段列表、高级 JSON 兜底对应 Task 6。
- 覆盖式抽屉、全屏、小屏和错误定位对应 Task 8。
- 校验 API、保存前校验和最终测试对应 Task 3、Task 9、Task 10。
- 计划中没有 `TBD`、`TODO` 或“以后补充”步骤；所有新增接口在任务中给出名称、输入和输出。
