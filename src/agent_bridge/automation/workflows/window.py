"""工作流每日执行窗口的时间计算。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any


def parse_hhmm(value: Any) -> time | None:
    """解析工作流执行窗口的 ``HH:MM`` 配置。"""
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        return None


def window_anchor(
    now: datetime,
    start: time | None,
    stop: time | None,
) -> date | None:
    """返回当前已开启窗口的起始日期；窗口外返回 ``None``。"""
    current = now.timetz().replace(tzinfo=None)
    if start is None and stop is None:
        return now.date()
    if start is None:
        return now.date() if current < stop else None
    if stop is None:
        return now.date() if current >= start else None
    if start <= stop:
        return now.date() if start <= current < stop else None
    if current >= start:
        return now.date()
    if current < stop:
        return now.date() - timedelta(days=1)
    return None


def previous_window_bounds(
    *,
    now: datetime,
    start: time | None,
    stop: time | None,
) -> tuple[datetime, datetime]:
    """返回最近一个已结束执行窗口的本地起止时间，区间左闭右开。"""
    tzinfo = now.tzinfo
    current = now.timetz().replace(tzinfo=None)

    def at(day: date, clock: time) -> datetime:
        return datetime.combine(day, clock, tzinfo=tzinfo)

    if start is None and stop is None:
        return at(now.date() - timedelta(days=1), time.min), at(now.date(), time.min)

    if start is None:
        anchor = now.date() if current >= stop else now.date() - timedelta(days=1)
        return at(anchor, time.min), at(anchor, stop)

    if stop is None:
        anchor = now.date() - timedelta(days=1)
        return at(anchor, start), at(anchor + timedelta(days=1), time.min)

    if start <= stop:
        anchor = now.date() if current >= stop else now.date() - timedelta(days=1)
        return at(anchor, start), at(anchor, stop)

    # Overnight window: if the current time is before stop, the active window
    # started yesterday, so the previous completed one started two days ago.
    anchor = now.date() - timedelta(days=2) if current < stop else now.date() - timedelta(days=1)
    return at(anchor, start), at(anchor + timedelta(days=1), stop)
