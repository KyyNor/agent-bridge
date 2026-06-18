from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_bridge.capabilities.builtin import BuiltinResourceRef, BuiltinTool
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.knowledge.service import AgentBridgeService


class PlatformBuiltinProvider:
    source_key = "built-in"
    name = "Built-in"
    description = "平台内置辅助工具"
    tags = ["builtin", "platform"]

    def __init__(self, service: "AgentBridgeService") -> None:
        self.service = service

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        return []

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return []

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        return None

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
    ) -> dict[str, Any]:
        if tool != "load_skill":
            raise NotFound("tool not found")
        skill_name = str(arguments.get("skill_name") or "").strip()
        if not skill_name:
            raise ValidationError("skill_name is required")
        return self.service.skills.load_skill(actor, skill_name)
