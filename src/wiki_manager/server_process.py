from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

import httpx

from wiki_manager.config import WikiManagerPaths, load_server_config


def server_status(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    if not resolved.server_pid_path.exists():
        return {"running": False, "pid": None}
    pid = int(resolved.server_pid_path.read_text(encoding="utf-8").strip())
    config = load_server_config(resolved)
    try:
        response = httpx.get(f"http://{config.host}:{config.port}/health", timeout=2)
        return {"running": response.status_code == 200, "pid": pid}
    except httpx.HTTPError:
        return {"running": False, "pid": pid}


def start_server(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    status = server_status(resolved)
    if status["running"]:
        return status
    config = load_server_config(resolved)
    log = resolved.server_log_path.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "wiki_manager.server:create_app",
            "--factory",
            "--host",
            config.host,
            "--port",
            str(config.port),
        ],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    resolved.server_pid_path.write_text(str(process.pid), encoding="utf-8")
    return {"running": True, "pid": process.pid}


def stop_server(paths: WikiManagerPaths | None = None) -> dict[str, Any]:
    resolved = paths or WikiManagerPaths.from_root()
    if not resolved.server_pid_path.exists():
        return {"stopped": True, "pid": None}
    pid = int(resolved.server_pid_path.read_text(encoding="utf-8").strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    resolved.server_pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
