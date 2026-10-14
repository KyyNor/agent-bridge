from __future__ import annotations

import asyncio
import threading
import time

from agent_bridge.api.shutdown import ShutdownStep, run_shutdown_steps


def test_shutdown_steps_run_in_order_and_isolate_failures() -> None:
    calls: list[str] = []

    def failing() -> None:
        calls.append("failing")
        raise RuntimeError("boom")

    steps = [
        ShutdownStep("first", lambda: calls.append("first")),
        ShutdownStep("failing", failing),
        ShutdownStep("last", lambda: calls.append("last")),
    ]

    unfinished = asyncio.run(run_shutdown_steps(steps, budget_seconds=5))

    assert calls == ["first", "failing", "last"]
    assert unfinished == []


def test_shutdown_steps_bound_hanging_step_and_skip_rest_when_budget_exhausted() -> None:
    """回归：任一收尾步骤阻塞都不得让进程无限挂起。"""
    release = threading.Event()
    calls: list[str] = []

    steps = [
        ShutdownStep("hanging", lambda: release.wait(30)),
        ShutdownStep("after", lambda: calls.append("after")),
    ]

    started = time.monotonic()
    try:
        unfinished = asyncio.run(run_shutdown_steps(steps, budget_seconds=0.3))
    finally:
        release.set()

    assert time.monotonic() - started < 5
    assert unfinished == ["hanging", "after"]
    assert calls == []
