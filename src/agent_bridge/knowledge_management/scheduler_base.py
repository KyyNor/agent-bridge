"""APScheduler cron 调度器的公共骨架。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseCronScheduler:
    """启停/刷新/cron 触发器解析的公共骨架。

    子类需覆盖类属性 ``_cron_config_key`` / ``_default_cron`` / ``_scheduler_name``,
    并实现 ``_refresh_jobs``(以及各自的 ``get_status`` / run 函数)。
    """

    _cron_config_key: str = ""
    _default_cron: str = ""
    _scheduler_name: str = ""

    def __init__(self, service: Any, store: Any, admins: set[str]) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._scheduler: BackgroundScheduler | None = None
        self._current_cron: str = self._default_cron

    def start(self) -> None:
        """启动调度器并按当前 cron 注册定时任务。"""
        self._current_cron = self._read_cron()
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info("%s 调度器已启动 cron: %s", self._scheduler_name, self._current_cron)

    def stop(self) -> None:
        """关闭调度器。未启动时静默跳过。"""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("%s 调度器已停止", self._scheduler_name)

    def refresh(self) -> None:
        """管理员改 cron 后被调用:重读配置并重建任务。

        若调度器尚未运行则顺便拉起;最终交给子类 ``_refresh_jobs`` 重建 job。
        """
        self._current_cron = self._read_cron()
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("%s 调度器已启动 cron: %s", self._scheduler_name, self._current_cron)
        self._refresh_jobs()

    def _read_cron(self) -> str:
        """从同步配置表读取 cron,缺省回落到子类默认值。"""
        return self._store.get_sync_config().get(self._cron_config_key) or self._default_cron

    def _ensure_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()

    def _build_trigger(self) -> CronTrigger | None:
        """解析当前 cron。

        无效表达式时记 WARNING 并返回 None —— 调用方在此之前已 ``remove_all_jobs``,
        返回 None 意味着该调度器进入「无 job 的空转」降级状态(不抛异常,等待下次 refresh 修复)。
        """
        try:
            return CronTrigger.from_crontab(self._current_cron)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "%s 调度器降级:无效 cron 表达式 '%s' 原因=%s,本轮跳过任务调度",
                self._scheduler_name, self._current_cron, exc,
            )
            return None

    def _refresh_jobs(self) -> None:
        raise NotImplementedError
