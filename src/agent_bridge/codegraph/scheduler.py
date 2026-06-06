"""Scheduled code repository sync using APScheduler."""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


class CodeGraphScheduler:
    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        config = self._store.get_sync_config()
        if not config.get("code_sync_enabled"):
            return
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info("CodeGraph scheduler started")

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("CodeGraph scheduler stopped")

    def refresh(self) -> None:
        config = self._store.get_sync_config()
        if not config.get("code_sync_enabled"):
            self.stop()
            return
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("CodeGraph scheduler started")
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "jobs": []}
        jobs = []
        for job in self._scheduler.get_jobs():
            repo_key = str(job.kwargs.get("repo_key", job.id))
            jobs.append({
                "repo_key": repo_key,
                "interval_minutes": job.trigger.interval.total_seconds() / 60 if hasattr(job.trigger, "interval") else 0,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
            })
        return {"running": True, "jobs": jobs}

    def _ensure_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        repos = self._store.list_code_repositories()
        for repo in repos:
            if repo.get("status") != "active":
                continue
            interval = repo.get("sync_interval_minutes", 60)
            if interval < 1:
                continue
            self._scheduler.add_job(
                self._sync_repo,
                "interval",
                minutes=interval,
                id=f"code_sync_{repo['repo_key']}",
                kwargs={"repo_key": repo["repo_key"]},
            )
            logger.debug("Scheduled sync for %s every %d min", repo["repo_key"], interval)

    def _sync_repo(self, repo_key: str) -> None:
        admin = next(iter(self._admins), "root")
        try:
            self._service.sync_repository(admin, repo_key)
            logger.info("Scheduled sync completed for %s", repo_key)
        except Exception:
            logger.exception("Scheduled sync failed for %s", repo_key)
