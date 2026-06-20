"""Capability service composed from MCP registry storage and HTTP client."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from agent_bridge.capability_hub.sources.builtin.base import (
    BuiltinCapabilityProvider,
    BuiltinResourceRef,
    BuiltinTool,
    mark_builtin_failure,
)
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage, McpServiceStatus, SourceType, ToolType
from agent_bridge.capability_hub.governance import CapabilityGovernanceService, monotonic_ms
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.capability_hub.sources.mcp.http_client import McpHttpClient
from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient
from agent_bridge.capability_hub.sources.openapi.parser import parse_openapi_operations
from agent_bridge.storage.sqlite import SQLiteStore


SERVICE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
READONLY_TOOL_TYPES = {ToolType.overview.value, ToolType.search.value, ToolType.detail.value}


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).lower()


def _schema_example(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {name: _example_value(definition) for name, definition in properties.items()}


def _example_value(definition: Any) -> Any:
    if not isinstance(definition, dict):
        return None
    if "default" in definition:
        return definition["default"]
    if "example" in definition:
        return definition["example"]

    value_type = definition.get("type")
    if value_type == "string":
        return "<string>"
    if value_type in {"integer", "number"}:
        return 0
    if value_type == "boolean":
        return False
    if value_type == "array":
        return []
    if value_type == "object":
        return {}
    return None


def _attach_log_id(exc: Exception, log_id: str) -> None:
    message = f"{getattr(exc, 'message', str(exc))} (log_id: {log_id})"
    if hasattr(exc, "message"):
        exc.message = message
    exc.args = (message,)


def _mark_call_log_status(exc: Exception, status: str) -> Exception:
    setattr(exc, "_tool_call_log_status", status)
    return exc


def _call_log_status(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_log_status", CallLogStatus.error.value))


def _mark_call_log_failure(
    exc: Exception,
    *,
    stage: str,
    owner: str,
    error_type: str,
) -> Exception:
    setattr(exc, "_tool_call_failure_stage", stage)
    setattr(exc, "_tool_call_failure_owner", owner)
    setattr(exc, "_tool_call_error_type", error_type)
    return exc


def _failure_stage(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_failure_stage", FailureStage.internal.value))


def _failure_owner(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_failure_owner", FailureOwner.platform.value))


def _error_type(exc: Exception) -> str:
    return str(getattr(exc, "_tool_call_error_type", "internal_error"))


class CapabilityService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        mcp_client: McpHttpClient | None = None,
        openapi_client: OpenApiHttpClient | None = None,
        governance: CapabilityGovernanceService | None = None,
    ) -> None:
        self.store = store
        self.admins = admins
        self.mcp_client = mcp_client or McpHttpClient()
        self.openapi_client = openapi_client or OpenApiHttpClient()
        self.governance = governance or CapabilityGovernanceService(store=store, admins=admins)
        self.builtin_providers: dict[str, BuiltinCapabilityProvider] = {}

    def register_builtin_provider(self, provider: BuiltinCapabilityProvider) -> None:
        self.builtin_providers[provider.source_key] = provider

    def register_service(
        self,
        actor: str,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._validate_service_key(service_key)
        if service_key in self.builtin_providers:
            raise ValidationError("service_key is reserved for built-in capability")
        if self.store.get_openapi_service(service_key) is not None:
            raise ValidationError("service_key is already used by an OpenAPI service")
        existing = self.store.get_mcp_service(service_key)
        if headers is None:
            headers = _json_loads(existing.get("headers_json"), {}) if existing is not None else {}
        if existing is None:
            service = self.store.create_mcp_service(
                service_key=service_key,
                name=name,
                endpoint_url=endpoint_url,
                headers=headers,
                description=description,
                tags=tags,
                created_by=actor,
            )
        else:
            service = self.store.update_mcp_service(
                service_key,
                name=name,
                endpoint_url=endpoint_url,
                headers=headers,
                description=description,
                tags=tags,
            )
        return self._service_payload(service)

    def register_openapi_service(
        self,
        actor: str,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any] | None,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._validate_service_key(service_key)
        if service_key in self.builtin_providers or self.store.get_mcp_service(service_key) is not None:
            raise ValidationError("service_key is already used by another capability source")
        if not base_url.strip():
            raise ValidationError("base_url is required")
        existing = self.store.get_openapi_service(service_key)
        if auth_config is None:
            auth_config = _json_loads(existing.get("auth_config_json"), {}) if existing is not None else {}
        if headers is None:
            headers = _json_loads(existing.get("headers_json"), {}) if existing is not None else {}
        if existing is None:
            service = self.store.create_openapi_service(
                service_key=service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config,
                headers=headers,
                description=description,
                tags=tags,
                created_by=actor,
            )
        else:
            service = self.store.update_openapi_service(
                service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config,
                headers=headers,
                description=description,
                tags=tags,
            )
        return self._openapi_service_payload(service)

    def list_openapi_services(self, actor: str) -> list[dict[str, Any]]:
        return [
            self._openapi_service_payload(service, redact_secrets=True)
            for service in self.store.list_openapi_services()
        ]

    def get_openapi_service(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.store.get_openapi_service(service_key)
        if service is None:
            raise NotFound("service not found")
        return self._openapi_service_payload(service, redact_secrets=True)

    def set_openapi_service_status(self, actor: str, service_key: str, status: McpServiceStatus | str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        try:
            next_status = McpServiceStatus(status)
        except ValueError as exc:
            raise ValidationError("invalid service status") from exc
        if self.store.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        self.store.update_openapi_service_status(service_key, next_status)
        updated = self.store.get_openapi_service(service_key)
        if updated is None:
            raise NotFound("service not found")
        return self._openapi_service_payload(updated)

    def import_openapi_operations(
        self,
        actor: str,
        service_key: str,
        *,
        spec_content: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        service = self.store.get_openapi_service(service_key)
        if service is None:
            raise NotFound("service not found")
        try:
            content = spec_content if spec_content is not None else str(service.get("spec_content") or "")
            if not content.strip():
                spec_url = str(service.get("spec_url") or "").strip()
                if not spec_url:
                    raise ValidationError("OpenAPI spec content or spec_url is required")
                response = httpx.get(spec_url, headers=_json_loads(service.get("headers_json"), {}), timeout=30.0)
                response.raise_for_status()
                content = response.text
            operations = parse_openapi_operations(content)
            self.store.mark_openapi_service_import(service_key, success=True)
            return {"service_key": service_key, "operations": operations}
        except Exception as exc:
            self.store.mark_openapi_service_import(service_key, success=False, error=str(exc))
            raise

    def upsert_openapi_tool(self, actor: str, service_key: str, tool: dict[str, Any]) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        tool_type = self._validate_tool_type(tool.get("tool_type"))
        saved = self.store.upsert_openapi_tool(
            service_key=service_key,
            tool_name=str(tool.get("tool_name") or "").strip(),
            operation_id=str(tool.get("operation_id") or ""),
            method=str(tool.get("method") or "GET").upper(),
            path=str(tool.get("path") or ""),
            display_name=str(tool.get("display_name") or tool.get("tool_name") or ""),
            description=str(tool.get("description") or ""),
            input_schema=tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
            request_mapping=tool.get("request_mapping") if isinstance(tool.get("request_mapping"), dict) else {},
            response_schema=tool.get("response_schema") if isinstance(tool.get("response_schema"), dict) else {},
            tool_type=tool_type,
            tags=[str(tag) for tag in tool.get("tags", [])] if isinstance(tool.get("tags"), list) else [],
            examples=tool.get("examples") if isinstance(tool.get("examples"), list) else [],
        )
        return self._openapi_tool_payload(saved)

    def list_openapi_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        self._require_enabled_openapi_service(service_key)
        return [self._openapi_tool_payload(tool) for tool in self._active_openapi_tools(service_key)]

    def set_openapi_tool_type(self, actor: str, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        next_tool_type = self._validate_tool_type(tool_type)
        if self.store.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        tool = self.store.get_openapi_tool(service_key, tool_name)
        if tool is None:
            raise NotFound("tool not found")
        return self._openapi_tool_payload(self.store.update_openapi_tool_type(service_key, tool_name, next_tool_type))

    def delete_openapi_tool(self, actor: str, service_key: str, tool_name: str) -> None:
        require_admin_user(actor, self.admins)
        if self.store.get_openapi_tool(service_key, tool_name) is None:
            raise NotFound("tool not found")
        self.store.delete_openapi_tool(service_key, tool_name)

    def list_services(self, actor: str) -> list[dict[str, Any]]:
        return [self._service_payload(service, redact_headers=True) for service in self.store.list_mcp_services()]

    def get_service(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("service not found")
        return self._service_payload(service, redact_headers=True)

    def set_service_status(self, actor: str, service_key: str, status: McpServiceStatus | str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        try:
            next_status = McpServiceStatus(status)
        except ValueError as exc:
            raise ValidationError("invalid service status") from exc
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("service not found")
        self.store.update_mcp_service_status(service_key, next_status)
        updated = self.store.get_mcp_service(service_key)
        if updated is None:
            raise NotFound("service not found")
        return self._service_payload(updated)

    def set_tool_type(self, actor: str, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        try:
            next_tool_type = ToolType(tool_type)
        except ValueError as exc:
            raise ValidationError("invalid tool type") from exc
        if self.store.get_mcp_service(service_key) is None:
            raise NotFound("service not found")
        tool = self.store.get_mcp_tool(service_key, tool_name)
        if tool is None:
            raise NotFound("tool not found")
        return self._tool_payload(self.store.update_mcp_tool_type(service_key, tool_name, next_tool_type))

    async def sync_tools(self, actor: str, service_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("service not found")
        headers = _json_loads(service.get("headers_json"), {})
        try:
            tools = await self.mcp_client.list_tools(service["endpoint_url"], headers)
            active_tool_names: set[str] = set()
            for tool in tools:
                normalized = self._normalize_synced_tool(tool)
                active_tool_names.add(normalized["tool_name"])
                self.store.upsert_mcp_tool(
                    service_key=service_key,
                    tool_name=normalized["tool_name"],
                    display_name=normalized["display_name"],
                    description=normalized["description"],
                    input_schema=normalized["input_schema"],
                    tool_type=normalized["tool_type"],
                    tags=normalized["tags"],
                    examples=normalized["examples"],
                )
            self.store.deactivate_missing_mcp_tools(service_key, active_tool_names)
            self.store.mark_mcp_service_sync(service_key, success=True)
            return {"service_key": service_key, "tool_count": len(tools)}
        except Exception as exc:
            self.store.mark_mcp_service_sync(service_key, success=False, error=str(exc))
            raise ValidationError(f"MCP tool sync failed: {exc}") from exc

    def list_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        self._require_enabled_service(service_key)
        return [self._tool_payload(tool) for tool in self._active_tools(service_key)]

    def pinned_tool_specs(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        if profile_key is None:
            return []
        preview = self.governance.profile_pin_preview(actor, profile_key)
        specs = []
        for item in preview.get("tools", []):
            input_schema = item.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = {}
            specs.append(
                {
                    **item,
                    "input_schema": input_schema,
                    "description": (
                        f"Direct pinned Agent Bridge tool for service {item['service_name']} "
                        f"({item['service_key']}), tool {item['tool_name']}, level {item['tool_type']}, "
                        f"source {item['source']}. Use search(path='{item['service_key']}') "
                        "to inspect the full service directory."
                    ),
                }
            )
        return specs

    def search(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int = 20,
        profile_key: str | None = None,
    ) -> dict[str, Any]:
        started = monotonic_ms()
        source_key = (path or "").strip("/") or None
        request = {"path": path, "query": query, "limit": limit, "profile_key": profile_key}
        try:
            result = self._search_without_log(actor, path, query, limit, profile_key)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=source_key,
                tool_name="search",
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=source_key,
                tool_name="search",
                request=request,
                response={"error": str(exc)},
                status=CallLogStatus.error.value,
                error_message=str(exc),
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
                duration_ms=monotonic_ms() - started,
            )
            _attach_log_id(exc, log["log_id"])
            raise

    def _search_without_log(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int,
        profile_key: str | None,
    ) -> dict[str, Any]:
        normalized_path = (path or "").strip("/")
        if normalized_path == "":
            items = self._root_search_items(actor=actor, profile_key=profile_key)
            response_path = "/"
        else:
            if normalized_path in self.builtin_providers:
                provider = self.builtin_providers[normalized_path]
                items = [
                    self._builtin_tool_search_item(provider.source_key, tool)
                    for tool in provider.list_tools(actor, profile_key)
                ]
                response_path = normalized_path
            elif self.store.get_openapi_service(normalized_path) is not None:
                if not self.governance.is_source_allowed(
                    actor,
                    profile_key,
                    SourceType.openapi_service.value,
                    normalized_path,
                ):
                    return {"path": normalized_path, "items": []}
                self._require_enabled_openapi_service(normalized_path)
                items = [self._openapi_tool_search_item(tool) for tool in self._active_openapi_tools(normalized_path)]
                response_path = normalized_path
            else:
                if not self.governance.is_source_allowed(
                    actor,
                    profile_key,
                    SourceType.mcp_service.value,
                    normalized_path,
                ):
                    return {"path": normalized_path, "items": []}
                self._require_enabled_service(normalized_path)
                items = [self._tool_search_item(tool) for tool in self._active_tools(normalized_path)]
                response_path = normalized_path

        if query:
            needle = query.lower()
            items = [item for item in items if needle in _json_text(item)]
        return {"path": response_path, "items": items[:limit]}

    async def execute(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None = None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = monotonic_ms()
        request = {"service": service, "tool_name": tool_name, "params": params, "profile_key": profile_key}
        is_openapi_service = self.store.get_openapi_service(service) is not None
        source_type = (
            SourceType.builtin.value
            if service in self.builtin_providers
            else SourceType.openapi_service.value
            if is_openapi_service
            else SourceType.mcp_service.value
        )
        resource_type = None
        resource_key = None
        try:
            if service in self.builtin_providers:
                resource = self.builtin_providers[service].resource_from_arguments(tool_name, params)
                resource_type, resource_key = self._builtin_resource_tuple(resource)
                result = await self._execute_builtin(actor, service, tool_name, params, profile_key, workflow_context)
            elif is_openapi_service:
                if not self.governance.is_source_allowed(actor, profile_key, SourceType.openapi_service.value, service):
                    raise _mark_call_log_failure(
                        _mark_call_log_status(
                            ValidationError("source is blocked by profile policy"),
                            CallLogStatus.blocked.value,
                        ),
                        stage=FailureStage.profile_policy.value,
                        owner=FailureOwner.policy.value,
                        error_type="profile_policy_blocked",
                    )
                result = self._execute_openapi_without_log(actor, service, tool_name, params)
            elif not self.governance.is_source_allowed(actor, profile_key, SourceType.mcp_service.value, service):
                raise _mark_call_log_failure(
                    _mark_call_log_status(
                        ValidationError("source is blocked by profile policy"),
                        CallLogStatus.blocked.value,
                    ),
                    stage=FailureStage.profile_policy.value,
                    owner=FailureOwner.policy.value,
                    error_type="profile_policy_blocked",
                )
            else:
                result = await self._execute_without_log(actor, service, tool_name, params)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=source_type,
                source_key=service,
                tool_name=tool_name,
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                resource_type=resource_type,
                resource_key=resource_key,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            if source_type == SourceType.builtin.value:
                resource_type = str(getattr(exc, "_tool_call_resource_type", resource_type or "")) or None
                resource_key = str(getattr(exc, "_tool_call_resource_key", resource_key or "")) or None
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=source_type,
                source_key=service,
                tool_name=tool_name,
                request=request,
                response={"error": str(exc)},
                status=_call_log_status(exc),
                error_message=str(exc),
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
                resource_type=resource_type,
                resource_key=resource_key,
                duration_ms=monotonic_ms() - started,
            )
            _attach_log_id(exc, log["log_id"])
            raise

    async def _execute_builtin(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        provider = self.builtin_providers[service]
        try:
            result = await provider.execute(actor, tool_name, params, profile_key, workflow_context)
        except ValidationError as exc:
            if str(exc) == "resource is blocked by profile policy":
                resource = provider.resource_from_arguments(tool_name, params)
                raise mark_builtin_failure(
                    _mark_call_log_status(exc, CallLogStatus.blocked.value),
                    stage=FailureStage.profile_policy.value,
                    owner=FailureOwner.policy.value,
                    error_type="profile_policy_blocked",
                    resource_type=resource.resource_type if resource is not None else None,
                    resource_key=resource.resource_key if resource is not None else None,
                ) from exc
            raise
        return {
            "service": service,
            "tool": tool_name,
            "tool_name": tool_name,
            "success": True,
            "result": result,
        }

    def _builtin_resource_tuple(self, resource: BuiltinResourceRef | None) -> tuple[str | None, str | None]:
        if resource is None:
            return None, None
        return resource.resource_type, resource.resource_key

    async def _execute_without_log(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        service_payload = self._require_enabled_service(service)
        tool_payload = self.store.get_mcp_tool(service, tool_name)
        if tool_payload is None or tool_payload.get("status") != "active":
            raise _mark_call_log_failure(
                NotFound("tool not found"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if tool_payload["tool_type"] not in READONLY_TOOL_TYPES:
            if tool_payload["tool_type"] == ToolType.unconfigured.value:
                raise _mark_call_log_failure(
                    _mark_call_log_status(
                        ValidationError("工具类型未配置，请联系管理员在 Agent Bridge 中配置工具类型"),
                        CallLogStatus.blocked.value,
                    ),
                    stage=FailureStage.capability_registry.value,
                    owner=FailureOwner.platform.value,
                    error_type="capability_registry_error",
                )
            raise _mark_call_log_failure(
                _mark_call_log_status(
                    ValidationError("tool type is not executable"),
                    CallLogStatus.blocked.value,
                ),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )

        headers = _json_loads(service_payload.get("headers_json"), {})
        try:
            result = await self.mcp_client.call_tool(
                service_payload["endpoint_url"],
                headers,
                tool_name,
                params,
            )
        except Exception as exc:
            raise _mark_call_log_failure(
                ValidationError(f"MCP tool execution failed: {exc}"),
                stage=FailureStage.mcp_transport.value,
                owner=FailureOwner.upstream_mcp.value,
                error_type="mcp_transport_error",
            ) from exc
        return {
            "service": service,
            "tool": tool_name,
            "tool_name": tool_name,
            "success": not bool(result.get("is_error")) if isinstance(result, dict) else True,
            "result": result,
        }

    def _execute_openapi_without_log(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        service_payload = self._require_enabled_openapi_service(service)
        tool_payload = self.store.get_openapi_tool(service, tool_name)
        if tool_payload is None or tool_payload.get("status") != "active":
            raise _mark_call_log_failure(
                NotFound("tool not found"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if tool_payload["tool_type"] not in READONLY_TOOL_TYPES:
            if tool_payload["tool_type"] == ToolType.unconfigured.value:
                raise _mark_call_log_failure(
                    _mark_call_log_status(
                        ValidationError("工具类型未配置，请联系管理员在 Agent Bridge 中配置工具类型"),
                        CallLogStatus.blocked.value,
                    ),
                    stage=FailureStage.capability_registry.value,
                    owner=FailureOwner.platform.value,
                    error_type="capability_registry_error",
                )
            raise _mark_call_log_failure(
                _mark_call_log_status(
                    ValidationError("tool type is not executable"),
                    CallLogStatus.blocked.value,
                ),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        try:
            result = self.openapi_client.call_tool(
                self._openapi_service_payload(service_payload),
                self._openapi_tool_payload(tool_payload),
                params,
            )
        except Exception as exc:
            raise _mark_call_log_failure(
                ValidationError(f"OpenAPI tool execution failed: {exc}"),
                stage=FailureStage.openapi_transport.value,
                owner=FailureOwner.upstream_openapi.value,
                error_type="openapi_transport_error",
            ) from exc
        return {"service": service, "tool": tool_name, "tool_name": tool_name, "success": True, "result": result}

    def _require_enabled_service(self, service_key: str) -> dict[str, Any]:
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise _mark_call_log_failure(
                NotFound("service not found"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if service["status"] != McpServiceStatus.enabled.value:
            raise _mark_call_log_failure(
                ValidationError("MCP service is not enabled"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        return service

    def _require_enabled_openapi_service(self, service_key: str) -> dict[str, Any]:
        service = self.store.get_openapi_service(service_key)
        if service is None:
            raise _mark_call_log_failure(
                NotFound("service not found"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if service["status"] != McpServiceStatus.enabled.value:
            raise _mark_call_log_failure(
                ValidationError("OpenAPI service is not enabled"),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        return service

    def _root_search_items(self, *, actor: str, profile_key: str | None = None) -> list[dict[str, Any]]:
        enabled_services = [
            service
            for service in self.store.list_mcp_services()
            if service["status"] == McpServiceStatus.enabled.value
            and service["service_key"] not in self.builtin_providers
        ]
        visible_keys = set(
            self.governance.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.mcp_service.value,
                source_keys=[service["service_key"] for service in enabled_services],
            )
        )
        enabled_services = [service for service in enabled_services if service["service_key"] in visible_keys]
        tools_by_service: dict[str, int] = {}
        for tool in self.store.list_mcp_tools():
            if tool.get("status") == "active":
                tools_by_service[tool["service_key"]] = tools_by_service.get(tool["service_key"], 0) + 1
        builtin_items = []
        for provider in self.builtin_providers.values():
            provider_tools = provider.list_tools(actor, profile_key)
            if not provider_tools:
                continue
            builtin_items.append(
                {
                    "kind": "builtin",
                    "service": provider.source_key,
                    "name": provider.name,
                    "description": provider.description,
                    "tags": provider.tags,
                    "tool_count": len(provider_tools),
                    "status": "enabled",
                    "resources": provider.list_resources(actor, profile_key),
                }
            )
        external_items = [
            {
                "kind": "service",
                "service": service["service_key"],
                "name": service["name"],
                "description": service["description"],
                "tags": _json_loads(service.get("tags_json"), []),
                "tool_count": tools_by_service.get(service["service_key"], 0),
                "status": service["status"],
            }
            for service in enabled_services
        ]
        openapi_services = [
            service
            for service in self.store.list_openapi_services()
            if service["status"] == McpServiceStatus.enabled.value
        ]
        visible_openapi_keys = set(
            self.governance.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.openapi_service.value,
                source_keys=[service["service_key"] for service in openapi_services],
            )
        )
        openapi_tools_by_service: dict[str, int] = {}
        for tool in self.store.list_openapi_tools():
            if tool.get("status") == "active":
                openapi_tools_by_service[tool["service_key"]] = openapi_tools_by_service.get(tool["service_key"], 0) + 1
        openapi_items = [
            {
                "kind": "service",
                "source_type": SourceType.openapi_service.value,
                "service": service["service_key"],
                "name": service["name"],
                "description": service["description"],
                "tags": _json_loads(service.get("tags_json"), []),
                "tool_count": openapi_tools_by_service.get(service["service_key"], 0),
                "status": service["status"],
            }
            for service in openapi_services
            if service["service_key"] in visible_openapi_keys
        ]
        return builtin_items + external_items + openapi_items

    def _active_tools(self, service_key: str) -> list[dict[str, Any]]:
        return [tool for tool in self.store.list_mcp_tools(service_key) if tool.get("status") == "active"]

    def _active_openapi_tools(self, service_key: str) -> list[dict[str, Any]]:
        return [tool for tool in self.store.list_openapi_tools(service_key) if tool.get("status") == "active"]

    def _service_payload(self, service: dict[str, Any], *, redact_headers: bool = False) -> dict[str, Any]:
        payload = dict(service)
        headers = _json_loads(payload.pop("headers_json", None), {})
        if redact_headers:
            headers = {key: "***" if value else value for key, value in headers.items()}
        payload["headers"] = headers
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        return payload

    def _tool_payload(self, tool: dict[str, Any]) -> dict[str, Any]:
        input_schema = _json_loads(tool.get("input_schema_json"), {})
        examples = _json_loads(tool.get("examples_json"), [])
        payload = dict(tool)
        payload.pop("input_schema_json", None)
        payload.pop("tags_json", None)
        payload.pop("examples_json", None)
        return {
            **payload,
            "service": tool["service_key"],
            "tool": tool["tool_name"],
            "name": tool["display_name"],
            "input_schema": input_schema,
            "tool_type": tool["tool_type"],
            "tags": _json_loads(tool.get("tags_json"), []),
            "examples": examples,
            "execute_example": examples[0] if examples else _schema_example(input_schema),
            "executable": tool["tool_type"] in READONLY_TOOL_TYPES,
        }

    def _openapi_service_payload(self, service: dict[str, Any], *, redact_secrets: bool = False) -> dict[str, Any]:
        payload = dict(service)
        headers = _json_loads(payload.pop("headers_json", None), {})
        auth_config = _json_loads(payload.pop("auth_config_json", None), {})
        if redact_secrets:
            headers = {key: "***" if value else value for key, value in headers.items()}
            auth_config = {
                key: "***" if key in {"token", "value", "api_key"} and value else value
                for key, value in auth_config.items()
            }
        payload["headers"] = headers
        payload["auth_config"] = auth_config
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        payload["source_type"] = SourceType.openapi_service.value
        return payload

    def _openapi_tool_payload(self, tool: dict[str, Any]) -> dict[str, Any]:
        input_schema = _json_loads(tool.get("input_schema_json"), {})
        request_mapping = _json_loads(tool.get("request_mapping_json"), {})
        response_schema = _json_loads(tool.get("response_schema_json"), {})
        examples = _json_loads(tool.get("examples_json"), [])
        payload = dict(tool)
        payload.pop("input_schema_json", None)
        payload.pop("request_mapping_json", None)
        payload.pop("response_schema_json", None)
        payload.pop("tags_json", None)
        payload.pop("examples_json", None)
        return {
            **payload,
            "source_type": SourceType.openapi_service.value,
            "service": tool["service_key"],
            "tool": tool["tool_name"],
            "name": tool["display_name"],
            "input_schema": input_schema,
            "request_mapping": request_mapping,
            "response_schema": response_schema,
            "tool_type": tool["tool_type"],
            "tags": _json_loads(tool.get("tags_json"), []),
            "examples": examples,
            "execute_example": examples[0] if examples else _schema_example(input_schema),
            "executable": tool["tool_type"] in READONLY_TOOL_TYPES,
        }

    def _builtin_tool_search_item(self, source_key: str, tool: BuiltinTool) -> dict[str, Any]:
        return {
            "kind": "tool",
            "service": source_key,
            "tool": tool.tool,
            "display_tool": f"{source_key}.{tool.tool}",
            "name": tool.name,
            "description": tool.description,
            "tags": ["builtin", source_key],
            "tool_type": tool.tool_type,
            "input_schema": tool.input_schema,
            "execute_example": {},
            "executable": True,
        }

    def _tool_search_item(self, tool: dict[str, Any]) -> dict[str, Any]:
        payload = self._tool_payload(tool)
        return {
            "kind": "tool",
            "service": payload["service"],
            "tool": payload["tool"],
            "name": payload["name"],
            "description": payload["description"],
            "tags": payload["tags"],
            "tool_type": payload["tool_type"],
            "input_schema": payload["input_schema"],
            "execute_example": payload["execute_example"],
            "executable": payload["executable"],
        }

    def _openapi_tool_search_item(self, tool: dict[str, Any]) -> dict[str, Any]:
        payload = self._openapi_tool_payload(tool)
        return {
            "kind": "tool",
            "source_type": SourceType.openapi_service.value,
            "service": payload["service"],
            "tool": payload["tool"],
            "name": payload["name"],
            "description": payload["description"],
            "tags": payload["tags"],
            "tool_type": payload["tool_type"],
            "input_schema": payload["input_schema"],
            "execute_example": payload["execute_example"],
            "executable": payload["executable"],
            "method": payload["method"],
            "path": payload["path"],
        }

    def _normalize_synced_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(tool.get("name") or "")
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        examples = tool.get("examples")
        if not isinstance(examples, list):
            examples = []
        return {
            "tool_name": tool_name,
            "display_name": self._display_name(tool),
            "description": str(tool.get("description") or ""),
            "input_schema": input_schema,
            "tool_type": ToolType.unconfigured,
            "tags": self._tool_tags(tool),
            "examples": examples,
        }

    def _display_name(self, tool: dict[str, Any]) -> str:
        annotations = tool.get("annotations")
        if isinstance(annotations, dict) and annotations.get("title"):
            return str(annotations["title"])
        return str(tool.get("name") or "")

    def _tool_tags(self, tool: dict[str, Any]) -> list[str]:
        tags = tool.get("tags")
        if isinstance(tags, list):
            return [str(tag) for tag in tags]
        return []

    def _validate_service_key(self, service_key: str) -> None:
        if not SERVICE_KEY_RE.fullmatch(service_key):
            raise ValidationError("service_key may contain only letters, numbers, hyphen, and underscore")

    def _validate_tool_type(self, tool_type: Any) -> ToolType:
        try:
            return ToolType(str(tool_type or ToolType.unconfigured.value))
        except ValueError as exc:
            raise ValidationError("invalid tool type") from exc
