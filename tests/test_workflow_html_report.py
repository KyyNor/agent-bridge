"""Tests for summary-workflow HTML report generation and the workflow_type /
artifact-format plumbing that supports it."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _seed_profile_and_workflow(
    svc,
    *,
    workflow_type: str = "operation",
    workflow_key: str = "page-report",
    profile_key: str = "report-plane",
) -> None:
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key=profile_key, name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key=workflow_key,
        name="Page Report",
        description="",
        profile_key=profile_key,
        workflow_js="",
        status="active",
        workflow_type=workflow_type,
    )


def _seed_markdown_artifact(
    svc,
    *,
    run_id: str = "run_1",
    path: str = "reports/page-a/index.md",
    task_key: str = "page:a",
) -> str:
    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id=run_id,
        task_key=task_key,
        title="Page A",
        path=path,
        tags=["finance"],
        format="markdown",
        summary="Finance report",
        content="# Page A\n\nFull body",
        metadata={},
    )
    return saved["artifact_id"]


class _FakeAgentRunResult(SimpleNamespace):
    pass


class _FakeAgentService:
    """Minimal stand-in for AgentService.run that returns a fixed payload."""

    def __init__(self, payload: dict[str, Any], *, ok: bool = True) -> None:
        self._payload = payload
        self._ok = ok
        self.last_prompt: str | None = None
        self.last_agent_name: str | None = None

    async def run(self, **kwargs: Any) -> Any:
        self.last_prompt = kwargs.get("prompt")
        self.last_agent_name = kwargs.get("agent_name")
        return _FakeAgentRunResult(
            ok=self._ok,
            result=self._payload if self._ok else None,
            error=None if self._ok else "boom",
        )


def _valid_html() -> str:
    return (
        "<!doctype html><html><head><style>body{color:#1f2937}</style></head>"
        "<body><h1>Report</h1><p>summary</p></body></html>"
    )


# --------------------------------------------------------------------------- #
# Data layer / workflow_type round-trip
# --------------------------------------------------------------------------- #

def test_workflow_type_defaults_to_operation_when_column_missing(wm_paths):
    # An older DB (no workflow_type column) must be auto-migrated to 'operation'.
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="p", name="P", created_by="root")
    svc.store.upsert_workflow_definition(
        workflow_key="w",
        name="W",
        description="",
        profile_key="p",
        workflow_js="",
        status="active",
        created_by="root",
    )
    wf = svc.store.get_workflow_definition("w")
    assert wf["workflow_type"] == "operation"


def test_workflow_type_round_trips_through_service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.core.domain import ValidationError

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="p", name="P", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="w",
        name="W",
        description="",
        profile_key="p",
        workflow_js="",
        status="active",
        workflow_type="summary",
    )
    assert svc.workflows.get_definition("root", "w")["workflow_type"] == "summary"

    try:
        svc.workflows.upsert_definition(
            actor="root", workflow_key="w2", name="W2", description="",
            profile_key="p", workflow_js="", status="active", workflow_type="bogus",
        )
    except ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown workflow_type")


def test_workflow_type_round_trips_through_api(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService
    from fastapi.testclient import TestClient

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="p", name="P", created_by="root")
    client = TestClient(create_app(wm_paths, {"root"}))
    resp = client.post(
        "/workflows",
        json={
            "workflow_key": "w", "name": "W", "description": "",
            "profile_key": "p", "workflow_js": "", "status": "active",
            "workflow_type": "summary",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workflow_type"] == "summary"


# --------------------------------------------------------------------------- #
# Artifact format plumbing
# --------------------------------------------------------------------------- #

def test_save_artifact_accepts_html(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Report",
        path="out/report.html",
        tags=["html-report"],
        format="html",
        summary="Human report",
        content=_valid_html(),
        metadata={"report_kind": "human_html"},
    )
    assert saved["format"] == "html"
    assert saved["path"] == "out/report.html"
    assert saved["metadata"]["report_kind"] == "human_html"


def test_search_artifacts_excludes_html_by_default(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    _seed_markdown_artifact(svc)
    svc.workflows.save_artifact(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1",
        task_key="page:a", title="Report", path="out/report.html", tags=[],
        format="html", summary="", content=_valid_html(), metadata={},
    )
    # Default (no format): only markdown returned (agent retrieval parity).
    default = svc.workflows.search_artifacts(
        actor="root", profile_key="report-plane", query=None, tags=[], path=None,
        workflow_key="page-report", limit=10, trusted_profile_context=True,
    )
    assert [i["format"] for i in default["items"]] == ["markdown"]
    # Explicit format=html surfaces the HTML report.
    only_html = svc.workflows.search_artifacts(
        actor="root", profile_key="report-plane", query=None, tags=[], path=None,
        workflow_key="page-report", limit=10, trusted_profile_context=True, format="html",
    )
    assert [i["format"] for i in only_html["items"]] == ["html"]
    # format=all returns both.
    everything = svc.workflows.search_artifacts(
        actor="root", profile_key="report-plane", query=None, tags=[], path=None,
        workflow_key="page-report", limit=10, trusted_profile_context=True, format="all",
    )
    assert sorted(i["format"] for i in everything["items"]) == ["html", "markdown"]


def test_api_artifacts_format_param(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService
    from fastapi.testclient import TestClient

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    _seed_markdown_artifact(svc)
    svc.workflows.save_artifact(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1",
        task_key="page:a", title="Report", path="out/report.html", tags=[],
        format="html", summary="", content=_valid_html(), metadata={},
    )
    client = TestClient(create_app(wm_paths, {"root"}))
    resp = client.get(
        "/workflow-artifacts?workflow_key=page-report&format=html",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["format"] == "html"


# --------------------------------------------------------------------------- #
# Report generation
# --------------------------------------------------------------------------- #

def _wire_fake_agent(svc, payload: dict[str, Any], *, ok: bool = True) -> _FakeAgentService:
    fake = _FakeAgentService(payload, ok=ok)
    svc.workflows.agent_service = fake
    return fake


def test_generate_report_skips_for_operation_workflow(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="operation")
    _seed_markdown_artifact(svc)
    fake = _wire_fake_agent(svc, {"title": "T", "summary": "S", "html": _valid_html()})
    outcome = svc.workflows.generate_html_report_for_run(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1", actor="root",
    )
    assert outcome["status"] == "skipped"
    assert fake.last_prompt is None  # agent never invoked


def test_generate_report_skips_when_no_markdown(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    fake = _wire_fake_agent(svc, {"title": "T", "summary": "S", "html": _valid_html()})
    outcome = svc.workflows.generate_html_report_for_run(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1", actor="root",
    )
    assert outcome["status"] == "no_markdown"
    assert fake.last_prompt is None


def test_generate_report_invokes_agent_and_saves_html(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    md_id = _seed_markdown_artifact(svc)
    _wire_fake_agent(
        svc,
        {
            "title": "人类报告",
            "summary": "一句话总结",
            "html": _valid_html(),
            "source_artifact_ids": [md_id],
        },
    )
    outcome = svc.workflows.generate_html_report_for_run(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1", actor="root",
    )
    assert outcome["status"] == "generated"
    saved = outcome["artifact"]
    assert saved["format"] == "html"
    assert saved["path"] == "out/report.html"
    assert saved["metadata"]["report_kind"] == "human_html"
    assert saved["metadata"]["derived_from_artifact_ids"] == [md_id]
    # Same task_key as the markdown artifact -> is_current overwrite grouping.
    assert saved["task_key"] == "page:a"


def test_generate_report_does_not_save_when_workflow_stops_after_reporter(wm_paths):
    from agent_bridge.agent_runtime.control import RunControlRegistry
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    md_id = _seed_markdown_artifact(svc)

    class _StoppingReporter(_FakeAgentService):
        def __init__(self):
            super().__init__(
                {
                    "title": "人类报告",
                    "summary": "一句话总结",
                    "html": _valid_html(),
                    "source_artifact_ids": [md_id],
                }
            )
            self.control_registry = RunControlRegistry()
            self.control_registry.register_workflow("run_1")

        async def run(self, **kwargs: Any) -> Any:
            result = await super().run(**kwargs)
            self.control_registry.request_workflow_stop("run_1")
            return result

    fake = _StoppingReporter()
    svc.workflows.agent_service = fake

    outcome = svc.workflows.generate_html_report_for_run(
        workflow_key="page-report", profile_key="report-plane", run_id="run_1", actor="root",
    )

    assert outcome == {"status": "stopped"}
    assert svc.store.search_workflow_artifacts(
        profile_key="report-plane", query=None, tags=[], path=None,
        workflow_key="page-report", run_id="run_1", include_history=True,
        format="html", limit=10,
    ) == []


def test_generate_report_rejects_non_html_output(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.core.domain import ValidationError

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_profile_and_workflow(svc, workflow_type="summary")
    _seed_markdown_artifact(svc)
    _wire_fake_agent(svc, {"title": "T", "summary": "S", "html": "this is not html at all"})
    try:
        svc.workflows.generate_html_report_for_run(
            workflow_key="page-report", profile_key="report-plane", run_id="run_1", actor="root",
        )
    except ValidationError as exc:
        assert "not a valid HTML document" in str(exc)
        return
    raise AssertionError("expected ValidationError for non-HTML output")


def _run_summary_e2e(svc, tmp_path, agent_service, run_id="summary-run-1"):
    """Drive a summary workflow through the scheduler with a FakeWorkflowRunner.

    FakeWorkflowRunner emits task_key='fake-task' but never leases it (the real
    agent leases via the get_task_for_agent MCP tool mid-run). So we pre-seed a
    task matching the runner's output and lease it to the explicit run_id the
    scheduler is told to use, letting complete_workflow_task succeed.
    """
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc.store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "fake-task", "task_version": "", "type": "report", "payload": {}}],
    )
    svc.store.create_workflow_run(
        run_id=run_id, workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="running", temp_dir=str(tmp_path / run_id),
    )
    svc.store.lease_workflow_task("page-report", run_id=run_id, lease_seconds=7200)
    svc.workflows.agent_service = agent_service
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"},
        runner=FakeWorkflowRunner(status="completed"),
        base_run_dir=tmp_path, max_concurrent_workflows=1,
    )
    result = scheduler.run_one_workflow("page-report", run_id=run_id)
    return result, run_id


def _seed_summary_workflow(svc) -> None:
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root", workflow_key="page-report", name="Page Report", description="",
        profile_key="report-plane", workflow_js="", status="active", workflow_type="summary",
    )


def test_report_generation_failure_keeps_run_completed(wm_paths, tmp_path):
    """End-to-end via the scheduler: a summary run whose reporter fails must
    still finish as 'completed' and record a warning run log."""
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_summary_workflow(svc)

    class _BoomAgent:
        async def run(self, **kwargs):
            raise RuntimeError("reporter crashed")

    result, run_id = _run_summary_e2e(svc, tmp_path, _BoomAgent())
    # Main run must stay completed even though the reporter failed.
    assert result["status"] == "completed"
    run = svc.store.get_workflow_run(run_id)
    assert run["status"] == "completed"
    # A warning run log must have been recorded for the html_report stage.
    logs = svc.store.list_workflow_run_logs(run_id)
    html_logs = [log for log in logs if log.get("stage") == "html_report"]
    assert html_logs and any(log.get("level") == "warning" for log in html_logs)


def test_report_generation_success_records_info_log(wm_paths, tmp_path):
    """End-to-end: a summary run with a working reporter produces an HTML
    artifact and records an info run log."""
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    _seed_summary_workflow(svc)
    fake = _wire_fake_agent(
        svc, {"title": "报告", "summary": "S", "html": _valid_html(), "source_artifact_ids": []}
    )
    result, run_id = _run_summary_e2e(svc, tmp_path, fake)
    assert result["status"] == "completed"
    html = svc.store.search_workflow_artifacts(
        profile_key="report-plane", query=None, tags=[], path=None,
        workflow_key="page-report", run_id=run_id, include_history=False,
        format="html", limit=10,
    )
    assert len(html) == 1 and html[0]["format"] == "html"
    logs = svc.store.list_workflow_run_logs(run_id)
    html_logs = [log for log in logs if log.get("stage") == "html_report"]
    assert html_logs and html_logs[-1].get("level") == "info"
