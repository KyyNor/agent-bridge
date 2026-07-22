from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.errors import capability_failure
from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef, BuiltinTool, mark_builtin_failure
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage, ProfileResourceType, ToolType
from agent_bridge.core.domain import NotFound, ValidationError, AgentBridgeError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService

logger = logging.getLogger(__name__)

WIKI_SEARCH_ENABLED = False


class WikiBuiltinProvider:
    source_key = "wiki"
    name = "Wiki"
    description = "内置知识库查询能力"
    tags = ["builtin", "knowledge"]

    def __init__(self, service: "AgentBridgeService") -> None:
        self.service = service

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        kbs = self.service.store.list_kbs()
        if actor not in self.service.admins and not profile_key:
            return []
        visible = set(
            self.service.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.wiki_kb.value,
                resource_keys=[kb["slug"] for kb in kbs],
            )
        )
        return [
            {"resource_type": ProfileResourceType.wiki_kb.value, "resource_key": kb["slug"], "name": kb["name"]}
            for kb in kbs
            if kb["slug"] in visible
        ]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        tools = [
            BuiltinTool(
                "ask",
                "Wiki Ask",
                "向已授权知识库提问。",
                {
                    "type": "object",
                    "properties": {
                        "kb": {"type": "string", "description": "要访问的知识库 slug。"},
                        "question": {"type": "string", "description": "要向知识库提出的问题。"},
                        "session_id": {"type": "string", "description": "可选的对话会话 ID，用于延续上下文。"},
                    },
                    "required": ["kb", "question"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get_document",
                "Wiki Document",
                "读取已授权知识库中的文档元数据。",
                {
                    "type": "object",
                    "properties": {
                        "kb": {"type": "string", "description": "要访问的知识库 slug。"},
                        "doc_slug": {"type": "string", "description": "要读取的文档 slug。"},
                    },
                    "required": ["kb", "doc_slug"],
                },
                ToolType.detail.value,
            ),
            BuiltinTool(
                "list_kbs",
                "Wiki KB List",
                "列出当前可访问的知识库。",
                {"type": "object", "properties": {}},
                ToolType.overview.value,
            ),
            BuiltinTool(
                "search_all",
                "Wiki Search All",
                "跨所有已授权知识库搜索，并返回命中的知识库。",
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "要搜索的知识库问题。"},
                        "top_k": {"type": "integer", "default": 6, "description": "每个知识库最多返回的结果数量。"},
                    },
                    "required": ["question"],
                },
                ToolType.search.value,
            ),
        ]
        if WIKI_SEARCH_ENABLED:
            tools.append(
                BuiltinTool(
                    "search",
                    "Wiki Search",
                    "在已授权知识库中搜索片段。",
                    {
                        "type": "object",
                        "properties": {
                            "kb": {"type": "string", "description": "要访问的知识库 slug。"},
                            "question": {"type": "string", "description": "要检索的知识库问题。"},
                            "top_k": {"type": "integer", "default": 6, "description": "最多返回的结果数量。"},
                        },
                        "required": ["kb", "question"],
                    },
                    ToolType.search.value,
                )
            )
        return tools

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        supported_tools = {"search_all", "ask", "get_document"}
        if WIKI_SEARCH_ENABLED:
            supported_tools.add("search")
        if tool not in supported_tools:
            return None
        kb_slug = str(arguments.get("kb") or arguments.get("kb_slug") or "").strip()
        if not kb_slug:
            return None
        return BuiltinResourceRef(ProfileResourceType.wiki_kb.value, kb_slug)

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if tool == "list_kbs":
            return {
                "kbs": [
                    {**kb, "slug": kb["resource_key"]}
                    for kb in self.list_resources(actor, profile_key)
                ]
            }
        if tool == "search" and not WIKI_SEARCH_ENABLED:
            raise NotFound("tool not found")
        if tool not in {"search", "search_all", "ask", "get_document"}:
            raise NotFound("tool not found")
        if tool == "search_all":
            question = str(arguments.get("question") or "").strip()
            if not question:
                raise ValidationError("question is required")
            logger.info("Wiki search_all 开始 actor=%s profile=%s", actor, profile_key)
            try:
                results = await asyncio.to_thread(
                    self.service.search_all,
                    actor,
                    question,
                    top_k=int(arguments.get("top_k") or 6),
                    profile_key=profile_key,
                )
            except AgentBridgeError:
                raise
            except Exception as exc:
                logger.error("Wiki search_all 后端失败 actor=%s 原因=%s", actor, exc, exc_info=True)
                raise mark_builtin_failure(
                    ValidationError(f"Wiki builtin backend failed: {exc}"),
                    stage=FailureStage.builtin_backend.value,
                    owner=FailureOwner.builtin_backend.value,
                    error_type="builtin_backend_error",
                    resource_type=ProfileResourceType.wiki_kb.value,
                    resource_key="",
                ) from exc
            return {"results": results}
        kb_slug = str(arguments.get("kb") or arguments.get("kb_slug") or "").strip()
        if not kb_slug:
            raise ValidationError("kb is required")
        if not self.service.governance.is_resource_allowed(
            actor,
            profile_key,
            ProfileResourceType.wiki_kb.value,
            kb_slug,
        ):
            logger.warning(
                "Wiki 资源被拒绝 actor=%s profile=%s kb=%s 原因=%s",
                actor,
                profile_key,
                kb_slug,
                "不在 allow 列表",
            )
            raise capability_failure(
                ValidationError("resource is blocked by profile policy"),
                status=CallLogStatus.blocked.value,
                stage=FailureStage.profile_policy.value,
                owner=FailureOwner.policy.value,
                error_type="profile_policy_blocked",
                resource_type=ProfileResourceType.wiki_kb.value,
                resource_key=kb_slug,
            )
        if tool == "search":
            question = str(arguments.get("question") or arguments.get("query") or "").strip()
            if not question:
                raise ValidationError("question is required")
            logger.info("Wiki search 开始 actor=%s kb=%s", actor, kb_slug)
            try:
                results = await asyncio.to_thread(
                    self.service.search,
                    actor,
                    kb_slug,
                    question,
                    top_k=int(arguments.get("top_k") or 6),
                    profile_key=profile_key,
                )
            except AgentBridgeError:
                raise
            except Exception as exc:
                logger.error("Wiki search 后端失败 actor=%s kb=%s 原因=%s", actor, kb_slug, exc, exc_info=True)
                raise self._backend_error(exc, kb_slug) from exc
            return {
                "kb": kb_slug,
                "results": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in results
                ],
            }
        if tool == "ask":
            question = str(arguments.get("question") or "").strip()
            if not question:
                raise ValidationError("question is required")
            logger.info("Wiki ask 开始 actor=%s kb=%s", actor, kb_slug)
            try:
                answer = await asyncio.to_thread(
                    self.service.ask,
                    actor,
                    kb_slug,
                    question,
                    session_id=arguments.get("session_id"),
                    profile_key=profile_key,
                )
            except AgentBridgeError:
                raise
            except Exception as exc:
                logger.error("Wiki ask 后端失败 actor=%s kb=%s 原因=%s", actor, kb_slug, exc, exc_info=True)
                raise self._backend_error(exc, kb_slug) from exc
            return {"kb": kb_slug, "answer": answer.model_dump() if hasattr(answer, "model_dump") else answer}
        if tool == "get_document":
            doc_slug = str(arguments.get("doc_slug") or "").strip()
            if not doc_slug:
                raise ValidationError("doc_slug is required")
            doc = self.service.get_doc_for_kb(actor, kb_slug, doc_slug, profile_key=profile_key)
            return {"document": doc}
        raise NotFound("tool not found")

    def _backend_error(self, exc: Exception, kb_slug: str) -> Exception:
        return mark_builtin_failure(
            ValidationError(f"Wiki builtin backend failed: {exc}"),
            stage=FailureStage.builtin_backend.value,
            owner=FailureOwner.builtin_backend.value,
            error_type="builtin_backend_error",
            resource_type=ProfileResourceType.wiki_kb.value,
            resource_key=kb_slug,
        )
