from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.workflows.result_parser import parse_workflow_result
from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunner, WorkflowRunSpec

logger = logging.getLogger(__name__)

_DEFAULT_START_TIME = "22:00"
_DEFAULT_STOP_TIME = "07:00"
_TICK_INTERVAL_SECONDS = 60


class WorkflowScheduler:
    def __init__(
        self,
        *,
        service: Any,
        store: SQLiteStore,
        admins: set[str],
        runner: WorkflowRunner | None = None,
        base_run_dir: Path | None = None,
        mcp_url: str = "http://127.0.0.1:8765/mcp",
        max_concurrent_workflows: int = 2,
    ) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        self._runner = runner or ClaudeWorkflowRunner()
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

    def _refresh_jobs(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.remove_all_jobs()
        self._scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(seconds=_TICK_INTERVAL_SECONDS),
            id="workflow_tick",
        )

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
                self._window_marker = anchor
            if anchor is None:
                return  # outside the daily window; no new runs
            workflows = [
                item
                for item in self._store.list_workflow_definitions()
                if item.get("status") == "active"
            ]
            candidates = {item["workflow_key"] for item in workflows} - self.finished_today
            available = self._max_concurrent - len(self._running)
            if available <= 0:
                return
            batch = self.next_workflow_batch(candidates, self._running)[:available]
            for workflow_key in batch:
                self._running.add(workflow_key)
                thread = threading.Thread(target=self._run_and_release, args=(workflow_key,), daemon=True)
                thread.start()

    def _run_and_release(self, workflow_key: str) -> None:
        try:
            self.run_one_workflow(workflow_key)
        except Exception:
            logger.exception("Workflow 执行异常 workflow=%s", workflow_key)
        finally:
            with self._lock:
                self._running.discard(workflow_key)

    def run_one_workflow(self, workflow_key: str) -> dict[str, Any]:
        workflow = self._store.get_workflow_definition(workflow_key)
        if workflow is None:
            self.finished_today.add(workflow_key)
            return {"status": "missing"}

        run_id = f"run_{uuid.uuid4().hex}"
        base_dir = self._base_run_dir or Path("workflow-runs")
        run = self._store.create_workflow_run(
            run_id=run_id,
            workflow_key=workflow_key,
            profile_key=workflow["profile_key"],
            task_key=None,
            status="running",
            temp_dir=str(base_dir / run_id),
        )
        process_result = None
        try:
            process_result = self._runner.run(
                base_dir,
                WorkflowRunSpec(
                    run_id=run_id,
                    workflow_key=workflow_key,
                    profile_key=workflow["profile_key"],
                    workflow_js=workflow["workflow_js"],
                    mcp_url=self._mcp_url,
                ),
            )
            if process_result.exit_code != 0:
                return self._finish_failed(run_id, process_result, "claude workflow runner failed")
            parsed = parse_workflow_result(process_result.run_dir)
            ingested = self._service.ingest_parsed_result(
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                run_id=run_id,
                parsed=parsed,
            )
            final_status = ingested["status"]
            if final_status == "no_task":
                self.finished_today.add(workflow_key)
            self._store.finish_workflow_run(
                run_id,
                status=final_status,
                exit_code=process_result.exit_code,
                stdout_path=str(process_result.stdout_path),
                stderr_path=str(process_result.stderr_path),
                error=None,
                duration_ms=process_result.duration_ms,
            )
            return ingested
        except Exception as exc:
            logger.exception("Workflow 执行失败 workflow=%s run=%s", workflow_key, run_id)
            stdout_path = str(process_result.stdout_path) if process_result else None
            stderr_path = str(process_result.stderr_path) if process_result else None
            duration_ms = process_result.duration_ms if process_result else None
            self._store.finish_workflow_run(
                run["run_id"],
                status="failed",
                exit_code=process_result.exit_code if process_result else None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error=str(exc),
                duration_ms=duration_ms,
            )
            return {"status": "failed", "error": str(exc)}

    def _finish_failed(self, run_id: str, result: Any, error: str) -> dict[str, Any]:
        self._store.finish_workflow_run(
            run_id,
            status="failed",
            exit_code=result.exit_code,
            stdout_path=str(result.stdout_path),
            stderr_path=str(result.stderr_path),
            error=error,
            duration_ms=result.duration_ms,
        )
        return {"status": "failed", "error": error}


def _parse_hhmm(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        return None
