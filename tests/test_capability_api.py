from __future__ import annotations

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


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


def test_capability_admin_page_serves_html(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Wiki-User": "root"})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Capability Hub" in response.text
    assert "MCP Services" in response.text
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
