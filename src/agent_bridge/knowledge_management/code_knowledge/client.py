"""Thin wrapper around the codegraph CLI."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any


class CodeGraphClient:
    def __init__(
        self,
        cli_path: str = "codegraph",
        *,
        command_timeout: float = 120,
        terminate_grace_seconds: float = 5,
    ) -> None:
        self.cli_path = cli_path
        self.command_timeout = command_timeout
        self.terminate_grace_seconds = terminate_grace_seconds
        self._available: bool | None = None
        self._active_processes: set[subprocess.Popen[str]] = set()
        self._active_processes_lock = threading.Lock()

    def is_available(self) -> bool:
        """Check if codegraph CLI is installed and reachable."""
        if self._available is not None:
            return self._available
        try:
            result = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            self._available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            self._available = False
        return self._available

    def init(self, project_dir: Path) -> None:
        """Initialize codegraph in a project directory."""
        self._run(project_dir, ["init", "-i"])

    def index(self, project_dir: Path) -> None:
        """Index the project."""
        self._run(project_dir, ["index"])

    def sync(self, project_dir: Path) -> None:
        """Sync the project with git."""
        self._run(project_dir, ["sync"])

    def status(self, project_dir: Path) -> dict[str, Any]:
        """Get project status. Parses text output like 'Files: 42\nNodes: 1,234'."""
        result = self._run(project_dir, ["status"])
        return self._parse_status(result.stdout)

    def query(self, project_dir: Path, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Query the code graph. Uses --json flag."""
        result = self._run(project_dir, ["query", "--json", term])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            items = data.get("results", data.get("nodes", []))
        else:
            items = data
        return items[:limit]

    def files(self, project_dir: Path) -> list[dict[str, Any]]:
        """List tracked files."""
        try:
            result = self._run(project_dir, ["files", "--json"])
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return data.get("files", [])
            return data if isinstance(data, list) else []
        except (RuntimeError, json.JSONDecodeError):
            return []

    def callers(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["callers", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("callers", data.get("results", []))
        return data if isinstance(data, list) else []

    def callees(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["callees", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("callees", data.get("results", []))
        return data if isinstance(data, list) else []

    def impact(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["impact", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("impacted", data.get("results", []))
        return data if isinstance(data, list) else []

    def _run(self, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [self.cli_path, *args]
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            with self._active_processes_lock:
                self._active_processes.add(process)
            try:
                stdout, stderr = process.communicate(timeout=self.command_timeout)
            except subprocess.TimeoutExpired as exc:
                self._terminate_process_group(process)
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    command,
                    self.command_timeout,
                    output=stdout,
                    stderr=stderr,
                ) from exc
        finally:
            with self._active_processes_lock:
                self._active_processes.discard(process)
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if result.returncode != 0:
            msg = result.stderr or result.stdout or f"codegraph {' '.join(args)} failed"
            raise RuntimeError(msg.strip())
        return result

    def terminate_active_processes(self) -> None:
        with self._active_processes_lock:
            processes = list(self._active_processes)
        for process in processes:
            self._terminate_process_group(process)

    def _terminate_process_group(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if hasattr(os, "killpg"):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=self.terminate_grace_seconds)
                return
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                process.wait(timeout=self.terminate_grace_seconds)
                return
        process.terminate()
        try:
            process.wait(timeout=self.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.terminate_grace_seconds)

    def _parse_status(self, output: str) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for line in output.splitlines():
            m = re.match(r"\s*(\w[\w\s]*?)\s*:\s*([\d,]+)", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                stats[key] = int(m.group(2).replace(",", ""))
        return stats
