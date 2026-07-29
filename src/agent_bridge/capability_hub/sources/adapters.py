"""能力来源适配器与注册表。

应用服务只负责审计编排；Builtin、MCP、OpenAPI 的发现、检索和执行差异由本模块
各自维护。新增来源时注册一个适配器即可，不再修改中心 ``if/elif`` 分发链。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Protocol

from agent_bridge.capability_hub.errors import capability_failure
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage, McpServiceStatus, SourceType
from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef
from agent_bridge.core.domain import ValidationError
from agent_bridge.core.json_util import json_loads


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceExecution:
    """来源执行结果及其资源归属。"""

    result: dict[str, Any]
    resource: BuiltinResourceRef | None = None


class CapabilitySourceAdapter(Protocol):
    source_type: str

    def matches(self, source_key: str) -> bool: ...
    def root_items(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]: ...
    def search_items(self, actor: str, source_key: str, profile_key: str | None) -> list[dict[str, Any]]: ...
    def tool_names(self, actor: str, source_key: str, profile_key: str | None) -> list[str]: ...
    async def execute(
        self,
        actor: str,
        source_key: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
    ) -> SourceExecution: ...


class CapabilitySourceRegistry:
    """按明确注册顺序解析来源，并检测不应出现的 key 冲突。"""

    def __init__(self, adapters: list[CapabilitySourceAdapter], *, fallback: CapabilitySourceAdapter) -> None:
        self._adapters = tuple(adapters)
        self._fallback = fallback

    @property
    def adapters(self) -> tuple[CapabilitySourceAdapter, ...]:
        return self._adapters

    def resolve(self, source_key: str) -> CapabilitySourceAdapter:
        matched = [adapter for adapter in self._adapters if adapter.matches(source_key)]
        if len(matched) > 1:
            kinds = ", ".join(adapter.source_type for adapter in matched)
            raise ValidationError(f"能力来源标识冲突：{source_key} 同时属于 {kinds}")
        return matched[0] if matched else self._fallback

    def root_items(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for adapter in self._adapters:
            items.extend(adapter.root_items(actor, profile_key))
        return items


class BuiltinSourceAdapter:
    source_type = SourceType.builtin.value

    def __init__(self, service: Any) -> None:
        self.service = service

    def matches(self, source_key: str) -> bool:
        return source_key in self.service.builtin_providers

    def root_items(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for provider in self.service.builtin_providers.values():
            tools = provider.list_tools(actor, profile_key)
            if not tools:
                continue
            items.append(
                {
                    "kind": "builtin",
                    "service": provider.source_key,
                    "name": provider.name,
                    "description": provider.description,
                    "tags": provider.tags,
                    "tool_count": len(tools),
                    "status": "enabled",
                    "resources": provider.list_resources(actor, profile_key),
                }
            )
        return items

    def search_items(self, actor: str, source_key: str, profile_key: str | None) -> list[dict[str, Any]]:
        provider = self.service.builtin_providers[source_key]
        return [self.service._builtin_tool_search_item(source_key, tool) for tool in provider.list_tools(actor, profile_key)]

    def tool_names(self, actor: str, source_key: str, profile_key: str | None) -> list[str]:
        provider = self.service.builtin_providers[source_key]
        return [tool.tool for tool in provider.list_tools(actor, profile_key)]

    async def execute(
        self,
        actor: str,
        source_key: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
    ) -> SourceExecution:
        provider = self.service.builtin_providers[source_key]
        resource = provider.resource_from_arguments(tool_name, params)
        logger.debug("能力分发 builtin service=%s tool=%s", source_key, tool_name)
        try:
            result = await provider.execute(actor, tool_name, params, profile_key, workflow_context)
        except Exception as exc:
            if resource is None:
                raise
            raise capability_failure(
                exc,
                resource_type=resource.resource_type,
                resource_key=resource.resource_key,
            ) from exc
        return SourceExecution(
            result={
                "success": True,
                "result": result,
            },
            resource=resource,
        )


class _GovernedExternalSourceAdapter:
    source_type: str
    source_label: str

    def __init__(self, service: Any) -> None:
        self.service = service

    def _allowed(self, actor: str, profile_key: str | None, source_key: str) -> bool:
        return self.service.governance.is_source_allowed(actor, profile_key, self.source_type, source_key)

    def _require_allowed(self, actor: str, profile_key: str | None, source_key: str) -> None:
        if self._allowed(actor, profile_key, source_key):
            return
        logger.warning(
            "能力执行被拒绝 actor=%s service=%s 原因=%s 来源=%s",
            actor,
            source_key,
            "被 profile 策略拦截",
            self.source_label,
        )
        raise capability_failure(
            ValidationError("source is blocked by profile policy"),
            status=CallLogStatus.blocked.value,
            stage=FailureStage.profile_policy.value,
            owner=FailureOwner.policy.value,
            error_type="profile_policy_blocked",
        )


class McpSourceAdapter(_GovernedExternalSourceAdapter):
    source_type = SourceType.mcp_service.value
    source_label = "MCP"

    def matches(self, source_key: str) -> bool:
        return self.service.store.get_mcp_service(source_key) is not None

    def root_items(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        services = [
            service
            for service in self.service.store.list_mcp_services()
            if service["status"] == McpServiceStatus.enabled.value
            and service["service_key"] not in self.service.builtin_providers
        ]
        visible = set(
            self.service.governance.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=self.source_type,
                source_keys=[service["service_key"] for service in services],
            )
        )
        tool_counts: dict[str, int] = {}
        for tool in self.service.store.list_mcp_tools():
            if tool.get("status") == "active":
                tool_counts[tool["service_key"]] = tool_counts.get(tool["service_key"], 0) + 1
        return [
            {
                "kind": "service",
                "service": service["service_key"],
                "name": service["name"],
                "description": service["description"],
                "tags": json_loads(service.get("tags_json"), []),
                "tool_count": tool_counts.get(service["service_key"], 0),
                "status": service["status"],
            }
            for service in services
            if service["service_key"] in visible
        ]

    def search_items(self, actor: str, source_key: str, profile_key: str | None) -> list[dict[str, Any]]:
        if not self._allowed(actor, profile_key, source_key):
            logger.debug("搜索 MCP 来源被拒绝 path=%s", source_key)
            return []
        self.service._require_enabled_service(source_key)
        return [self.service._tool_search_item(tool) for tool in self.service._active_tools(source_key)]

    def tool_names(self, actor: str, source_key: str, profile_key: str | None) -> list[str]:
        return [tool["tool_name"] for tool in self.service._active_tools(source_key)]

    async def execute(
        self,
        actor: str,
        source_key: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
    ) -> SourceExecution:
        self._require_allowed(actor, profile_key, source_key)
        logger.debug("能力分发 MCP service=%s tool=%s", source_key, tool_name)
        return SourceExecution(
            result=await self.service._execute_without_log(actor, source_key, tool_name, params, profile_key)
        )


class OpenApiSourceAdapter(_GovernedExternalSourceAdapter):
    source_type = SourceType.openapi_service.value
    source_label = "OpenAPI"

    def matches(self, source_key: str) -> bool:
        return self.service.store.get_openapi_service(source_key) is not None

    def root_items(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        services = [
            service
            for service in self.service.store.list_openapi_services()
            if service["status"] == McpServiceStatus.enabled.value
        ]
        visible = set(
            self.service.governance.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=self.source_type,
                source_keys=[service["service_key"] for service in services],
            )
        )
        tool_counts: dict[str, int] = {}
        for tool in self.service.store.list_openapi_tools():
            if tool.get("status") == "active":
                tool_counts[tool["service_key"]] = tool_counts.get(tool["service_key"], 0) + 1
        return [
            {
                "kind": "service",
                "source_type": self.source_type,
                "service": service["service_key"],
                "name": service["name"],
                "description": service["description"],
                "tags": json_loads(service.get("tags_json"), []),
                "tool_count": tool_counts.get(service["service_key"], 0),
                "status": service["status"],
            }
            for service in services
            if service["service_key"] in visible
        ]

    def search_items(self, actor: str, source_key: str, profile_key: str | None) -> list[dict[str, Any]]:
        if not self._allowed(actor, profile_key, source_key):
            logger.debug("搜索 OpenAPI 来源被拒绝 path=%s", source_key)
            return []
        self.service._require_enabled_openapi_service(source_key)
        return [self.service._openapi_tool_search_item(tool) for tool in self.service._active_openapi_tools(source_key)]

    def tool_names(self, actor: str, source_key: str, profile_key: str | None) -> list[str]:
        return [tool["tool_name"] for tool in self.service._active_openapi_tools(source_key)]

    async def execute(
        self,
        actor: str,
        source_key: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
    ) -> SourceExecution:
        self._require_allowed(actor, profile_key, source_key)
        logger.debug("能力分发 OpenAPI service=%s tool=%s", source_key, tool_name)
        result = await asyncio.to_thread(
            self.service._execute_openapi_without_log,
            actor,
            source_key,
            tool_name,
            params,
            profile_key,
        )
        return SourceExecution(result=result)
