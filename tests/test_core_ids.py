from __future__ import annotations

import time

from agent_bridge.core.ids import new_run_id, uuid7


def test_uuid7_reports_version_7() -> None:
    assert uuid7().version == 7


def test_uuid7_hex_sorts_chronologically() -> None:
    values = []
    for _ in range(10):
        values.append(uuid7().hex)
        time.sleep(0.002)
    assert values == sorted(values)


def test_new_run_id_format() -> None:
    run_id = new_run_id("my-workflow")
    assert run_id.startswith("my-workflow_")
    suffix = run_id.removeprefix("my-workflow_")
    assert len(suffix) == 32  # uuid7 .hex length
    assert suffix != uuid7().hex  # unique per call


def test_new_run_id_sanitizes_and_handles_empty_prefix() -> None:
    dirty = new_run_id("Daily Report!")
    assert " " not in dirty and "!" not in dirty
    assert dirty.startswith("Daily-Report_")
    assert new_run_id("").startswith("run_")
    assert new_run_id(None).startswith("run_")
