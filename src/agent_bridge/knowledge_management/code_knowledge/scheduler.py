"""Scheduled code repository sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
from typing import Any

from agent_bridge.core.timeutil import utc_iso
from agent_bridge.knowledge_management.scheduler_base import BaseCronScheduler

logger = logging.getLogger(__name__)

_DEFAULT_CRON = "0 * * * *"


class CodeGraphScheduler(BaseCronScheduler):
    _cron_config_key = "code_sync_cron"
    _default_cron = _DEFAULT_CRON
    _scheduler_name = "CodeGraph"

    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        super().__init__(service, store, admins)
        self._runs: dict[str, dict[str, Any]] = {}

    def stop(self) -> None:
        super().stop()
        stop_processes = getattr(self._service, "stop_active_processes", None)
        if callable(stop_processes):
            stop_processes()

    def get_status(self) -> dict[str, Any]:
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "cron": self._current_cron, "jobs": []}
        jobs = []
        for job in self._scheduler.get_jobs():
            repo_key = str(job.kwargs.get("repo_key", job.id))
            jobs.append({
                "repo_key": repo_key,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
                "progress": self._runs.get(repo_key),
            })
        return {"running": True, "cron": self._current_cron, "jobs": jobs}

    def _refresh_jobs(self) -> None:
        """按当前 cron 为每个 active 仓库注册一个同步 job（先清空旧 job）。"""
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        trigger = self._build_trigger()
        if trigger is None:
            return
        for repo in self._store.list_code_repositories():
            if repo.get("status") != "active":
                continue
            self._scheduler.add_job(
                self._sync_repo,
                trigger=trigger,
                id=f"code_sync_{repo['repo_key']}",
                kwargs={"repo_key": repo["repo_key"]},
            )
            logger.debug("已调度同步 %s cron: %s", repo["repo_key"], self._current_cron)

    def _sync_repo(self, repo_key: str) -> None:
        """cron tick 触发的单仓库同步任务（成功/失败各记一条）。"""
        admin = next(iter(self._admins), "root")
        logger.info("CodeGraph tick 同步开始 repo=%s", repo_key)
        self._runs[repo_key] = {
            "status": "running",
            "started_at": utc_iso(),
            "finished_at": None,
            "message": "正在同步代码仓库",
            "error": None,
        }
        try:
            result = self._service.sync_repository(admin, repo_key)
            self._runs[repo_key].update({
                "status": "succeeded",
                "finished_at": utc_iso(),
                "message": f"同步完成，索引 {result.get('indexed', 0)} 项",
                "error": None,
            })
            logger.info("定时同步完成 %s", repo_key)
        except Exception as exc:
            self._runs[repo_key].update({
                "status": "failed",
                "finished_at": utc_iso(),
                "message": "同步失败",
                "error": str(exc),
            })
            logger.exception("定时同步失败 %s", repo_key)
