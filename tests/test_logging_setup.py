"""``core/logging.py`` 与日志配置的单测：loguru 装配、stdlib 拦截、保留策略、
uvicorn 接管、``server.toml`` 的 ``[logging]`` 段解析。"""
from __future__ import annotations

import io
import logging
import os
import time
from pathlib import Path

from loguru import logger

from agent_bridge.core.config import (
    AgentBridgePaths,
    LoggingConfig,
    load_logging_config,
    parse_size,
)
from agent_bridge.core.logging import (
    InterceptHandler,
    _AccessLogNonSuccessFilter,
    _make_retention,
    setup_logging,
)


def _paths(tmp_path: Path) -> AgentBridgePaths:
    return AgentBridgePaths.from_root(tmp_path / "agent-bridge")


def _no_console() -> LoggingConfig:
    return LoggingConfig(console=False)


def test_file_sink_writes_to_agent_bridge_log(tmp_path: Path) -> None:
    """loguru 主文件 sink 落到 paths.logs_dir/agent-bridge.log 并能写入中文。"""
    paths = _paths(tmp_path)
    try:
        setup_logging(paths, config=_no_console())
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


def test_setup_logging_respects_config_level(tmp_path: Path) -> None:
    """LoggingConfig.level 驱动 sink 级别：DEBUG 配置下 stdlib debug 日志能落盘。"""
    paths = _paths(tmp_path)
    try:
        setup_logging(paths, config=LoggingConfig(level="DEBUG", console=False))
        logging.getLogger("agent_bridge.tests.cfg").debug("debug 级 marker")
        logger.complete()
    finally:
        logger.remove()

    content = (paths.logs_dir / "agent-bridge.log").read_text(encoding="utf-8")
    assert "debug 级 marker" in content, "DEBUG 配置未放行 debug 日志"


def test_intercept_handler_routes_stdlib_to_loguru(tmp_path: Path) -> None:
    """stdlib logging.getLogger(...).info(...) 经 InterceptHandler 进入 loguru。"""
    try:
        setup_logging(paths=_paths(tmp_path), config=_no_console())
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
        setup_logging(paths=_paths(tmp_path), config=_no_console())
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


def test_retention_drops_old_and_oversized(tmp_path: Path) -> None:
    """保留策略：先删超龄分卷，再按最旧优先删到累计容量上限内。"""
    retention = _make_retention(max_age_days=1, max_total_bytes=200)

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

    removed = set(retention([old, f1, f2, f3]))

    assert old in removed              # 超龄
    assert f1 in removed               # 累计 250 > 200，最旧的 f1 被删
    assert f2 not in removed           # 删 f1 后累计 130 <= 200，停止
    assert f3 not in removed


def test_uvicorn_loggers_intercepted(tmp_path: Path) -> None:
    """uvicorn.* 三个 logger 的 handler 被替换为 InterceptHandler。"""
    try:
        setup_logging(paths=_paths(tmp_path), config=_no_console())
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
        setup_logging(paths=_paths(tmp_path), config=_no_console())
        setup_logging(paths=_paths(tmp_path), config=_no_console())  # 第二次，幂等
        intercept_count = sum(1 for h in root.handlers if isinstance(h, InterceptHandler))
        assert intercept_count == 1, "root 上叠加了多个 InterceptHandler"
        assert marker in root.handlers, "既有 handler（如 caplog）被破坏性清除"
    finally:
        root.removeHandler(marker)
        logger.remove()


# ---------- server.toml [logging] 解析 ----------

def test_parse_size_supports_units() -> None:
    assert parse_size(1024) == 1024
    assert parse_size("1024") == 1024
    assert parse_size("500 MB") == 500 * 1000 ** 2
    assert parse_size("5 GiB") == 5 * 1024 ** 3
    assert parse_size("2GB") == 2 * 1000 ** 3
    try:
        parse_size("5 xlbot")
        raise AssertionError("应拒绝未知单位")
    except ValueError:
        pass


def test_load_logging_config_defaults_when_absent(tmp_path: Path) -> None:
    """server.toml 不存在或无 [logging] 段时，返回全默认值。"""
    paths = _paths(tmp_path)
    assert load_logging_config(paths) == LoggingConfig()

    # 有 server.toml 但无 [logging] 段 → 同样默认
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.server_config_path.write_text('host = "127.0.0.1"\nport = 8765\n', encoding="utf-8")
    assert load_logging_config(paths) == LoggingConfig()


def test_load_logging_config_parses_section(tmp_path: Path) -> None:
    """[logging] 段的自定义值被正确解析（含容量字符串解析）。"""
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.server_config_path.write_text(
        "[logging]\n"
        'level = "DEBUG"\n'
        "console = false\n"
        "rotation_size_mb = 25\n"
        "retention_days = 14\n"
        'retention_max_bytes = "500 MB"\n'
        'compression = "tar.gz"\n',
        encoding="utf-8",
    )

    cfg = load_logging_config(paths)
    assert cfg.level == "DEBUG"
    assert cfg.console is False
    assert cfg.rotation_size_mb == 25
    assert cfg.rotation == "25 MB"
    assert cfg.retention_days == 14
    assert cfg.retention_max_bytes == 500 * 1000 ** 2
    assert cfg.compression == "tar.gz"


# ---------- 访问日志 / httpx 转发噪音过滤 ----------

def _access_record(message: str) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, message, None, None)


def test_access_filter_drops_success_keeps_errors() -> None:
    """_AccessLogNonSuccessFilter 丢弃 2xx/3xx，保留 4xx/5xx 与无法解析的行。"""
    flt = _AccessLogNonSuccessFilter()
    # 2xx/3xx 成功转发 → 丢弃
    assert flt.filter(_access_record('127.0.0.1:1 - "GET /a HTTP/1.1" 200')) is False
    assert flt.filter(_access_record('127.0.0.1:1 - "GET /icon.svg HTTP/1.1" 304')) is False
    # 4xx/5xx → 保留
    assert flt.filter(_access_record('127.0.0.1:1 - "POST /x HTTP/1.1" 404')) is True
    assert flt.filter(_access_record('127.0.0.1:1 - "GET /x HTTP/1.1" 500')) is True
    # 无状态码行 → 保留（不误删）
    assert flt.filter(_access_record("some non-access message")) is True


def test_configure_silences_httpx_and_access_by_default(tmp_path: Path) -> None:
    """默认配置：httpx 提到 WARNING；uvicorn.access 仅保留 4xx5xx。"""
    try:
        setup_logging(paths=_paths(tmp_path), config=LoggingConfig(console=False))
        assert logging.getLogger("httpx").level == logging.WARNING
        access = logging.getLogger("uvicorn.access")
        assert access.level == 0  # NOTSET → 由过滤器而非级别阻断成功请求
        assert any(isinstance(f, _AccessLogNonSuccessFilter) for f in access.filters)
    finally:
        logger.remove()


def test_configure_access_log_modes(tmp_path: Path) -> None:
    """access_log=off → 整体静默（WARNING）；access_log=all → 不过滤。"""
    try:
        # off
        setup_logging(paths=_paths(tmp_path), config=LoggingConfig(console=False, access_log="off"))
        access = logging.getLogger("uvicorn.access")
        assert access.level == logging.WARNING
        assert not any(isinstance(f, _AccessLogNonSuccessFilter) for f in access.filters)
        # all
        setup_logging(paths=_paths(tmp_path), config=LoggingConfig(console=False, access_log="all"))
        assert access.level == 0
        assert not any(isinstance(f, _AccessLogNonSuccessFilter) for f in access.filters)
    finally:
        logger.remove()


def test_configure_httpx_log_level_respected(tmp_path: Path) -> None:
    """httpx_log_level 配置项驱动 httpx logger 级别。"""
    try:
        setup_logging(paths=_paths(tmp_path), config=LoggingConfig(console=False, httpx_log_level="DEBUG"))
        assert logging.getLogger("httpx").level == logging.DEBUG
    finally:
        logger.remove()

