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
        """确保插件仓库就绪：未克隆则 clone，已存在则 pull --ff-only。"""
        repo_dir = self.plugin_repo_dir(plugin_key)
        if not git_url.strip():
            logger.info("插件跳过 %s：未配置 git url", plugin_key)
            return {
                "status": "skipped",
                "plugin_key": plugin_key,
                "repo_dir": str(repo_dir),
                "message": "git url is not configured",
            }
        if not (repo_dir / ".git").is_dir():
            logger.info("插件开始克隆 %s url=%s", plugin_key, git_url)
            return self._clone(plugin_key=plugin_key, git_url=git_url, repo_dir=repo_dir, timeout_seconds=timeout_seconds)
        logger.info("插件开始更新 %s", plugin_key)
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
        logger.info("插件更新完成 %s", plugin_key)
        return {
            "status": "updated",
            "plugin_key": plugin_key,
            "repo_dir": str(repo_dir),
            "message": (completed.stdout or completed.stderr).strip(),
        }

    def run_install(self, *, plugin_key: str, cwd: Path, command: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        """在插件目录执行依赖安装命令（如 npm install / pip install）。"""
        logger.info("插件开始装依赖 %s cmd=%s", plugin_key, " ".join(command))
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
        logger.info("插件依赖安装完成 %s", plugin_key)
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
        logger.info("插件克隆完成 %s -> %s", plugin_key, repo_dir)
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
