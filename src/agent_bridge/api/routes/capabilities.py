"""MCP service and tool registry endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_bridge.api.runtime_context import profile_from_headers, workflow_context_from_headers
from agent_bridge.api.schemas import (
    ExecuteCapabilityRequest,
    ImportOpenApiOperationsRequest,
    RegisterOpenApiServiceRequest,
    RegisterMcpServiceRequest,
    UpdateMcpServiceStatusRequest,
    UpdateMcpToolTypeRequest,
    UpsertOpenApiToolRequest,
)




def create_capability_routes(service, actor, catalog_sources):
    router = APIRouter()

    @router.post("/capabilities/mcp-services")
    def register_mcp_service(payload: RegisterMcpServiceRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.register_service(current_actor, payload.service_key, payload.name, payload.endpoint_url, payload.headers, payload.description, payload.tags)

    @router.get("/capabilities/mcp-services")
    def list_mcp_services(summary: bool = False, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return (
            service.capabilities.list_service_summaries(current_actor)
            if summary
            else service.capabilities.list_services(current_actor)
        )

    @router.get("/capabilities/mcp-services/{service_key}")
    def get_mcp_service(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.get_service(current_actor, service_key)

    @router.post("/capabilities/mcp-services/{service_key}/status")
    def update_mcp_service_status(service_key: str, payload: UpdateMcpServiceStatusRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.set_service_status(current_actor, service_key, payload.status)

    @router.post("/capabilities/mcp-services/{service_key}/sync")
    async def sync_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return await service.capabilities.sync_tools(current_actor, service_key)

    @router.get("/capabilities/mcp-services/{service_key}/tools")
    def list_mcp_service_tools(service_key: str, summary: bool = False, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        if summary:
            result = service.capabilities.list_tool_summaries(
                current_actor, source_type="mcp_service", service_key=service_key, limit=200
            )
            return result["items"]
        return service.capabilities.list_tools(current_actor, service_key)

    @router.get("/capabilities/mcp-services/{service_key}/tools/{tool_name}")
    def get_mcp_service_tool(service_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.get_tool(current_actor, service_key, tool_name)

    @router.put("/capabilities/mcp-services/{service_key}/tools/{tool_name}/type")
    def update_mcp_tool_type(service_key: str, tool_name: str, payload: UpdateMcpToolTypeRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.set_tool_type(current_actor, service_key, tool_name, payload.tool_type)

    @router.post("/capabilities/mcp-services/{service_key}/delete")
    def delete_mcp_service(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        service.capabilities.delete_mcp_service(current_actor, service_key)
        return {"ok": True}

    @router.post("/capabilities/openapi-services")
    def register_openapi_service(payload: RegisterOpenApiServiceRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.register_openapi_service(
            current_actor,
            payload.service_key,
            payload.name,
            payload.base_url,
            payload.spec_url,
            payload.spec_content,
            payload.auth_config,
            payload.headers,
            payload.description,
            payload.tags,
        )

    @router.get("/capabilities/openapi-services")
    def list_openapi_services(summary: bool = False, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return (
            service.capabilities.list_openapi_service_summaries(current_actor)
            if summary
            else service.capabilities.list_openapi_services(current_actor)
        )

    @router.get("/capabilities/openapi-services/{service_key}")
    def get_openapi_service(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.get_openapi_service(current_actor, service_key)

    @router.post("/capabilities/openapi-services/{service_key}/status")
    def update_openapi_service_status(service_key: str, payload: UpdateMcpServiceStatusRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.set_openapi_service_status(current_actor, service_key, payload.status)

    @router.post("/capabilities/openapi-services/{service_key}/import")
    def import_openapi_operations(service_key: str, payload: ImportOpenApiOperationsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.import_openapi_operations(current_actor, service_key, spec_content=payload.spec_content)

    @router.get("/capabilities/openapi-services/{service_key}/tools")
    def list_openapi_service_tools(service_key: str, summary: bool = False, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        if summary:
            result = service.capabilities.list_tool_summaries(
                current_actor, source_type="openapi_service", service_key=service_key, limit=200
            )
            return result["items"]
        return service.capabilities.list_openapi_tools(current_actor, service_key)

    @router.get("/capabilities/openapi-services/{service_key}/tools/{tool_name}")
    def get_openapi_service_tool(service_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.get_openapi_tool(current_actor, service_key, tool_name)

    @router.put("/capabilities/openapi-services/{service_key}/tools/{tool_name}")
    def upsert_openapi_tool(service_key: str, tool_name: str, payload: UpsertOpenApiToolRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        data = payload.model_dump()
        data["tool_name"] = tool_name
        return service.capabilities.upsert_openapi_tool(current_actor, service_key, data)

    @router.put("/capabilities/openapi-services/{service_key}/tools/{tool_name}/type")
    def update_openapi_tool_type(service_key: str, tool_name: str, payload: UpdateMcpToolTypeRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.capabilities.set_openapi_tool_type(current_actor, service_key, tool_name, payload.tool_type)

    @router.delete("/capabilities/openapi-services/{service_key}/tools/{tool_name}")
    def delete_openapi_tool(service_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        service.capabilities.delete_openapi_tool(current_actor, service_key, tool_name)
        return {"ok": True}

    @router.post("/capabilities/openapi-services/{service_key}/delete")
    def delete_openapi_service(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        service.capabilities.delete_openapi_service(current_actor, service_key)
        return {"ok": True}

    @router.post("/capabilities/execute")
    async def execute_capability(
        payload: ExecuteCapabilityRequest,
        request: Request,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        profile_key = profile_from_headers(request)
        if profile_key is None:
            profile_key = payload.profile_key
        return await service.capabilities.execute(
            actor=current_actor,
            service=payload.service,
            tool_name=payload.tool_name,
            params=payload.params,
            profile_key=profile_key,
            workflow_context=workflow_context_from_headers(request),
        )

    @router.get("/capability-tools")
    def list_capability_tools(
        source_type: str | None = None,
        service_key: str | None = None,
        tool_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.capabilities.list_tool_summaries(
            current_actor,
            source_type=source_type,
            service_key=service_key,
            tool_type=tool_type,
            query=query,
            limit=limit,
            offset=offset,
        )

    @router.get("/capability-catalog")
    def capability_catalog(profile_key: str | None = None, query: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return {"sources": catalog_sources(current_actor, profile_key, query)}

    @router.get("/capability-catalog/sources/{source_type}/{source_key}")
    def capability_source_detail(source_type: str, source_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        if source_type == "openapi_service":
            service_payload = service.capabilities.get_openapi_service(current_actor, source_key)
            tools = service.capabilities.list_openapi_tools(current_actor, source_key)
            return {"source_type": source_type, "source": service_payload, "tools": tools}
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="source not found")
        service_payload = service.capabilities.get_service(current_actor, source_key)
        tools = service.capabilities.list_tools(current_actor, source_key)
        return {"source_type": source_type, "source": service_payload, "tools": tools}

    @router.get("/capability-catalog/sources/{source_type}/{source_key}/tools/{tool_name}")
    def capability_tool_detail(source_type: str, source_key: str, tool_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        if source_type == "openapi_service":
            tool = service.capabilities.get_openapi_tool(current_actor, source_key, tool_name)
            logs = service.governance.list_logs(actor=current_actor, source_type=source_type, source_key=source_key, tool_name=tool_name, limit=10)
            return {"source_type": source_type, "source_key": source_key, "tool": tool, "logs": logs}
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="tool not found")
        tool = service.capabilities.get_tool(current_actor, source_key, tool_name)
        logs = service.governance.list_logs(actor=current_actor, source_type=source_type, source_key=source_key, tool_name=tool_name, limit=10)
        return {"source_type": source_type, "source_key": source_key, "tool": tool, "logs": logs}

    return router
