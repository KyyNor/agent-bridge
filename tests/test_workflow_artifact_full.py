"""Artifact retrieval with full content (feature 2 — view outputs from tasks page)."""

from __future__ import annotations


def _service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="w",
        name="w",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    return svc


def _seed_artifact(svc, *, run_id: str, task_version: str, title: str, content: str):
    svc.store.create_workflow_run(
        run_id=run_id,
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="completed",
        temp_dir=f"/tmp/{run_id}",
    )
    svc.workflows.save_artifact(
        workflow_key="w",
        profile_key="report-plane",
        run_id=run_id,
        task_key="page:a",
        task_version=task_version,
        title=title,
        path="pages/page-a.md",
        tags=[],
        format="markdown",
        summary="",
        content=content,
        metadata={},
    )


def test_search_artifacts_default_omits_full_content(wm_paths):
    svc = _service(wm_paths)
    _seed_artifact(svc, run_id="run_1", task_version="v1", title="A", content="# full body v1")

    result = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query=None,
        tags=[],
        path=None,
        workflow_key="w",
        task_key="page:a",
        include_history=False,
        limit=10,
    )
    items = result["items"]
    assert len(items) == 1
    # Default (non-exact-path) returns a snippet, never the raw content body.
    assert "content" not in items[0]
    assert "snippet" in items[0]


def test_search_artifacts_full_returns_complete_content_and_history(wm_paths):
    svc = _service(wm_paths)
    _seed_artifact(svc, run_id="run_1", task_version="v1", title="A v1", content="# full body v1")
    _seed_artifact(svc, run_id="run_2", task_version="v2", title="A v2", content="# full body v2")

    result = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query=None,
        tags=[],
        path=None,
        workflow_key="w",
        task_key="page:a",
        include_history=True,
        full=True,
        limit=10,
    )
    items = result["items"]
    # Both versions present, newest first.
    assert [item["task_version"] for item in items] == ["v2", "v1"]
    # Each entry carries the full content (feature 2 requirement).
    assert items[0]["content"] == "# full body v2"
    assert items[1]["content"] == "# full body v1"


def test_search_artifacts_full_default_false_keeps_snippet_behaviour(wm_paths):
    svc = _service(wm_paths)
    _seed_artifact(svc, run_id="run_1", task_version="v1", title="A", content="# full body v1")

    # full left at its default must behave exactly like the old behaviour:
    # snippet present, content absent for a non-exact-path search.
    result = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query=None,
        tags=[],
        path=None,
        workflow_key="w",
        task_key="page:a",
        include_history=True,
        limit=10,
    )
    assert len(result["items"]) == 1
    assert "content" not in result["items"][0]
    assert "snippet" in result["items"][0]


def test_running_artifacts_do_not_replace_current_until_run_completes(wm_paths):
    svc = _service(wm_paths)
    _seed_artifact(svc, run_id="run_1", task_version="v1", title="A v1", content="# full body v1")

    svc.store.create_workflow_run(
        run_id="run_2",
        workflow_key="w",
        profile_key="report-plane",
        task_key="page:a",
        status="running",
        temp_dir="/tmp/run_2",
        task_version="v2",
    )
    saved = svc.workflows.save_artifact(
        workflow_key="w",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        task_version="v2",
        title="A v2",
        path="pages/page-a.md",
        tags=[],
        format="markdown",
        summary="",
        content="# full body v2",
        metadata={},
        producer_node_id="output",
        producer_node_fingerprint="fingerprint-v2",
    )

    previous = svc.store.search_workflow_artifacts(
        profile_key="report-plane", query=None, tags=[], path="pages/page-a.md",
        workflow_key="w", task_key="page:a", include_history=False, limit=10,
    )
    assert [item["run_id"] for item in previous] == ["run_1"]
    assert saved["is_current"] is False
    assert saved["producer_node_id"] == "output"
    assert saved["producer_node_fingerprint"] == "fingerprint-v2"

    svc.store.finish_workflow_run(
        "run_2", status="completed", exit_code=0, stdout_path=None, stderr_path=None,
        error=None, duration_ms=0, output={},
    )
    current = svc.store.search_workflow_artifacts(
        profile_key="report-plane", query=None, tags=[], path="pages/page-a.md",
        workflow_key="w", task_key="page:a", include_history=False, limit=10,
    )
    assert [item["run_id"] for item in current] == ["run_2"]
