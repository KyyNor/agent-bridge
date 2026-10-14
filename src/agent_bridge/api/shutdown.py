"""lifespan 收尾步骤执行器：逐步执行、单步隔离异常、总时长封顶。

收尾步骤多为同步阻塞调用（回收工作台/claude-mem/评测子进程、停止调度器），
任何一步卡住都会让进程无法退出。每步在独立 daemon 线程中执行，事件循环只
等待到剩余预算为止；超时的步骤记录后跳过，其线程不阻塞解释器退出。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShutdownStep:
    name: str
    action: Callable[[], object]


async def _run_in_daemon_thread(step: ShutdownStep) -> None:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()

    def _resolve(error: BaseException | None) -> None:
        if future.done():
            return
        if error is None:
            future.set_result(None)
        else:
            future.set_exception(error)

    def _runner() -> None:
        error: BaseException | None = None
        try:
            step.action()
        except BaseException as exc:  # noqa: BLE001 - 交回事件循环统一记录
            error = exc
        # 超时后事件循环可能已关闭，此时结果无人等待，直接丢弃。
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_resolve, error)

    threading.Thread(target=_runner, name=f"shutdown-{step.name}", daemon=True).start()
    await future


async def run_shutdown_steps(steps: list[ShutdownStep], *, budget_seconds: float) -> list[str]:
    """按顺序执行收尾步骤，返回未在预算内完成（超时或被跳过）的步骤名。"""
    deadline = time.monotonic() + budget_seconds
    unfinished: list[str] = []
    for index, step in enumerate(steps):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            skipped = [item.name for item in steps[index:]]
            logger.error("服务收尾总预算 %.0fs 已耗尽，跳过剩余步骤 steps=%s", budget_seconds, skipped)
            unfinished.extend(skipped)
            break
        started = time.monotonic()
        logger.info("服务收尾开始 step=%s", step.name)
        try:
            await asyncio.wait_for(_run_in_daemon_thread(step), timeout=remaining)
        except asyncio.TimeoutError:
            logger.error(
                "服务收尾步骤超时 step=%s 耗时=%.1fs（相关子进程可能未回收）",
                step.name,
                time.monotonic() - started,
            )
            unfinished.append(step.name)
            continue
        except Exception:
            logger.error(
                "服务收尾步骤失败 step=%s 耗时=%.1fs",
                step.name,
                time.monotonic() - started,
                exc_info=True,
            )
            continue
        logger.info("服务收尾完成 step=%s 耗时=%.1fs", step.name, time.monotonic() - started)
    return unfinished
