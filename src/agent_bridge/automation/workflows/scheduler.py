from __future__ import annotations

import logging
import asyncio
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent_bridge.core.domain import ConflictError, NotFound
from agent_bridge.core.ids import new_run_id
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor

logger = logging.getLogger(__name__)

_DEFAULT_START_TIME = "22:00"
_DEFAULT_STOP_TIME = "07:00"
_TICK_INTERVAL_SECONDS = 60
_MAX_TASK_ATTEMPTS = 3
_TICK_JOB_ID = "workflow_tick"
_WINDOW_OPEN_JOB_ID = "workflow_window_open"
_WINDOW_CLOSE_JOB_ID = "workflow_window_close"


class WorkflowScheduler:
    def __init__(
        self,
        *,
        service: Any,
        store: SQLiteStore,
        admins: set[str],
        executor: WorkflowDagExecutor | None = None,
        agent_service: Any = None,
        runner: Any = None,
        base_run_dir: Path | None = None,
        mcp_url: str = "http://127.0.0.1:8765/mcp",
        max_concurrent_workflows: int = 2,
    ) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        if not admins:
            raise ValueError("workflow scheduler requires at least one admin")
        self._executor = executor
        self._base_run_dir = base_run_dir
        self._mcp_url = mcp_url
        self._max_concurrent = max_concurrent_workflows
        self._scheduler: BackgroundScheduler | None = None
        # Daily execution window [start, stop]; blank start+stop means always-on.
        self._start_time_str = _DEFAULT_START_TIME
        self._stop_time_str = _DEFAULT_STOP_TIME
        self._start_time: time | None = _parse_hhmm(_DEFAULT_START_TIME)
        self._stop_time: time | None = _parse_hhmm(_DEFAULT_STOP_TIME)
        self._window_marker: date | None = None
        self._cursor = 0
        self._running: set[str] = set()
        self.finished_today: set[str] = set()
        # Per-window cap on auto-scheduled runs per workflow. 0 = unlimited.
        # Manual runs (run_workflow_now) bypass both the cap and the counting.
        self._max_runs: int = 0
        self.run_counts: dict[str, int] = {}
        # Hard wall-clock cap (minutes) for a single workflow run. 0 = fall back
        # to AgentService's own default timeout instead of applying a cap.
        self._max_runtime_minutes: int = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._load_window()
        self._ensure_scheduler()
        self._refresh_jobs()
        self._scheduler.start()
        logger.info(
            "Workflow 调度器已启动 窗口: %s - %s",
            self._start_time_str,
            self._stop_time_str,
        )

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("Workflow 调度器已停止")

    def refresh(self) -> None:
        self._load_window()
        self._ensure_scheduler()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info(
                "Workflow 调度器已启动 窗口: %s - %s",
                self._start_time_str,
                self._stop_time_str,
            )
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "start_time": self._start_time_str,
            "stop_time": self._stop_time_str,
            "in_window": self._window_anchor(datetime.now()) is not None,
            "jobs": [
                {
                    "repo_key": job.id,
                    "next_run_at": str(job.next_run_time.isoformat()) if job.next_run_time else None,
                }
                for job in self._scheduler.get_jobs()
            ] if self._scheduler and self._scheduler.running else [],
            "running_workflows": sorted(self._running),
            "finished_today": sorted(self.finished_today),
            "max_concurrent_workflows": self._max_concurrent,
            "max_runs": self._max_runs,
            "run_counts": dict(self.run_counts),
            "max_runtime_minutes": self._max_runtime_minutes,
        }

    def _ensure_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()

    def _load_window(self) -> None:
        config = self._store.get_sync_config()
        self._start_time_str = config.get("workflow_start_time") or _DEFAULT_START_TIME
        self._stop_time_str = config.get("workflow_stop_time") or _DEFAULT_STOP_TIME
        self._start_time = _parse_hhmm(self._start_time_str)
        self._stop_time = _parse_hhmm(self._stop_time_str)
        self._max_runs = int(config.get("workflow_max_runs") or 0)
        self._max_runtime_minutes = int(config.get("workflow_max_runtime_minutes") or 0)

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        if self._start_time is None and self._stop_time is None:
            self._ensure_tick_job()
            return
        self._schedule_window_boundary_jobs()
        if self._window_anchor(datetime.now()) is not None:
            self._ensure_tick_job()
            self.tick()

    def _schedule_window_boundary_jobs(self) -> None:
        if not self._scheduler:
            return
        open_at = self._start_time or time(0, 0)
        close_at = self._stop_time or time(0, 0)
        if open_at == close_at:
            return
        self._scheduler.add_job(
            self._open_window,
            trigger=CronTrigger(hour=open_at.hour, minute=open_at.minute),
            id=_WINDOW_OPEN_JOB_ID,
        )
        self._scheduler.add_job(
            self._close_window,
            trigger=CronTrigger(hour=close_at.hour, minute=close_at.minute),
            id=_WINDOW_CLOSE_JOB_ID,
        )

    def _ensure_tick_job(self) -> None:
        if not self._scheduler or self._scheduler.get_job(_TICK_JOB_ID):
            return
        self._scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(seconds=_TICK_INTERVAL_SECONDS),
            id=_TICK_JOB_ID,
        )

    def _open_window(self) -> None:
        logger.info("Workflow 执行窗口已开启 %s - %s", self._start_time_str, self._stop_time_str)
        self._ensure_tick_job()
        self.tick()

    def _close_window(self) -> None:
        logger.info("Workflow 执行窗口已关闭 %s - %s", self._start_time_str, self._stop_time_str)
        if self._scheduler and self._scheduler.get_job(_TICK_JOB_ID):
            self._scheduler.remove_job(_TICK_JOB_ID)

    def next_workflow_batch(self, candidates: set[str], running: set[str]) -> list[str]:
        ordered = sorted(candidates)
        if not ordered:
            return []
        selected: list[str] = []
        attempts = 0
        while len(selected) < self._max_concurrent and attempts < len(ordered):
            index = (self._cursor + attempts) % len(ordered)
            key = ordered[index]
            if key not in running and key not in selected:
                selected.append(key)
            attempts += 1
        self._cursor = (self._cursor + attempts) % len(ordered)
        return selected

    def _window_anchor(self, now: datetime) -> date | None:
        """Date identifying the currently-open window, or None if outside it.

        Uniquely identifies a window so the scheduler can reset per-window state
        (finished_today / in-flight slots) exactly once when a new window opens,
        including overnight windows that span midnight.
        """
        current = now.time()
        start = self._start_time
        stop = self._stop_time
        if start is None and stop is None:
            return now.date()  # always-on: reset daily
        if start is None:
            return now.date() if current < stop else None
        if stop is None:
            return now.date() if current >= start else None
        if start <= stop:
            return now.date() if start <= current < stop else None
        # Overnight window (start > stop): spans midnight.
        if current >= start:
            return now.date()
        if current < stop:
            return now.date() - timedelta(days=1)
        return None

    def tick(self) -> None:
        with self._lock:
            now = datetime.now()
            anchor = self._window_anchor(now)
            if anchor is not None and anchor != self._window_marker:
                # A new window just opened: clear per-window finished state so
                # workflows can run again. (_running self-maintains via the
                # release callback; never cleared here, to avoid double-launching
                # a workflow whose previous run is still in flight.)
                self.finished_today.clear()
                self.run_counts.clear()
                self._window_marker = anchor
            if anchor is None:
                return  # outside the daily window; no new runs
            workflows = [
                item
                for item in self._store.list_workflow_definitions()
                if item.get("status") == "active" and item.get("definition") is not None
            ]
            candidates = {item["workflow_key"] for item in workflows} - self.finished_today
            if self._max_runs > 0:
                candidates = {
                    key for key in candidates if self.run_counts.get(key, 0) < self._max_runs
                }
            available = self._max_concurrent - len(self._running)
            if available <= 0:
                logger.info(
                    "Workflow 并发上限已占满 运行中=%s 上限=%d",
                    sorted(self._running),
                    self._max_concurrent,
                )
                return
            batch = self.next_workflow_batch(candidates, self._running)[:available]
            for workflow_key in batch:
                self._running.add(workflow_key)
                if self._max_runs > 0:
                    self.run_counts[workflow_key] = self.run_counts.get(workflow_key, 0) + 1
                logger.info(
                    "Workflow run 启动 workflow=%s run=%s",
                    workflow_key,
                    "(pending)",
                )
                thread = threading.Thread(target=self._run_and_release, args=(workflow_key,), daemon=True)
                thread.start()

    def _run_and_release(self, workflow_key: str, run_id: str | None = None, input_data: dict[str, Any] | None = None, actor: str | None = None) -> None:
        try:
            self.run_one_workflow(workflow_key, run_id=run_id, input_data=input_data, actor=actor)
        except Exception:
            logger.exception("Workflow 执行异常 workflow=%s", workflow_key)
        finally:
            with self._lock:
                self._running.discard(workflow_key)

    def run_workflow_now(self, workflow_key: str, input_data: dict[str, Any] | None = None, actor: str | None = None) -> dict[str, Any]:
        """Launch a single on-demand run immediately — a "test run".

        Bypasses the daily window and the active/disabled status check (those
        live only in tick()). Shares the in-memory _running guard with the
        scheduler so a workflow cannot run twice concurrently. The run row is
        created synchronously so callers can poll it immediately.
        """
        with self._lock:
            if workflow_key in self._running:
                raise ConflictError("workflow is already running")
            workflow = self._store.get_workflow_definition(workflow_key)
            if workflow is None:
                raise NotFound("workflow not found")
            if workflow.get("definition") is None:
                from agent_bridge.core.domain import ValidationError
                raise ValidationError("工作流需要通过新编辑器迁移")
            run_id = new_run_id(workflow_key)
            base_dir = self._base_run_dir or Path("workflow-runs")
            self._store.create_workflow_run(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                task_key=None,
                status="running",
                temp_dir=str(base_dir / run_id),
                definition_snapshot=workflow["definition"],
                input_data=input_data or {},
            )
            self._running.add(workflow_key)
        logger.info(
            "Workflow 即时测试 run 启动 workflow=%s run=%s profile=%s",
            workflow_key,
            run_id,
            workflow["profile_key"],
        )
        thread = threading.Thread(
            target=self._run_and_release, args=(workflow_key, run_id, input_data, actor), daemon=True
        )
        thread.start()
        return {"status": "started", "run_id": run_id}

    def run_one_workflow(self, workflow_key: str, run_id: str | None = None, input_data: dict[str, Any] | None = None, actor: str | None = None) -> dict[str, Any]:
        """执行单个 workflow run 的完整生命周期：建 run 行 -> 跑 agent -> 解析 result -> 入库。

        失败（agent 非零退出、result 解析不通过、异常）统一落 failed 状态并
        释放已租借的任务以便快速重试。
        """
        workflow = self._store.get_workflow_definition(workflow_key)
        if workflow is None:
            logger.warning("Workflow 定义不存在 workflow=%s", workflow_key)
            self.finished_today.add(workflow_key)
            return {"status": "missing"}

        base_dir = self._base_run_dir or Path("workflow-runs")
        if run_id is None:
            run_id = new_run_id(workflow_key)
            self._store.create_workflow_run(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                task_key=None,
                status="running",
                temp_dir=str(base_dir / run_id),
                definition_snapshot=workflow.get("definition") or {"nodes": [], "edges": []},
                input_data=input_data or {},
            )
        logger.info(
            "Workflow run 开始执行 workflow=%s run=%s profile=%s",
            workflow_key,
            run_id,
            workflow["profile_key"],
        )
        if workflow.get("definition") is None:
            return {"status": "missing_definition"}
        if self._executor is None:
            raise RuntimeError("workflow DAG executor is not configured")
        try:
            run = self._store.get_workflow_run(run_id)
            if run is None:
                raise RuntimeError("workflow run not found after creation")
            execution_workflow = {
                **workflow,
                "definition": run["definition_snapshot"],
            }
            execution = asyncio.run(self._executor.run(
                workflow=execution_workflow,
                run_id=run_id,
                input_data=run["input"],
                actor=actor or sorted(self._admins)[0],
            ))
            if execution.status == "no_task":
                self.finished_today.add(workflow_key)
            if execution.status == "completed" and execution.task is not None:
                completed = self._store.complete_workflow_task(
                    workflow_key,
                    execution.task["task_key"],
                    task_version=str(execution.task.get("task_version") or ""),
                    run_id=run_id,
                )
                if not completed:
                    raise RuntimeError("workflow task completion failed")
            self._store.finish_workflow_run(
                run_id,
                status=execution.status, exit_code=0 if execution.status != "failed" else 1,
                stdout_path=None, stderr_path=None, error=execution.error, duration_ms=None, output=execution.output,
            )
            if execution.status == "failed":
                self._store.fail_workflow_task_for_run(workflow_key, run_id, execution.error or "workflow failed")
            logger.info(
                "Workflow run 完成 workflow=%s run=%s 状态=%s 耗时=%dms",
                workflow_key,
                run_id,
                execution.status,
                0,
            )
            return {"status": execution.status, "output": execution.output, "warnings": execution.warnings}
        except Exception as exc:
            logger.exception("Workflow 执行失败 workflow=%s run=%s", workflow_key, run_id)
            self._store.finish_workflow_run(
                run_id,
                status="failed",
                exit_code=1,
                stdout_path=None,
                stderr_path=None,
                error=str(exc),
                duration_ms=None,
            )
            self._store.fail_workflow_task_for_run(workflow_key, run_id, str(exc))
            return {"status": "failed", "error": str(exc)}

    def _release_leased_tasks(self, workflow_key: str, run_id: str, error: str) -> None:
        """On a failed run, release the task it leased for fast retry, or
        abandon it once retries are exhausted."""
        try:
            self._store.release_or_abandon_tasks_for_run(
                workflow_key,
                run_id,
                max_attempts=_MAX_TASK_ATTEMPTS,
                error_message=error,
            )
        except Exception:
            logger.exception("释放工作流任务失败 workflow=%s run=%s", workflow_key, run_id)

    def _maybe_generate_html_report(
        self,
        workflow_key: str,
        workflow: dict[str, Any],
        run_id: str,
    ) -> None:
        """Best-effort HTML report generation for summary workflows.

        Any error is swallowed (logged + recorded as a warning run log) so the
        main workflow run stays completed. Uses an admin actor so the reporter
        agent can call ``load_skill``.
        """
        if (workflow.get("workflow_type") or "operation") != "summary":
            return
        actor = sorted(self._admins)[0] if self._admins else "root"
        try:
            outcome = self._service.generate_html_report_for_run(
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                run_id=run_id,
                actor=actor,
            )
            status = outcome.get("status")
            if status == "generated":
                self._store.append_workflow_run_log(
                    run_id=run_id,
                    workflow_key=workflow_key,
                    task_key=None,
                    level="info",
                    stage="html_report",
                    message="HTML 报告已生成",
                    payload=outcome,
                )
            elif status == "skipped":
                logger.debug("HTML 报告跳过 workflow=%s run=%s 原因=%s", workflow_key, run_id, outcome.get("reason"))
            elif status == "no_markdown":
                self._store.append_workflow_run_log(
                    run_id=run_id,
                    workflow_key=workflow_key,
                    task_key=None,
                    level="warning",
                    stage="html_report",
                    message="本轮无 Markdown 产物，未生成 HTML 报告",
                    payload={},
                )
        except Exception as exc:
            logger.warning(
                "HTML 报告生成失败 workflow=%s run=%s error=%s",
                workflow_key,
                run_id,
                exc,
                exc_info=True,
            )
            try:
                self._store.append_workflow_run_log(
                    run_id=run_id,
                    workflow_key=workflow_key,
                    task_key=None,
                    level="warning",
                    stage="html_report",
                    message=f"HTML 报告生成失败：{exc}",
                    payload={},
                )
            except Exception:
                logger.exception("写入 HTML 报告失败日志失败 workflow=%s run=%s", workflow_key, run_id)

    def _finish_failed(self, workflow_key: str, run_id: str, result: Any, error: str) -> dict[str, Any]:
        self._store.finish_workflow_run(
            run_id,
            status="failed",
            exit_code=result.exit_code,
            stdout_path=str(result.stdout_path),
            stderr_path=str(result.stderr_path),
            error=error,
            duration_ms=result.duration_ms,
        )
        self._release_leased_tasks(workflow_key, run_id, error)
        return {"status": "failed", "error": error}


def _parse_hhmm(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        return None
