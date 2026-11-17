"""execute 参数的无损包装兼容与目标 Schema 校验。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from jsonschema import exceptions, validators
from referencing import Registry
from referencing.exceptions import Unresolvable

from agent_bridge.core.domain import ValidationError

logger = logging.getLogger(__name__)
_ROUTE_FIELDS = frozenset({"service", "tool_name", "params"})


def _same_json(left: Any, right: Any) -> bool:
    """按 JSON 类型和值判等，避免 Python 把 true 与 1 判为相同。"""
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def normalize_execute_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """只移动业务字段的包装位置；冲突、类型错误均不猜测。"""
    started = time.monotonic()
    for name in ("service", "tool_name"):
        if not isinstance(arguments.get(name), str) or not arguments[name].strip():
            raise ValidationError(f"execute.{name} 必须是非空字符串；本次未执行。")
    params = arguments.get("params", {})
    if not isinstance(params, dict):
        raise ValidationError("execute.params 必须是 JSON 对象；本次未执行，不会自动解析字符串或转换类型。")
    flattened = {key: value for key, value in arguments.items() if key not in _ROUTE_FIELDS}
    conflicts = [key for key, value in flattened.items() if key in params and not _same_json(params[key], value)]
    if conflicts:
        raise ValidationError(f"execute 参数冲突：字段 {conflicts!r} 在顶层与 params 内的值不同；请仅在 params 内保留确定的值。本次未执行。")
    if flattened:
        logger.info(
            "MCP 参数兼容阶段完成 service=%s tool=%s 状态=已归一化 平铺字段数=%s 耗时=%.0fms",
            arguments["service"], arguments["tool_name"], len(flattened), (time.monotonic() - started) * 1000,
        )
    return {"service": arguments["service"], "tool_name": arguments["tool_name"], "params": {**flattened, **params}}


def validate_execute_params(params: dict[str, Any], schema: dict[str, Any]) -> None:
    """校验目标参数；错误只报告结构，不回显参数值或上游 Schema 内容。"""
    try:
        validator_type = validators.validator_for(schema)
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=Registry())
        error = next(validator.iter_errors(params), None)
        if error is None:
            return
        if len(params) == 1:
            wrapper_value = next(iter(params.values()))
            if isinstance(wrapper_value, dict) and validator.is_valid(wrapper_value):
                raise ValidationError(
                    "目标参数结构不符：疑似把 params 包装字段写成了其他名称。本次未执行。"
                    '请将该内层对象原样放到 params 内，保留原 service 与 tool_name。'
                    '标准结构：{"service":"服务标识","tool_name":"工具名","params":{}}。'
                )
        path = ".".join(str(part) for part in error.absolute_path) or "params"
        detail = f"字段：{path}，规则：{error.validator}"
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = [name for name in error.validator_value if name not in error.instance]
            detail += f"，缺少字段：{missing!r}"
        raise ValidationError(
            f"目标参数 Schema 校验失败（{detail}）；"
            "本次未执行。请按 search 返回的 input_schema 将原始业务字段放入 params，保留规定类型与必填字段。"
        )
    except ValidationError:
        raise
    except (exceptions.SchemaError, Unresolvable) as exc:
        raise ValidationError("目标工具 Schema 无效或包含无法解析的引用；本次未执行，请联系管理员检查工具定义。") from exc
