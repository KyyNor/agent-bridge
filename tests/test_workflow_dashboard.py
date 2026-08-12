from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def _create_completed_run(store, workflow_key: str, run_id: str, finished_at: str, status: str = "completed") -> None:
    store.create_workflow_run(
        run_id=run_id,
        workflow_key=workflow_key,
        profile_key="report-plane",
        task_key=None,
        status=status,
        temp_dir="",
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_runs SET finished_at = ? WHERE run_id = ?",
            (finished_at, run_id),
        )


def test_completed_workflow_top_filters_period_and_status(wm_paths) -> None:
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for workflow_key, name in (("alpha", "Alpha 报告"), ("beta", "Beta 摘要"), ("gamma", "Gamma 同步")):
        store.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description="",
            profile_key="report-plane",
            definition={"nodes": [], "edges": []},
            status="active",
            created_by="root",
        )

    period_start = "2026-08-01T16:00:00+00:00"
    period_end = "2026-08-02T16:00:00+00:00"
    for index in range(3):
        _create_completed_run(store, "alpha", f"alpha-{index}", "2026-08-02T08:00:00+00:00")
    for index in range(2):
        _create_completed_run(store, "beta", f"beta-{index}", "2026-08-02T09:00:00+00:00")
    _create_completed_run(store, "gamma", "gamma-failed", "2026-08-02T10:00:00+00:00", status="failed")
    _create_completed_run(store, "gamma", "gamma-today", "2026-08-02T16:00:00+00:00")

    assert store.list_completed_workflow_top(period_start=period_start, period_end=period_end) == [
        {"workflow_key": "alpha", "workflow_name": "Alpha 报告", "completed_count": 3},
        {"workflow_key": "beta", "workflow_name": "Beta 摘要", "completed_count": 2},
    ]


def test_completed_workflow_top_api_uses_previous_local_day(wm_paths, monkeypatch) -> None:
    from agent_bridge.api.app import create_app
    from agent_bridge.automation.workflows import service as workflow_service_module
    from agent_bridge.core.timeutil import utc_iso

    fixed_now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(workflow_service_module, "local_now", lambda: fixed_now)
    app = create_app(wm_paths, {"root"})
    service = app.state.agent_bridge_service
    service.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    service.store.upsert_workflow_definition(
        workflow_key="daily-report",
        name="每日报告",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
        created_by="root",
    )
    included_finished_at = utc_iso(datetime(2026, 8, 3, 6, 0, tzinfo=fixed_now.tzinfo))
    excluded_finished_at = utc_iso(datetime(2026, 8, 2, 9, 0, tzinfo=fixed_now.tzinfo))
    _create_completed_run(service.store, "daily-report", "daily-report-1", included_finished_at)
    _create_completed_run(service.store, "daily-report", "daily-report-2", excluded_finished_at)

    response = TestClient(app).get(
        "/api/v1/workflows/completed-top",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "period_start": "2026-08-02T14:00:00+00:00",
        "period_end": "2026-08-02T23:00:00+00:00",
        "period_label": "08-02 22:00 → 08-03 07:00",
        "items": [
            {
                "workflow_key": "daily-report",
                "workflow_name": "每日报告",
                "completed_count": 1,
            }
        ],
    }
