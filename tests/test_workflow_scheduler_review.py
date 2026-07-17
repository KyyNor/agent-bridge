from agent_bridge.automation.workflows.executor import WorkflowExecutionResult
from agent_bridge.automation.workflows.scheduler import WorkflowScheduler


class SchedulerStore:
    def __init__(self):
        self.current_definition = {"nodes": [{"id": "current"}], "edges": []}
        self.snapshot = {"nodes": [{"id": "snapshot"}], "edges": []}
        self.completed_task = None
        self.finished_run = None

    def get_workflow_definition(self, workflow_key):
        return {"workflow_key": workflow_key, "profile_key": "p", "workflow_type": "operation", "definition": self.current_definition}

    def get_workflow_run(self, run_id):
        return {"run_id": run_id, "definition_snapshot": self.snapshot, "input": {"topic": "snapshot-input"}}

    def complete_workflow_task(self, workflow_key, task_key, *, task_version, run_id):
        self.completed_task = (workflow_key, task_key, task_version, run_id)
        return True

    def finish_workflow_run(self, run_id, **kwargs):
        self.finished_run = (run_id, kwargs)

    def fail_workflow_task_for_run(self, *args):
        raise AssertionError("successful run must not fail task")


class RecordingExecutor:
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return WorkflowExecutionResult(
            status="completed",
            output={"final": {"text": "ok"}},
            task={"task_key": "task-1", "task_version": "v2"},
            error=None,
            warnings=[],
            node_statuses={"final": "completed"},
        )


def test_scheduler_executes_run_snapshot_and_completes_leased_task(tmp_path):
    store = SchedulerStore()
    executor = RecordingExecutor()
    scheduler = WorkflowScheduler(
        service=object(), store=store, admins={"root"}, executor=executor, base_run_dir=tmp_path
    )

    result = scheduler.run_one_workflow("workflow-1", run_id="run-1", input_data={"topic": "current-input"})

    assert result["status"] == "completed"
    assert executor.kwargs["workflow"]["definition"] == store.snapshot
    assert executor.kwargs["input_data"] == {"topic": "snapshot-input"}
    assert store.completed_task == ("workflow-1", "task-1", "v2", "run-1")
    assert store.finished_run[1]["output"] == {"final": {"text": "ok"}}
