"""项目统一时间处理工具。

业务时间统一使用带时区的 UTC ``datetime``，序列化统一输出
ISO 8601 ``+00:00`` 后缀。读取历史数据时仍兼容 ``Z`` 和无时区值。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(UTC)


def local_now() -> datetime:
    """返回带系统本地时区的当前时间，仅用于本地日历/调度语义。"""
    return utc_now().astimezone()


def utc_iso(value: datetime | None = None) -> str:
    """将时间统一序列化为带 ``+00:00`` 的 UTC ISO 8601 字符串。"""
    resolved = value or utc_now()
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=UTC)
    else:
        resolved = resolved.astimezone(UTC)
    return resolved.isoformat()


def parse_utc(value: Any) -> datetime | None:
    """解析 ISO 8601 或 SQLite 默认时间字符串，统一返回 aware UTC。"""
    if not value:
        return None
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
