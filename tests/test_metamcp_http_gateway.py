from __future__ import annotations

from fastapi.testclient import TestClient

from wiki_manager.capabilities import CallLogStatus, ProfileRuleEffect, SourceType
from wiki_manager.server import create_app
from wiki_manager.storage import SQLiteStore


def _client(wm_paths) -> TestClient:
    return TestClient(create_app(paths=wm_paths, admins={"root"}))


def _register_service(client: TestClient, service_key: str, name: str) -> None:
    response = client.post(
        "/capabilities/mcp-services",
        json={"service_key": service_key, "name": name, "endpoint_url": f"https://{service_key}.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    assert response.status_code == 200


def _create_profile(wm_paths, *, denied_service: str = "hive") -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="安全只读",
        description="",
        status="active",
        created_by="root",
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": denied_service,
                "effect": ProfileRuleEffect.deny.value,
            }
        ],
    )


def test_metamcp_http_search_uses_profile_header(wm_paths) -> None:
    client = _client(wm_paths)
    _register_service(client, "mysql", "MySQL")
    _register_service(client, "hive", "Hive")
    _create_profile(wm_paths)

    response = client.post(
        "/mcp/search",
        json={},
        headers={"X-Wiki-User": "root", "X-Wiki-MetaMCP-Profile": "safe-readonly"},
    )

    assert response.status_code == 200
    data = response.json()
    assert [item["service"] for item in data["items"]] == ["mysql"]
    assert data["log_id"].startswith("call_")


def test_metamcp_http_search_without_profile_header_lists_all_services(wm_paths) -> None:
    client = _client(wm_paths)
    _register_service(client, "mysql", "MySQL")
    _register_service(client, "hive", "Hive")
    _create_profile(wm_paths)

    response = client.post("/mcp/search", json={}, headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    data = response.json()
    assert [item["service"] for item in data["items"]] == ["hive", "mysql"]
    assert data["log_id"].startswith("call_")


def test_metamcp_http_execute_denied_by_profile_returns_log_id_error(wm_paths) -> None:
    client = _client(wm_paths)
    _register_service(client, "hive", "Hive")
    _create_profile(wm_paths)

    response = client.post(
        "/mcp/execute",
        json={"service": "hive", "tool": "query_sql", "arguments": {"sql": "select 1"}},
        headers={"X-Wiki-User": "root", "X-Wiki-MetaMCP-Profile": "safe-readonly"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail.startswith("source is blocked by profile policy")
    assert "log_id: call_" in detail

    store = SQLiteStore(wm_paths.db_path)
    logs = store.list_tool_call_logs(status=CallLogStatus.blocked.value)
    assert len(logs) == 1
    assert logs[0]["source_key"] == "hive"
    assert logs[0]["tool_name"] == "query_sql"
    assert logs[0]["log_id"] in detail
