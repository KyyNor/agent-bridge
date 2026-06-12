"""Scheduled document knowledge sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from agent_bridge.knowledge.service import AgentBridgeService

logger = logging.getLogger(__name__)

_DEFAULT_CRON = "*/30 * * * *"


class DocSyncScheduler:
    def __init__(self, service: "AgentBridgeService", store: Any, admins: set[str]) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._scheduler: BackgroundScheduler | None = None
        self._current_cron: str = _DEFAULT_CRON
        self._current_run: dict[str, Any] | None = None
        self._last_run: dict[str, Any] | None = None

    def start(self) -> None:
        config = self._store.get_sync_config()
        self._current_cron = config.get("doc_sync_cron") or _DEFAULT_CRON
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info("DocSync 调度器已启动 cron: %s", self._current_cron)

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("DocSync 调度器已停止")

    def refresh(self) -> None:
        config = self._store.get_sync_config()
        self._current_cron = config.get("doc_sync_cron") or _DEFAULT_CRON
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("DocSync 调度器已启动 cron: %s", self._current_cron)
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "cron": self._current_cron, "jobs": [], "current_run": self._current_run, "last_run": self._last_run}
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "repo_key": job.id,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
            })
        return {"running": True, "cron": self._current_cron, "jobs": jobs, "current_run": self._current_run, "last_run": self._last_run}

    def _ensure_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        try:
            trigger = CronTrigger.from_crontab(self._current_cron)
        except (ValueError, TypeError) as exc:
            logger.error("无效的 cron 表达式 '%s': %s", self._current_cron, exc)
            return
        self._scheduler.add_job(
            self._run_sync,
            trigger=trigger,
            id="doc_sync",
        )
        logger.debug("已调度文档同步 cron: %s", self._current_cron)

    def _run_sync(self) -> None:
        admin = next(iter(self._admins), "root")
        self._current_run = {
            "status": "running",
            "started_at": _now_iso(),
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
                    "finished_at": _now_iso(),
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
                    "finished_at": _now_iso(),
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
