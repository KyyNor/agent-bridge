from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_bridge.automation.workflows.definition import EdgeCondition

REFERENCE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")


@dataclass(frozen=True)
class ConditionResult:
    matched: bool
    actual: Any


class MissingReferenceError(ValueError):
    def __init__(self, path: str) -> None:
        super().__init__(f"引用字段不存在: {path}")
        self.path = path


_MISSING = object()


def parse_reference(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = REFERENCE_RE.fullmatch(value)
    return match.group(1) if match else None


def resolve_path(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise MissingReferenceError(path)
        value = value[part]
    return value


def render_text(template: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        return str(resolve_path(context, match.group(1)))

    return REFERENCE_RE.sub(replace, template)


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item, context) for item in value]
    if not isinstance(value, str):
        return value
    path = parse_reference(value)
    return resolve_path(context, path) if path else render_text(value, context)


def evaluate_condition(condition: EdgeCondition | None, context: dict[str, Any]) -> ConditionResult:
    if condition is None:
        return ConditionResult(matched=True, actual=None)
    try:
        actual = resolve_path(context, condition.field)
    except MissingReferenceError:
        actual = _MISSING
    if condition.operator == "exists":
        return ConditionResult(actual is not _MISSING, None if actual is _MISSING else actual)
    if condition.operator == "not_exists":
        return ConditionResult(actual is _MISSING, None if actual is _MISSING else actual)
    if actual is _MISSING:
        return ConditionResult(False, None)
    if condition.operator == "equals":
        return ConditionResult(actual == condition.value, actual)
    if condition.operator == "not_equals":
        return ConditionResult(actual != condition.value, actual)
    if condition.operator == "contains":
        try:
            return ConditionResult(condition.value in actual, actual)
        except TypeError:
            return ConditionResult(False, actual)
    return ConditionResult(False, actual)
