"""Scheduled Understand Anything analysis using APScheduler cron trigger."""
from __future__ import annotations

import logging
from typing import Any

from agent_bridge.knowledge_management.scheduler_base import BaseCronScheduler, now_iso

logger = logging.getLogger(__name__)

_DEFAULT_CRON = "0 2 * * *"


class UnderstandingScheduler(BaseCronScheduler):
    _cron_config_key = "understand_cron"
    _default_cron = _DEFAULT_CRON
    _scheduler_name = "Understand"

    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        super().__init__(service, store, admins)
        self._runs: dict[str, dict[str, Any]] = {}
        self._current_run: dict[str, Any] | None = None
        self._last_run: dict[str, Any] | None = None

    def get_status(self) -> dict[str, Any]:
        base = {"cron": self._current_cron, "current_run": self._current_run, "last_run": self._last_run}
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "jobs": [], **base}
        jobs = []
        for job in self._scheduler.get_jobs():
            repo_key = str(job.kwargs.get("repo_key", job.id))
            jobs.append({
                "repo_key": repo_key,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
                "progress": self._runs.get(repo_key),
            })
        return {"running": True, "jobs": jobs, **base}

    def _refresh_jobs(self) -> None:
        """按当前 cron 为每个 active 且开启 auto_understand 的仓库注册分析 job。"""
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        trigger = self._build_trigger()
        if trigger is None:
            return
        for repo in self._store.list_code_repositories():
            if repo.get("status") != "active" or not repo.get("auto_understand"):
                continue
            self._scheduler.add_job(
                self._run_understand,
                trigger=trigger,
                id=f"understand_{repo['repo_key']}",
                kwargs={"repo_key": repo["repo_key"]},
            )
            logger.debug("已调度 Understand 分析 %s cron: %s", repo["repo_key"], self._current_cron)

    def _run_understand(self, repo_key: str) -> None:
        """cron tick 触发的单仓库 Understand 分析任务（成功/失败各记一条）。"""
        admin = next(iter(self._admins), "root")
        logger.info("Understand tick 分析开始 repo=%s", repo_key)
        self._current_run = self._runs[repo_key] = {
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "message": "正在执行代码理解",
            "error": None,
        }
        try:
            result = self._service.analyze_understand(admin, repo_key)
            run_status = "succeeded" if result.get("success") else "failed"
            self._runs[repo_key].update({
                "status": run_status,
                "finished_at": now_iso(),
                "message": (
                    f"理解完成，节点 {result.get('node_count', 0)}，边 {result.get('edge_count', 0)}"
                    if run_status == "succeeded"
                    else "代码理解失败"
                ),
                "error": result.get("error"),
            })
            self._last_run = dict(self._runs[repo_key])
            self._current_run = None
            if run_status == "succeeded":
                logger.info("定时 Understand 分析完成 %s", repo_key)
            else:
                logger.warning(
                    "定时 Understand 分析失败 %s: %s",
                    repo_key,
                    result.get("error") or "unknown error",
                )
        except Exception as exc:
            self._runs[repo_key].update({
                "status": "failed",
                "finished_at": now_iso(),
                "message": "代码理解失败",
                "error": str(exc),
            })
            self._last_run = dict(self._runs[repo_key])
            self._current_run = None
            logger.exception("定时 Understand 分析失败 %s", repo_key)
