"""MCP service and tool registry endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent_bridge.api.schemas import (
    RegisterMcpServiceRequest,
    UpdateMcpServiceStatusRequest,
    UpdateMcpToolTypeRequest,
)




def create_capability_routes(service, actor, call_safely, call_safely_async, ensure_capability_schema, catalog_sources):
    router = APIRouter()

    @router.post("/capabilities/mcp-services")
    def register_mcp_service(payload: RegisterMcpServiceRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.register_service(current_actor, payload.service_key, payload.name, payload.endpoint_url, payload.headers, payload.description, payload.tags))

    @router.get("/capabilities/mcp-services")
    def list_mcp_services(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_services(current_actor))

    @router.post("/capabilities/mcp-services/{service_key}/status")
    def update_mcp_service_status(service_key: str, payload: UpdateMcpServiceStatusRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.set_service_status(current_actor, service_key, payload.status))

    @router.post("/capabilities/mcp-services/{service_key}/sync")
    async def sync_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(lambda: service.capabilities.sync_tools(current_actor, service_key))

    @router.get("/capabilities/mcp-services/{service_key}/tools")
    def list_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_tools(current_actor, service_key))

    @router.put("/capabilities/mcp-services/{service_key}/tools/{tool_name}/type")
    def update_mcp_tool_type(service_key: str, tool_name: str, payload: UpdateMcpToolTypeRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.set_tool_type(current_actor, service_key, tool_name, payload.tool_type))

    @router.get("/capability-catalog")
    def capability_catalog(profile_key: str | None = None, query: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"sources": catalog_sources(current_actor, profile_key, query)})

    @router.get("/capability-catalog/sources/{source_type}/{source_key}")
    def capability_source_detail(source_type: str, source_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="source not found")
        service_payload = call_safely(lambda: service.capabilities.get_service(current_actor, source_key))
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        return {"source_type": source_type, "source": service_payload, "tools": tools}

    @router.get("/capability-catalog/sources/{source_type}/{source_key}/tools/{tool_name}")
    def capability_tool_detail(source_type: str, source_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="tool not found")
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        for tool in tools:
            if tool["tool"] == tool_name:
                logs = call_safely(lambda: service.governance.list_logs(actor=current_actor, source_type=source_type, source_key=source_key, tool_name=tool_name, limit=10))
                return {"source_type": source_type, "source_key": source_key, "tool": tool, "logs": logs}
        raise HTTPException(status_code=404, detail="tool not found")

    return router
