"""集中式日志配置：loguru + stdlib ``logging`` 拦截。

设计要点
--------
- 现有代码全部使用 stdlib ``logging.getLogger(__name__)``，本模块通过
  :class:`InterceptHandler` 把这些 stdlib 日志转发进 loguru，**无需改动任何现有调用点**，
  且保留原始的 模块名 / 函数名 / 行号。
- uvicorn 的 ``uvicorn`` / ``uvicorn.error`` / ``uvicorn.access`` 三个 logger 的 handler
  也被替换为 :class:`InterceptHandler`，使 access / error 日志并入同一份应用主日志。
- 主日志文件 ``logs/agent-bridge.log`` 支持 **分卷、最大容量、自动打包**；具体参数
  （级别 / 分卷大小 / 保留天数 / 容量上限 / 压缩格式）由 ``server.toml`` 的
  ``[logging]`` 段配置，见 :class:`agent_bridge.core.config.LoggingConfig`。
- root logger 采用 **非破坏式** 装配：仅在尚未挂载 InterceptHandler 时追加，避免破坏
  pytest ``caplog`` 等既有 handler。

调用约定
--------
:func:`setup_logging` 应在服务进程最早期调用一次（``create_app`` 顶部）。它幂等，
重复调用会先清空既有 loguru sink 再重装——因此测试里每次 ``create_app`` 用不同 tmp
路径时，日志文件都会落到对应的隔离目录。
"""
from __future__ import annotations

import inspect
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

from loguru import logger

from .config import AgentBridgePaths, LoggingConfig

# 日志行格式：时间 | 级别 | 模块:函数:行号 - 消息
_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "{message}"
)


class InterceptHandler(logging.Handler):
    """把 stdlib ``logging`` 记录转发到 loguru。

    安装到 root logger（以及各被接管的 logger）后，所有 ``logging.getLogger(...)``
    产生的记录都会进入 loguru 的统一 sink，并保留原始 模块 / 函数 / 行号。
    实现取自 loguru 官方 cookbook。
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging 钩子
        # 把 stdlib 级别名（如 "WARNING"）映射到 loguru 级别名
        try:
            level: str | int = logger.level(record.levelname).name
        except (ValueError, TypeError):
            level = record.levelno

        # 回溯到真正发起日志的栈帧，保证 {name}/{function}/{line} 指向业务代码而非本 handler。
        # loguru 的 depth=0 指向 .log() 调用处（即 emit 自身），故先跨过 emit 这一帧，
        # 再持续上跨所有位于 stdlib logging 模块内部的帧，停在第一个业务帧上。
        frame = inspect.currentframe()
        depth = 1  # 跨过 emit 自身
        if frame is not None:
            frame = frame.f_back
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


class _AccessLogNonSuccessFilter(logging.Filter):
    """``uvicorn.access`` 过滤器：丢弃 2xx/3xx 成功访问行，仅保留 4xx/5xx。

    uvicorn 访问行把状态码作为行尾数字（如 ``... HTTP/1.1" 304``）；匹配不到状态码
    的行一律保留，避免误删自定义格式。
    """

    _STATUS_TAIL = re.compile(r"(\d{3})\s*$")

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - logging 钩子
        m = self._STATUS_TAIL.search(record.getMessage().strip())
        if m and int(m.group(1)) < 400:
            return False  # 2xx/3xx 成功转发 → 丢弃
        return True


def _make_retention(max_age_days: int, max_total_bytes: int) -> Callable[[Iterable[Path]], list[Path]]:
    """构造 loguru ``retention`` callable：同时满足「不超过 max_age_days 天」且「累计不超过 max_total_bytes」。

    loguru 的 ``retention`` 在每次轮转后接收 **已轮转的分卷文件列表**（不含当前活跃文件），
    返回需要删除的子集。本 callable 先删超过年龄上限的分卷，再从最旧的开始删，直到累计容量
    回到上限内。用闭包而非模块常量，便于按 :class:`LoggingConfig` 注入参数。
    """
    def _retention(files: Iterable[Path]) -> list[Path]:
        # 最旧在前，便于「超容量时优先删最旧」
        files = sorted(files, key=lambda p: os.path.getmtime(str(p)))
        now = time.time()
        age_cutoff = now - max_age_days * 86400

        to_remove = [f for f in files if os.path.getmtime(str(f)) < age_cutoff]
        survivors = [f for f in files if os.path.getmtime(str(f)) >= age_cutoff]

        total = sum(os.path.getsize(str(f)) for f in survivors)
        for f in survivors:  # 最旧在前
            if total <= max_total_bytes:
                break
            to_remove.append(f)
            total -= os.path.getsize(str(f))

        return to_remove

    return _retention


def _configure(paths: AgentBridgePaths, cfg: LoggingConfig) -> None:
    """实际装配 loguru sink 与 stdlib 拦截（由 :func:`setup_logging` 调用）。"""
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = paths.logs_dir / "agent-bridge.log"

    # 1) 清空 loguru 默认 sink，统一接管（幂等：重复调用不叠加）
    logger.remove()

    # 2) 主日志：分卷 / 容量上限 / 自动打包（参数全部来自 LoggingConfig）
    logger.add(
        log_file,
        level=cfg.level,
        format=_LOG_FORMAT,
        rotation=cfg.rotation,
        retention=_make_retention(cfg.retention_days, cfg.retention_max_bytes),
        compression=cfg.compression,
        encoding="utf-8",
        enqueue=True,  # 多线程 / 多请求安全写盘
        backtrace=True,
        diagnose=False,  # 生产环境不在 traceback 中泄漏局部变量值
    )

    # 3) 控制台（stderr）；服务子进程下 stderr 会被重定向到 server.log，兼作崩溃捕获
    if cfg.console:
        logger.add(
            sys.stderr,
            level=cfg.level,
            format=_LOG_FORMAT,
            colorize=True,
            backtrace=True,
            diagnose=False,
        )

    # 4) 接管 stdlib logging：root 非破坏式追加 + uvicorn.* 替换
    root = logging.getLogger()
    root.setLevel(0)  # 放行所有级别，由 loguru sink 做最终过滤
    if not any(isinstance(h, InterceptHandler) for h in root.handlers):
        root.addHandler(InterceptHandler())
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        if not any(isinstance(h, InterceptHandler) for h in uv.handlers):
            uv.handlers = [InterceptHandler()]  # 移除默认 StreamHandler，避免直写 stderr 绕过 loguru
        uv.propagate = False
        uv.setLevel(0)
    # uvicorn.access 按配置过滤：all / errors_only（仅 4xx5xx）/ off
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(h, InterceptHandler) for h in access.handlers):
        access.handlers = [InterceptHandler()]
    access.propagate = False
    # 先摘除既有的成功请求过滤器（幂等：支持重复 setup_logging 切换模式）
    for flt in [f for f in access.filters if isinstance(f, _AccessLogNonSuccessFilter)]:
        access.removeFilter(flt)
    if cfg.access_log == "off":
        # uvicorn.access 仅在 INFO 打访问行；提到 WARNING 即整体静默
        access.setLevel(logging.WARNING)
    else:
        access.setLevel(0)
        if cfg.access_log == "errors_only":
            access.addFilter(_AccessLogNonSuccessFilter())
    # httpx 每请求转发日志按配置级别；默认 WARNING 屏蔽「纯转发 200」噪音，保留超时等告警
    logging.getLogger("httpx").setLevel(cfg.httpx_log_level)


def setup_logging(paths: AgentBridgePaths, *, config: LoggingConfig | None = None) -> None:
    """初始化全局日志。

    应在服务进程最早期调用（``create_app`` 顶部）。``config`` 缺省时用
    :class:`LoggingConfig` 默认值；生产路径由 ``create_app`` 传入
    :func:`load_logging_config` 的解析结果。幂等：重复调用会先清空既有 sink 再重装，
    因此测试里每次用不同 tmp 路径 ``create_app`` 都能落到对应隔离目录。
    """
    cfg = config or LoggingConfig()
    _configure(paths, cfg)
    logger.info(
        "日志系统已就绪：主日志={} 级别={} 轮转={} 保留={}天/{}字节 压缩={}",
        paths.logs_dir / "agent-bridge.log",
        cfg.level,
        cfg.rotation,
        cfg.retention_days,
        cfg.retention_max_bytes,
        cfg.compression,
    )
