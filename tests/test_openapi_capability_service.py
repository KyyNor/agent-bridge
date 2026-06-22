from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from agent_bridge.capability_hub.models import CallLogStatus, SourceType, ToolType
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


class FakeOpenApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, dict]] = []

    def call_tool(self, service: dict, tool: dict, params: dict) -> dict:
        self.calls.append((service, tool, params))
        return {"status_code": 200, "body": {"id": params["petId"]}}


def test_openapi_service_search_and_execute(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    fake_client = FakeOpenApiClient()
    service = CapabilityService(store=store, admins={"root"}, openapi_client=fake_client)
    service.register_openapi_service(
        "root",
        "petstore",
        "Petstore",
        "https://api.example.test",
        "",
        "",
        {},
        {},
        "Pet API",
        ["pets"],
    )
    service.upsert_openapi_tool(
        "root",
        "petstore",
        {
            "tool_name": "get_pet",
            "operation_id": "getPet",
            "method": "GET",
            "path": "/pets/{petId}",
            "display_name": "Get Pet",
            "description": "Fetch pet",
            "input_schema": {"type": "object", "properties": {"petId": {"type": "string"}}, "required": ["petId"]},
            "request_mapping": {"path": {"petId": "petId"}, "query": {}, "headers": {}, "body": None},
            "response_schema": {},
            "tool_type": ToolType.detail.value,
            "tags": ["pets"],
            "examples": [{"petId": "p1"}],
        },
    )

    root = service.search("root", None, None)
    assert any(item["service"] == "petstore" and item["kind"] == "service" for item in root["items"])

    path = service.search("root", "petstore", None)
    assert path["items"][0]["tool"] == "get_pet"

    result = asyncio.run(service.execute("root", "petstore", "get_pet", {"petId": "p1"}))
    assert result["success"] is True
    assert result["result"]["body"] == {"id": "p1"}
    assert fake_client.calls[0][0]["service_key"] == "petstore"

    log = store.list_tool_call_logs(source_type=SourceType.openapi_service.value, source_key="petstore", limit=1)[0]
    assert log["status"] == CallLogStatus.success.value


def test_openapi_execute_does_not_block_event_loop(wm_paths: AgentBridgePaths) -> None:
    class SlowOpenApiClient(FakeOpenApiClient):
        def call_tool(self, service: dict, tool: dict, params: dict) -> dict:
            time.sleep(0.2)
            return super().call_tool(service, tool, params)

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, admins={"root"}, openapi_client=SlowOpenApiClient())
    service.register_openapi_service("root", "petstore", "Petstore", "https://api.example.test", "", "", {}, {}, "", [])
    service.upsert_openapi_tool(
        "root",
        "petstore",
        {
            "tool_name": "get_pet",
            "operation_id": "getPet",
            "method": "GET",
            "path": "/pets/{petId}",
            "display_name": "Get Pet",
            "description": "",
            "input_schema": {"type": "object", "properties": {"petId": {"type": "string"}}, "required": ["petId"]},
            "request_mapping": {"path": {"petId": "petId"}, "query": {}, "headers": {}, "body": None},
            "response_schema": {},
            "tool_type": ToolType.detail.value,
            "tags": [],
            "examples": [],
        },
    )

    async def run_concurrent_tasks() -> float:
        started = time.monotonic()
        execute_task = asyncio.create_task(service.execute("root", "petstore", "get_pet", {"petId": "p1"}))
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - started
        await execute_task
        return elapsed

    assert asyncio.run(run_concurrent_tasks()) < 0.1


def test_openapi_unconfigured_tool_is_blocked(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, admins={"root"}, openapi_client=FakeOpenApiClient())
    service.register_openapi_service("root", "crm", "CRM", "https://crm.example.test", "", "", {}, {}, "", [])
    service.upsert_openapi_tool(
        "root",
        "crm",
        {
            "tool_name": "create_ticket",
            "operation_id": "createTicket",
            "method": "POST",
            "path": "/tickets",
            "display_name": "Create Ticket",
            "description": "",
            "input_schema": {"type": "object"},
            "request_mapping": {"path": {}, "query": {}, "headers": {}, "body": "body"},
            "response_schema": {},
            "tool_type": ToolType.unconfigured.value,
            "tags": [],
            "examples": [],
        },
    )

    with pytest.raises(ValidationError):
        asyncio.run(service.execute("root", "crm", "create_ticket", {}))

    log = store.list_tool_call_logs(source_type=SourceType.openapi_service.value, source_key="crm", limit=1)[0]
    assert log["status"] == CallLogStatus.blocked.value


@respx.mock
def test_openapi_import_fetches_spec_url_when_content_is_blank(wm_paths: AgentBridgePaths) -> None:
    respx.get("https://api.example.test/openapi.yaml").mock(
        return_value=httpx.Response(
            200,
            text="openapi: 3.0.0\npaths:\n  /pets:\n    get:\n      operationId: listPets\n",
        )
    )
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, admins={"root"}, openapi_client=FakeOpenApiClient())
    service.register_openapi_service(
        "root",
        "petstore",
        "Petstore",
        "https://api.example.test",
        "https://api.example.test/openapi.yaml",
        "",
        {},
        {},
        "",
        [],
    )

    result = service.import_openapi_operations("root", "petstore")

    assert result["operations"][0]["tool_name"] == "list_pets"
