from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, time
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.workflows.result_parser import parse_workflow_result
from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunner, WorkflowRunSpec

logger = logging.getLogger(__name__)


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
        self._cursor = 0
        self._running: set[str] = set()
        self.finished_today: set[str] = set()
        self._finished_date = datetime.now().date()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self.tick, trigger=IntervalTrigger(seconds=30), id="workflow_tick")
        self._scheduler.start()
        logger.info("Workflow 调度器已启动")

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("Workflow 调度器已停止")

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "running_workflows": sorted(self._running),
            "finished_today": sorted(self.finished_today),
            "max_concurrent_workflows": self._max_concurrent,
        }

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

    def schedule_allows_start(self, schedule: dict[str, Any], *, now: datetime | None = None) -> bool:
        if not schedule.get("enabled", True):
            return False
        current = (now or datetime.now()).time()
        start = _parse_hhmm(schedule.get("start_time"))
        stop = _parse_hhmm(schedule.get("stop_time"))
        if start is None and stop is None:
            return True
        if start is None:
            return current < stop
        if stop is None:
            return current >= start
        if start <= stop:
            return start <= current < stop
        return current >= start or current < stop

    def tick(self) -> None:
        with self._lock:
            self._reset_finished_today()
            workflows = [
                item
                for item in self._store.list_workflow_definitions()
                if item.get("status") == "active" and self.schedule_allows_start(item.get("schedule") or {})
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

    def _reset_finished_today(self) -> None:
        today = datetime.now().date()
        if today != self._finished_date:
            self.finished_today.clear()
            self._finished_date = today

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
