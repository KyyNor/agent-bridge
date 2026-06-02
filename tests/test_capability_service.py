from __future__ import annotations

import asyncio

import pytest

from wiki_manager.capabilities import McpServiceStatus, ToolType
from wiki_manager.capability_service import CapabilityService
from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import AccessDenied, NotFound, ValidationError
from wiki_manager.storage import SQLiteStore


class FakeMcpClient:
    def __init__(self) -> None:
        self.list_tools_calls: list[dict[str, object]] = []
        self.call_tool_calls: list[dict[str, object]] = []

    async def list_tools(self, endpoint_url: str, headers: dict[str, str]) -> list[dict[str, object]]:
        self.list_tools_calls.append({"endpoint_url": endpoint_url, "headers": headers})
        return [
            {
                "name": "search_docs",
                "description": "Search documents",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "annotations": {"readOnlyHint": True},
                "output_schema": {"type": "object"},
            },
            {
                "name": "delete_doc",
                "description": "Delete a document",
                "input_schema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                "annotations": {"destructiveHint": True},
                "output_schema": {"type": "object"},
            },
        ]

    async def call_tool(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        self.call_tool_calls.append(
            {
                "endpoint_url": endpoint_url,
                "headers": headers,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )
        return {"is_error": False, "structured": {"rows": [{"id": 1}]}, "content": []}


def _service(wm_paths: WikiManagerPaths) -> tuple[CapabilityService, FakeMcpClient]:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    mcp_client = FakeMcpClient()
    return CapabilityService(store=store, mcp_client=mcp_client, admins={"root"}), mcp_client


def test_register_service_creates_updates_and_lists_parsed_payload(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)

    created = service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API",
        endpoint_url="https://example.test/mcp",
        headers={"Authorization": "Bearer first"},
        description="Document capabilities",
        tags=["docs"],
    )
    updated = service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API v2",
        endpoint_url="https://example.test/v2/mcp",
        headers={"Authorization": "Bearer second"},
        description="Updated document capabilities",
        tags=["docs", "search"],
    )

    assert created["service_key"] == "docs-api"
    assert updated["name"] == "Docs API v2"
    assert service.list_services(actor="alice")[0]["headers"] == {"Authorization": "Bearer second"}
    assert service.list_services(actor="alice")[0]["tags"] == ["docs", "search"]
    assert "headers_json" not in service.list_services(actor="alice")[0]
    assert "tags_json" not in service.list_services(actor="alice")[0]


def test_register_service_requires_admin_and_valid_service_key(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)

    with pytest.raises(AccessDenied):
        service.register_service(
            actor="alice",
            service_key="docs-api",
            name="Docs API",
            endpoint_url="https://example.test/mcp",
            headers={},
            description="Document capabilities",
            tags=[],
        )

    with pytest.raises(ValidationError, match="service_key"):
        service.register_service(
            actor="root",
            service_key="docs/api",
            name="Docs API",
            endpoint_url="https://example.test/mcp",
            headers={},
            description="Document capabilities",
            tags=[],
        )


def test_set_service_status_requires_admin_validates_status_and_missing_service(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)

    with pytest.raises(AccessDenied):
        service.set_service_status("alice", "docs-api", McpServiceStatus.disabled.value)
    with pytest.raises(ValidationError, match="invalid service status"):
        service.set_service_status("root", "docs-api", "paused")
    with pytest.raises(NotFound, match="service not found"):
        service.set_service_status("root", "docs-api", McpServiceStatus.disabled.value)


def test_sync_tools_stores_tools_and_passes_headers(wm_paths: WikiManagerPaths) -> None:
    service, client = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API",
        endpoint_url="https://example.test/mcp",
        headers={"Authorization": "Bearer token", "X-Tenant": "docs"},
        description="Document capabilities",
        tags=["docs"],
    )

    result = asyncio.run(service.sync_tools("root", "docs-api"))

    assert result == {"service_key": "docs-api", "tool_count": 2}
    assert client.list_tools_calls == [
        {
            "endpoint_url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer token", "X-Tenant": "docs"},
        }
    ]
    tools = service.list_tools("alice", "docs-api")
    assert [tool["tool"] for tool in tools] == ["delete_doc", "search_docs"]
    assert {tool["tool"]: tool["tool_type"] for tool in tools} == {
        "delete_doc": ToolType.action.value,
        "search_docs": ToolType.search.value,
    }


def test_sync_tools_marks_failure(wm_paths: WikiManagerPaths) -> None:
    class FailingMcpClient(FakeMcpClient):
        async def list_tools(self, endpoint_url: str, headers: dict[str, str]) -> list[dict[str, object]]:
            raise RuntimeError("mcp unavailable")

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FailingMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])

    with pytest.raises(RuntimeError, match="mcp unavailable"):
        asyncio.run(service.sync_tools("root", "docs-api"))

    listed = service.list_services("root")
    assert listed[0]["status"] == McpServiceStatus.error.value
    assert listed[0]["last_error"] == "mcp unavailable"


def test_search_root_and_service_path_filters_by_query(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    service.register_service("root", "admin-api", "Admin API", "https://example.test/admin", {}, "Admin capabilities", ["admin"])
    service.set_service_status("root", "admin-api", McpServiceStatus.disabled.value)
    asyncio.run(service.sync_tools("root", "docs-api"))

    root_items = service.search(actor="alice", path="/", query=None)
    tool_items = service.search(actor="alice", path="docs-api", query="Search")

    assert root_items == [
        {
            "kind": "service",
            "service": "docs-api",
            "name": "Docs API",
            "description": "Document capabilities",
            "tags": ["docs"],
            "tool_count": 2,
            "status": McpServiceStatus.enabled.value,
        }
    ]
    assert len(tool_items) == 1
    assert tool_items[0]["kind"] == "tool"
    assert tool_items[0]["service"] == "docs-api"
    assert tool_items[0]["tool"] == "search_docs"
    assert tool_items[0]["execute_example"] == {"query": "<string>"}
    assert tool_items[0]["executable"] is True


def test_execute_rejects_annotation_destructive_action_tool(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    asyncio.run(service.sync_tools("root", "docs-api"))

    with pytest.raises(ValidationError, match="action tools are not executable in phase 1"):
        asyncio.run(service.execute("alice", "docs-api", "delete_doc", {"doc_id": "1"}))


def test_execute_calls_readonly_tool(wm_paths: WikiManagerPaths) -> None:
    service, client = _service(wm_paths)
    service.register_service(
        "root",
        "docs-api",
        "Docs API",
        "https://example.test/mcp",
        {"Authorization": "Bearer token"},
        "Document capabilities",
        ["docs"],
    )
    asyncio.run(service.sync_tools("root", "docs-api"))

    result = asyncio.run(service.execute("alice", "docs-api", "search_docs", {"query": "hello"}))

    assert result == {
        "service": "docs-api",
        "tool": "search_docs",
        "success": True,
        "result": {"is_error": False, "structured": {"rows": [{"id": 1}]}, "content": []},
    }
    assert client.call_tool_calls == [
        {
            "endpoint_url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer token"},
            "tool_name": "search_docs",
            "arguments": {"query": "hello"},
        }
    ]


def test_execute_requires_existing_service_and_tool(wm_paths: WikiManagerPaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])

    with pytest.raises(NotFound, match="service not found"):
        asyncio.run(service.execute("alice", "missing", "search_docs", {}))
    with pytest.raises(NotFound, match="tool not found"):
        asyncio.run(service.execute("alice", "docs-api", "missing", {}))
