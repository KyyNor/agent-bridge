from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.config import ROOT_ENV_VAR, AgentBridgePaths, load_server_config


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
    resolved = paths or AgentBridgePaths.from_root()
    status = server_status(resolved)
    if status["running"]:
        return status
    config = load_server_config(resolved)
    env = os.environ.copy()
    env[ROOT_ENV_VAR] = str(resolved.root)
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
            ],
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
        )
    resolved.server_pid_path.write_text(str(process.pid), encoding="utf-8")
    health_url = f"http://{config.host}:{config.port}/health"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            resolved.server_pid_path.unlink(missing_ok=True)
            raise RuntimeError(f"server exited before health check passed; see log: {resolved.server_log_path}")
        try:
            response = httpx.get(health_url, timeout=0.5)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return {"running": True, "pid": process.pid}
        time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy within 5 seconds; see log: {resolved.server_log_path}")


def stop_server(paths: AgentBridgePaths | None = None) -> dict[str, Any]:
    resolved = paths or AgentBridgePaths.from_root()
    pid = _read_pid(resolved.server_pid_path)
    if pid is None:
        return {"stopped": True, "pid": None}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    resolved.server_pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
