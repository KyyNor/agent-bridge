from __future__ import annotations

from typing import Any

from agent_bridge.capabilities.builtin import BuiltinResourceRef, BuiltinTool, mark_builtin_failure
from agent_bridge.capabilities.models import FailureOwner, FailureStage, ProfileResourceType, ToolType
from agent_bridge.capabilities.governance import CapabilityGovernanceService
from agent_bridge.codegraph.service import CodeGraphService
from agent_bridge.core.domain import NotFound, ValidationError, AgentBridgeError


REPO_TOOLS = {"search_code", "get_file", "find_symbol", "repository_overview"}


class CodeGraphBuiltinProvider:
    source_key = "codegraph"
    name = "CodeGraph"
    description = "内置代码仓库结构和代码查询能力"
    tags = ["builtin", "code"]

    def __init__(self, codegraph: CodeGraphService, governance: CapabilityGovernanceService) -> None:
        self.codegraph = codegraph
        self.governance = governance

    def list_resources(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        repos = [repo for repo in self.codegraph.store.list_code_repositories() if repo["status"] == "active"]
        filtered = set(
            self.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.code_repo.value,
                resource_keys=[repo["repo_key"] for repo in repos],
            )
        )
        return [
            {
                "resource_type": ProfileResourceType.code_repo.value,
                "resource_key": repo["repo_key"],
                "name": repo["name"],
            }
            for repo in repos
            if repo["repo_key"] in filtered
        ]

    def list_tools(self, actor: str, profile_key: str | None) -> list[BuiltinTool]:
        return [
            BuiltinTool(
                "find_symbol",
                "CodeGraph Symbol",
                "Find symbol definitions in an allowed repository.",
                {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "symbol": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["repo", "symbol"],
                },
                ToolType.search.value,
            ),
            BuiltinTool(
                "get_file",
                "CodeGraph File",
                "Read a file from an allowed repository.",
                {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["repo", "path"],
                },
                ToolType.detail.value,
            ),
            BuiltinTool(
                "list_repositories",
                "CodeGraph Repositories",
                "List allowed code repositories.",
                {"type": "object", "properties": {}},
                ToolType.overview.value,
            ),
            BuiltinTool(
                "repository_overview",
                "CodeGraph Repository Overview",
                "Show repository status and summary.",
                {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                    },
                    "required": ["repo"],
                },
                ToolType.detail.value,
            ),
            BuiltinTool(
                "search_code",
                "CodeGraph Search",
                "Search code in an allowed repository.",
                {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["repo", "query"],
                },
                ToolType.search.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        if tool not in REPO_TOOLS:
            return None
        repo_key = self._repo_key(arguments)
        if not repo_key:
            return None
        return BuiltinResourceRef(ProfileResourceType.code_repo.value, repo_key)

    async def execute(
        self,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        profile_key: str | None,
    ) -> dict[str, Any]:
        if tool == "list_repositories":
            return {"repositories": self.list_resources(actor, profile_key)}
        if tool not in REPO_TOOLS:
            raise NotFound("tool not found")

        repo_key = self._repo_key(arguments)
        if not repo_key:
            raise ValidationError("repo is required")
        if not self.governance.is_resource_allowed(
            actor,
            profile_key,
            ProfileResourceType.code_repo.value,
            repo_key,
        ):
            raise ValidationError("resource is blocked by profile policy")

        try:
            if tool == "search_code":
                query = str(arguments.get("query") or "").strip()
                if not query:
                    raise ValidationError("query is required")
                return {
                    "matches": self.codegraph.search_code(
                        actor,
                        repo_key,
                        query=query,
                        limit=self._limit(arguments),
                    )
                }
            if tool == "get_file":
                path = str(arguments.get("path") or "").strip()
                if not path:
                    raise ValidationError("path is required")
                return self.codegraph.get_file(actor, repo_key, path)
            if tool == "find_symbol":
                symbol = str(arguments.get("symbol") or "").strip()
                if not symbol:
                    raise ValidationError("symbol is required")
                return {
                    "matches": self.codegraph.find_symbol(
                        actor,
                        repo_key,
                        symbol=symbol,
                        limit=self._limit(arguments),
                    )
                }
            if tool == "repository_overview":
                return self.codegraph.repository_overview(actor, repo_key)
        except AgentBridgeError:
            raise
        except Exception as exc:
            raise self._backend_error(exc, repo_key) from exc

        raise NotFound("tool not found")

    def _repo_key(self, arguments: dict[str, Any]) -> str:
        return str(arguments.get("repo") or arguments.get("repo_key") or "").strip()

    def _limit(self, arguments: dict[str, Any]) -> int:
        raw = arguments.get("limit", 20)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValidationError("limit must be an integer") from None
        return max(1, min(value, 50))

    def _backend_error(self, exc: Exception, repo_key: str) -> Exception:
        return mark_builtin_failure(
            ValidationError(f"CodeGraph builtin backend failed: {exc}"),
            stage=FailureStage.builtin_backend.value,
            owner=FailureOwner.builtin_backend.value,
            error_type="builtin_backend_error",
            resource_type=ProfileResourceType.code_repo.value,
            resource_key=repo_key,
        )
