"""Scheduled document knowledge sync using APScheduler cron trigger."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agent_bridge.core.timeutil import utc_iso
from agent_bridge.knowledge_management.scheduler_base import BaseCronScheduler

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
        """重建文档同步 job。cron 无效时跳过注册(降级,沿用基类逻辑)。"""
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        trigger = self._build_trigger()
        if trigger is None:
            # 基类已记 WARNING;此处仅说明本调度器本轮未注册任何 job
            logger.warning("DocSync 本轮未注册定时任务,文档同步暂停直到 cron 修复")
            return
        self._scheduler.add_job(self._run_sync, trigger=trigger, id="doc_sync")
        logger.debug("已调度文档同步 cron: %s", self._current_cron)

    def _run_sync(self) -> None:
        """cron 触发的同步入口:扇出到所有检索后端(具体扇出在 service.sync 内完成)。"""
        admin = next(iter(self._admins), "root")
        logger.info("DocSync 定时同步开始 actor=%s", admin)
        self._current_run = {
            "status": "running",
            "started_at": utc_iso(),
            "finished_at": None,
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "current_job": None,
            "error": None,
        }
        try:
            self._sync_repo_sources()
            result = self._service.sync(admin, all_users=True, progress_callback=self._update_progress)
            if self._current_run is not None:
                self._current_run.update({
                    "status": "succeeded",
                    "finished_at": utc_iso(),
                    "processed": result.get("processed", self._current_run.get("processed", 0)),
                    "succeeded": result.get("succeeded", self._current_run.get("succeeded", 0)),
                    "failed": result.get("failed", self._current_run.get("failed", 0)),
                    "current_job": None,
                })
                self._last_run = dict(self._current_run)
                self._current_run = None
            logger.info(
                "DocSync 定时同步完成: 已处理=%s 成功=%s 失败=%s",
                result.get("processed", 0), result.get("succeeded", 0), result.get("failed", 0),
            )
        except Exception as exc:
            if self._current_run is not None:
                self._current_run.update({
                    "status": "failed",
                    "finished_at": utc_iso(),
                    "error": str(exc),
                })
                self._last_run = dict(self._current_run)
                self._current_run = None
            logger.error("DocSync 定时同步失败 原因=%s", exc, exc_info=True)

    def _sync_repo_sources(self) -> None:
        """git 数据源增量同步:遍历所有 active 源,diff 生成同步任务。

        在 service.sync drain 之前执行。单源失败仅记录 last_error 并跳过,
        不阻塞后续 drain。
        """
        admin = next(iter(self._admins), "root")
        for src in self._store.list_all_active_repo_sources():
            kb_id = src["kb_id"]
            kb_slug = src.get("kb_slug")
            repo_key = src["repo_key"]
            try:
                self._service.sync_kb_repo_source_changes(admin, kb_slug, repo_key)
            except Exception as exc:
                self._store.mark_kb_repo_source_sync(kb_id, repo_key, success=False, error=str(exc))
                logger.warning("git 源同步失败 kb=%s repo=%s: %s", kb_slug, repo_key, exc)

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
