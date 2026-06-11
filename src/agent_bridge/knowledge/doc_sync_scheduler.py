"""Scheduled document knowledge sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
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
            return {"running": False, "cron": self._current_cron, "jobs": []}
        return {"running": True, "cron": self._current_cron}

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
        try:
            result = self._service.sync(admin, all_users=True)
            logger.info("定时文档同步完成: 已处理 %s 个任务", result.get("processed", 0))
        except Exception:
            logger.exception("定时文档同步异常失败")
