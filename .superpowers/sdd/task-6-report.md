# Task 6 报告

## 结果

已完成 Task 6 前端实现：

- 新增 `schemaFields.ts` 纯函数与真实 Node 测试。
- 新增 `SchemaFieldEditor.vue`，支持顶层 object 字段列表与高级 JSON 双模式编辑。
- `ScriptsView.vue` 改为复用共享 Schema 编辑器，并补充可选 `output_schema`。
- `scripts` 前端 API / types 补齐 `output_schema`、`source` 与 `resetScript()`。
- built-in 脚本显示来源，UI 禁删、禁停，并提供恢复默认操作入口。

## TDD 过程

### Red

先创建 `frontend/capabilities/tests/schemaFields.test.ts`，覆盖：

1. 简单 object schema 与字段列表的 round-trip。
2. 嵌套 schema 识别为高级模式。

运行：

```bash
cd frontend/capabilities
node --test tests/schemaFields.test.ts
```

首次结果按预期失败，错误为：

- `ERR_MODULE_NOT_FOUND`
- 缺少 `src/lib/schemaFields.ts`

### Green

新增 `frontend/capabilities/src/lib/schemaFields.ts`：

- `schemaToFields()`
- `fieldsToSchema()`
- `isSimpleObjectSchema()`
- `SchemaField` / `SchemaFieldType`

随后新增 `frontend/capabilities/src/components/SchemaFieldEditor.vue`，并在 `ScriptsView.vue` 中替换原输入字段实现，加入输出 Schema 与 built-in 管理逻辑。

## 主要实现说明

### 1. Schema 工具函数

- 仅把“顶层 object + 平铺字段 + 默认 `additionalProperties: false`”识别为简单模式。
- 遇到嵌套结构、组合关键字、数组 items、额外约束等复杂结构时，保留为高级 JSON。

### 2. 共享 Schema 编辑器

- 字段模式下支持：字段名、类型、必填、说明、新增、删除。
- 高级 JSON 模式下即时解析对象 Schema，并在组件内显示错误。
- 当高级 JSON 已变回简单结构时，允许切回字段模式。
- 复杂结构不会在字段模式里被错误折叠或丢失。

### 3. ScriptsView 改造

- 输入 Schema 改为 `SchemaFieldEditor`。
- 输出 Schema 通过“声明输出 Schema”开关控制；关闭时保存为 `null`。
- 设计 Agent 当前草稿、详情回填、保存 payload 全部带上 `output_schema`。
- 测试运行参数表单从 `form.input_schema` 实时推导，不再依赖页面内私有 schema 行状态。

### 4. Built-in 脚本管理

- 列表页与详情页展示来源 badge。
- built-in 脚本状态选择器禁用，删除按钮禁用。
- 详情页新增“恢复默认”，调用 `api.resetScript()`。

## 修改文件

- `frontend/capabilities/src/lib/schemaFields.ts`
- `frontend/capabilities/src/components/SchemaFieldEditor.vue`
- `frontend/capabilities/src/api/types.ts`
- `frontend/capabilities/src/api/client.ts`
- `frontend/capabilities/src/views/system/ScriptsView.vue`
- `frontend/capabilities/tests/schemaFields.test.ts`

## 验证

已运行：

```bash
cd frontend/capabilities
node --test tests/schemaFields.test.ts && npm run typecheck
```

结果：

- `schemaFields.test.ts` 2/2 通过
- `vue-tsc --noEmit` 通过

## Concerns

1. 前端已按 `POST /scripts/{script_key}/reset` 接入 `resetScript()`；当前工作区内的后端 service 已有 `reset_script()`，但我没有在本地实际启动接口验证该 route。
2. 由于后端未返回显式 `is_builtin` 字段，前端目前用 `source === "default"` 或 `script_key` 以 `system.` 开头来识别 built-in 脚本；这对当前已知内置脚本可用，但后续若扩展 built-in 类型，最好由后端直接返回稳定标识。

## Reviewer 修复（第二轮）

### P1：补齐 reset route 实际契约

确认 reviewer 指出的问题成立：`ScriptService.reset_script()` 已存在，但 `src/agent_bridge/api/routes/builtins.py` 之前没有挂 `POST /scripts/{script_key}/reset`。

本轮修复：

- 在 `src/agent_bridge/api/routes/builtins.py` 新增 `POST /scripts/{script_key}/reset`
- route 直接调用 `service.scripts.reset_script(current_actor, script_key)`
- 在 `tests/test_script_runtime_api.py` 新增真实 API 测试：
  - 先把 `system.validate_workflow` 覆盖成 `source="database"`
  - 再调用 `/scripts/system.validate_workflow/reset`
  - 断言返回 `200` 且 `source == "default"`

### P2：补齐脚本管理纯函数测试接口

确认 reviewer 指出的问题成立：之前脚本管理逻辑仍散落在 `ScriptsView.vue`，只有 `schemaFields.test.ts`，没有覆盖脚本保存/详情回填/built-in 保护/reset path 的独立前端测试。

本轮修复：

- 新增 `frontend/capabilities/src/lib/scriptManagement.ts`
- 新增 `frontend/capabilities/tests/scriptManagement.test.ts`
- `ScriptsView.vue` 改为复用 helper：
  - `toScriptFormState()`
  - `toScriptUpsertPayload()`
  - `canDeleteScript()`
  - `canDisableScript()`
  - `canResetScript()`
  - `isBuiltInScriptFamily()`
- `frontend/capabilities/src/api/client.ts` 改为通过 `scriptResetPath()` 生成 reset path，避免 API wiring 与测试脱节

覆盖点：

1. `output_schema` 在详情回填和保存 payload 中不丢失
2. 默认内置脚本不可删除/停用
3. 数据库覆盖的 built-in 脚本仍然可恢复默认
4. `resetScript` 使用正确的 `/scripts/{scriptKey}/reset` 路径

### 保持不变的行为

- `SchemaFieldEditor` 的复杂 JSON Schema 仍保持高级模式，不会被字段模式误折叠或丢失。

## 本轮新增/修改文件

- `src/agent_bridge/api/routes/builtins.py`
- `tests/test_script_runtime_api.py`
- `frontend/capabilities/src/lib/scriptManagement.ts`
- `frontend/capabilities/tests/scriptManagement.test.ts`
- `frontend/capabilities/src/api/client.ts`
- `frontend/capabilities/src/views/system/ScriptsView.vue`

## 本轮测试命令

```bash
PYTHONPATH=. uv run pytest tests/test_scripts.py tests/test_script_runtime_api.py -q
cd frontend/capabilities && node --test tests/schemaFields.test.ts tests/scriptManagement.test.ts && npm run typecheck
```

## 本轮实际输出

### 后端

```text
.....................                                                    [100%]
21 passed, 1 warning in 4.85s
```

### 前端

```text
✔ simple object schema round trips through field rows
✔ nested schema stays in advanced mode without dropping data
✔ script payload preserves output schema on save and detail rehydrate
✔ default built-ins are protected and database overrides remain resettable
✔ reset path uses scripts reset endpoint

tests 5
pass 5
fail 0

> agent-bridge-capabilities-ui@0.1.0 typecheck
> vue-tsc --noEmit
```

## 更新后的 Concerns

1. 后端 reset route 已补齐并通过真实 API 测试；之前报告里的这个 concern 已消除。
2. built-in 识别前端仍依赖 `script_key` 前缀 `system.` 来识别“built-in family”，因为后端没有显式 `is_builtin` 字段。当前与后端内置脚本集合一致，但如果未来 built-in key 规则扩展，最好由后端返回稳定标识。
