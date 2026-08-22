from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.dashboard.service import DashboardOverviewService


def test_dashboard_overview_uses_disk_cache_and_manual_refresh_rebuilds(tmp_path: Path) -> None:
    calls = {"runs": 0, "logs": 0, "stats": 0}

    def workflow_runs(actor: str, workflow_key: str) -> list[dict[str, str]]:
        calls["runs"] += 1
        return [{"status": "completed", "finished_at": "2026-08-21T02:00:00+00:00"}]

    def logs(**kwargs) -> dict[str, object]:
        calls["logs"] += 1
        return {"items": [{
            "resource_type": "knowledge_base",
            "source_type": "wiki",
            "source_key": "wiki-main",
            "tool_name": "wiki_ask",
            "created_at": "2026-08-21T02:00:00+00:00",
            "status": "success",
        }]}

    def stats(**kwargs) -> dict[str, object]:
        calls["stats"] += 1
        return {"items": [{
            "resource_type": "knowledge_base",
            "source_type": "wiki",
            "bucket": "2026-08-21",
            "calls": 3,
        }]}

    service = DashboardOverviewService(
        cache_dir=tmp_path / "dashboard-cache",
        admins={"root"},
        actor_group_key=lambda actor: "team-a",
        list_kbs=lambda actor: [{"slug": "main", "document_count": 4}],
        list_repositories=lambda actor: [{"repo_key": "app"}],
        list_memory_blocks=lambda actor: [{"block_key": "memory"}],
        list_ledgers=lambda actor: [{"ledger_key": "sales"}],
        list_mcp_services=lambda actor: [{"service_key": "mcp"}],
        list_openapi_services=lambda actor: [{"service_key": "openapi"}],
        list_workflows=lambda actor: [{"workflow_key": "daily"}],
        list_workflow_runs=workflow_runs,
        list_logs=logs,
        tool_stats=stats,
    )
    params = {
        "actor": "alice",
        "created_from": "2026-08-15 00:00:00",
        "created_to": "2026-08-22 00:00:00",
    }

    first = service.overview(**params)
    second = service.overview(**params)

    assert first["asset_totals"] == {
        "documents": 4,
        "code": 1,
        "memory": 1,
        "ledger": 1,
        "capability": 2,
    }
    assert second == first
    assert calls == {"runs": 1, "logs": 1, "stats": 1}

    service.overview(**params, refresh=True)

    assert calls == {"runs": 2, "logs": 2, "stats": 2}


def test_dashboard_overview_api_returns_cached_aggregate_payload(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/dashboard/overview",
            headers={"X-Agent-Bridge-User": "root"},
            params={
                "created_from": "2026-08-15 00:00:00",
                "created_to": "2026-08-22 00:00:00",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_totals"] == {
        "documents": 0,
        "code": 0,
        "memory": 0,
        "ledger": 0,
        "capability": 0,
    }
    assert len(payload["workflow_days"]) == 7
    assert payload["tool_calls_by_day"]["documents"] == [0] * 7
