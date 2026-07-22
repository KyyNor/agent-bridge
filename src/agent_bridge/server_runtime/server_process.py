"""管理 uvicorn 服务器子进程；与 ``agent_runtime`` 的 Agent 执行职责无关。"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.config import ROOT_ENV_VAR, AgentBridgePaths, load_server_config

logger = logging.getLogger(__name__)

_LOG_CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "logging.json")


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    raw_pid = path.read_text(encoding="utf-8").strip()
    try:
        return int(raw_pid)
    except ValueError as exc:
        raise RuntimeError(f"invalid pid file: {path}") from exc


def server_status(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    resolved = paths or AgentBridgePaths.from_root()
    pid = _read_pid(resolved.server_pid_path)
    if pid is None:
        return {"running": False, "pid": None}
    config = load_server_config(resolved)
    try:
        response = httpx.get(f"http://{config.host}:{config.port}/health", timeout=2)
        return {"running": response.status_code == 200, "pid": pid}
    except httpx.HTTPError:
        return {"running": False, "pid": pid}


def start_server(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    """启动 uvicorn 子进程并做最长 5s 的健康检查轮询。"""
    resolved = paths or AgentBridgePaths.from_root()
    status = server_status(resolved)
    if status["running"]:
        logger.info("服务已在运行 pid=%s，跳过启动", status.get("pid"))
        return status
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
            ],
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
        )
    resolved.server_pid_path.write_text(str(process.pid), encoding="utf-8")
    logger.info("uvicorn 子进程已拉起 pid=%s", process.pid)
    health_url = f"http://{config.host}:{config.port}/health"
    deadline = time.monotonic() + 5.0
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
    logger.error("服务在 5 秒内未通过健康检查 pid=%s 日志=%s", process.pid, resolved.server_log_path)
    raise RuntimeError(f"server did not become healthy within 5 seconds; see log: {resolved.server_log_path}")


def stop_server(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    """向 PID 文件记录的进程发送 SIGTERM 并清理 PID 文件。"""
    resolved = paths or AgentBridgePaths.from_root()
    pid = _read_pid(resolved.server_pid_path)
    if pid is None:
        logger.info("未发现 PID 文件，视为已停止")
        return {"stopped": True, "pid": None}
    logger.info("服务进程停止 pid=%s", pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # 进程已不存在，仍需清理残留 PID 文件
        logger.warning("PID=%s 对应进程已不存在，清理 PID 文件", pid)
    resolved.server_pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
