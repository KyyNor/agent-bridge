from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef, BuiltinTool
from agent_bridge.capability_hub.models import ToolType
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService


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
        return [
            BuiltinTool(
                "load_skill",
                "Load Skill",
                "Load a managed Agent Bridge skill prompt.",
                {
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string"},
                    },
                    "required": ["skill_name"],
                },
                ToolType.detail.value,
            ),
            BuiltinTool(
                "run_script",
                "Run Script",
                "Run a managed server-side Python script and return its JSON result.",
                {
                    "type": "object",
                    "properties": {
                        "script_key": {"type": "string"},
                        "script_params": {"type": "object", "default": {}},
                        "timeout_seconds": {"type": "integer", "default": 60},
                    },
                    "required": ["script_key"],
                },
                ToolType.action.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        return None

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
    ) -> dict[str, Any]:
        if tool == "load_skill":
            skill_name = str(arguments.get("skill_name") or "").strip()
            if not skill_name:
                raise ValidationError("skill_name is required")
            return self.service.skills.load_skill(actor, skill_name)
        if tool == "run_script":
            script_key = str(arguments.get("script_key") or "").strip()
            if not script_key:
                raise ValidationError("script_key is required")
            script_params = arguments.get("script_params") or {}
            if not isinstance(script_params, dict):
                raise ValidationError("script_params must be an object")
            return self.service.scripts.run_script(
                actor=actor,
                script_key=script_key,
                script_params=script_params,
                timeout_seconds=arguments.get("timeout_seconds"),
                profile_key=profile_key,
                run_type="mcp",
            )
        else:
            raise NotFound("tool not found")
