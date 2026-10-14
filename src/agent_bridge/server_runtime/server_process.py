"""管理 uvicorn 服务器子进程；与 ``agent_runtime`` 的 Agent 执行职责无关。"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.config import ROOT_ENV_VAR, AgentBridgePaths, load_server_config

logger = logging.getLogger(__name__)

_LOG_CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "logging.json")
_STARTUP_HEALTH_TIMEOUT_SECONDS = 15.0
# WebSocket 单条消息上限（uvicorn ``--ws-max-size``）。工作台代理连接上游时使用
# 同一个值，保证浏览器 ↔ 代理 ↔ 工作台两段上限一致。
WS_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# uvicorn ``--timeout-graceful-shutdown``：SIGTERM 后等待在途连接结束的上限。
# 默认 None 会无限等待，SSE（Agent run 事件、claude-mem /stream、Pi 聊天流）
# 这类长连接会让 lifespan 收尾永远无法开始；超时后 uvicorn 取消剩余请求任务，
# 再进入 lifespan 收尾。
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 10
# lifespan 收尾（回收工作台/claude-mem/评测等子进程、停止调度器）的总时长上限。
LIFESPAN_SHUTDOWN_BUDGET_SECONDS = 60.0
# ``server stop`` 等待 SIGTERM 生效的上限：覆盖连接排空 + lifespan 收尾，再留
# 余量给解释器退出；超时升级为 SIGKILL。
_STOP_TIMEOUT_SECONDS = GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS + LIFESPAN_SHUTDOWN_BUDGET_SECONDS + 15.0
_KILL_WAIT_SECONDS = 5.0
_STOP_POLL_INTERVAL_SECONDS = 0.2
# 用于确认 PID 文件指向的仍是本服务进程（防 PID 复用误杀其他进程）。
_SERVER_CMDLINE_MARKER = "agent_bridge.api.app"


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    raw_pid = path.read_text(encoding="utf-8").strip()
    try:
        return int(raw_pid)
    except ValueError as exc:
        raise RuntimeError(f"invalid pid file: {path}") from exc


def _reap_if_child(pid: int) -> None:
    """若 pid 是当前进程的子进程且已退出，回收僵尸，避免被误判为存活。"""
    wnohang = getattr(os, "WNOHANG", None)
    if wnohang is None:
        return
    # 非子进程抛 ChildProcessError（OSError 子类），属正常情况。
    with suppress(OSError):
        os.waitpid(pid, wnohang)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    _reap_if_child(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_server_process(pid: int) -> bool | None:
    """判断 pid 是否为本服务的 uvicorn 进程；无法判断时返回 ``None``。"""
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
    except FileNotFoundError:
        if Path("/proc/self").exists():
            return False
        raw = None
    except OSError:
        return None
    if raw is None:
        try:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return _SERVER_CMDLINE_MARKER in result.stdout
    return _SERVER_CMDLINE_MARKER.encode("ascii") in raw


def _server_alive(pid: int) -> bool:
    """PID 存活且（可判断时）确为本服务进程；PID 已被复用视为不存活。"""
    if not _pid_alive(pid):
        return False
    if _is_server_process(pid) is False:
        logger.warning("PID=%s 仍存活但不是 Agent Bridge 服务进程（PID 已被复用），视为已停止", pid)
        return False
    return True


def _health_ok(host: str, port: int, *, timeout: float) -> bool:
    try:
        response = httpx.get(f"http://{host}:{port}/health", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _wait_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not _pid_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)


def server_status(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    """按进程存活判断运行状态；``healthy`` 单独反映 ``/health`` 是否可达。

    不能用健康检查代替存活判断：进程繁忙或正在收尾时 ``/health`` 可能超时，
    若据此报告“未运行”，``start`` 会再拉起一个实例并覆盖 PID 文件，旧进程
    成为无人管理的孤儿。
    """
    resolved = paths or AgentBridgePaths.from_root()
    pid = _read_pid(resolved.server_pid_path)
    if pid is None:
        return {"running": False, "healthy": False, "pid": None}
    if not _server_alive(pid):
        return {"running": False, "healthy": False, "pid": pid}
    config = load_server_config(resolved)
    return {"running": True, "healthy": _health_ok(config.host, config.port, timeout=2), "pid": pid}


def start_server(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    """启动 uvicorn 子进程并做最长 15s 的健康检查轮询。"""
    resolved = paths or AgentBridgePaths.from_root()
    status = server_status(resolved)
    if status["running"]:
        if status["healthy"]:
            logger.info("服务已在运行 pid=%s，跳过启动", status["pid"])
            return status
        logger.error("服务进程仍存活但健康检查未通过 pid=%s，拒绝重复启动", status["pid"])
        raise RuntimeError(
            f"server process {status['pid']} is alive but not healthy; "
            "refusing to start a second instance, run `agent-bridge server stop` first"
        )
    if status["pid"] is not None:
        logger.warning("清理残留 PID 文件 pid=%s（进程已不存在）", status["pid"])
        resolved.server_pid_path.unlink(missing_ok=True)
    config = load_server_config(resolved)
    env = os.environ.copy()
    env[ROOT_ENV_VAR] = str(resolved.root)
    logger.info("服务进程启动 host=%s port=%s root=%s", config.host, config.port, resolved.root)
    with resolved.server_log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_bridge.api.app:create_app",
                "--factory",
                "--host",
                config.host,
                "--port",
                str(config.port),
                "--log-config",
                _LOG_CONFIG_PATH,
                "--ws-max-size",
                str(WS_MAX_MESSAGE_BYTES),
                "--timeout-graceful-shutdown",
                str(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS),
            ],
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
        )
    resolved.server_pid_path.write_text(str(process.pid), encoding="utf-8")
    logger.info("uvicorn 子进程已拉起 pid=%s", process.pid)
    health_url = f"http://{config.host}:{config.port}/health"
    deadline = time.monotonic() + _STARTUP_HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            resolved.server_pid_path.unlink(missing_ok=True)
            logger.error("服务进程在健康检查通过前退出 pid=%s 日志=%s", process.pid, resolved.server_log_path)
            raise RuntimeError(f"server exited before health check passed; see log: {resolved.server_log_path}")
        try:
            response = httpx.get(health_url, timeout=0.5)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            logger.info("健康检查通过 pid=%s host=%s port=%s", process.pid, config.host, config.port)
            return {"running": True, "pid": process.pid}
        time.sleep(0.1)
    logger.error("服务在 15 秒内未通过健康检查 pid=%s 日志=%s", process.pid, resolved.server_log_path)
    raise RuntimeError(f"server did not become healthy within 15 seconds; see log: {resolved.server_log_path}")


def _signal_server(pid: int, sig: signal.Signals) -> None:
    """向服务进程组发信号；服务以独立会话启动，进程组只含它与同组子进程。"""
    try:
        os.killpg(pid, sig)
        return
    except (ProcessLookupError, PermissionError, AttributeError):
        pass
    with suppress(ProcessLookupError):
        os.kill(pid, sig)


def stop_server(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    """SIGTERM 停止服务并确认退出；超时升级 SIGKILL，确认退出后才删除 PID 文件。"""
    resolved = paths or AgentBridgePaths.from_root()
    pid = _read_pid(resolved.server_pid_path)
    if pid is None:
        logger.info("未发现 PID 文件，视为已停止")
        return {"stopped": True, "pid": None, "forced": False}
    if not _server_alive(pid):
        logger.warning("PID=%s 对应服务进程已不存在，清理 PID 文件", pid)
        resolved.server_pid_path.unlink(missing_ok=True)
        return {"stopped": True, "pid": pid, "forced": False}

    started = time.monotonic()
    logger.info("服务进程停止 pid=%s 等待上限=%.0fs", pid, _STOP_TIMEOUT_SECONDS)
    with suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    forced = False
    if not _wait_exit(pid, _STOP_TIMEOUT_SECONDS):
        forced = True
        logger.warning(
            "服务进程 SIGTERM 后 %.0fs 未退出，升级为 SIGKILL pid=%s（工作台等独立会话子进程"
            "可能残留，下次启动由 recover 回收）",
            _STOP_TIMEOUT_SECONDS,
            pid,
        )
        _signal_server(pid, signal.SIGKILL)
        if not _wait_exit(pid, _KILL_WAIT_SECONDS):
            logger.error("服务进程 SIGKILL 后仍未退出 pid=%s，保留 PID 文件", pid)
            raise RuntimeError(f"server process {pid} did not exit after SIGKILL")
    resolved.server_pid_path.unlink(missing_ok=True)
    logger.info(
        "服务进程已退出 pid=%s forced=%s 耗时=%.1fs", pid, forced, time.monotonic() - started
    )
    return {"stopped": True, "pid": pid, "forced": forced}
