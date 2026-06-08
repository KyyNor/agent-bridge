"""Scheduled code repository sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_DEFAULT_CRON = "0 * * * *"


class CodeGraphScheduler:
    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._scheduler: BackgroundScheduler | None = None
        self._current_cron: str = _DEFAULT_CRON

    def start(self) -> None:
        config = self._store.get_sync_config()
        self._current_cron = config.get("code_sync_cron") or _DEFAULT_CRON
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info("CodeGraph scheduler started with cron: %s", self._current_cron)

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("CodeGraph scheduler stopped")

    def refresh(self) -> None:
        config = self._store.get_sync_config()
        self._current_cron = config.get("code_sync_cron") or _DEFAULT_CRON
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("CodeGraph scheduler started with cron: %s", self._current_cron)
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "cron": self._current_cron, "jobs": []}
        jobs = []
        for job in self._scheduler.get_jobs():
            repo_key = str(job.kwargs.get("repo_key", job.id))
            jobs.append({
                "repo_key": repo_key,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
            })
        return {"running": True, "cron": self._current_cron, "jobs": jobs}

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
            logger.error("Invalid cron expression '%s': %s", self._current_cron, exc)
            return
        repos = self._store.list_code_repositories()
        for repo in repos:
            if repo.get("status") != "active":
                continue
            self._scheduler.add_job(
                self._sync_repo,
                trigger=trigger,
                id=f"code_sync_{repo['repo_key']}",
                kwargs={"repo_key": repo["repo_key"]},
            )
            logger.debug("Scheduled sync for %s with cron: %s", repo["repo_key"], self._current_cron)

    def _sync_repo(self, repo_key: str) -> None:
        admin = next(iter(self._admins), "root")
        try:
            self._service.sync_repository(admin, repo_key)
            logger.info("Scheduled sync completed for %s", repo_key)
        except Exception:
            logger.exception("Scheduled sync failed for %s", repo_key)
