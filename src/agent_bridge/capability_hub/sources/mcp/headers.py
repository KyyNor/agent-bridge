"""合并 MCP 请求 Header 的逐项编辑。"""

import re
from typing import Any

from agent_bridge.core.domain import ValidationError

_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HEADER_VALUE = re.compile(r"[\t\x20-\x7e]*")


def validate_header(name: str, value: Any) -> None:
    if not _HEADER_NAME.fullmatch(name):
        raise ValidationError("Header 名称须使用合法的 HTTP 字段名")
    if (
        not isinstance(value, str) or not _HEADER_VALUE.fullmatch(value) or value != value.strip(" \t")
    ):
        raise ValidationError("Header 值须为不含换行或首尾空白的 ASCII 文本")


def apply_header_updates(
    stored: dict[str, Any], updates: dict[str, str | None]
) -> dict[str, Any]:
    names: set[str] = set()
    for name, value in updates.items():
        validate_header(name, "" if value is None else value)
        normalized = name.lower()
        if normalized in names:
            raise ValidationError("Header 名称不能重复（不区分大小写）")
        names.add(normalized)
    return {
        **{name: value for name, value in stored.items() if name.lower() not in names},
        **{name: value for name, value in updates.items() if value is not None},
    }
