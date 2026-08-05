"""MCP 与 OpenAPI 能力的兼容方法。"""

from __future__ import annotations

from typing import Any


class CapabilitiesFacadeMixin:
    def list_metamcp_tool_settings(self) -> list[dict[str, Any]]:
        return self.capabilities.list_metamcp_tool_settings()

    def upsert_metamcp_tool_status(self, tool_name: str, status: str, updated_by: str) -> dict[str, Any]:
        return self.capabilities.upsert_metamcp_tool_status(tool_name, status, updated_by)

    def create_mcp_service(
        self,
        *,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        return self.capabilities.create_mcp_service(service_key=service_key, name=name, endpoint_url=endpoint_url, headers=headers, description=description, tags=tags, created_by=created_by)

    def update_mcp_service(
        self,
        service_key: str,
        *,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        return self.capabilities.update_mcp_service(service_key=service_key, name=name, endpoint_url=endpoint_url, headers=headers, description=description, tags=tags)

    def get_mcp_service(self, service_key: str) -> dict[str, Any] | None:
        return self.capabilities.get_mcp_service(service_key=service_key)

    def list_mcp_services(self) -> list[dict[str, Any]]:
        return self.capabilities.list_mcp_services()

    def list_mcp_service_summaries(self) -> list[dict[str, Any]]:
        return self.capabilities.list_mcp_service_summaries()

    def update_mcp_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        return self.capabilities.update_mcp_service_status(service_key=service_key, status=status)

    def mark_mcp_service_sync(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        return self.capabilities.mark_mcp_service_sync(service_key=service_key, success=success, error=error)

    def upsert_mcp_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        tool_type: ToolType | str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.capabilities.upsert_mcp_tool(service_key=service_key, tool_name=tool_name, display_name=display_name, description=description, input_schema=input_schema, tool_type=tool_type, tags=tags, examples=examples)

    def update_mcp_tool_type(
        self,
        service_key: str,
        tool_name: str,
        tool_type: ToolType | str,
    ) -> dict[str, Any]:
        return self.capabilities.update_mcp_tool_type(service_key=service_key, tool_name=tool_name, tool_type=tool_type)

    def list_mcp_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        return self.capabilities.list_mcp_tools(service_key=service_key)

    def list_tool_summaries(
        self,
        *,
        source_type: str | None = None,
        service_key: str | None = None,
        tool_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self.capabilities.list_tool_summaries(
            source_type=source_type,
            service_key=service_key,
            tool_type=tool_type,
            query=query,
            limit=limit,
            offset=offset,
        )

    def get_mcp_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        return self.capabilities.get_mcp_tool(service_key=service_key, tool_name=tool_name)

    def deactivate_missing_mcp_tools(self, service_key: str, active_tool_names: set[str]) -> None:
        return self.capabilities.deactivate_missing_mcp_tools(service_key=service_key, active_tool_names=active_tool_names)

    def create_openapi_service(
        self,
        *,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any],
        headers: dict[str, Any],
        description: str,
        tags: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        return self.capabilities.create_openapi_service(
            service_key=service_key,
            name=name,
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
            auth_config=auth_config,
            headers=headers,
            description=description,
            tags=tags,
            created_by=created_by,
        )

    def update_openapi_service(
        self,
        service_key: str,
        *,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any],
        headers: dict[str, Any],
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        return self.capabilities.update_openapi_service(
            service_key=service_key,
            name=name,
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
            auth_config=auth_config,
            headers=headers,
            description=description,
            tags=tags,
        )

    def get_openapi_service(self, service_key: str) -> dict[str, Any] | None:
        return self.capabilities.get_openapi_service(service_key=service_key)

    def list_openapi_services(self) -> list[dict[str, Any]]:
        return self.capabilities.list_openapi_services()

    def list_openapi_service_summaries(self) -> list[dict[str, Any]]:
        return self.capabilities.list_openapi_service_summaries()

    def update_openapi_service_status(self, service_key: str, status: McpServiceStatus | str) -> None:
        return self.capabilities.update_openapi_service_status(service_key=service_key, status=status)

    def mark_openapi_service_import(self, service_key: str, *, success: bool, error: str | None = None) -> None:
        return self.capabilities.mark_openapi_service_import(service_key=service_key, success=success, error=error)

    def upsert_openapi_tool(
        self,
        *,
        service_key: str,
        tool_name: str,
        operation_id: str,
        method: str,
        path: str,
        display_name: str,
        description: str,
        input_schema: dict[str, Any],
        request_mapping: dict[str, Any],
        response_schema: dict[str, Any],
        tool_type: ToolType | str,
        tags: list[str],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.capabilities.upsert_openapi_tool(
            service_key=service_key,
            tool_name=tool_name,
            operation_id=operation_id,
            method=method,
            path=path,
            display_name=display_name,
            description=description,
            input_schema=input_schema,
            request_mapping=request_mapping,
            response_schema=response_schema,
            tool_type=tool_type,
            tags=tags,
            examples=examples,
        )

    def list_openapi_tools(self, service_key: str | None = None) -> list[dict[str, Any]]:
        return self.capabilities.list_openapi_tools(service_key=service_key)

    def get_openapi_tool(self, service_key: str, tool_name: str) -> dict[str, Any] | None:
        return self.capabilities.get_openapi_tool(service_key=service_key, tool_name=tool_name)

    def update_openapi_tool_type(self, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        return self.capabilities.update_openapi_tool_type(service_key=service_key, tool_name=tool_name, tool_type=tool_type)

    def delete_openapi_tool(self, service_key: str, tool_name: str) -> None:
        return self.capabilities.delete_openapi_tool(service_key=service_key, tool_name=tool_name)

    def delete_mcp_service(self, service_key: str) -> None:
        return self.capabilities.delete_mcp_service(service_key=service_key)

    def delete_openapi_service(self, service_key: str) -> None:
        return self.capabilities.delete_openapi_service(service_key=service_key)
