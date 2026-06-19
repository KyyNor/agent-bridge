"""Thin wrapper around the codegraph CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


class CodeGraphClient:
    def __init__(self, cli_path: str = "codegraph") -> None:
        self.cli_path = cli_path
        self._available: bool | None = None

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
        result = subprocess.run(
            [self.cli_path, *args],
            cwd=cwd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            msg = result.stderr or result.stdout or f"codegraph {' '.join(args)} failed"
            raise RuntimeError(msg.strip())
        return result

    def _parse_status(self, output: str) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for line in output.splitlines():
            m = re.match(r"\s*(\w[\w\s]*?)\s*:\s*([\d,]+)", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                stats[key] = int(m.group(2).replace(",", ""))
        return stats
