"""``core/logging.py`` 的单测：loguru 装配、stdlib 拦截、保留策略、uvicorn 接管。"""
from __future__ import annotations

import io
import logging
import os
import time
from pathlib import Path

from loguru import logger

from agent_bridge.core import logging as ab_logging
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.logging import InterceptHandler, _retention_files, setup_logging


def _paths(tmp_path: Path) -> AgentBridgePaths:
    return AgentBridgePaths.from_root(tmp_path / "agent-bridge")


def test_file_sink_writes_to_agent_bridge_log(tmp_path: Path) -> None:
    """loguru 主文件 sink 落到 paths.logs_dir/agent-bridge.log 并能写入中文。"""
    paths = _paths(tmp_path)
    try:
        setup_logging(paths, console=False)
        logger.info("文件 sink 中文 marker")
        logger.complete()  # 等待 enqueue 队列处理完
    finally:
        logger.remove()  # 关闭并 flush 所有 sink

    log_file = paths.logs_dir / "agent-bridge.log"
    assert log_file.exists(), "agent-bridge.log 未生成"
    content = log_file.read_text(encoding="utf-8")
    assert "文件 sink 中文 marker" in content
    # 格式应包含模块:函数:行号 段
    assert "test_file_sink_writes_to_agent_bridge_log" in content


def test_intercept_handler_routes_stdlib_to_loguru(tmp_path: Path) -> None:
    """stdlib logging.getLogger(...).info(...) 经 InterceptHandler 进入 loguru。"""
    try:
        setup_logging(paths=_paths(tmp_path), console=False)
        # 额外加一个同步内存 sink 捕获路由结果，避免文件缓冲时序问题
        buf = io.StringIO()
        logger.add(buf, format="{message}", enqueue=False, level="DEBUG")

        std = logging.getLogger("agent_bridge.tests.intercept")
        std.info("stdlib 路由 marker 数值=%s", 42)
        logger.complete()
    finally:
        logger.remove()

    captured = buf.getvalue()
    assert "stdlib 路由 marker 数值=42" in captured


def test_intercept_handler_preserves_level_and_exception(tmp_path: Path) -> None:
    """ERROR 级别与异常栈也能正确路由（stdlib exception() 固定 ERROR）。"""
    try:
        setup_logging(paths=_paths(tmp_path), console=False)
        buf = io.StringIO()
        logger.add(buf, format="{level}|{message}", enqueue=False, level="DEBUG")
        std = logging.getLogger("agent_bridge.tests.level")
        try:
            raise ValueError("boom")
        except ValueError:
            std.exception("带异常的警告")
        logger.complete()
    finally:
        logger.remove()

    captured = buf.getvalue()
    assert "ERROR" in captured
    assert "带异常的警告" in captured
    assert "ValueError" in captured  # 异常类型随栈路由


def test_retention_drops_old_and_oversized(tmp_path: Path, monkeypatch) -> None:
    """保留策略：先删超龄分卷，再按最旧优先删到累计容量上限内。"""
    monkeypatch.setattr(ab_logging, "_MAX_AGE_DAYS", 1)      # 1 天
    monkeypatch.setattr(ab_logging, "_MAX_TOTAL_BYTES", 200)  # 200 字节

    now = time.time()

    def mk(name: str, age_days: float, size: int) -> Path:
        p = tmp_path / name
        p.write_bytes(b"x" * size)
        os.utime(p, (now, now - age_days * 86400))
        return p

    old = mk("old.zip", age_days=2.0, size=50)        # 超龄 → 必删
    f1 = mk("f1.zip", age_days=0.5, size=120)          # 未超龄但较旧、较大
    f2 = mk("f2.zip", age_days=0.3, size=120)          # 未超龄
    f3 = mk("f3.zip", age_days=0.1, size=10)           # 未超龄、小 → 保留

    removed = set(_retention_files([old, f1, f2, f3]))

    assert old in removed              # 超龄
    assert f1 in removed               # 累计 250 > 200，最旧的 f1 被删
    assert f2 not in removed           # 删 f1 后累计 130 <= 200，停止
    assert f3 not in removed


def test_uvicorn_loggers_intercepted(tmp_path: Path) -> None:
    """uvicorn.* 三个 logger 的 handler 被替换为 InterceptHandler。"""
    try:
        setup_logging(paths=_paths(tmp_path), console=False)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uv = logging.getLogger(name)
            assert any(isinstance(h, InterceptHandler) for h in uv.handlers), name
            assert uv.propagate is False
    finally:
        logger.remove()


def test_root_intercept_is_idempotent_and_non_destructive(tmp_path: Path) -> None:
    """重复 setup_logging 不在 root 叠加多个 InterceptHandler；既有 handler 不被清除。"""
    class MarkerHandler(logging.Handler):
        def emit(self, record):  # noqa: D401
            pass

    root = logging.getLogger()
    marker = MarkerHandler()
    root.addHandler(marker)
    try:
        setup_logging(paths=_paths(tmp_path), console=False)
        setup_logging(paths=_paths(tmp_path), console=False)  # 第二次，幂等
        intercept_count = sum(1 for h in root.handlers if isinstance(h, InterceptHandler))
        assert intercept_count == 1, "root 上叠加了多个 InterceptHandler"
        assert marker in root.handlers, "既有 handler（如 caplog）被破坏性清除"
    finally:
        root.removeHandler(marker)
        logger.remove()
