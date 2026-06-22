from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef, BuiltinTool, mark_builtin_failure
from agent_bridge.capability_hub.models import FailureOwner, FailureStage, ProfileResourceType, ToolType
from agent_bridge.core.domain import NotFound, ValidationError, AgentBridgeError

if TYPE_CHECKING:
    from agent_bridge.app.service import AgentBridgeService


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
        return [
            BuiltinTool(
                "ask",
                "Wiki Ask",
                "Ask a question against an allowed KB.",
                {
                    "type": "object",
                    "properties": {
                        "kb": {"type": "string"},
                        "question": {"type": "string"},
                        "session_id": {"type": "string"},
                    },
                    "required": ["kb", "question"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get_document",
                "Wiki Document",
                "Read document metadata from an allowed KB.",
                {
                    "type": "object",
                    "properties": {
                        "kb": {"type": "string"},
                        "doc_slug": {"type": "string"},
                    },
                    "required": ["kb", "doc_slug"],
                },
                ToolType.detail.value,
            ),
            BuiltinTool(
                "list_kbs",
                "Wiki KB List",
                "List allowed knowledge bases.",
                {"type": "object", "properties": {}},
                ToolType.overview.value,
            ),
            BuiltinTool(
                "search_all",
                "Wiki Search All",
                "Search across all allowed KBs, returning which KBs have matching content.",
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "top_k": {"type": "integer", "default": 6},
                    },
                    "required": ["question"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "search",
                "Wiki Search",
                "Search snippets in an allowed KB.",
                {
                    "type": "object",
                    "properties": {
                        "kb": {"type": "string"},
                        "question": {"type": "string"},
                        "top_k": {"type": "integer", "default": 6},
                    },
                    "required": ["kb", "question"],
                },
                ToolType.search.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        if tool not in {"search", "search_all", "ask", "get_document"}:
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
        if tool not in {"search", "search_all", "ask", "get_document"}:
            raise NotFound("tool not found")
        if tool == "search_all":
            question = str(arguments.get("question") or "").strip()
            if not question:
                raise ValidationError("question is required")
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
            raise ValidationError("resource is blocked by profile policy")
        if tool == "search":
            question = str(arguments.get("question") or arguments.get("query") or "").strip()
            if not question:
                raise ValidationError("question is required")
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
