# 工作流编写体验与统一校验设计

日期：2026-07-13

## 1. 背景

新的工作流编辑器已经使用结构化 JSON DAG，不再执行 `workflow_js`。当前仍有四个明显问题：

1. `design_workflow` 技能混入了过多实现历史和“不支持项”，没有把重点放在如何产出可执行定义上。
2. Agent 生成工作流后缺少稳定的自动校验步骤；保存、执行与未来的校验脚本也不应各自维护规则。
3. 节点提示词、脚本参数和连线条件依赖人工记忆引用路径，编辑器没有展示当前位置可访问的数据。
4. JSON Schema 只能手写，节点和连线配置栏也不足以容纳复杂配置。

本设计解决以上问题，并引入与 built-in skills 类似的 built-in scripts。范围只覆盖当前工作流编写和校验所需能力。

## 2. 目标

- 将 `design_workflow` 改写为简洁、面向产出的工作流设计指南。
- 提供 `system.validate_workflow` 内置脚本，让 Coding Agent 在交付前校验完整工作流。
- 保存、测试运行、正式执行、校验 API 和内置脚本共用同一个校验核心。
- 在节点和连线配置中展示当前可用的前置数据，并支持一键插入引用。
- 为常用的顶层 object JSON Schema 提供字段列表编辑器，并保留高级 JSON 兜底。
- 将固定窄侧栏替换为覆盖式配置抽屉，并允许扩展为全屏配置。
- 为托管脚本补充可选输出 Schema，使下游引用可以被推导和校验。

## 3. 非目标

本次不扩展工作流节点类型、执行语义或条件表达式能力，也不实现递归 JSON Schema 设计器。复杂 Schema 继续使用高级 JSON 编辑。

## 4. 总体架构

### 4.1 统一校验服务

新增 `WorkflowValidator`，作为工作流定义校验的唯一应用层入口。调用关系如下：

```text
编辑器保存 ───────┐
测试运行 ─────────┤
正式执行 ─────────┼──▶ WorkflowValidator
校验 API ─────────┤
built-in tool ────┘
       ▲
system.validate_workflow
       ▲
Coding Agent
```

`WorkflowValidator` 接收当前 actor 和完整工作流对象，统一完成：

1. 工作流元信息、节点、连线与配置的解析。
2. DAG、引用和工作流类型约束校验。
3. Profile、Coding Agent 后端、技能和脚本资源校验。
4. 脚本输入参数与已声明 Schema 的匹配检查。

现有 `collect_graph_issues` 中的纯图规则继续保留为独立函数，由 `WorkflowValidator` 调用。现有 `WorkflowService._resource_issues` 迁移到统一校验服务，不再由保存流程私有持有。

保存、测试运行和正式执行都必须在开始前调用统一校验。正式执行前重新校验，可发现保存后被停用或删除的依赖资源。

### 4.2 校验结果

统一返回：

```json
{
  "valid": false,
  "errors": [
    {
      "scope": "node",
      "id": "enrich",
      "field": "config.prompt",
      "code": "invalid_reference",
      "message": "节点引用必须来自祖先节点: collect"
    }
  ],
  "warnings": []
}
```

字段语义：

- `scope`：`workflow`、`node` 或 `edge`。
- `id`：节点或连线 ID；工作流级问题为 `null`。
- `field`：可定位到配置控件的字段路径；无法细分时为 `null`。
- `code`：稳定、机器可读的错误代码。
- `message`：面向管理员和 Agent 的中文说明。

`errors` 非空时禁止保存和执行。`warnings` 只提示，不阻止操作。前端不得依赖中文文案判断错误类型。

校验分为三个阶段：

- `parse`：Pydantic 模型或 JSON Schema 无法解析。
- `graph`：重复 ID、悬空连线、环、非法引用和总结型约束等。
- `resource`：Profile、后端、技能或脚本不可用，脚本参数不符合契约。

## 5. Built-in Scripts

### 5.1 定义与覆盖模型

`ScriptService` 增加仓库内置脚本注册表，结构与 `SkillService` 的默认定义相似。每项包含：

- 固定 `script_key`、名称、描述、语言和归属。
- 默认 Python 文件。
- 默认输入 Schema 和输出 Schema。

列表和详情接口将内置定义与数据库记录合并：

- 没有数据库覆盖时使用仓库默认值，`source=default`。
- 保存内置脚本时只产生数据库覆盖，`source=database`。
- “恢复默认”删除数据库覆盖并立即回到仓库版本。
- 内置脚本不能删除、不能停用，固定标识和归属不可修改。
- 普通托管脚本继续支持新增、编辑、停用和删除。

脚本管理页使用“内置”标识区分来源。内置脚本的删除操作替换为“恢复默认”。

### 5.2 脚本输出 Schema

脚本模型、存储、API 和管理页面增加可选 `output_schema`：

- 新建普通脚本时可以不填写，以兼容现有行为。
- 所有内置脚本必须声明输出 Schema。
- 声明输出 Schema 后，`ScriptService` 在脚本成功返回 JSON 对象后继续校验返回值；不符合契约时本次脚本运行失败。
- 未声明输出 Schema 的历史脚本仍可运行，但编辑器只能展示 `nodes.<id>.output` 根路径。

### 5.3 工作流校验脚本

首个内置脚本为 `system.validate_workflow`。

输入：

```json
{
  "workflow": {
    "workflow_key": "repo-summary",
    "name": "仓库总结",
    "description": "...",
    "profile_key": "default",
    "status": "active",
    "workflow_type": "summary",
    "definition": {"nodes": [], "edges": []}
  }
}
```

输出遵循统一的 `{valid, errors, warnings}` 结构。

脚本不实现校验规则。它通过运行时 `execute` 调用 built-in capability 的 `validate_workflow` 工具；该工具直接委托 `WorkflowValidator`。这种薄适配既保留通用脚本调用方式，又避免子进程重新装配数据库和服务依赖。

## 6. design_workflow 技能

技能重写后聚焦“如何产出可执行定义”，删除版本历史、实现内部说明和大段“不支持项”。主要结构为：

1. 工作流产出格式和元信息。
2. 四类节点的必要配置与输出契约。
3. 引用和条件边规则。
4. 总结型工作流的固定 Markdown 到 HTML 约束。
5. 简短的设计步骤与最终检查。
6. 完整但紧凑的示例。

技能要求 Agent 在开始设计前使用系统提供的真实后端、技能和脚本信息，不虚构资源标识。对于 JSON Agent，应优先声明足以支撑后续引用的输出 Schema。

Agent 生成完整工作流对象后必须执行：

```text
execute service='built-in' tool_name='run_script'
params={
  "script_key": "system.validate_workflow",
  "script_params": {"workflow": <完整工作流对象>}
}
```

若 `valid=false`，应根据错误 `code` 和定位信息修改定义并重新运行校验。只有 `valid=true` 时才交付最终工作流 JSON。

## 7. 可用前置数据

### 7.1 推导规则

前端新增纯函数 `deriveAvailableData(graph, target, resources)`。结果由 DAG 拓扑和节点输出契约推导，不依赖历史运行样本。

节点配置可访问：

- 已知的 `input.*` 字段。
- 固定任务字段 `task.*`。
- 当前节点所有祖先的 `nodes.<id>.output.*`。

连线条件可访问：

- 连线来源节点及其祖先的 `nodes.<id>.output.*`。
- 不展示与来源并行的兄弟节点或后继节点。

输出契约来源：

- `get_task`：固定任务结构。
- 文本 Agent：`nodes.<id>.output.text`。
- JSON Agent：展开 `output_schema.properties`。
- 托管脚本：展开脚本 `output_schema.properties`；没有 Schema 时仅展示输出根路径。
- 输出节点：展示固定的 `title`、`summary`、`content` 和 `artifact_ids`。

`task.payload` 的业务结构未知，因此只展示根路径。`input.*` 使用当前工作流能够推导出的手动输入字段。

### 7.2 选择与插入

所有支持引用的控件提供“插入数据”按钮，包括：

- Agent 和输出节点提示词。
- 脚本参数。
- 输出标题与产物路径。
- 连线条件字段。

点击后打开可搜索的路径选择框，每项展示来源节点、完整路径、类型和说明。

插入规则：

- 模板字段插入 `{{ nodes.enrich.output.summary }}`。
- 连线条件字段插入 `nodes.enrich.output.summary`。
- 插入到当前焦点控件的光标位置，并保留焦点。
- 没有可插入目标时复制对应路径到剪贴板。

前端列表用于提升可发现性，后端祖先关系校验仍是最终边界。

## 8. Schema 字段编辑器

新增共享 `SchemaFieldEditor`，用于：

- Agent 的 JSON 输出 Schema。
- 托管脚本输入 Schema。
- 托管脚本输出 Schema。

默认模式为顶层字段列表。每行包含：

- 字段名。
- 类型：`string`、`number`、`integer`、`boolean`、`array` 或 `object`。
- 是否必填。
- 说明。
- 删除操作。

列表生成顶层 object Schema，并默认设置 `additionalProperties: false`。

编辑器保留“高级 JSON”模式：

- 简单顶层 object 可在两种模式间无损切换。
- 包含嵌套、组合关键字或其他列表不支持内容时，自动使用高级模式，不删除未知字段。
- 高级 JSON 解析错误就地展示。
- 修正为受支持的简单结构后，可以重新切回字段列表。

本次不提供递归嵌套表单。`array` 和 `object` 的内部结构需要时使用高级 JSON 描述。

## 9. 配置抽屉

工作流编辑区域不再使用固定 340px 第三列。选择节点或连线后，在 DAG 右侧打开覆盖式配置抽屉：

- 默认宽度约为 `min(560px, 52vw)`。
- 抽屉覆盖画布右侧，但左侧仍保留当前节点和部分上下游上下文。
- 切换节点或连线时保持抽屉打开，只替换配置内容。
- 抽屉顶部提供关闭和全屏图标按钮。
- 全屏模式只覆盖 DAG 编辑区域，保留工作流元信息和页面级保存、测试按钮。
- 小屏直接使用全屏模式。

节点与连线共用同一个配置容器，不为连线条件另建弹窗。配置仍直接更新当前表单草稿，关闭抽屉不会丢失未保存修改。

后端校验问题通过 `scope + id + field` 定位：

- DAG 上对应节点或连线保持错误高亮。
- 打开配置抽屉后，尽量定位到具体字段。
- 抽屉关闭后错误标记仍保留。

## 10. API 与数据变化

新增或调整的主要契约：

- 托管脚本增加可选 `output_schema` 和来源信息。
- 内置脚本增加恢复默认操作。
- built-in capability 增加 `validate_workflow` 工具。
- 新增 `POST /workflows/validate`，供编辑器在不保存时预检完整草稿；请求体使用与保存相同的完整工作流对象，响应为统一校验结果。
- 工作流保存和执行响应继续使用结构化 validation issues，不通过中文字符串传递定位信息。

SQLite 迁移为 `scripts` 表补充 `output_schema_json`。已有记录默认无输出 Schema。

## 11. 测试范围

后端：

- `WorkflowValidator` 的解析、结构、引用、资源与稳定错误代码。
- 保存、测试运行和正式执行调用同一校验入口。
- 工作流保存后依赖资源变化时，正式执行前能够阻止运行。
- 内置脚本默认值、数据库覆盖、恢复默认、禁止删除和禁止停用。
- `system.validate_workflow` 委托统一校验服务。
- 普通脚本输出 Schema 可选；声明后验证实际返回值。

前端：

- 祖先计算和不同节点输出契约的路径展开。
- 节点与连线的可用数据范围不同，且不暴露非法兄弟节点。
- 光标插入、条件字段裸路径和无活动目标时复制。
- Schema 字段列表生成、必填项、字段重名和高级 JSON 无损切换。
- 覆盖式抽屉、全屏切换、小屏行为和错误定位。

最终验证运行现有工作流与脚本后端测试、前端测试、类型检查和构建。

## 12. 验收标准

1. Coding Agent 按 `design_workflow` 生成定义后，可以运行 `system.validate_workflow` 并得到结构化结果。
2. 相同非法定义在保存、测试运行、正式执行和校验脚本中得到一致的核心错误。
3. 工作流编辑器能够列出当前节点或连线实际可访问的数据路径，并插入正确格式的引用。
4. Agent 和脚本的常用顶层 Schema 不需要手写 JSON，复杂 Schema 仍可完整保留。
5. 节点和连线配置使用覆盖式抽屉，并可以扩展为全屏，不再长期压缩 DAG 画布。
6. 内置脚本可覆盖和恢复默认，不能被删除或停用。
7. 现有无输出 Schema 的普通脚本仍可运行。
