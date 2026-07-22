from datetime import UTC, datetime

from agent_bridge.core.timeutil import local_now, parse_utc, utc_iso, utc_now


def test_utc_now_returns_aware_utc_datetime() -> None:
    value = utc_now()

    assert value.tzinfo is UTC
    assert value.utcoffset().total_seconds() == 0
    assert local_now().tzinfo is not None


def test_utc_iso_normalizes_naive_and_offset_values() -> None:
    assert utc_iso(datetime(2026, 7, 22, 8, 30)) == "2026-07-22T08:30:00+00:00"
    parsed = parse_utc("2026-07-22T16:30:00+08:00")

    assert parsed == datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    assert utc_iso(parsed) == "2026-07-22T08:30:00+00:00"


def test_parse_utc_accepts_legacy_z_and_sqlite_values() -> None:
    assert parse_utc("2026-07-22T08:30:00Z") == datetime(
        2026, 7, 22, 8, 30, tzinfo=UTC
    )
    assert parse_utc("2026-07-22 08:30:00") == datetime(
        2026, 7, 22, 8, 30, tzinfo=UTC
    )
    assert parse_utc("not-a-time") is None
