from __future__ import annotations

from fastapi.testclient import TestClient

from wiki_manager.capabilities import SourceType, ToolType
from wiki_manager.server import create_app
from wiki_manager.storage import SQLiteStore


def test_mcp_service_registration_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    created = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "description": "Database tools",
            "tags": ["database"],
        },
        headers={"X-Wiki-User": "root"},
    )
    listed = client.get("/capabilities/mcp-services", headers={"X-Wiki-User": "alice"})

    assert created.status_code == 200
    assert created.json()["service_key"] == "mysql"
    assert listed.status_code == 200
    assert listed.json()[0]["service_key"] == "mysql"
    assert listed.json()[0]["tags"] == ["database"]
    assert listed.json()[0]["headers"] == {"Authorization": "***"}


def test_mcp_service_registration_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {},
            "description": "",
            "tags": [],
        },
        headers={"X-Wiki-User": "alice"},
    )

    assert response.status_code == 403


def test_mcp_service_update_without_headers_preserves_existing_headers(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "description": "Database tools",
            "tags": ["database"],
        },
        headers={"X-Wiki-User": "root"},
    )

    updated = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL Reporting MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "description": "Updated description",
            "tags": ["database", "reporting"],
        },
        headers={"X-Wiki-User": "root"},
    )
    listed = client.get("/capabilities/mcp-services", headers={"X-Wiki-User": "root"})

    assert updated.status_code == 200
    assert updated.json()["headers"] == {"Authorization": "Bearer secret"}
    assert listed.json()[0]["headers"] == {"Authorization": "***"}
    assert listed.json()[0]["tags"] == ["database", "reporting"]


def test_capability_admin_page_serves_html(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Capability Hub" in response.text
    assert "能力治理控制台" in response.text
    assert "能力目录" in response.text
    assert "/static/capabilities/app.css" in response.text
    assert "/static/capabilities/app.js" in response.text


def test_capability_static_assets_are_served(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    css = client.get("/static/capabilities/app.css")
    js = client.get("/static/capabilities/app.js")

    assert css.status_code == 200
    assert "sidebar" in css.text
    assert js.status_code == 200
    assert "loadServices" in js.text


def test_capability_admin_page_is_chinese_control_console(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert "能力治理控制台" in response.text
    assert "能力目录" in response.text
    assert "调用日志" in response.text
    assert "Project Profile" in response.text
    assert "Claude Code 接入" in response.text
    assert "MCP Services" not in response.text
    assert "Register Service" not in response.text


def test_capability_static_assets_use_chinese_labels(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    js = client.get("/static/capabilities/app.js")

    assert js.status_code == 200
    assert "加载服务" not in js.text
    assert "登记服务" in js.text
    assert "同步工具" in js.text
    assert "调用日志" in js.text


def test_mcp_service_status_and_tools_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
        },
        headers={"X-Wiki-User": "root"},
    )

    disabled = client.post(
        "/capabilities/mcp-services/mysql/status",
        json={"status": "disabled"},
        headers={"X-Wiki-User": "root"},
    )
    tools = client.get("/capabilities/mcp-services/mysql/tools", headers={"X-Wiki-User": "root"})

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert tools.status_code == 400
    assert tools.json()["detail"] == "MCP service is not enabled"


def test_profile_api_and_catalog_preview(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "hive", "name": "Hive", "endpoint_url": "https://hive.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )

    created = client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Wiki-User": "root"},
    )
    rules = client.put(
        "/capability-profiles/safe-readonly/rules",
        json={"rules": [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}]},
        headers={"X-Wiki-User": "root"},
    )
    listed = client.get("/capability-profiles", headers={"X-Wiki-User": "root"})
    detail = client.get("/capability-profiles/safe-readonly", headers={"X-Wiki-User": "root"})
    catalog = client.get(
        "/capability-catalog",
        params={"profile_key": "safe-readonly"},
        headers={"X-Wiki-User": "root"},
    )

    assert created.status_code == 200
    assert created.json()["profile_key"] == "safe-readonly"
    assert rules.status_code == 200
    assert rules.json()["rules"][0]["source_key"] == "hive"
    assert listed.status_code == 200
    assert listed.json()[0]["deny_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["rules"][0]["effect"] == "deny"
    assert catalog.status_code == 200
    assert [item["source_key"] for item in catalog.json()["sources"]] == ["mysql"]


def test_tool_call_log_api_returns_full_payload(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    response = client.post(
        "/mcp/search",
        json={"query": "mysql"},
        headers={"X-Wiki-User": "root"},
    )
    log_id = response.json()["log_id"]

    listed = client.get("/tool-call-logs", headers={"X-Wiki-User": "root"})
    detail = client.get(f"/tool-call-logs/{log_id}", headers={"X-Wiki-User": "root"})

    assert listed.status_code == 200
    assert listed.json()[0]["log_id"] == log_id
    assert detail.status_code == 200
    assert '"query": "mysql"' in detail.json()["request_json"]
    assert detail.json()["response_json"]


def test_capability_catalog_source_and_tool_details(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Wiki-User": "root"},
    )
    store = SQLiteStore(wm_paths.db_path)
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run SQL",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=["sql"],
        examples=[],
    )
    store.create_tool_call_log(
        log_id="call_catalog_detail",
        actor="root",
        profile_key=None,
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={"sql": "select 1"},
        response={"rows": []},
        status="success",
    )

    source = client.get("/capability-catalog/sources/mcp_service/mysql", headers={"X-Wiki-User": "root"})
    tool = client.get(
        "/capability-catalog/sources/mcp_service/mysql/tools/query_sql",
        headers={"X-Wiki-User": "root"},
    )

    assert source.status_code == 200
    assert source.json()["source"]["service_key"] == "mysql"
    assert source.json()["tools"][0]["tool"] == "query_sql"
    assert tool.status_code == 200
    assert tool.json()["tool"]["tool"] == "query_sql"
    assert tool.json()["logs"][0]["log_id"] == "call_catalog_detail"
