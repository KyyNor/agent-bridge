from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.models import ProfileResourceType, ToolType
from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef, BuiltinTool
from agent_bridge.core.domain import NotFound, ValidationError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService


class BusinessLedgerBuiltinProvider:
    """只读业务台账查询；台账管理 API 不注册给 Agent。"""

    source_key = "business_ledger"
    name = "业务台账"
    description = "查询当前能力平面获授权的业务台账"
    tags = ["builtin", "business-ledger"]

    def __init__(self, service: "AgentBridgeService") -> None:
        self.service = service

    def _visible_contexts(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        if profile_key is None:
            return []
        all_contexts = self.service.business_ledgers.ledger_contexts(
            self.service.business_ledgers.ledger_keys(), actor=actor
        )
        visible = self.service.governance.filter_resource_keys(
            actor=actor,
            profile_key=profile_key,
            resource_type=ProfileResourceType.business_ledger.value,
            resource_keys=[item["ledger_key"] for item in all_contexts],
        )
        return self.service.business_ledgers.ledger_contexts(visible, actor=actor)

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        return [
            {"resource_type": ProfileResourceType.business_ledger.value, "resource_key": item["ledger_key"], "name": item["name"]}
            for item in self._visible_contexts(actor, profile_key)
        ]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        if not self._visible_contexts(actor, profile_key):
            return []
        return [
            BuiltinTool(
                "query",
                "Query Business Ledger",
                "查询已授权业务台账。可用台账和字段已由 Profile 注入；所有 filters 条件为 AND。",
                {
                    "type": "object",
                    "properties": {
                        "ledger_key": {"type": "string", "description": "已授权业务台账标识。"},
                        "filters": {"type": "object", "default": {}, "description": "字段条件对象，格式为 field_key: {op, value/values/from/to}。"},
                        "keyword": {"type": "string", "description": "在已配置 contains 的文本字段中做字面包含检索。"},
                        "sort": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
                                },
                                "required": ["field"],
                            },
                            "description": "可选多字段排序，按数组顺序依次生效，格式为 [{field, direction}]。",
                        },
                        "limit": {"type": "integer", "default": 50, "description": "返回行数，1-100。"},
                        "offset": {"type": "integer", "default": 0, "description": "分页偏移量。"},
                    },
                    "required": ["ledger_key"],
                },
                ToolType.search.value,
            )
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        if tool != "query":
            return None
        ledger_key = str(arguments.get("ledger_key") or "").strip()
        return BuiltinResourceRef(ProfileResourceType.business_ledger.value, ledger_key) if ledger_key else None

    async def execute(
        self, actor: str, tool: str, arguments: dict[str, Any], profile_key: str | None, workflow_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del workflow_context
        if tool != "query":
            raise NotFound("tool not found")
        ledger_key = str(arguments.get("ledger_key") or "").strip()
        if not ledger_key:
            raise ValidationError("ledger_key is required")
        contexts = self._visible_contexts(actor, profile_key)
        visible = {item["ledger_key"] for item in contexts}
        if ledger_key not in visible:
            hints = ", ".join(f"{item['name']} ({item['ledger_key']})" for item in contexts) or "无"
            raise NotFound(f"ledger_not_available；当前可用业务台账：{hints}")
        filters = arguments.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValidationError("filters must be an object")
        return self.service.business_ledgers.query(
            ledger_key,
            actor=actor,
            filters=filters,
            keyword=str(arguments.get("keyword") or "").strip() or None,
            sort=arguments.get("sort") if isinstance(arguments.get("sort"), (dict, list)) else None,
            limit=int(arguments.get("limit") or 50),
            offset=int(arguments.get("offset") or 0),
            agent_visible_only=True,
        )
