"""受小组权限保护的资源 adapter 与 registry。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from agent_bridge.core.domain import ValidationError


class ScopedResourceType(str, Enum):
    knowledge_base = "knowledge_base"
    mcp_service = "mcp_service"
    openapi_service = "openapi_service"
    code_repository = "code_repository"


@runtime_checkable
class ScopedResourceAdapter(Protocol):
    resource_type: ScopedResourceType

    def get(self, resource_key: str) -> dict[str, Any] | None: ...

    def list(self) -> list[dict[str, Any]]: ...


class ScopedResourceRegistry:
    def __init__(self, adapters: list[ScopedResourceAdapter] | None = None) -> None:
        self._adapters: dict[ScopedResourceType, ScopedResourceAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ScopedResourceAdapter) -> None:
        self._adapters[adapter.resource_type] = adapter

    def get(self, resource_type: ScopedResourceType | str) -> ScopedResourceAdapter:
        try:
            resolved = ScopedResourceType(resource_type)
        except ValueError as exc:
            raise ValidationError(f"不支持的数据权限资源类型：{resource_type}") from exc
        adapter = self._adapters.get(resolved)
        if adapter is None:
            raise ValidationError(f"数据权限资源类型尚未注册：{resolved.value}")
        return adapter


class KnowledgeBaseAccessAdapter:
    resource_type = ScopedResourceType.knowledge_base

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_kb_by_slug(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_kbs()


class McpServiceAccessAdapter:
    resource_type = ScopedResourceType.mcp_service

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_mcp_service(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_mcp_services()


class OpenApiServiceAccessAdapter:
    resource_type = ScopedResourceType.openapi_service

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_openapi_service(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_openapi_services()


class CodeRepositoryAccessAdapter:
    resource_type = ScopedResourceType.code_repository

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_code_repository(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_code_repositories()


def create_scoped_resource_registry(store) -> ScopedResourceRegistry:
    return ScopedResourceRegistry(
        [
            KnowledgeBaseAccessAdapter(store),
            McpServiceAccessAdapter(store),
            OpenApiServiceAccessAdapter(store),
            CodeRepositoryAccessAdapter(store),
        ]
    )
