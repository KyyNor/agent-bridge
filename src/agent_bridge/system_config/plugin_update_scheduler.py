"""Scheduled updates for managed plugin repositories."""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from agent_bridge.core.timeutil import utc_iso

logger = logging.getLogger(__name__)


class PluginUpdateScheduler:
    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._scheduler: BackgroundScheduler | None = None
        self._runs: dict[str, dict[str, Any]] = {}
        self._current_run: dict[str, Any] | None = None
        self._last_run: dict[str, Any] | None = None

    def start(self) -> None:
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info("插件更新调度器已启动")

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("插件更新调度器已停止")

    def refresh(self) -> None:
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("插件更新调度器已启动")
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        base = {"current_run": self._current_run, "last_run": self._last_run}
        if not self._scheduler or not self._scheduler.running:
            return {"running": False, "jobs": [], **base}
        jobs = []
        for job in self._scheduler.get_jobs():
            plugin_key = str(job.kwargs.get("plugin_key", job.id))
            jobs.append({
                "plugin_key": plugin_key,
                "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
                "progress": self._runs.get(plugin_key),
            })
        return {"running": True, "jobs": jobs, **base}

    def _ensure_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        config = self._store.get_sync_config()
        self._add_plugin_job(
            plugin_key="understand-anything",
            cron=str(config.get("ua_plugin_update_cron") or "0 3 * * 0"),
            enabled=bool(str(config.get("ua_git_url") or "").strip()),
        )
        self._add_plugin_job(
            plugin_key="claude-mem",
            cron=str(config.get("claude_mem_plugin_update_cron") or "30 3 * * 0"),
            enabled=bool(str(config.get("claude_mem_git_url") or "").strip()),
        )

    def _add_plugin_job(self, *, plugin_key: str, cron: str, enabled: bool) -> None:
        """为单个插件注册定时更新 job。

        ``enabled`` 为 False(git url 未配置)时静默跳过;cron 解析失败记 WARNING 降级,
        不影响其他插件的 job 注册。
        """
        if not enabled or not self._scheduler:
            return
        try:
            trigger = CronTrigger.from_crontab(cron)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "插件更新 %s 降级:无效 cron '%s' 原因=%s,跳过该插件调度",
                plugin_key, cron, exc,
            )
            return
        self._scheduler.add_job(
            self._run_plugin_update,
            trigger=trigger,
            id=f"plugin_update_{plugin_key}",
            kwargs={"plugin_key": plugin_key},
        )
        logger.debug("已调度插件更新 %s cron: %s", plugin_key, cron)

    def _run_plugin_update(self, plugin_key: str) -> None:
        """cron 触发的插件更新入口,按 plugin_key 分发到对应 service 方法。"""
        admin = next(iter(self._admins), "root")
        logger.info("插件更新开始 plugin=%s actor=%s", plugin_key, admin)
        self._current_run = self._runs[plugin_key] = {
            "status": "running",
            "started_at": utc_iso(),
            "finished_at": None,
            "message": f"正在更新 {plugin_key}",
            "error": None,
        }
        try:
            if plugin_key == "understand-anything":
                result = self._service.update_understand_plugin(admin)
            elif plugin_key == "claude-mem":
                result = self._service.update_claude_mem_plugin(admin)
            else:
                raise ValueError(f"unknown plugin: {plugin_key}")
            status = "succeeded" if result.get("status") not in {"failed", "missing"} else "failed"
            self._runs[plugin_key].update({
                "status": status,
                "finished_at": utc_iso(),
                "message": result.get("message") or f"{plugin_key} 更新完成",
                "error": None if status == "succeeded" else result.get("message"),
            })
            self._last_run = dict(self._runs[plugin_key])
            self._current_run = None
            logger.info(
                "插件更新完成 plugin=%s status=%s",
                plugin_key, status,
            )
        except Exception as exc:
            self._runs[plugin_key].update({
                "status": "failed",
                "finished_at": utc_iso(),
                "message": f"{plugin_key} 更新失败",
                "error": str(exc),
            })
            self._last_run = dict(self._runs[plugin_key])
            self._current_run = None
            logger.error("插件更新失败 plugin=%s 原因=%s", plugin_key, exc, exc_info=True)
