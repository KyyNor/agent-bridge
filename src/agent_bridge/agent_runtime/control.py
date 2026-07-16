"""Thread-safe cancellation controls for in-process agent runs."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


DEFAULT_TOMBSTONE_TTL_SECONDS = 600.0


@dataclass(slots=True)
class RunControl:
    """Cancellation handle owned by one active AgentService run."""

    run_key: str
    workflow_run_id: str | None = None
    stop_requested: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class _WorkflowControl:
    stop_requested: threading.Event = field(default_factory=threading.Event)
    agent_keys: set[str] = field(default_factory=set)


class RunControlRegistry:
    """Coordinate stop requests across API threads and async agent runs.

    Unknown keys are retained as short-lived tombstones.  This closes the
    small race where a caller can request a stop before ``AgentService`` has
    registered the run key.
    """

    def __init__(self, tombstone_ttl_seconds: float = DEFAULT_TOMBSTONE_TTL_SECONDS) -> None:
        self._lock = threading.RLock()
        self._tombstone_ttl_seconds = max(0.0, float(tombstone_ttl_seconds))
        self._runs: dict[str, RunControl] = {}
        self._workflows: dict[str, _WorkflowControl] = {}
        self._run_tombstones: dict[str, float] = {}
        self._workflow_tombstones: dict[str, float] = {}

    def register(
        self, run_key: str, workflow_run_id: str | None = None
    ) -> RunControl:
        with self._lock:
            self._purge_expired_tombstones()
            control = self._runs.get(run_key)
            if control is None:
                control = RunControl(run_key=run_key, workflow_run_id=workflow_run_id)
                if run_key in self._run_tombstones:
                    control.stop_requested.set()
                    self._run_tombstones.pop(run_key, None)
                self._runs[run_key] = control
            elif workflow_run_id and control.workflow_run_id is None:
                control.workflow_run_id = workflow_run_id

            if workflow_run_id:
                workflow = self._workflows.get(workflow_run_id)
                if workflow is None:
                    workflow = _WorkflowControl()
                    if workflow_run_id in self._workflow_tombstones:
                        workflow.stop_requested.set()
                        self._workflow_tombstones.pop(workflow_run_id, None)
                    self._workflows[workflow_run_id] = workflow
                workflow.agent_keys.add(run_key)
                if workflow.stop_requested.is_set():
                    control.stop_requested.set()
            return control

    def register_workflow(self, workflow_run_id: str) -> None:
        with self._lock:
            self._purge_expired_tombstones()
            if workflow_run_id in self._workflows:
                return
            control = _WorkflowControl()
            if workflow_run_id in self._workflow_tombstones:
                control.stop_requested.set()
                self._workflow_tombstones.pop(workflow_run_id, None)
            self._workflows[workflow_run_id] = control

    def request_stop(self, run_key: str) -> bool:
        with self._lock:
            self._purge_expired_tombstones()
            control = self._runs.get(run_key)
            if control is not None:
                control.stop_requested.set()
                return True
            self._run_tombstones[run_key] = self._tombstone_deadline()
            return True

    def request_workflow_stop(self, workflow_run_id: str) -> bool:
        with self._lock:
            self._purge_expired_tombstones()
            control = self._workflows.get(workflow_run_id)
            if control is None:
                self._workflow_tombstones[workflow_run_id] = self._tombstone_deadline()
                return True
            control.stop_requested.set()
            for run_key in tuple(control.agent_keys):
                agent = self._runs.get(run_key)
                if agent is not None:
                    agent.stop_requested.set()
            return True

    def is_stop_requested(self, run_key: str) -> bool:
        with self._lock:
            self._purge_expired_tombstones()
            control = self._runs.get(run_key)
            return bool(
                (control is not None and control.stop_requested.is_set())
                or run_key in self._run_tombstones
            )

    def is_workflow_stop_requested(self, workflow_run_id: str) -> bool:
        with self._lock:
            self._purge_expired_tombstones()
            control = self._workflows.get(workflow_run_id)
            return bool(
                (control is not None and control.stop_requested.is_set())
                or workflow_run_id in self._workflow_tombstones
            )

    def has_pending_control(self, run_key: str) -> bool:
        """Return whether a stop tombstone is waiting for a future run."""
        with self._lock:
            self._purge_expired_tombstones()
            return run_key in self._run_tombstones

    def finish(self, run_key: str) -> None:
        with self._lock:
            control = self._runs.pop(run_key, None)
            if control is not None and control.workflow_run_id:
                workflow = self._workflows.get(control.workflow_run_id)
                if workflow is not None:
                    workflow.agent_keys.discard(run_key)

    def finish_workflow(self, workflow_run_id: str) -> None:
        with self._lock:
            self._workflows.pop(workflow_run_id, None)

    def is_active(self, run_key: str) -> bool:
        with self._lock:
            return run_key in self._runs

    def is_workflow_active(self, workflow_run_id: str) -> bool:
        with self._lock:
            return workflow_run_id in self._workflows

    def _tombstone_deadline(self) -> float:
        return time.monotonic() + self._tombstone_ttl_seconds

    def _purge_expired_tombstones(self) -> None:
        now = time.monotonic()
        self._run_tombstones = {
            key: deadline
            for key, deadline in self._run_tombstones.items()
            if deadline > now
        }
        self._workflow_tombstones = {
            key: deadline
            for key, deadline in self._workflow_tombstones.items()
            if deadline > now
        }
