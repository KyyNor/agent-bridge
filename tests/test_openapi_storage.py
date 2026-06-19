from __future__ import annotations

import json

from agent_bridge.capability_hub.models import McpServiceStatus, ToolType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def test_openapi_service_and_tool_round_trip(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    service = store.create_openapi_service(
        service_key="petstore",
        name="Petstore",
        base_url="https://api.example.test/v1",
        spec_url="https://api.example.test/openapi.json",
        spec_content='{"openapi":"3.0.0"}',
        auth_config={"type": "bearer", "token": "secret"},
        headers={"X-Tenant": "demo"},
        description="Pet API",
        tags=["pets"],
        created_by="root",
    )

    assert service["service_key"] == "petstore"
    assert service["base_url"] == "https://api.example.test/v1"
    assert json.loads(service["auth_config_json"]) == {"type": "bearer", "token": "secret"}
    assert json.loads(service["headers_json"]) == {"X-Tenant": "demo"}
    assert service["status"] == McpServiceStatus.enabled.value

    tool = store.upsert_openapi_tool(
        service_key="petstore",
        tool_name="get_pet",
        operation_id="getPet",
        method="GET",
        path="/pets/{petId}",
        display_name="Get Pet",
        description="Fetch a pet",
        input_schema={"type": "object", "properties": {"petId": {"type": "string"}}},
        request_mapping={"path": {"petId": "petId"}, "query": {}, "headers": {}, "body": None},
        response_schema={"type": "object"},
        tool_type=ToolType.detail,
        tags=["pets"],
        examples=[{"petId": "p1"}],
    )

    assert tool["tool_name"] == "get_pet"
    assert tool["operation_id"] == "getPet"
    assert tool["method"] == "GET"
    assert json.loads(tool["request_mapping_json"])["path"] == {"petId": "petId"}
    assert [item["tool_name"] for item in store.list_openapi_tools("petstore")] == ["get_pet"]

    updated = store.update_openapi_tool_type("petstore", "get_pet", ToolType.search)
    assert updated["tool_type"] == ToolType.search.value

    store.delete_openapi_tool("petstore", "get_pet")
    assert store.get_openapi_tool("petstore", "get_pet")["status"] == "inactive"


def test_openapi_service_update_preserves_status_and_import_marker(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_openapi_service(
        service_key="crm",
        name="CRM",
        base_url="https://crm.example.test",
        spec_url="",
        spec_content="",
        auth_config={},
        headers={},
        description="",
        tags=[],
        created_by="root",
    )

    store.update_openapi_service_status("crm", McpServiceStatus.disabled)
    updated = store.update_openapi_service(
        "crm",
        name="CRM API",
        base_url="https://crm.example.test/api",
        spec_url="https://crm.example.test/openapi.yaml",
        spec_content="openapi: 3.0.0",
        auth_config={"type": "api_key", "header": "X-API-Key", "value": "k"},
        headers={"Accept": "application/json"},
        description="Updated",
        tags=["crm"],
    )

    assert updated["status"] == McpServiceStatus.disabled.value
    assert updated["name"] == "CRM API"
    assert json.loads(updated["tags_json"]) == ["crm"]

    store.mark_openapi_service_import("crm", success=False, error="invalid spec")
    failed = store.get_openapi_service("crm")
    assert failed is not None
    assert failed["last_imported_at"] is not None
    assert failed["last_error"] == "invalid spec"
