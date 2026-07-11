from __future__ import annotations


def _service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    return svc


def test_workflow_service_creates_definition_with_existing_profile(wm_paths):
    svc = _service(wm_paths)

    created = svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="Nightly page report",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"


def test_workflow_service_rejects_missing_profile(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)

    try:
        svc.workflows.upsert_definition(
            actor="root",
            workflow_key="page-report",
            name="Page Report",
            description="",
            profile_key="missing",
            definition={"nodes": [], "edges": []},
            status="active",
        )
    except ValidationError as exc:
        assert any(issue.field == "profile_key" for issue in exc.issues)
    else:
        raise AssertionError("missing profile should be rejected")


def test_workflow_service_applies_summary_validation_for_string_type(wm_paths):
    from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError

    svc = _service(wm_paths)
    try:
        svc.workflows.upsert_definition(
            actor="root",
            workflow_key="summary-report",
            name="Summary",
            description="",
            profile_key="report-plane",
            definition={"nodes": [], "edges": []},
            status="active",
            workflow_type="summary",
        )
    except WorkflowDefinitionValidationError as exc:
        assert any("Markdown 和 HTML" in issue.message for issue in exc.issues)
    else:
        raise AssertionError("expected summary graph validation error")


def test_manual_input_type_conflict_uses_structured_reference_parser(wm_paths):
    from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError

    svc = _service(wm_paths)
    code = "def main(envelope):\n    return {}\n"
    for key, field_type in (("string-script", "string"), ("integer-script", "integer")):
        svc.scripts.upsert_script(
            actor="root",
            script_key=key,
            name=key,
            description="",
            language="python",
            code=code,
            input_schema={"type": "object", "properties": {"value": {"type": field_type}}, "required": ["value"]},
            status="active",
            owner_type="system",
            owner_key="",
        )
    definition = {"nodes": [
        {"id": "compact", "type": "script", "name": "Compact", "position": {"x": 0, "y": 0}, "config": {"script_key": "string-script", "params": {"value": "{{input.topic}}"}}},
        {"id": "spaced", "type": "script", "name": "Spaced", "position": {"x": 1, "y": 0}, "config": {"script_key": "integer-script", "params": {"value": "{{ input.topic }}"}}},
    ]}
    try:
        svc.workflows.upsert_definition(actor="root", workflow_key="manual", name="Manual", description="", profile_key="report-plane", definition=definition, status="active")
    except WorkflowDefinitionValidationError as exc:
        assert any("手动输入类型冲突" in issue.message for issue in exc.issues)
    else:
        raise AssertionError("expected manual input type conflict")


def test_workflow_service_appends_run_log(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )

    svc.workflows.append_run_log(
        workflow_key="page-report",
        run_id="run_1",
        task_key="page:a",
        level="info",
        stage="analyze",
        message="started",
        payload={"step": 1},
    )

    logs = svc.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "started"
    assert logs[0]["payload"]["step"] == 1


def test_workflow_service_saves_and_searches_artifacts(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )

    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "finance"],
        format="markdown",
        summary="Finance page report",
        content="# Page A\n\nUses table finance_orders.",
        metadata={"page_key": "page-a"},
    )

    assert saved["artifact_id"].startswith("artifact_")
    results = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query="finance_orders",
        tags=["finance"],
        path="reports/",
        workflow_key=None,
        limit=10,
    )
    assert [item["title"] for item in results["items"]] == ["Page A Report"]
    assert "finance_orders" in results["items"][0]["snippet"]


def test_workflow_service_search_returns_full_content_only_for_exact_path(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=[],
        format="markdown",
        summary="summary",
        content="# Page A\n\nFULL BODY",
        metadata={},
    )

    exact = svc.workflows.search_artifacts(
        actor="root", profile_key="report-plane", query=None, tags=[],
        path="reports/page-a/index.md", workflow_key=None, limit=10,
    )
    assert exact["items"][0]["content"] == "# Page A\n\nFULL BODY"
    assert "snippet" in exact["items"][0]  # snippet still present alongside content

    prefix = svc.workflows.search_artifacts(
        actor="root", profile_key="report-plane", query=None, tags=[],
        path="reports/", workflow_key=None, limit=10,
    )
    assert prefix["items"][0]["path"] == "reports/page-a/index.md"
    assert "content" not in prefix["items"][0]  # prefix match -> snippet only
    assert "snippet" in prefix["items"][0]


def test_workflow_service_allows_non_admin_profile_artifact_search(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "finance"],
        format="markdown",
        summary="Finance page report",
        content="# Page A\n\nUses table finance_orders.",
        metadata={"page_key": "page-a"},
    )

    results = svc.workflows.search_artifacts(
        actor="alice",
        profile_key="report-plane",
        query="finance_orders",
        tags=[],
        path=None,
        workflow_key=None,
        limit=10,
        trusted_profile_context=True,
    )

    assert [item["title"] for item in results["items"]] == ["Page A Report"]


def test_workflow_service_rejects_non_admin_untrusted_artifact_search(wm_paths):
    from agent_bridge.core.domain import AccessDenied

    svc = _service(wm_paths)

    try:
        svc.workflows.search_artifacts(
            actor="alice",
            profile_key=None,
            query="anything",
            tags=[],
            path=None,
            workflow_key=None,
            limit=10,
        )
    except AccessDenied as exc:
        assert "capability profile is required" in exc.message
    else:
        raise AssertionError("non-admin artifact search without profile should fail")

    try:
        svc.workflows.search_artifacts(
            actor="alice",
            profile_key="report-plane",
            query="anything",
            tags=[],
            path=None,
            workflow_key=None,
            limit=10,
        )
    except AccessDenied as exc:
        assert "profile context is not trusted" in exc.message
    else:
        raise AssertionError("non-admin untrusted artifact search with profile should fail")


def test_workflow_service_artifact_search_applies_tags_before_limit(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    for index in range(3):
        svc.workflows.save_artifact(
            workflow_key="page-report",
            profile_key="report-plane",
            run_id=f"run_{index}",
            task_key=f"page:new-{index}",
            title=f"New Report {index}",
            path=f"reports/new-{index}/index.md",
            tags=["recent"],
            format="markdown",
            summary="Recent report",
            content="recent content",
            metadata={},
        )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_old",
        task_key="page:old",
        title="Older Finance Report",
        path="reports/older-finance/index.md",
        tags=["finance"],
        format="markdown",
        summary="Older finance report",
        content="older finance content",
        metadata={},
    )

    results = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query=None,
        tags=["finance"],
        path=None,
        workflow_key=None,
        limit=1,
    )

    assert [item["title"] for item in results["items"]] == ["Older Finance Report"]


def test_workflow_service_artifact_search_matches_literal_tag_wildcards(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:percent",
        title="Percent Tag Report",
        path="reports/percent/index.md",
        tags=["%"],
        format="markdown",
        summary="Percent tag",
        content="percent tag",
        metadata={},
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:abc",
        title="ABC Tag Report",
        path="reports/abc/index.md",
        tags=["abc"],
        format="markdown",
        summary="ABC tag",
        content="abc tag",
        metadata={},
    )

    results = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query=None,
        tags=["%"],
        path=None,
        workflow_key=None,
        limit=1,
    )

    assert [item["title"] for item in results["items"]] == ["Percent Tag Report"]


def test_workflow_service_rejects_disabled_profile_artifact_search(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)
    svc.store.upsert_project_profile(
        profile_key="disabled-plane",
        name="Disabled Plane",
        status="disabled",
        created_by="root",
    )

    try:
        svc.workflows.search_artifacts(
            actor="alice",
            profile_key="disabled-plane",
            query=None,
            tags=[],
            path=None,
            workflow_key=None,
            limit=10,
            trusted_profile_context=True,
        )
    except ValidationError as exc:
        assert "profile is disabled" in exc.message
    else:
        raise AssertionError("disabled profile should not search artifacts")


def test_workflow_service_rejects_artifact_profile_mismatch(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)
    svc.store.upsert_project_profile(profile_key="other-plane", name="Other Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )

    try:
        svc.workflows.save_artifact(
            workflow_key="page-report",
            profile_key="other-plane",
            run_id="run_1",
            task_key="page:a",
            title="Page A Report",
            path="reports/page-a/index.md",
            tags=["report"],
            format="markdown",
            summary="",
            content="# Page A",
            metadata={},
        )
    except ValidationError as exc:
        assert "workflow profile mismatch" in exc.message
    else:
        raise AssertionError("artifact profile mismatch should fail")


def test_workflow_service_artifact_upsert_tracks_current_version_and_metadata(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )

    first = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report"],
        format="markdown",
        summary="first",
        content="# First",
        metadata={"version": 1},
    )
    second = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "updated"],
        format="markdown",
        summary="second",
        content="# Second",
        metadata={"version": 2},
    )

    assert second["artifact_id"] != first["artifact_id"]
    assert svc.store.get_workflow_artifact(first["artifact_id"])["is_current"] is False
    assert svc.store.get_workflow_artifact(second["artifact_id"])["is_current"] is True
    assert second["content_hash"] != first["content_hash"]
    assert second["metadata"]["version"] == 2
    assert second["tags"] == ["report", "updated"]
