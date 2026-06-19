from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.core.config import AgentBridgePaths


def test_openapi_admin_flow_import_preview_save_and_catalog(wm_paths: AgentBridgePaths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}

        created = client.post(
            "/capabilities/openapi-services",
            headers=headers,
            json={
                "service_key": "petstore",
                "name": "Petstore",
                "base_url": "https://api.example.test",
                "spec_content": """
                openapi: 3.0.0
                paths:
                  /pets:
                    get:
                      operationId: listPets
                      summary: List pets
                """,
                "tags": ["pets"],
            },
        )
        assert created.status_code == 200
        assert created.json()["service_key"] == "petstore"

        preview = client.post(
            "/capabilities/openapi-services/petstore/import",
            headers=headers,
            json={},
        )
        assert preview.status_code == 200
        assert preview.json()["operations"][0]["tool_name"] == "list_pets"

        saved = client.put(
            "/capabilities/openapi-services/petstore/tools/list_pets",
            headers=headers,
            json={**preview.json()["operations"][0], "description": "Admin edited description"},
        )
        assert saved.status_code == 200
        assert saved.json()["description"] == "Admin edited description"

        tools = client.get("/capabilities/openapi-services/petstore/tools", headers=headers)
        assert tools.status_code == 200
        assert [item["tool"] for item in tools.json()] == ["list_pets"]

        catalog = client.get("/capability-catalog", headers=headers)
        assert catalog.status_code == 200
        assert any(item["source_type"] == "openapi_service" and item["source_key"] == "petstore" for item in catalog.json()["sources"])

        detail = client.get("/capability-catalog/sources/openapi_service/petstore", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["tools"][0]["tool"] == "list_pets"


def test_openapi_import_preview_does_not_persist_operations(wm_paths: AgentBridgePaths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        client.post(
            "/capabilities/openapi-services",
            headers=headers,
            json={
                "service_key": "crm",
                "name": "CRM",
                "base_url": "https://crm.example.test",
                "spec_content": "openapi: 3.0.0\npaths:\n  /accounts:\n    get:\n      operationId: listAccounts\n",
            },
        )

        response = client.post("/capabilities/openapi-services/crm/import", headers=headers, json={})

        assert response.status_code == 200
        assert response.json()["operations"][0]["tool_name"] == "list_accounts"
        assert client.get("/capabilities/openapi-services/crm/tools", headers=headers).json() == []
