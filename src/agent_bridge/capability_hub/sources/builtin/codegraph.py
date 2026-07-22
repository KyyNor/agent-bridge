from __future__ import annotations

import logging
from typing import Any

from agent_bridge.capability_hub.errors import capability_failure
from agent_bridge.capability_hub.sources.builtin.base import BuiltinResourceRef, BuiltinTool, mark_builtin_failure
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage, ProfileResourceType, ToolType
from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.core.domain import NotFound, ValidationError, AgentBridgeError

logger = logging.getLogger(__name__)


EXPLORE_TOOL = "codegraph_explore"


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
                EXPLORE_TOOL,
                "CodeGraph Explore",
                "在已授权代码仓库中进行结构化探索。",
                {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "要访问的代码仓库标识。"},
                        "query": {"type": "string", "description": "要在仓库内执行的查询内容。"},
                    },
                    "required": ["repo", "query"],
                },
                ToolType.search.value,
            ),
        ]

    def resource_from_arguments(self, tool: str, arguments: dict[str, Any]) -> BuiltinResourceRef | None:
        if tool != EXPLORE_TOOL:
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
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if tool != EXPLORE_TOOL:
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
            logger.warning(
                "CodeGraph 资源被拒绝 actor=%s profile=%s repo=%s 原因=%s",
                actor,
                profile_key,
                repo_key,
                "不在 allow 列表",
            )
            raise capability_failure(
                ValidationError("resource is blocked by profile policy"),
                status=CallLogStatus.blocked.value,
                stage=FailureStage.profile_policy.value,
                owner=FailureOwner.policy.value,
                error_type="profile_policy_blocked",
                resource_type=ProfileResourceType.code_repo.value,
                resource_key=repo_key,
            )

        try:
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ValidationError("query is required")
            logger.info("CodeGraph 探索开始 actor=%s repo=%s query=%s", actor, repo_key, query)
            return await self.codegraph.explore(
                actor,
                repo_key,
                query=query,
            )
        except AgentBridgeError:
            raise
        except Exception as exc:
            logger.error("CodeGraph 探索失败 actor=%s repo=%s 原因=%s", actor, repo_key, exc, exc_info=True)
            raise self._backend_error(exc, repo_key) from exc

    def _repo_key(self, arguments: dict[str, Any]) -> str:
        return str(arguments.get("repo") or arguments.get("repo_key") or "").strip()

    def _backend_error(self, exc: Exception, repo_key: str) -> Exception:
        return mark_builtin_failure(
            ValidationError(f"CodeGraph builtin backend failed: {exc}"),
            stage=FailureStage.builtin_backend.value,
            owner=FailureOwner.builtin_backend.value,
            error_type="builtin_backend_error",
            resource_type=ProfileResourceType.code_repo.value,
            resource_key=repo_key,
        )
