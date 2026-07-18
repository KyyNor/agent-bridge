from __future__ import annotations

import logging
import asyncio
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent_bridge.core.domain import ConflictError, NotFound
from agent_bridge.core.ids import new_run_id
from agent_bridge.agent_runtime.service import STOPPED_ERROR
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError

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
        validator: Any = None,
        base_run_dir: Path | None = None,
        max_concurrent_workflows: int = 2,
    ) -> None:
        self._service = service
        self._store = store
        self._admins = admins
        if not admins:
            raise ValueError("workflow scheduler requires at least one admin")
        self._executor = executor
        self._validator = validator or getattr(service, "validator", None)
        self._base_run_dir = base_run_dir
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
        self._run_locks_lock = threading.Lock()
        self._run_locks: dict[str, threading.RLock] = {}

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
            workflows_by_key = {item["workflow_key"]: item for item in workflows}
            for workflow_key in batch:
                workflow = workflows_by_key[workflow_key]
                try:
                    graph = self._require_valid_workflow(workflow, actor=None)
                except WorkflowDefinitionValidationError as exc:
                    self.finished_today.add(workflow_key)
                    self._log_validation_failure(workflow_key, exc)
                    continue
                definition_snapshot = self._definition_snapshot(graph, workflow["definition"])
                self._running.add(workflow_key)
                if self._max_runs > 0:
                    self.run_counts[workflow_key] = self.run_counts.get(workflow_key, 0) + 1
                logger.info(
                    "Workflow run 启动 workflow=%s run=%s",
                    workflow_key,
                    "(pending)",
                )
                thread = threading.Thread(
                    target=self._run_and_release,
                    args=(workflow_key, None, None, None, True, definition_snapshot),
                    daemon=True,
                )
                thread.start()

    def _run_and_release(
        self,
        workflow_key: str,
        run_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        actor: str | None = None,
        resources_validated: bool = False,
        validated_definition: dict[str, Any] | None = None,
        plan: Any | None = None,
    ) -> None:
        try:
            self.run_one_workflow(
                workflow_key,
                run_id=run_id,
                input_data=input_data,
                actor=actor,
                resources_validated=resources_validated,
                validated_definition=validated_definition,
                plan=plan,
            )
        except Exception:
            logger.exception("Workflow 执行异常 workflow=%s", workflow_key)
        finally:
            with self._lock:
                self._running.discard(workflow_key)

    def stop_workflow_run(self, run_id: str) -> dict[str, Any]:
        with self._workflow_run_lock(run_id):
            run = self._store.get_workflow_run(run_id)
            if run is None:
                raise NotFound("workflow run not found")
            if run.get("status") != "running":
                return run
            registry = self._control_registry()
            if registry is None or not registry.is_workflow_active(run_id):
                raise ConflictError("workflow run controller is not available")
            registry.request_workflow_stop(run_id)
            return {"status": "stopping", "run_id": run_id}

    def run_workflow_now(
        self,
        workflow_key: str,
        input_data: dict[str, Any] | None = None,
        actor: str | None = None,
        *,
        task_key: str | None = None,
        task_version: str | None = None,
        execution_mode: str = "normal",
    ) -> dict[str, Any]:
        """Launch a single on-demand run immediately — a "test run".

        Bypasses the daily window and the active/disabled status check (those
        live only in tick()). Shares the in-memory _running guard with the
        scheduler so a workflow cannot run twice concurrently. The run row is
        created synchronously so callers can poll it immediately.
        """
        with self._lock:
            if execution_mode not in {"normal", "incremental", "force_full"}:
                from agent_bridge.core.domain import ValidationError
                raise ValidationError("unsupported workflow execution mode")
            if workflow_key in self._running:
                raise ConflictError("workflow is already running")
            workflow = self._store.get_workflow_definition(workflow_key)
            if workflow is None:
                raise NotFound("workflow not found")
            if workflow.get("definition") is None:
                from agent_bridge.core.domain import ValidationError
                raise ValidationError("工作流需要通过新编辑器迁移")
            graph = self._require_valid_workflow(workflow, actor=actor)
            definition_snapshot = self._definition_snapshot(graph, workflow["definition"])
            selected_task = None
            effective_mode = execution_mode
            if task_key is not None:
                selected_task = self._store.get_workflow_task(
                    workflow_key, task_key, task_version=task_version
                )
                if selected_task is None:
                    raise NotFound("workflow task not found")
                if selected_task.get("status") == "stale" and execution_mode == "normal":
                    effective_mode = "incremental"
                self._store.set_priority_for_task(
                    workflow_key,
                    str(selected_task["task_key"]),
                    task_version=str(selected_task.get("task_version") or ""),
                )
            plan = self._build_plan(
                workflow=workflow,
                definition=definition_snapshot,
                actor=actor,
                task=selected_task,
                execution_mode=effective_mode,
            )
            run_id = new_run_id(workflow_key)
            base_dir = self._base_run_dir or Path("workflow-runs")
            self._store.create_workflow_run(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                task_key=str(selected_task["task_key"]) if selected_task is not None else None,
                status="running",
                temp_dir=str(base_dir / run_id),
                definition_snapshot=definition_snapshot,
                input_data=input_data or {},
                workflow_revision_no=getattr(plan, "workflow_revision_no", None),
                workflow_content_hash=getattr(plan, "workflow_content_hash", None),
                task_version=str(getattr(plan, "task_version", "") or ""),
                execution_mode=effective_mode,
                execution_plan=self._plan_payload(plan),
                source_run_id=getattr(plan, "baseline_run_id", None),
            )
            self._register_workflow_control(run_id)
            self._running.add(workflow_key)
        logger.info(
            "Workflow 即时测试 run 启动 workflow=%s run=%s profile=%s",
            workflow_key,
            run_id,
            workflow["profile_key"],
        )
        thread = threading.Thread(
            target=self._run_and_release,
            args=(workflow_key, run_id, input_data, actor, True, None, plan),
            daemon=True,
        )
        thread.start()
        return {"status": "started", "run_id": run_id}

    def run_one_workflow(
        self,
        workflow_key: str,
        run_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        actor: str | None = None,
        *,
        resources_validated: bool = False,
        validated_definition: dict[str, Any] | None = None,
        plan: Any | None = None,
    ) -> dict[str, Any]:
        """执行单个 workflow run 的完整生命周期：建 run 行 -> 跑 agent -> 解析 result -> 入库。

        失败（agent 非零退出、result 解析不通过、异常）统一落 failed 状态并
        释放已租借的任务以便快速重试。
        """
        workflow = self._store.get_workflow_definition(workflow_key)
        if workflow is None:
            logger.warning("Workflow 定义不存在 workflow=%s", workflow_key)
            self.finished_today.add(workflow_key)
            return {"status": "missing"}

        if workflow.get("definition") is None:
            return {"status": "missing_definition"}

        base_dir = self._base_run_dir or Path("workflow-runs")
        run = self._store.get_workflow_run(run_id) if run_id is not None else None
        graph: WorkflowGraph | Any | None = None
        if resources_validated:
            if run_id is None and validated_definition is None:
                raise RuntimeError("validated_definition is required when creating a pre-validated workflow run")
        else:
            validation_workflow = {
                **workflow,
                "definition": run["definition_snapshot"] if run is not None else workflow["definition"],
            }
            try:
                graph = self._require_valid_workflow(validation_workflow, actor=actor)
            except WorkflowDefinitionValidationError as exc:
                self.finished_today.add(workflow_key)
                self._log_validation_failure(workflow_key, exc, run_id=run_id)
                if run_id is not None:
                    self._store.finish_workflow_run(
                        run_id,
                        status="failed",
                        exit_code=1,
                        stdout_path=None,
                        stderr_path=None,
                        error=str(exc),
                        duration_ms=None,
                    )
                    self._release_leased_tasks(workflow_key, run_id, str(exc))
                return {
                    "status": "failed",
                    "error": str(exc),
                    "issues": [asdict(issue) for issue in exc.issues],
                }
        if run_id is None:
            run_id = new_run_id(workflow_key)
            definition_snapshot = (
                validated_definition
                if resources_validated
                else self._definition_snapshot(graph, workflow["definition"])
            )
            plan = plan or self._build_plan(
                workflow=workflow,
                definition=definition_snapshot,
                actor=actor,
                task=None,
                execution_mode="normal",
            )
            self._store.create_workflow_run(
                run_id=run_id,
                workflow_key=workflow_key,
                profile_key=workflow["profile_key"],
                task_key=None,
                status="running",
                temp_dir=str(base_dir / run_id),
                definition_snapshot=definition_snapshot,
                input_data=input_data or {},
                workflow_revision_no=getattr(plan, "workflow_revision_no", None),
                workflow_content_hash=getattr(plan, "workflow_content_hash", None),
                task_version=str(getattr(plan, "task_version", "") or ""),
                execution_mode=str(getattr(plan, "mode", "normal")),
                execution_plan=self._plan_payload(plan),
                source_run_id=getattr(plan, "baseline_run_id", None),
            )
            run = None
        self._register_workflow_control(run_id)
        logger.info(
            "Workflow run 开始执行 workflow=%s run=%s profile=%s",
            workflow_key,
            run_id,
            workflow["profile_key"],
        )
        if self._executor is None:
            raise RuntimeError("workflow DAG executor is not configured")
        try:
            run = run or self._store.get_workflow_run(run_id)
            if run is None:
                raise RuntimeError("workflow run not found after creation")
            plan = plan or self._plan_from_run(run)
            if self._is_parent_stop_requested(run_id):
                return self._finish_stopped(workflow_key, run_id)
            execution_workflow = {
                **workflow,
                "definition": run["definition_snapshot"],
            }
            execution = asyncio.run(self._executor.run(
                workflow=execution_workflow,
                run_id=run_id,
                input_data=run["input"],
                actor=actor or sorted(self._admins)[0],
                plan=plan,
            ))
            if self._is_parent_stop_requested(run_id):
                return self._finish_stopped(workflow_key, run_id)
            if execution.status == "no_task":
                self.finished_today.add(workflow_key)
            if execution.status == "completed" and not any(
                node.get("type") == "get_task"
                for node in run["definition_snapshot"].get("nodes", [])
            ):
                # Manual-input workflows have no queue to drain. Run them at
                # most once per scheduler window; explicit test runs remain
                # available through run_workflow_now().
                self.finished_today.add(workflow_key)
            if execution.status == "completed" and execution.task is not None:
                if self._can_complete_task_for_run(workflow_key, run, execution.task):
                    completed = self._store.complete_workflow_task(
                        workflow_key,
                        execution.task["task_key"],
                        task_version=str(execution.task.get("task_version") or ""),
                        run_id=run_id,
                    )
                    if not completed:
                        raise RuntimeError("workflow task completion failed")
                else:
                    self._release_revision_mismatch_task(workflow_key, run_id)
            finished = self._store.finish_workflow_run(
                run_id,
                expected_status="running",
                status=execution.status, exit_code=0 if execution.status != "failed" else 1,
                stdout_path=None, stderr_path=None, error=execution.error, duration_ms=None, output=execution.output,
            )
            if execution.status == "failed":
                self._release_leased_tasks(workflow_key, run_id, execution.error or "workflow failed")
            logger.info(
                "Workflow run 完成 workflow=%s run=%s 状态=%s 耗时=%dms",
                workflow_key,
                run_id,
                execution.status,
                0,
            )
            return {
                "status": self._finished_status(finished, execution.status),
                "output": execution.output,
                "warnings": execution.warnings,
            }
        except Exception as exc:
            logger.exception("Workflow 执行失败 workflow=%s run=%s", workflow_key, run_id)
            if self._is_parent_stop_requested(run_id):
                return self._finish_stopped(workflow_key, run_id)
            finished = self._store.finish_workflow_run(
                run_id,
                expected_status="running",
                status="failed",
                exit_code=1,
                stdout_path=None,
                stderr_path=None,
                error=str(exc),
                duration_ms=None,
            )
            status = self._finished_status(finished, "failed")
            if status == "failed":
                self._release_leased_tasks(workflow_key, run_id, str(exc))
            return {"status": status, "error": str(exc)}
        finally:
            self._finish_workflow_control(run_id)

    def _release_leased_tasks(self, workflow_key: str, run_id: str, error: str) -> None:
        """Release a failed task lease for retry, preserving the branch's DAG execution flow."""
        release = getattr(self._store, "release_or_abandon_tasks_for_run", None)
        if callable(release):
            release(
                workflow_key,
                run_id,
                max_attempts=_MAX_TASK_ATTEMPTS,
                error_message=error,
            )
            return
        self._store.fail_workflow_task_for_run(workflow_key, run_id, error)

    def _build_plan(
        self,
        *,
        workflow: dict[str, Any],
        definition: dict[str, Any],
        actor: str | None,
        task: dict[str, Any] | None,
        execution_mode: str,
    ) -> Any | None:
        build = getattr(self._service, "build_incremental_plan", None)
        if not callable(build):
            return None
        return build(
            actor=self._default_actor(actor),
            workflow_key=str(workflow["workflow_key"]),
            task_key=str(task["task_key"]) if task is not None else None,
            task_version=str(task.get("task_version") or "") if task is not None else None,
            execution_mode=execution_mode,
            workflow=workflow,
            definition=definition,
        )

    def _plan_payload(self, plan: Any | None) -> dict[str, Any]:
        if plan is None:
            return {}
        payload = getattr(self._service, "incremental_plan_payload", None)
        return payload(plan) if callable(payload) else {}

    def _plan_from_run(self, run: dict[str, Any]) -> Any | None:
        payload = run.get("execution_plan")
        from_payload = getattr(self._service, "incremental_plan_from_payload", None)
        if not isinstance(payload, dict) or not payload or not callable(from_payload):
            return None
        return from_payload(payload)

    def _can_complete_task_for_run(
        self,
        workflow_key: str,
        run: dict[str, Any],
        task: dict[str, Any],
    ) -> bool:
        expected_key = run.get("task_key")
        expected_version = str(run.get("task_version") or "")
        actual_version = str(task.get("task_version") or "")
        if expected_key is not None and (expected_key != task.get("task_key") or expected_version != actual_version):
            return False
        repository = getattr(self._store, "workflows", None)
        current_revision = getattr(repository, "get_current_definition_revision_no", None)
        get_revision = getattr(repository, "get_definition_revision", None)
        if not callable(current_revision) or not callable(get_revision):
            return True
        revision_no = run.get("workflow_revision_no")
        if revision_no is None or int(revision_no) != int(current_revision(workflow_key)):
            return False
        revision = get_revision(workflow_key, int(revision_no))
        return revision is not None and revision.get("content_hash") == run.get("workflow_content_hash")

    def _release_revision_mismatch_task(self, workflow_key: str, run_id: str) -> None:
        repository = getattr(self._store, "workflows", None)
        release = getattr(repository, "release_tasks_for_revision_mismatch", None)
        if callable(release):
            release(workflow_key, run_id, "workflow definition or task version changed during run")
            return
        self._release_leased_tasks(
            workflow_key, run_id, "workflow definition or task version changed during run"
        )

    @staticmethod
    def _finished_status(result: Any, fallback: str) -> str:
        return str(result.get("status") or fallback) if isinstance(result, dict) else fallback

    def _control_registry(self) -> Any | None:
        return getattr(getattr(self._service, "agent_service", None), "control_registry", None)

    def _register_workflow_control(self, run_id: str) -> None:
        registry = self._control_registry()
        if registry is not None:
            registry.register_workflow(run_id)

    def _finish_workflow_control(self, run_id: str) -> None:
        registry = self._control_registry()
        if registry is not None:
            registry.finish_workflow(run_id)

    def _is_parent_stop_requested(self, run_id: str) -> bool:
        registry = self._control_registry()
        return registry is not None and registry.is_workflow_stop_requested(run_id)

    def _workflow_run_lock(self, run_id: str) -> threading.RLock:
        with self._run_locks_lock:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.RLock()
                self._run_locks[run_id] = lock
            return lock

    def _finish_stopped(self, workflow_key: str, run_id: str) -> dict[str, Any]:
        with self._workflow_run_lock(run_id):
            result = self._store.finish_workflow_run(
                run_id,
                expected_status="running",
                status="stopped",
                exit_code=None,
                stdout_path=None,
                stderr_path=None,
                error=STOPPED_ERROR,
                duration_ms=None,
            )
            status = self._finished_status(result, "stopped")
            if status == "stopped":
                self._store.workflows.release_tasks_for_stopped_run(
                    workflow_key,
                    run_id,
                    STOPPED_ERROR,
                )
            return {"status": status, "run_id": run_id}

    def _default_actor(self, actor: str | None) -> str:
        return actor or sorted(self._admins)[0]

    def _require_valid_workflow(self, workflow: dict[str, Any], *, actor: str | None) -> WorkflowGraph | Any:
        if self._validator is None:
            return workflow["definition"]
        return self._validator.require_valid(actor=self._default_actor(actor), workflow=workflow)

    @staticmethod
    def _definition_snapshot(definition: WorkflowGraph | Any, fallback: dict[str, Any]) -> dict[str, Any]:
        if isinstance(definition, WorkflowGraph):
            return definition.model_dump(mode="json")
        if isinstance(definition, dict):
            return definition
        return fallback

    def _log_validation_failure(
        self,
        workflow_key: str,
        exc: WorkflowDefinitionValidationError,
        *,
        run_id: str | None = None,
    ) -> None:
        issues = [asdict(issue) for issue in exc.issues]
        logger.warning(
            "Workflow 执行前校验失败 workflow=%s run=%s issues=%s",
            workflow_key,
            run_id,
            issues,
        )
        if run_id is None or not hasattr(self._service, "append_run_log"):
            return
        try:
            self._service.append_run_log(
                workflow_key=workflow_key,
                run_id=run_id,
                task_key=None,
                level="error",
                stage="validate",
                message="workflow validation failed before execution",
                payload={"issues": issues},
            )
        except Exception:
            logger.exception("Workflow 校验失败日志写入失败 workflow=%s run=%s", workflow_key, run_id)


def _parse_hhmm(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        return None
