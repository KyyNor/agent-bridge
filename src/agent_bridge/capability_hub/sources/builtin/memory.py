from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService

logger = logging.getLogger(__name__)


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
                "检索当前 profile 绑定的记忆区块。",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "要检索的记忆关键词或问题。"},
                        "limit": {"type": "integer", "default": 10, "description": "本次最多返回的结果数量。"},
                        "block": {"type": "string", "description": "可选的记忆区块标识；留空时使用当前 profile 绑定区块。"},
                    },
                    "required": ["query"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "timeline",
                "Memory Timeline",
                "读取当前记忆区块的最近时间线。",
                {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 20, "description": "本次读取的时间线条目数量上限。"},
                        "cursor": {"type": "string", "description": "时间线分页游标。"},
                        "block": {"type": "string", "description": "可选的记忆区块标识；留空时使用当前 profile 绑定区块。"},
                    },
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get",
                "Memory Get",
                "按 ID 读取单条记忆 observation。",
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "要读取的记忆 observation ID。"},
                        "block": {"type": "string", "description": "可选的记忆区块标识；留空时使用当前 profile 绑定区块。"},
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
        logger.debug("Memory 调用 actor=%s profile=%s tool=%s block=%s", actor, profile_key, tool, block_key)
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
