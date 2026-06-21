"""Scheduled document knowledge sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agent_bridge.knowledge_management.scheduler_base import BaseCronScheduler, now_iso

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService

logger = logging.getLogger(__name__)

_DEFAULT_CRON = "*/30 * * * *"


class DocSyncScheduler(BaseCronScheduler):
    _cron_config_key = "doc_sync_cron"
    _default_cron = _DEFAULT_CRON
    _scheduler_name = "DocSync"

    def __init__(self, service: "AgentBridgeService", store: Any, admins: set[str]) -> None:
        super().__init__(service, store, admins)
        self._current_run: dict[str, Any] | None = None
        self._last_run: dict[str, Any] | None = None

    def get_status(self) -> dict[str, Any]:
        base = {"cron": self._current_cron, "current_run": self._current_run, "last_run": self._last_run}
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "jobs": [], **base}
        jobs = [{
            "repo_key": job.id,
            "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
        } for job in self._scheduler.get_jobs()]
        return {"running": True, "jobs": jobs, **base}

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        trigger = self._build_trigger()
        if trigger is None:
            return
        self._scheduler.add_job(self._run_sync, trigger=trigger, id="doc_sync")
        logger.debug("已调度文档同步 cron: %s", self._current_cron)

    def _run_sync(self) -> None:
        admin = next(iter(self._admins), "root")
        self._current_run = {
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "current_job": None,
            "error": None,
        }
        try:
            result = self._service.sync(admin, all_users=True, progress_callback=self._update_progress)
            if self._current_run is not None:
                self._current_run.update({
                    "status": "succeeded",
                    "finished_at": now_iso(),
                    "processed": result.get("processed", self._current_run.get("processed", 0)),
                    "succeeded": result.get("succeeded", self._current_run.get("succeeded", 0)),
                    "failed": result.get("failed", self._current_run.get("failed", 0)),
                    "current_job": None,
                })
                self._last_run = dict(self._current_run)
                self._current_run = None
            logger.info("定时文档同步完成: 已处理 %s 个任务", result.get("processed", 0))
        except Exception as exc:
            if self._current_run is not None:
                self._current_run.update({
                    "status": "failed",
                    "finished_at": now_iso(),
                    "error": str(exc),
                })
                self._last_run = dict(self._current_run)
                self._current_run = None
            logger.exception("定时文档同步异常失败")

    def _update_progress(self, event: dict[str, Any]) -> None:
        if self._current_run is None:
            return
        self._current_run.update({
            "total": event.get("total", self._current_run.get("total", 0)),
            "processed": event.get("processed", self._current_run.get("processed", 0)),
            "succeeded": event.get("succeeded", self._current_run.get("succeeded", 0)),
            "failed": event.get("failed", self._current_run.get("failed", 0)),
            "current_job": event.get("current_job"),
        })
