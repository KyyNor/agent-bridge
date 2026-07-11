# design_script

你正在为 Agent Bridge 编写“受控脚本”（managed script）。先理解用户目标，再维护脚本主体：

1. `script.py`：运行在 Agent Bridge 脚本沙箱中的 Python 脚本。
2. `input_schema`：根 `type` 必须为 `object` 的 JSON Schema，声明每个输入字段的类型、描述和 `required` 字段。

## 脚本运行协议（务必先理解）

脚本不再通过 `stdout` 输出业务结果，而是实现：

```python
def main(envelope) -> dict:
    ...
    return {...}
```

运行器会把 `main(envelope)` 的返回值写入 `result.json` 并作为最终结果回传；`print(...)` / `stdout` 仅用于日志展示。

### envelope 字段约定

- `envelope["run_id"]`：本次脚本运行 ID。
- `envelope["run_type"]`：运行类型，如 `test` / `mcp`。
- `envelope["script_key"]`：脚本标识。
- `envelope["script_params"]`：调用方传入的参数对象。
- `envelope["profile_key"]`：本次执行继承的 profile。
- `envelope["workflow"]`：工作流上下文：
  - `enabled`
  - `workflow_key`
  - `run_id`

## 铁律

- ✅ 必须导出 `main(envelope)`，且返回值必须是 JSON 对象（Python `dict`）。
- ✅ 自定义业务结果走 `return {...}`，调试日志走 `print(...)`。
- ✅ 调其他能力时用 `from agent_bridge_runtime import execute`。
- ✅ 调工作流顶级能力时用 `workflow_get_task` / `workflow_set_task` / `workflow_run_log`。
- ✅ `execute(...)` 的权限始终继承本次执行 header 的 profile，不能脚本内自带 profile 提权。
- ✅ workflow helper 只有在完整 workflow context 下才能调用。
- ❌ 不要再把“打印一段 JSON”当成返回协议。
- ❌ 不要给 `execute(...)` 传 `profile_key`。
- ❌ 不要在缺少 workflow headers 的情况下调用 workflow helper。

## 最小骨架

```python
from agent_bridge_runtime import execute


def main(envelope):
    params = envelope["script_params"]
    skill = execute("built-in", "load_skill", {"skill_name": "design_workflow"})
    return {
        "ok": True,
        "params": params,
        "profile_key": envelope["profile_key"],
        "workflow": envelope["workflow"],
        "skill_name": skill["result"]["skill_name"],
    }
```

## 调用其他 MCP

使用：

```python
from agent_bridge_runtime import execute


def main(envelope):
    result = execute("built-in", "load_skill", {"skill_name": "design_workflow"})
    return {"result": result["result"]}
```

约束：

- `execute(service, tool_name, params=None)` 只接受 service / tool_name / params。
- profile 不由脚本传入，而是由运行时环境自动附带到请求头。
- 因此脚本调用其他 MCP 时，仍会按照来源 profile 做权限校验。

## 调用 workflow 顶级工具

使用：

```python
from agent_bridge_runtime import workflow_get_task, workflow_run_log


def main(envelope):
    leased = workflow_get_task()
    task = leased["task"]
    workflow_run_log(
        level="info",
        stage="lease",
        message="leased task",
        task_key=task["task_key"] if task else None,
    )
    return {"task": task}
```

可用 helper：

- `workflow_get_task()`
- `workflow_set_task(tasks)`
- `workflow_run_log(level="info", stage="", message="", task_key=None, payload=None)`

要求：

- 只有完整 workflow context 存在时才能调用。
- 缺少上下文时会直接报：`workflow context is required`。

## 返回格式建议

返回值应保持稳定、扁平、可序列化，例如：

```python
def main(envelope):
    params = envelope["script_params"]
    return {
        "status": "ok",
        "input": params,
        "summary": f"processed {len(params)} keys",
    }
```

避免：

- 返回列表 / 字符串 / 数字作为顶层结果。
- 返回不可 JSON 序列化对象。
- 既 `print(JSON)` 又 `return dict` 造成双重协议。

## 智能体协作方式

如果用户要求智能体协助编写脚本，应提示智能体先读取本技能：

```text
请执行 execute service='built-in' tool_name='load_skill' params={"skill_name":"design_script"} 读取技能，
然后参照技能内容与我的需求，完成 script.py。
```

智能体完成后应检查：

- 是否实现了 `main(envelope)` 且返回 `dict`？
- 是否正确读取了 `envelope["script_params"]`？
- 是否把业务结果放在 `return`，日志放在 `print`？
- 是否使用 `execute(...)` 调用 MCP，而没有传 `profile_key`？
- 如果用了 workflow helper，是否依赖完整 workflow context？
- 返回值是否稳定、易于消费、能被 JSON 序列化？
- 是否提供了合法的 object `input_schema`，并覆盖脚本读取的全部参数？
