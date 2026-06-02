from __future__ import annotations

import json

from wiki_manager.capabilities import McpServiceStatus, ToolType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.storage import SQLiteStore


def test_mcp_service_crud_round_trip(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    store.create_mcp_service(
        service_key="analytics",
        name="Analytics",
        endpoint_url="http://localhost:9000/mcp",
        headers={},
        description="Analytics service",
        tags=["report"],
        created_by="root",
    )
    service = store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        description="SQL database MCP service",
        tags=["database", "report"],
        created_by="root",
    )

    assert service["service_key"] == "mysql"
    assert service["endpoint_url"] == "http://localhost:9001/mcp"
    assert json.loads(service["headers_json"]) == {"Authorization": "Bearer secret"}
    assert service["description"] == "SQL database MCP service"
    assert json.loads(service["tags_json"]) == ["database", "report"]
    assert service["status"] == McpServiceStatus.enabled.value

    assert [item["service_key"] for item in store.list_mcp_services()] == ["analytics", "mysql"]
    assert store.get_mcp_service("mysql")["name"] == "MySQL"

    store.update_mcp_service_status("mysql", McpServiceStatus.disabled)

    disabled = store.get_mcp_service("mysql")
    assert disabled is not None
    assert disabled["status"] == McpServiceStatus.disabled.value


def test_mcp_tool_upsert_replaces_synced_schema(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={},
        description="SQL database MCP service",
        tags=["database"],
        created_by="root",
    )

    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run a SQL query",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
        tool_type=ToolType.search,
        tags=["database"],
        examples=[{"sql": "select 1"}],
    )
    tool = store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run a SQL query with limit",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
        tool_type=ToolType.search,
        tags=["database", "report"],
        examples=[{"query": "select * from users", "limit": 10}],
    )

    tools = store.list_mcp_tools("mysql")
    assert len(tools) == 1
    assert tools[0]["id"] == tool["id"]
    assert tools[0]["tool_name"] == "query_sql"
    assert tools[0]["description"] == "Run a SQL query with limit"
    assert json.loads(tools[0]["input_schema_json"]) == {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }
    assert json.loads(tools[0]["tags_json"]) == ["database", "report"]
    assert json.loads(tools[0]["examples_json"]) == [{"query": "select * from users", "limit": 10}]
    assert store.get_mcp_tool("mysql", "query_sql")["id"] == tool["id"]
