from __future__ import annotations

import json

from agent_bridge.capabilities.models import McpServiceStatus, ToolType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def test_mcp_service_crud_round_trip(wm_paths: AgentBridgePaths) -> None:
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


def test_update_mcp_service_replaces_editable_fields(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="http://localhost:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        description="SQL database MCP service",
        tags=["database", "report"],
        created_by="root",
    )

    updated = store.update_mcp_service(
        "mysql",
        name="MySQL Reporting",
        endpoint_url="http://localhost:9101/mcp",
        headers={"Authorization": "Bearer rotated", "X-Tenant": "finance"},
        description="Updated SQL database MCP service",
        tags=["database", "finance"],
    )

    assert updated["name"] == "MySQL Reporting"
    assert updated["endpoint_url"] == "http://localhost:9101/mcp"
    assert json.loads(updated["headers_json"]) == {"Authorization": "Bearer rotated", "X-Tenant": "finance"}
    assert updated["description"] == "Updated SQL database MCP service"
    assert json.loads(updated["tags_json"]) == ["database", "finance"]


def test_mark_mcp_service_sync_preserves_status_and_tracks_errors(wm_paths: AgentBridgePaths) -> None:
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
    store.update_mcp_service_status("mysql", McpServiceStatus.disabled)

    store.mark_mcp_service_sync("mysql", success=True)

    synced = store.get_mcp_service("mysql")
    assert synced is not None
    assert synced["status"] == McpServiceStatus.disabled.value
    assert synced["last_synced_at"] is not None
    assert synced["last_error"] is None

    store.mark_mcp_service_sync("mysql", success=False, error="sync failed")

    failed = store.get_mcp_service("mysql")
    assert failed is not None
    assert failed["status"] == McpServiceStatus.error.value
    assert failed["last_synced_at"] is not None
    assert failed["last_error"] == "sync failed"


def test_mcp_tool_upsert_replaces_synced_schema(wm_paths: AgentBridgePaths) -> None:
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
        tool_type=ToolType.detail,
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
    assert tools[0]["tool_type"] == ToolType.detail.value
    assert json.loads(tools[0]["input_schema_json"]) == {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }
    assert json.loads(tools[0]["tags_json"]) == ["database", "report"]
    assert json.loads(tools[0]["examples_json"]) == [{"query": "select * from users", "limit": 10}]
    assert store.get_mcp_tool("mysql", "query_sql")["id"] == tool["id"]


def test_update_mcp_tool_type_changes_only_admin_configured_type(wm_paths: AgentBridgePaths) -> None:
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
        input_schema={"type": "object"},
        tool_type=ToolType.unconfigured,
        tags=["database"],
        examples=[],
    )

    updated = store.update_mcp_tool_type("mysql", "query_sql", ToolType.search)

    assert updated["tool_type"] == ToolType.search.value
    assert updated["description"] == "Run a SQL query"


def test_mcp_tool_upsert_reactivates_inactive_tool(wm_paths: AgentBridgePaths) -> None:
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
        input_schema={"type": "object"},
        tool_type=ToolType.search,
        tags=["database"],
        examples=[],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE mcp_tools SET status = 'inactive' WHERE service_key = ? AND tool_name = ?",
            ("mysql", "query_sql"),
        )

    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run a SQL query again",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        tool_type=ToolType.search,
        tags=["database", "report"],
        examples=[],
    )

    tool = store.get_mcp_tool("mysql", "query_sql")
    assert tool is not None
    assert tool["status"] == "active"


def test_deactivate_missing_mcp_tools_marks_only_removed_tools_inactive(wm_paths: AgentBridgePaths) -> None:
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
    store.create_mcp_service(
        service_key="analytics",
        name="Analytics",
        endpoint_url="http://localhost:9002/mcp",
        headers={},
        description="Analytics MCP service",
        tags=["analytics"],
        created_by="root",
    )
    for service_key, tool_name in [
        ("mysql", "query_sql"),
        ("mysql", "describe_table"),
        ("analytics", "query_report"),
    ]:
        store.upsert_mcp_tool(
            service_key=service_key,
            tool_name=tool_name,
            display_name=tool_name,
            description="",
            input_schema={"type": "object"},
            tool_type=ToolType.search,
            tags=[],
            examples=[],
        )

    store.deactivate_missing_mcp_tools("mysql", {"query_sql"})

    assert store.get_mcp_tool("mysql", "query_sql")["status"] == "active"
    assert store.get_mcp_tool("mysql", "describe_table")["status"] == "inactive"
    assert store.get_mcp_tool("analytics", "query_report")["status"] == "active"
