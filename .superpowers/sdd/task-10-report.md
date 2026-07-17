# Task 10: 集成验证与交付检查报告

## 状态

PASS，已完成 Task 10 集成验证。

修复提交：

- `bb2683a` `test(workflows): verify authoring and validation integration`

## 阻断性失败与修复

后端首次完整验证发现 3 个阻断性失败，均为 Task 1-9 后契约变化造成的测试/文案探针未同步：

- `frontend/capabilities/src/views/system/ScriptsView.vue`
  - 原因：脚本使用指引已经说明 `params` 会作为 JSON 对象传入 `script_params`，但测试探针要求保留明确短语 `params (JSON 对象)`。
  - 修复：在指引文案中显式标注 `params (JSON 对象)`。

- `tests/test_mcp_server.py`
  - 原因：`built-in` 内置能力新增 `validate_workflow`，默认搜索结果中的 `tool_count` 从 2 变为 3。
  - 修复：同步期望值为 3。

- `tests/test_workflow_mcp.py`
  - 原因：`design_workflow` 技能已重写为结构化 JSON DAG + `system.validate_workflow` 校验脚本契约，不再要求提示词包含 `workflow_get_task` / `workflow_set_task`。
  - 修复：同步断言为 `system.validate_workflow`、`run_script` 和稳定错误 `code` 契约。

- `frontend/capabilities/tsconfig.tsbuildinfo`
  - 原因：前端 typecheck/build 重新生成 tracked buildinfo，补齐 Task 1-9 新增前端模块的 root 列表。
  - 修复：提交构建后的一致状态。

## 验证结果

### 后端核心测试

命令：

```bash
PYTHONPATH=. uv run pytest -q -m 'not ragflow and not weknora'
```

结果：

- `796 passed`
- `8 deselected`
- `1 warning`
- 耗时约 `53.29s`

### 前端测试、类型检查与构建

命令：

```bash
cd frontend/capabilities && node --test tests/*.test.ts && npm run typecheck && npm run build
```

结果：

- Node test：`78 passed`
- `npm run typecheck`：通过
- `npm run build`：通过，`2526 modules transformed`
- 构建提示仅包含上游依赖/工具链 warnings：
  - Node `DEP0205 module.register()` deprecation warning
  - Rollup 移除 `@vueuse/core` 中无法解释位置的 `/* #__PURE__ */` 注释

### 内置脚本闭环烟测

使用临时 Agent Bridge root、临时真实 HTTP server 端口、本地管理员账号 `root` 验证：

- `system.validate_workflow` 初始来源为 `default`
- 修改默认代码形成数据库覆盖后，来源变为 `database`
- reset 后代码与来源恢复为默认
- 通过 `built-in/run_script` 执行 `system.validate_workflow`
- 合法工作流返回 `{valid: true, errors: [], warnings: []}`
- 非法工作流返回 `{valid: false, errors: [...], warnings: [...]}`，且错误包含稳定 `code`

结果：

- `script-loop smoke passed: default->database->reset, run_script valid/invalid returned {valid, errors, warnings}`

### 前端交互覆盖

已由前端测试覆盖 brief 中对应交互闭环：

- 引用提示只暴露 input/task/祖先输出
- 连线条件插入裸路径
- Schema 字段列表与高级 JSON 切换不丢复杂字段
- 配置抽屉默认覆盖 DAG，全屏布局仍保留页面级入口
- 保存前 draft validation 与重复运行 guard

### Git 检查

命令：

```bash
git diff --check
git status --short
```

提交前结果：

- `git diff --check`：通过，无空白错误
- `git status --short`：仅包含本计划相关 4 个文件

提交后结果：

- 工作区干净，随后仅新增本报告文件。

## Concerns

- 未进行真实浏览器人工点击验收；对应 UI 行为由前端自动化测试和脚本闭环烟测覆盖。
- 仍存在既有 warning：FastAPI TestClient 的 StarletteDeprecationWarning、Node `DEP0205`、Rollup pure annotation 提示；均未阻断测试或构建。

---

## Final Review Fix Wave

### Important 修复映射

1. **统一校验入口**
   - `AgentBridgeService.validate_workflow_draft` 不再补造 metadata/profile，也不再过滤 `missing_profile`，直接复用 `WorkflowValidator`。
   - workflow validation API、built-in tool、默认校验脚本和前端请求统一要求完整 workflow 对象；持久化行仅忽略数据库附加字段。

2. **默认 built-in script 资源解析**
   - `WorkflowValidator` 通过注入的 `ScriptService.get_script` 解析默认脚本和数据库覆盖，不再只查 `store.scripts`。
   - 覆盖默认脚本尚未物化时作为 workflow script node 的校验测试。

3. **运行契约校验**
   - 按 Draft 2020-12 校验脚本 literal/nested 参数类型；完整引用占位符不因字符串类型误报，required 缺失仍返回稳定错误。
   - 校验 Agent JSON `output_schema` 本身是否合法。
   - 模板只允许 `input`、`task`、`nodes`；`nodes` 只允许祖先并检查已知输出字段，`task.payload` 保持动态子字段。
   - 条件只允许 source 或其祖先的 `nodes.*.output`，并检查已知字段；前端 edge picker 只提供 source lineage。

4. **built-in 身份与升级**
   - 脚本 payload 增加显式 `is_builtin`，前端不再根据 `system.` 前缀推断。
   - `DEFAULT_SCRIPT_ACTOR` 物化行在 get/list/run 时按仓库代码与 Schema 自动刷新，保留 `script_runs`；管理员覆盖保持不变。

5. **design_workflow 契约**
   - skill 固定输出 `{summary, notes, workflow}`，校验脚本只接收 `workflow` 子对象。
   - `WORKFLOW_DESIGN_SCHEMA` 由 `WorkflowGraph.model_json_schema()` 派生，严格校验完整 node/edge/config 结构，并要求完整 envelope。

6. **design_script output_schema**
   - `SCRIPT_DESIGN_SCHEMA` 要求 nullable `output_schema`。
   - skill、前端采纳与保存逻辑保留已有输出 Schema；新脚本可使用 `null`。

7. **Schema 编辑器无损与有效性**
   - 字段列表 round-trip 保留顶层 `description`；复杂或未知结构保持高级 JSON。
   - 暴露 validity，invalid JSON、空字段名、重复字段显示错误并阻止 workflow/script 保存；补纯函数和组件接线测试。

8. **脚本历史完整性**
   - 普通脚本存在 `script_runs` 时拒绝硬删除并提示改用 `disabled`；无历史脚本仍可删除，内置脚本仍不可删除。

9. **workflow_type 无损迁移**
   - operation/summary 切换保留普通节点和仍有效的边；进入 summary 时重建并保护固定 Markdown/HTML 输出，离开时只移除固定输出及失效边。

附带修复：生产 `WorkflowDagExecutor` 关闭重复纯图校验；本 wave 未修改 `frontend/capabilities/tsconfig.tsbuildinfo`。

### Final Review 验证结果

指定后端套件：

```bash
PYTHONPATH=. uv run pytest tests/test_workflow_validator.py tests/test_workflow_service.py tests/test_workflow_api.py tests/test_scripts.py tests/test_script_runtime_api.py tests/test_design_agent_api.py tests/test_skills.py tests/test_workflow_executor.py -q
```

- `90 passed`
- `1 warning`
- 耗时 `8.96s`

整仓非外部集成套件：

```bash
PYTHONPATH=. uv run pytest -q -m 'not ragflow and not weknora'
```

- `813 passed`
- `8 deselected`
- `1 warning`
- 耗时 `65.35s`

前端：

```bash
cd frontend/capabilities
node --test tests/*.test.ts
npm run typecheck
npm run build
```

- Node test：`86 passed`
- `npm run typecheck`：通过
- `npm run build`：通过，`2526 modules transformed`
- `git diff --check`：通过
- `frontend/capabilities/tsconfig.tsbuildinfo`：未修改

### Final Review Concerns

- 未运行需要真实 RagFlow/Weknora 服务的 8 个外部集成测试。
- 未进行真实浏览器人工点击验收；前端行为由纯函数测试、组件接线测试、类型检查和生产构建覆盖。
- 仅剩既有 warning：FastAPI TestClient 的 StarletteDeprecationWarning、Node `DEP0205`、Rollup pure annotation 提示。

---

## Final Acceptance Repair Wave

### 实施记录

1. 保存 API 的 `definition` 只校验 object envelope，完整 graph parsing 统一交给 `WorkflowValidator`；默认 `system.validate_workflow` 也保持 definition 原样进入同一 validator。保存、`/workflows/validate` 与 built-in 现在对同一畸形 Agent 节点返回一致的 `{scope,id,field,code,message}` issue。
2. Pydantic union parse location 会移除 collection/index 后的 node discriminator 及 `config` 层；覆盖 `get_task`、`agent`（含 `output_schema`）、`script`、`output`。
3. validator 新增 profile `status == active` 约束；执行前重新校验失败时不会创建 workflow run。
4. MCP 能力直接读取已注册 adapter 的 `capabilities.supports_mcp`，Codex/OpenCode 返回稳定 `unsupported_mcp`，Claude 保持可用；runtime config API 暴露真实 registry backend/capabilities，前端 backend 选项不再硬编码并保留已注册自定义 slug。
5. `AgentService` 对最终结构化结果执行 Draft 2020-12 校验；失败结果使用稳定字段路径并以 failed 收口，不写入可供下游消费的 result，同时保留 native schema adapter 流程。
6. script params 与 edge condition value 使用 schema/reference 驱动的 string/integer/number/boolean/object/array 编辑器；复杂值使用 JSON，条件 equality 在后端区分 boolean 与 number。
7. Schema simple editor 保留缺省 `additionalProperties`、顶层 description/title 等 annotation metadata；只有可无损表达的 schema 进入字段模式。
8. summary 系统节点和系统 edge 增加显式 role marker；切换类型只移除 marker 或可识别的 legacy output pair，普通 DAG、同名用户节点、悬空边均保留，新系统 node/edge ID 会避让冲突。
9. 删除 workflow definition 的同一事务中显式清理 `workflow_run_logs`。

附带修复：built-in ScriptsView 锁定 name/description/owner/status/schema，只开放 code 编辑；workflow references 增加 `task.task_version`、`task.type`，且无活动目标时复制 raw path。

### 验证记录

相关后端套件：

```bash
uv run pytest -q tests/test_workflow_api.py tests/test_workflow_validator.py tests/test_workflow_executor.py tests/test_agent_service.py tests/test_agent_runtime_config_api.py tests/test_workflow_storage.py tests/test_workflow_definition.py tests/test_workflow_references.py tests/test_scripts.py
```

- `131 passed`
- `1 warning`

整仓非外部集成套件：

```bash
PYTHONPATH=. uv run pytest -q -m 'not ragflow and not weknora'
```

- `828 passed`
- `8 deselected`
- `1 warning`
- 首次未带 `PYTHONPATH=.` 的命令因 `tests.test_capability_service` 无法导入而在 collection 阶段退出；按仓库既有验证方式重跑后全部通过。

前端：

```bash
cd frontend/capabilities
node --experimental-strip-types --test tests/*.test.ts
npm run typecheck
npm run build
```

- Node test：`95 passed`
- `npm run typecheck`：通过
- `npm run build`：通过，`2529 modules transformed`
- build 后已恢复 `frontend/capabilities/tsconfig.tsbuildinfo`，未提交缓存 metadata。

### 未执行验证

- 未运行需要真实 RagFlow/Weknora 服务的 8 个外部集成测试。
- 未进行真实浏览器人工点击验收；前端由纯函数测试、组件接线测试、typecheck 和生产 build 覆盖。
- 保留既有非阻断 warning：FastAPI TestClient 的 StarletteDeprecationWarning、Node `DEP0205`、Rollup pure annotation 提示。
