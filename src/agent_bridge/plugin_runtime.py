from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from agent_bridge.core.config import AgentBridgePaths

logger = logging.getLogger(__name__)


class GitPluginRuntime:
    def __init__(self, paths: AgentBridgePaths) -> None:
        self.paths = paths

    def plugin_repo_dir(self, plugin_key: str) -> Path:
        return self.paths.plugins_dir / plugin_key

    def ensure_repo(self, *, plugin_key: str, git_url: str, timeout_seconds: int = 120) -> dict[str, Any]:
        repo_dir = self.plugin_repo_dir(plugin_key)
        if not git_url.strip():
            return {
                "status": "skipped",
                "plugin_key": plugin_key,
                "repo_dir": str(repo_dir),
                "message": "git url is not configured",
            }
        if not (repo_dir / ".git").is_dir():
            return self._clone(plugin_key=plugin_key, git_url=git_url, repo_dir=repo_dir, timeout_seconds=timeout_seconds)
        return self.update_repo(plugin_key=plugin_key, timeout_seconds=timeout_seconds)

    def update_repo(self, *, plugin_key: str, timeout_seconds: int = 60) -> dict[str, Any]:
        repo_dir = self.plugin_repo_dir(plugin_key)
        if not (repo_dir / ".git").is_dir():
            return {
                "status": "missing",
                "plugin_key": plugin_key,
                "repo_dir": str(repo_dir),
                "message": "plugin repository has not been cloned",
            }
        try:
            completed = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("更新插件仓库失败 %s: %s", plugin_key, exc)
            return {
                "status": "failed",
                "plugin_key": plugin_key,
                "repo_dir": str(repo_dir),
                "message": self._error_message(exc),
            }
        return {
            "status": "updated",
            "plugin_key": plugin_key,
            "repo_dir": str(repo_dir),
            "message": (completed.stdout or completed.stderr).strip(),
        }

    def run_install(self, *, plugin_key: str, cwd: Path, command: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("安装插件依赖失败 %s: %s", plugin_key, exc)
            return {
                "status": "failed",
                "plugin_key": plugin_key,
                "cwd": str(cwd),
                "command": command,
                "message": self._error_message(exc),
            }
        return {
            "status": "installed",
            "plugin_key": plugin_key,
            "cwd": str(cwd),
            "command": command,
            "message": (completed.stdout or completed.stderr).strip(),
        }

    def _clone(self, *, plugin_key: str, git_url: str, repo_dir: Path, timeout_seconds: int) -> dict[str, Any]:
        try:
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                ["git", "clone", git_url, str(repo_dir)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("克隆插件仓库失败 %s: %s", plugin_key, exc)
            return {
                "status": "failed",
                "plugin_key": plugin_key,
                "repo_dir": str(repo_dir),
                "message": self._error_message(exc),
            }
        return {
            "status": "cloned",
            "plugin_key": plugin_key,
            "repo_dir": str(repo_dir),
            "message": (completed.stdout or completed.stderr).strip(),
        }

    @staticmethod
    def _error_message(exc: Exception) -> str:
        stderr = getattr(exc, "stderr", None)
        stdout = getattr(exc, "stdout", None)
        if stderr:
            return str(stderr).strip()
        if stdout:
            return str(stdout).strip()
        return str(exc)
