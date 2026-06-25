"""集中式日志配置：loguru + stdlib ``logging`` 拦截。

设计要点
--------
- 现有代码全部使用 stdlib ``logging.getLogger(__name__)``，本模块通过
  :class:`InterceptHandler` 把这些 stdlib 日志转发进 loguru，**无需改动任何现有调用点**，
  且保留原始的 模块名 / 函数名 / 行号。
- uvicorn 的 ``uvicorn`` / ``uvicorn.error`` / ``uvicorn.access`` 三个 logger 的 handler
  也被替换为 :class:`InterceptHandler`，使 access / error 日志并入同一份应用主日志。
- 主日志文件 ``logs/agent-bridge.log`` 支持 **分卷（100 MB）、最大容量（90 天 / 5 GiB）、
  自动打包（zip）**，详见 :func:`setup_logging`。
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
import sys
import time
from pathlib import Path
from typing import Iterable

from loguru import logger

from .config import AgentBridgePaths

# 保留策略参数（见 :func:`_retention_files`）
_MAX_AGE_DAYS = 90
_MAX_TOTAL_BYTES = 5 * 1024 ** 3  # 5 GiB
_ROTATION_SIZE = "100 MB"

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


def _retention_files(files: Iterable[Path]) -> list[Path]:
    """自定义保留策略：同时满足「不超过 90 天」且「累计不超过 5 GiB」。

    loguru 的 ``retention`` 在每次轮转后接收 **已轮转的分卷文件列表**（不含当前活跃文件），
    返回需要删除的子集。本函数先删超过年龄上限的分卷，再从最旧的开始删，直到累计容量
    回到 5 GiB 以内。
    """
    # 最旧在前，便于「超容量时优先删最旧」
    files = sorted(files, key=lambda p: os.path.getmtime(str(p)))
    now = time.time()
    age_cutoff = now - _MAX_AGE_DAYS * 86400

    to_remove = [f for f in files if os.path.getmtime(str(f)) < age_cutoff]
    survivors = [f for f in files if os.path.getmtime(str(f)) >= age_cutoff]

    total = sum(os.path.getsize(str(f)) for f in survivors)
    for f in survivors:  # 最旧在前
        if total <= _MAX_TOTAL_BYTES:
            break
        to_remove.append(f)
        total -= os.path.getsize(str(f))

    return to_remove


def _configure(paths: AgentBridgePaths, *, level: str, console: bool) -> None:
    """实际装配 loguru sink 与 stdlib 拦截（由 :func:`setup_logging` 调用）。"""
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = paths.logs_dir / "agent-bridge.log"

    # 1) 清空 loguru 默认 sink，统一接管（幂等：重复调用不叠加）
    logger.remove()

    # 2) 主日志：分卷 / 容量上限 / 自动打包
    logger.add(
        log_file,
        level=level,
        format=_LOG_FORMAT,
        rotation=_ROTATION_SIZE,
        retention=_retention_files,
        compression="zip",
        encoding="utf-8",
        enqueue=True,  # 多线程 / 多请求安全写盘
        backtrace=True,
        diagnose=False,  # 生产环境不在 traceback 中泄漏局部变量值
    )

    # 3) 控制台（stderr）；服务子进程下 stderr 会被重定向到 server.log，兼作崩溃捕获
    if console:
        logger.add(
            sys.stderr,
            level=level,
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
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        if not any(isinstance(h, InterceptHandler) for h in uv.handlers):
            uv.handlers = [InterceptHandler()]  # 移除默认 StreamHandler，避免直写 stderr 绕过 loguru
        uv.propagate = False
        uv.setLevel(0)


def setup_logging(paths: AgentBridgePaths, *, level: str = "INFO", console: bool = True) -> None:
    """初始化全局日志。

    应在服务进程最早期调用（``create_app`` 顶部）。幂等：重复调用会先清空既有 sink 再重装，
    因此测试里每次用不同 tmp 路径 ``create_app`` 都能落到对应隔离目录。
    """
    _configure(paths, level=level, console=console)
    logger.info(
        "日志系统已就绪：主日志={} 轮转={} 保留={}天/{}GiB 压缩=zip",
        paths.logs_dir / "agent-bridge.log",
        _ROTATION_SIZE,
        _MAX_AGE_DAYS,
        _MAX_TOTAL_BYTES // (1024 ** 3),
    )
