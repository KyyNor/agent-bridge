from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BuiltinTool:
    tool: str
    name: str
    description: str
    input_schema: dict[str, Any]
    tool_type: str


@dataclass(frozen=True)
class BuiltinResourceRef:
    resource_type: str
    resource_key: str


def mark_builtin_failure(
    exc: Exception,
    *,
    stage: str,
    owner: str,
    error_type: str,
    resource_type: str | None = None,
    resource_key: str | None = None,
) -> Exception:
    setattr(exc, "_tool_call_failure_stage", stage)
    setattr(exc, "_tool_call_failure_owner", owner)
    setattr(exc, "_tool_call_error_type", error_type)
    if resource_type is not None:
        setattr(exc, "_tool_call_resource_type", resource_type)
    if resource_key is not None:
        setattr(exc, "_tool_call_resource_key", resource_key)
    return exc


class BuiltinCapabilityProvider(Protocol):
    source_key: str
    name: str
    description: str
    tags: list[str]

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        pass

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        pass

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        pass

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pass
