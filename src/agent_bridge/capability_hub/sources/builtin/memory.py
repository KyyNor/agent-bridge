from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService


class MemoryBuiltinProvider:
    source_key = "memory"
    name = "Memory"
    description = "内置记忆检索能力"
    tags = ["builtin", "memory"]

    def __init__(self, service: "AgentBridgeService") -> None:
        self.service = service

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        resolved = self.service.memory.resolve_profile_block(actor, profile_key)
        if resolved["status"] != "ok":
            return []
        block = resolved["block"]
        return [{"resource_type": "memory_block", "resource_key": block["block_key"], "name": block["name"]}]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return [
            BuiltinTool(
                "search",
                "Memory Search",
                "Search the active memory block bound to this profile.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                        "block": {"type": "string"},
                    },
                    "required": ["query"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "timeline",
                "Memory Timeline",
                "Read recent timeline items from the active memory block.",
                {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 20},
                        "cursor": {"type": "string"},
                        "block": {"type": "string"},
                    },
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get",
                "Memory Get",
                "Read a memory observation by id.",
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "block": {"type": "string"},
                    },
                    "required": ["id"],
                },
                ToolType.detail.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]):
        return None

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del workflow_context
        block_key = str(arguments.get("block") or "").strip() or None
        if tool == "search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ValidationError("query is required")
            return self.service.memory.search(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                query=query,
                limit=int(arguments.get("limit") or 10),
            )
        if tool == "timeline":
            return self.service.memory.timeline(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                limit=int(arguments.get("limit") or 20),
                cursor=arguments.get("cursor"),
            )
        if tool == "get":
            observation_id = str(arguments.get("id") or "").strip()
            if not observation_id:
                raise ValidationError("id is required")
            return self.service.memory.get_observation(
                actor=actor,
                profile_key=profile_key,
                block_key=block_key,
                observation_id=observation_id,
            )
        raise NotFound("tool not found")
