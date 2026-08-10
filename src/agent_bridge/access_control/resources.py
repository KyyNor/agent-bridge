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
    capability_profile = "capability_profile"
    memory_block = "memory_block"
    business_ledger = "business_ledger"
    workflow_definition = "workflow_definition"
    workflow_artifact = "workflow_artifact"
    user_script = "user_script"
    model_evaluation_run = "model_evaluation_run"


SHAREABLE_RESOURCE_TYPES = frozenset(
    {
        ScopedResourceType.knowledge_base,
        ScopedResourceType.mcp_service,
        ScopedResourceType.openapi_service,
        ScopedResourceType.code_repository,
        ScopedResourceType.business_ledger,
        ScopedResourceType.workflow_artifact,
    }
)


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

    @staticmethod
    def is_shareable(resource_type: ScopedResourceType | str) -> bool:
        try:
            resolved = ScopedResourceType(resource_type)
        except ValueError as exc:
            raise ValidationError(f"不支持的数据权限资源类型：{resource_type}") from exc
        return resolved in SHAREABLE_RESOURCE_TYPES


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


class CapabilityProfileAccessAdapter:
    resource_type = ScopedResourceType.capability_profile

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_project_profile(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_project_profiles()


class MemoryBlockAccessAdapter:
    resource_type = ScopedResourceType.memory_block

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.memory.get_memory_block(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.memory.list_memory_blocks()


class WorkflowDefinitionAccessAdapter:
    resource_type = ScopedResourceType.workflow_definition

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_workflow_definition(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_workflow_definitions()


class WorkflowArtifactAccessAdapter:
    resource_type = ScopedResourceType.workflow_artifact

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_workflow_artifact(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return []


class UserScriptAccessAdapter:
    resource_type = ScopedResourceType.user_script

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.scripts.get_script(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.scripts.list_scripts()


class ModelEvaluationRunAccessAdapter:
    resource_type = ScopedResourceType.model_evaluation_run

    def __init__(self, store) -> None:
        self.store = store

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.store.get_model_evaluation_run(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_model_evaluation_runs(limit=100)


class BusinessLedgerAccessAdapter:
    resource_type = ScopedResourceType.business_ledger

    def __init__(self, service) -> None:
        self.service = service

    def get(self, resource_key: str) -> dict[str, Any] | None:
        return self.service.get_definition(resource_key)

    def list(self) -> list[dict[str, Any]]:
        return self.service.list_definitions()


def create_scoped_resource_registry(store) -> ScopedResourceRegistry:
    return ScopedResourceRegistry(
        [
            KnowledgeBaseAccessAdapter(store),
            McpServiceAccessAdapter(store),
            OpenApiServiceAccessAdapter(store),
            CodeRepositoryAccessAdapter(store),
            CapabilityProfileAccessAdapter(store),
            MemoryBlockAccessAdapter(store),
            WorkflowDefinitionAccessAdapter(store),
            WorkflowArtifactAccessAdapter(store),
            UserScriptAccessAdapter(store),
            ModelEvaluationRunAccessAdapter(store),
        ]
    )
