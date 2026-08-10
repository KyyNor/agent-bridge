"""Capability service composed from MCP registry storage and HTTP client."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import logging
import re
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

from agent_bridge.capability_hub.sources.builtin.base import (
    BuiltinCapabilityProvider,
    BuiltinResourceRef,
    BuiltinTool,
    mark_builtin_failure,
)
from agent_bridge.capability_hub.errors import capability_failure, failure_metadata, with_log_id
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage, McpServiceStatus, SourceType, ToolType
from agent_bridge.capability_hub.governance import CapabilityGovernanceService, monotonic_ms
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.access_control.resources import (
    ScopedResourceType,
    create_scoped_resource_registry,
)
from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.core.json_util import json_loads as _json_loads
from agent_bridge.capability_hub.sources.mcp.http_client import McpHttpClient
from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient
from agent_bridge.capability_hub.sources.openapi.parser import parse_openapi_operations
from agent_bridge.capability_hub.sources.adapters import (
    BuiltinSourceAdapter,
    CapabilitySourceRegistry,
    McpSourceAdapter,
    OpenApiSourceAdapter,
)
from agent_bridge.capability_hub.gateway.top_level_tools import (
    top_level_mcp_tools,
    top_level_tool_by_name,
    top_level_tool_for_capability,
)
from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.storage.sqlite import SQLiteStore


SERVICE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
READONLY_TOOL_TYPES = {ToolType.overview.value, ToolType.search.value, ToolType.detail.value}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).lower()


def _root_cause_message(exc: BaseException) -> str:
    """Flatten anyio/asyncio ExceptionGroup wrappers so logs show the real cause.

    ``streamablehttp_client`` raises ``BaseExceptionGroup`` ("unhandled errors in a
    taskgroup (N sub-exceptions)") on connection/transport failures, and ``str()``
    on the group hides the leaf exception. Unwrap to the leaf cause(s).
    """
    leaves: list[str] = []
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        else:
            leaves.append(f"{type(current).__name__}: {current}")
    if not leaves:
        return str(exc)
    if len(leaves) == 1:
        return leaves[0]
    return f"{len(leaves)} errors: " + "; ".join(leaves)


def _schema_example(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {name: _example_value(definition) for name, definition in properties.items()}


def _example_value(definition: Any) -> Any:
    if not isinstance(definition, dict):
        return None
    if "default" in definition:
        return definition["default"]
    if "example" in definition:
        return definition["example"]

    value_type = definition.get("type")
    if value_type == "string":
        return "<string>"
    if value_type in {"integer", "number"}:
        return 0
    if value_type == "boolean":
        return False
    if value_type == "array":
        return []
    if value_type == "object":
        return {}
    return None


def _compact_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _similarity_score(query: str, candidate: str) -> float:
    query_lower = query.strip().lower()
    candidate_lower = candidate.strip().lower()
    if not query_lower or not candidate_lower:
        return 0.0
    compact_query = _compact_match_key(query_lower)
    compact_candidate = _compact_match_key(candidate_lower)
    score = SequenceMatcher(None, query_lower, candidate_lower).ratio()
    if compact_query and compact_candidate:
        score = max(score, SequenceMatcher(None, compact_query, compact_candidate).ratio())
    if candidate_lower.startswith(query_lower) or (compact_query and compact_candidate.startswith(compact_query)):
        score = max(score, 0.98)
    elif query_lower in candidate_lower or (compact_query and compact_query in compact_candidate):
        score = max(score, 0.92)
    return score


def _top_similar_names(query: str, candidates: list[str], *, limit: int = 3, cutoff: float = 0.45) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for candidate in dict.fromkeys(candidates):
        score = _similarity_score(query, candidate)
        if score >= cutoff:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _, candidate in ranked[:limit]]


def _friendly_not_found_message(
    kind: str,
    noun: str,
    suggestions: list[str],
    *,
    search_command: str,
) -> str:
    base = f"{kind}_not_found"
    message = base
    if suggestions:
        message += f"，可用的类似{noun}有：[{', '.join(suggestions)}]"
    if noun == "工具":
        detail = "该服务下工具及参数"
    else:
        detail = "当前可用服务及工具"
    return f"{message}。请使用 {search_command} 查看{detail}的详细说明。"


def _attach_log_id(exc: Exception, log_id: str) -> Exception:
    return with_log_id(exc, log_id)


def _mark_call_log_status(exc: Exception, status: str) -> Exception:
    return capability_failure(exc, status=status)


def _call_log_status(exc: Exception) -> str:
    return failure_metadata(exc).status


def _failure_stage(exc: Exception) -> str:
    return failure_metadata(exc).stage


def _failure_owner(exc: Exception) -> str:
    return failure_metadata(exc).owner


def _error_type(exc: Exception) -> str:
    return failure_metadata(exc).error_type


class CapabilityService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        mcp_client: McpHttpClient | None = None,
        openapi_client: OpenApiHttpClient | None = None,
        governance: CapabilityGovernanceService | None = None,
        access: AccessControlService | None = None,
    ) -> None:
        self.store = store
        self.admins = admins
        self.mcp_client = mcp_client or McpHttpClient()
        self.openapi_client = openapi_client or OpenApiHttpClient()
        self.governance = governance or CapabilityGovernanceService(store=store, admins=admins)
        self._default_visibility = "group" if access is not None else "shared"
        self.access = access or AccessControlService(
            store.access_control,
            admins,
            create_scoped_resource_registry(store),
        )
        if access is None:
            self.access.bootstrap_admin_memberships()
        self.builtin_providers: dict[str, BuiltinCapabilityProvider] = {}
        builtin_source = BuiltinSourceAdapter(self)
        mcp_source = McpSourceAdapter(self)
        openapi_source = OpenApiSourceAdapter(self)
        self.source_registry = CapabilitySourceRegistry(
            [builtin_source, mcp_source, openapi_source],
            fallback=mcp_source,
        )

    def _mcp_timeout_seconds(self) -> float:
        config = self.store.get_sync_config()
        return float(config.get("mcp_timeout_seconds") or DEFAULT_MCP_TIMEOUT_SECONDS)

    def register_builtin_provider(self, provider: BuiltinCapabilityProvider) -> None:
        self.builtin_providers[provider.source_key] = provider

    def list_top_level_mcp_tools(self, actor: str) -> list[dict[str, Any]]:
        """列出可在系统管理中临时关闭的 MetaMCP 顶层工具。"""

        require_admin_user(actor, self.admins)
        settings = {item["tool_name"]: item for item in self.store.list_metamcp_tool_settings()}
        return [
            {
                "name": spec.name,
                "title": spec.title,
                "description": spec.description,
                "kind": spec.kind,
                "service_key": spec.service_key,
                "tool_name": spec.tool_name,
                "status": settings.get(spec.name, {}).get("status", McpServiceStatus.enabled.value),
                "updated_by": settings.get(spec.name, {}).get("updated_by"),
                "updated_at": settings.get(spec.name, {}).get("updated_at"),
            }
            for spec in top_level_mcp_tools()
        ]

    def set_top_level_mcp_tool_status(self, actor: str, tool_name: str, status: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if top_level_tool_by_name(tool_name) is None:
            raise NotFound("top-level MCP tool not found")
        if status not in {McpServiceStatus.enabled.value, McpServiceStatus.disabled.value}:
            raise ValidationError("invalid top-level MCP tool status")
        self.store.upsert_metamcp_tool_status(tool_name, status, actor)
        logger.info("顶层 MCP 工具状态已更新 tool=%s status=%s actor=%s", tool_name, status, actor)
        return next(item for item in self.list_top_level_mcp_tools(actor) if item["name"] == tool_name)

    def is_top_level_mcp_tool_enabled(self, tool_name: str) -> bool:
        settings = {item["tool_name"]: item["status"] for item in self.store.list_metamcp_tool_settings()}
        return settings.get(tool_name, McpServiceStatus.enabled.value) == McpServiceStatus.enabled.value

    def is_capability_tool_enabled(self, service_key: str, tool_name: str) -> bool:
        top_level_name = top_level_tool_for_capability(service_key, tool_name)
        return top_level_name is None or self.is_top_level_mcp_tool_enabled(top_level_name)

    def require_capability_tool_enabled(self, service_key: str, tool_name: str) -> None:
        top_level_name = top_level_tool_for_capability(service_key, tool_name)
        if top_level_name is None or self.is_top_level_mcp_tool_enabled(top_level_name):
            return
        raise mark_builtin_failure(
            ValidationError(f"顶层 MCP 工具已临时关闭：{top_level_name}"),
            stage=FailureStage.capability_registry.value,
            owner=FailureOwner.policy.value,
            error_type="top_level_mcp_tool_disabled",
        )

    def require_top_level_mcp_tool_enabled(self, tool_name: str) -> None:
        if self.is_top_level_mcp_tool_enabled(tool_name):
            return
        raise mark_builtin_failure(
            ValidationError(f"顶层 MCP 工具已临时关闭：{tool_name}"),
            stage=FailureStage.capability_registry.value,
            owner=FailureOwner.policy.value,
            error_type="top_level_mcp_tool_disabled",
        )

    def invoke_logged_tool(
        self,
        *,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None,
        source_key: str | None,
        tool_name: str,
        request: dict[str, Any],
        handler: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """带审计的工作流辅助工具/脚本执行入口。

        让 artifacts_search / workflow_get_task / workflow_set_task / workflow_run_log / run_script
        等也走和 ``execute`` 相同的 ``log_tool_call`` 审计路径。成功写一行
        success，失败捕获异常、写错误行、把 log_id 缝进异常再抛出。
        """
        logger.info(
            "invoke_logged_tool 入口 actor=%s profile=%s source=%s tool=%s",
            actor,
            profile_key,
            source_key,
            tool_name,
        )
        started = monotonic_ms()
        try:
            result = handler()
            self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint=entrypoint,
                source_type=source_type,
                source_key=source_key,
                tool_name=tool_name,
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=monotonic_ms() - started,
            )
            logger.info(
                "invoke_logged_tool 完成 source=%s tool=%s 耗时=%dms",
                source_key,
                tool_name,
                monotonic_ms() - started,
            )
            return result
        except Exception as exc:
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint=entrypoint,
                source_type=source_type,
                source_key=source_key,
                tool_name=tool_name,
                request=request,
                response={"error": str(exc)},
                status=_call_log_status(exc),
                error_message=str(exc),
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
                duration_ms=monotonic_ms() - started,
            )
            enriched = _attach_log_id(exc, log["log_id"])
            logger.warning(
                "invoke_logged_tool 失败 source=%s tool=%s 耗时=%dms log_id=%s 原因=%s",
                source_key,
                tool_name,
                monotonic_ms() - started,
                log["log_id"],
                exc,
            )
            raise enriched from exc

    def register_service(
        self,
        actor: str,
        service_key: str,
        name: str,
        endpoint_url: str,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
        *,
        visibility: str | None = None,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._validate_service_key(service_key)
        if service_key in self.builtin_providers:
            raise ValidationError("service_key is reserved for built-in capability")
        if self.store.get_openapi_service(service_key) is not None:
            raise ValidationError("service_key is already used by an OpenAPI service")
        existing = self.store.get_mcp_service(service_key)
        if existing is not None:
            self.access.require_resource_write(
                actor=actor,
                resource_type=ScopedResourceType.mcp_service,
                resource_key=service_key,
            )
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._service_edit_snapshot(existing) if existing else None,
            resource_type="mcp_service",
            resource_key=service_key,
            actor=actor,
        )
        if headers is None:
            headers = _json_loads(existing.get("headers_json"), {}) if existing is not None else {}
        if existing is None:
            scope = self.access.new_resource_scope(
                actor=actor,
                visibility=visibility or self._default_visibility,
            )
            service = self.store.create_mcp_service(
                service_key=service_key,
                name=name,
                endpoint_url=endpoint_url,
                headers=headers,
                description=description,
                tags=tags,
                created_by=actor,
                owner_group_key=scope.owner_group_key,
                visibility=scope.visibility.value,
            )
        else:
            service = self.store.update_mcp_service(
                service_key,
                name=name,
                endpoint_url=endpoint_url,
                headers=headers,
                description=description,
                tags=tags,
                visibility=visibility,
            )
        return self._service_payload(service)

    def register_openapi_service(
        self,
        actor: str,
        service_key: str,
        name: str,
        base_url: str,
        spec_url: str,
        spec_content: str,
        auth_config: dict[str, Any] | None,
        headers: dict[str, Any] | None,
        description: str,
        tags: list[str],
        *,
        visibility: str | None = None,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._validate_service_key(service_key)
        if service_key in self.builtin_providers or self.store.get_mcp_service(service_key) is not None:
            raise ValidationError("service_key is already used by another capability source")
        if not base_url.strip():
            raise ValidationError("base_url is required")
        existing = self.store.get_openapi_service(service_key)
        if existing is not None:
            self.access.require_resource_write(
                actor=actor,
                resource_type=ScopedResourceType.openapi_service,
                resource_key=service_key,
            )
        require_edit_token(
            expected=expected_edit_token,
            current_snapshot=self._openapi_service_edit_snapshot(existing) if existing else None,
            resource_type="openapi_service",
            resource_key=service_key,
            actor=actor,
        )
        if auth_config is None:
            auth_config = _json_loads(existing.get("auth_config_json"), {}) if existing is not None else {}
        if headers is None:
            headers = _json_loads(existing.get("headers_json"), {}) if existing is not None else {}
        if existing is None:
            scope = self.access.new_resource_scope(
                actor=actor,
                visibility=visibility or self._default_visibility,
            )
            service = self.store.create_openapi_service(
                service_key=service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config,
                headers=headers,
                description=description,
                tags=tags,
                created_by=actor,
                owner_group_key=scope.owner_group_key,
                visibility=scope.visibility.value,
            )
        else:
            service = self.store.update_openapi_service(
                service_key,
                name=name,
                base_url=base_url,
                spec_url=spec_url,
                spec_content=spec_content,
                auth_config=auth_config,
                headers=headers,
                description=description,
                tags=tags,
                visibility=visibility,
            )
        return self._openapi_service_payload(service)

    def list_openapi_services(self, actor: str) -> list[dict[str, Any]]:
        return [
            self._openapi_service_payload(service, redact_secrets=True)
            for service in self.access.visible_resources(
                actor=actor,
                resource_type=ScopedResourceType.openapi_service,
            )
        ]

    def list_openapi_service_summaries(self, actor: str) -> list[dict[str, Any]]:
        visible = {
            item["service_key"]
            for item in self.access.visible_resources(
                actor=actor,
                resource_type=ScopedResourceType.openapi_service,
            )
        }
        return [self._openapi_service_summary_payload(service) for service in self.store.list_openapi_service_summaries() if service["service_key"] in visible]

    def get_openapi_service(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.access.require_resource_read(
            actor=actor,
            resource_type=ScopedResourceType.openapi_service,
            resource_key=service_key,
        )
        return self._openapi_service_payload(service, redact_secrets=True)

    def set_openapi_service_status(self, actor: str, service_key: str, status: McpServiceStatus | str) -> dict[str, Any]:
        try:
            next_status = McpServiceStatus(status)
        except ValueError as exc:
            raise ValidationError("invalid service status") from exc
        if self.store.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        self.store.update_openapi_service_status(service_key, next_status)
        updated = self.store.get_openapi_service(service_key)
        if updated is None:
            raise NotFound("service not found")
        return self._openapi_service_payload(updated)

    def import_openapi_operations(
        self,
        actor: str,
        service_key: str,
        *,
        spec_content: str | None = None,
    ) -> dict[str, Any]:
        service = self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        try:
            content = spec_content if spec_content is not None else str(service.get("spec_content") or "")
            if not content.strip():
                spec_url = str(service.get("spec_url") or "").strip()
                if not spec_url:
                    raise ValidationError("OpenAPI spec content or spec_url is required")
                response = httpx.get(spec_url, headers=_json_loads(service.get("headers_json"), {}), timeout=30.0)
                response.raise_for_status()
                content = response.text
            operations = parse_openapi_operations(content)
            self.store.mark_openapi_service_import(service_key, success=True)
            return {"service_key": service_key, "operations": operations}
        except Exception as exc:
            self.store.mark_openapi_service_import(service_key, success=False, error=str(exc))
            raise

    def upsert_openapi_tool(self, actor: str, service_key: str, tool: dict[str, Any]) -> dict[str, Any]:
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        tool_type = self._validate_tool_type(tool.get("tool_type"))
        saved = self.store.upsert_openapi_tool(
            service_key=service_key,
            tool_name=str(tool.get("tool_name") or "").strip(),
            operation_id=str(tool.get("operation_id") or ""),
            method=str(tool.get("method") or "GET").upper(),
            path=str(tool.get("path") or ""),
            display_name=str(tool.get("display_name") or tool.get("tool_name") or ""),
            description=str(tool.get("description") or ""),
            input_schema=tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
            request_mapping=tool.get("request_mapping") if isinstance(tool.get("request_mapping"), dict) else {},
            response_schema=tool.get("response_schema") if isinstance(tool.get("response_schema"), dict) else {},
            tool_type=tool_type,
            tags=[str(tag) for tag in tool.get("tags", [])] if isinstance(tool.get("tags"), list) else [],
            examples=tool.get("examples") if isinstance(tool.get("examples"), list) else [],
        )
        return self._openapi_tool_payload(saved)

    def list_openapi_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        self._require_enabled_openapi_service(service_key, actor=actor)
        return [self._openapi_tool_payload(tool) for tool in self._active_openapi_tools(service_key)]

    def get_openapi_tool(self, actor: str, service_key: str, tool_name: str) -> dict[str, Any]:
        self._require_enabled_openapi_service(service_key, actor=actor)
        tool = self.store.get_openapi_tool(service_key, tool_name)
        if tool is None or tool.get("status") != "active":
            raise NotFound("tool not found")
        return self._openapi_tool_payload(tool)

    def set_openapi_tool_type(self, actor: str, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        next_tool_type = self._validate_tool_type(tool_type)
        if self.store.get_openapi_service(service_key) is None:
            raise NotFound("service not found")
        tool = self.store.get_openapi_tool(service_key, tool_name)
        if tool is None:
            raise NotFound("tool not found")
        return self._openapi_tool_payload(self.store.update_openapi_tool_type(service_key, tool_name, next_tool_type))

    def delete_openapi_tool(self, actor: str, service_key: str, tool_name: str) -> None:
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        if self.store.get_openapi_tool(service_key, tool_name) is None:
            raise NotFound("tool not found")
        self.store.delete_openapi_tool(service_key, tool_name)

    def delete_mcp_service(self, actor: str, service_key: str) -> None:
        """硬删除一个 MCP 服务，并清理能力平面里的软关联规则。

        删除顺序：先校验权限与存在性，再清理治理软关联（source/pin 规则无外键），
        最后删除服务行 —— mcp_tools 由外键 ON DELETE CASCADE 自动清除。
        """
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.mcp_service, resource_key=service_key)
        self._purge_service_governance_rules(SourceType.mcp_service.value, service_key)
        self.store.delete_mcp_service(service_key)
        logger.info("已删除 MCP 能力服务 %s", service_key)

    def delete_openapi_service(self, actor: str, service_key: str) -> None:
        """硬删除一个 OpenAPI 服务，并清理能力平面里的软关联规则。"""
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.openapi_service, resource_key=service_key)
        self._purge_service_governance_rules(SourceType.openapi_service.value, service_key)
        self.store.delete_openapi_service(service_key)
        logger.info("已删除 OpenAPI 能力服务 %s", service_key)

    def _purge_service_governance_rules(self, source_type: str, service_key: str) -> None:
        """清理某个能力来源在所有能力平面上的软关联规则（无外键，需手动删）。"""
        self.store.delete_source_rules_by_key(source_type=source_type, source_key=service_key)
        self.store.delete_pin_rules_by_service(service_key=service_key)

    def list_services(self, actor: str) -> list[dict[str, Any]]:
        return [self._service_payload(service, redact_headers=True) for service in self.access.visible_resources(actor=actor, resource_type=ScopedResourceType.mcp_service)]

    def list_service_summaries(self, actor: str) -> list[dict[str, Any]]:
        visible = {item["service_key"] for item in self.access.visible_resources(actor=actor, resource_type=ScopedResourceType.mcp_service)}
        return [self._service_summary_payload(service) for service in self.store.list_mcp_service_summaries() if service["service_key"] in visible]

    def get_service(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.access.require_resource_read(actor=actor, resource_type=ScopedResourceType.mcp_service, resource_key=service_key)
        return self._service_payload(service, redact_headers=True)

    def set_service_status(self, actor: str, service_key: str, status: McpServiceStatus | str) -> dict[str, Any]:
        try:
            next_status = McpServiceStatus(status)
        except ValueError as exc:
            raise ValidationError("invalid service status") from exc
        service = self.store.get_mcp_service(service_key)
        if service is None:
            raise NotFound("service not found")
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.mcp_service, resource_key=service_key)
        self.store.update_mcp_service_status(service_key, next_status)
        updated = self.store.get_mcp_service(service_key)
        if updated is None:
            raise NotFound("service not found")
        return self._service_payload(updated)

    def set_tool_type(self, actor: str, service_key: str, tool_name: str, tool_type: ToolType | str) -> dict[str, Any]:
        self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.mcp_service, resource_key=service_key)
        try:
            next_tool_type = ToolType(tool_type)
        except ValueError as exc:
            raise ValidationError("invalid tool type") from exc
        if self.store.get_mcp_service(service_key) is None:
            raise NotFound("service not found")
        tool = self.store.get_mcp_tool(service_key, tool_name)
        if tool is None:
            raise NotFound("tool not found")
        return self._tool_payload(self.store.update_mcp_tool_type(service_key, tool_name, next_tool_type))

    async def sync_tools(self, actor: str, service_key: str) -> dict[str, Any]:
        service = self.access.require_resource_write(actor=actor, resource_type=ScopedResourceType.mcp_service, resource_key=service_key)
        headers = _json_loads(service.get("headers_json"), {})
        try:
            tools = await self.mcp_client.list_tools(
                service["endpoint_url"],
                headers,
                timeout=self._mcp_timeout_seconds(),
            )
            active_tool_names: set[str] = set()
            for tool in tools:
                normalized = self._normalize_synced_tool(tool)
                active_tool_names.add(normalized["tool_name"])
                self.store.upsert_mcp_tool(
                    service_key=service_key,
                    tool_name=normalized["tool_name"],
                    display_name=normalized["display_name"],
                    description=normalized["description"],
                    input_schema=normalized["input_schema"],
                    tool_type=normalized["tool_type"],
                    tags=normalized["tags"],
                    examples=normalized["examples"],
                )
            self.store.deactivate_missing_mcp_tools(service_key, active_tool_names)
            self.store.mark_mcp_service_sync(service_key, success=True)
            return {"service_key": service_key, "tool_count": len(tools)}
        except Exception as exc:
            self.store.mark_mcp_service_sync(service_key, success=False, error=str(exc))
            raise ValidationError(f"MCP tool sync failed: {exc}") from exc

    def list_tools(self, actor: str, service_key: str) -> list[dict[str, Any]]:
        self._require_enabled_service(service_key, actor=actor)
        return [self._tool_payload(tool) for tool in self._active_tools(service_key)]

    def get_tool(self, actor: str, service_key: str, tool_name: str) -> dict[str, Any]:
        self._require_enabled_service(service_key, actor=actor)
        tool = self.store.get_mcp_tool(service_key, tool_name)
        if tool is None or tool.get("status") != "active":
            raise NotFound("tool not found")
        return self._tool_payload(tool)

    def list_tool_summaries(
        self,
        actor: str,
        *,
        source_type: str | None = None,
        service_key: str | None = None,
        tool_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        visible_mcp = {
            item["service_key"]
            for item in self.access.visible_resources(
                actor=actor, resource_type=ScopedResourceType.mcp_service
            )
        }
        visible_openapi = {
            item["service_key"]
            for item in self.access.visible_resources(
                actor=actor, resource_type=ScopedResourceType.openapi_service
            )
        }
        all_items: list[dict[str, Any]] = []
        raw_offset = 0
        while True:
            page = self.store.list_tool_summaries(
                source_type=source_type,
                service_key=service_key,
                tool_type=tool_type,
                query=query,
                limit=200,
                offset=raw_offset,
            )
            all_items.extend(page["items"])
            raw_offset += len(page["items"])
            if raw_offset >= page["total"] or not page["items"]:
                break
        visible_items = [
            item
            for item in all_items
            if (
                item["service_key"] in visible_mcp
                if item["source_type"] == SourceType.mcp_service.value
                else item["service_key"] in visible_openapi
            )
        ]
        bounded_limit = min(max(int(limit), 1), 200)
        bounded_offset = max(int(offset), 0)
        counts: dict[str, int] = {}
        for item in visible_items:
            counts[item["tool_type"]] = counts.get(item["tool_type"], 0) + 1
        return {
            "items": visible_items[bounded_offset : bounded_offset + bounded_limit],
            "total": len(visible_items),
            "limit": bounded_limit,
            "offset": bounded_offset,
            "counts": counts,
        }

    def pinned_tool_specs(self, actor: str, profile_key: str | None) -> list[dict[str, Any]]:
        if profile_key is None:
            return []
        preview = self.governance.profile_pin_preview(actor, profile_key)
        specs = []
        for item in preview.get("tools", []):
            resource_type = (
                ScopedResourceType.openapi_service
                if item.get("source") == SourceType.openapi_service.value
                else ScopedResourceType.mcp_service
            )
            record = self.access.get_resource(resource_type, item["service_key"])
            if not self.access.can_read(actor=actor, scope=ResourceScope.from_record(record)):
                continue
            input_schema = item.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = {}
            specs.append(
                {
                    **item,
                    "input_schema": input_schema,
                    "description": (
                        f"Direct pinned Agent Bridge tool for service {item['service_name']} "
                        f"({item['service_key']}), tool {item['tool_name']}, level {item['tool_type']}, "
                        f"source {item['source']}. Use search(path='{item['service_key']}') "
                        "to inspect the full service directory."
                    ),
                }
            )
        return specs

    def search(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int = 20,
        profile_key: str | None = None,
    ) -> dict[str, Any]:
        started = monotonic_ms()
        source_key = (path or "").strip("/") or None
        request = {"path": path, "query": query, "limit": limit, "profile_key": profile_key}
        try:
            result = self._search_without_log(actor, path, query, limit, profile_key)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=source_key,
                tool_name="search",
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_search",
                source_type=None,
                source_key=source_key,
                tool_name="search",
                request=request,
                response={"error": str(exc)},
                status=CallLogStatus.error.value,
                error_message=str(exc),
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
                duration_ms=monotonic_ms() - started,
            )
            raise _attach_log_id(exc, log["log_id"]) from exc

    def _search_without_log(
        self,
        actor: str,
        path: str | None,
        query: str | None,
        limit: int,
        profile_key: str | None,
    ) -> dict[str, Any]:
        """实际的搜索逻辑（不带审计写库，由 ``search`` 包一层再写 log）。

        来源解析、策略校验和工具投影委托给 ``CapabilitySourceRegistry``；应用层
        不感知 Builtin、MCP、OpenAPI 的具体分支。
        """
        normalized_path = (path or "").strip("/")
        if normalized_path == "":
            items = self._root_search_items(actor=actor, profile_key=profile_key)
            response_path = "/"
        else:
            source = self.source_registry.resolve(normalized_path)
            items = source.search_items(actor, normalized_path, profile_key)
            response_path = normalized_path

        if query:
            needle = query.lower()
            items = [item for item in items if needle in _json_text(item)]
        logger.debug("搜索完成 path=%s 结果数=%d", response_path, len(items))
        return {"path": response_path, "items": items[:limit]}

    async def execute(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None = None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """能力执行的统一入口（经 MetaMCP gateway ``execute`` 工具暴露给 agent）。

        来源解析与执行委托给注册表中的 adapter；本方法只统一编排审计记录。
        """
        started = monotonic_ms()
        request = {"service": service, "tool_name": tool_name, "params": params, "profile_key": profile_key}
        source = self.source_registry.resolve(service)
        source_type = source.source_type
        resource_type = None
        resource_key = None
        try:
            execution = await source.execute(
                actor,
                service,
                tool_name,
                params,
                profile_key,
                workflow_context,
            )
            result = execution.result
            resource_type, resource_key = self._builtin_resource_tuple(execution.resource)
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=source_type,
                source_key=service,
                tool_name=tool_name,
                request=request,
                response=result,
                status=CallLogStatus.success.value,
                error_message=None,
                resource_type=resource_type,
                resource_key=resource_key,
                duration_ms=monotonic_ms() - started,
            )
            return {**result, "log_id": log["log_id"]}
        except Exception as exc:
            failure = failure_metadata(exc)
            resource_type = failure.resource_type or resource_type
            resource_key = failure.resource_key or resource_key
            log = self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="metamcp_execute",
                source_type=source_type,
                source_key=service,
                tool_name=tool_name,
                request=request,
                response={"error": str(exc)},
                status=_call_log_status(exc),
                error_message=str(exc),
                failure_stage=_failure_stage(exc),
                failure_owner=_failure_owner(exc),
                error_type=_error_type(exc),
                resource_type=resource_type,
                resource_key=resource_key,
                duration_ms=monotonic_ms() - started,
            )
            enriched = _attach_log_id(exc, log["log_id"])
            logger.warning(
                "能力执行失败 actor=%s service=%s tool=%s 耗时=%dms log_id=%s 原因=%s",
                actor,
                service,
                tool_name,
                monotonic_ms() - started,
                log["log_id"],
                exc,
            )
            raise enriched from exc

    def _builtin_resource_tuple(self, resource: BuiltinResourceRef | None) -> tuple[str | None, str | None]:
        if resource is None:
            return None, None
        return resource.resource_type, resource.resource_key

    async def _execute_without_log(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
    ) -> dict[str, Any]:
        service_payload = self._require_enabled_service(
            service,
            actor=actor,
            profile_key=profile_key,
            friendly_not_found=True,
        )
        tool_payload = self.store.get_mcp_tool(service, tool_name)
        self._assert_tool_executable(
            tool_payload,
            actor=actor,
            service_key=service,
            tool_name=tool_name,
            profile_key=profile_key,
            source_type=SourceType.mcp_service.value,
            friendly_not_found=True,
        )

        headers = _json_loads(service_payload.get("headers_json"), {})
        try:
            result = await self.mcp_client.call_tool(
                service_payload["endpoint_url"],
                headers,
                tool_name,
                params,
                timeout=self._mcp_timeout_seconds(),
            )
        except Exception as exc:
            logger.error(
                "MCP 传输失败 service=%s tool=%s 原因=%s",
                service,
                tool_name,
                _root_cause_message(exc),
                exc_info=True,
            )
            raise mark_builtin_failure(
                ValidationError(f"MCP tool execution failed: {_root_cause_message(exc)}"),
                stage=FailureStage.mcp_transport.value,
                owner=FailureOwner.upstream_mcp.value,
                error_type="mcp_transport_error",
            ) from exc
        return {
            "success": not bool(result.get("is_error")) if isinstance(result, dict) else True,
            "result": result,
        }

    def _execute_openapi_without_log(
        self,
        actor: str,
        service: str,
        tool_name: str,
        params: dict[str, Any],
        profile_key: str | None,
    ) -> dict[str, Any]:
        service_payload = self._require_enabled_openapi_service(
            service,
            actor=actor,
            profile_key=profile_key,
            friendly_not_found=True,
        )
        tool_payload = self.store.get_openapi_tool(service, tool_name)
        self._assert_tool_executable(
            tool_payload,
            actor=actor,
            service_key=service,
            tool_name=tool_name,
            profile_key=profile_key,
            source_type=SourceType.openapi_service.value,
            friendly_not_found=True,
        )
        try:
            result = self.openapi_client.call_tool(
                self._openapi_service_payload(service_payload),
                self._openapi_tool_payload(tool_payload),
                params,
            )
        except Exception as exc:
            logger.error(
                "OpenAPI 传输失败 service=%s tool=%s 原因=%s",
                service,
                tool_name,
                exc,
                exc_info=True,
            )
            raise mark_builtin_failure(
                ValidationError(f"OpenAPI tool execution failed: {exc}"),
                stage=FailureStage.openapi_transport.value,
                owner=FailureOwner.upstream_openapi.value,
                error_type="openapi_transport_error",
            ) from exc
        return {"success": True, "result": result}

    def _require_enabled(
        self,
        service_key: str,
        getter: Callable[[str], dict[str, Any] | None],
        disabled_message: str,
        *,
        actor: str | None = None,
        profile_key: str | None = None,
        friendly_not_found: bool = False,
    ) -> dict[str, Any]:
        service = getter(service_key)
        if service is None:
            message = "service not found"
            if friendly_not_found and actor is not None:
                message = _friendly_not_found_message(
                    "service",
                    "服务",
                    self._similar_service_names(actor, service_key, profile_key),
                    search_command="search()",
                )
            raise mark_builtin_failure(
                NotFound(message),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if service["status"] != McpServiceStatus.enabled.value:
            raise mark_builtin_failure(
                ValidationError(disabled_message),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        return service

    def _require_enabled_service(
        self,
        service_key: str,
        *,
        actor: str | None = None,
        profile_key: str | None = None,
        friendly_not_found: bool = False,
    ) -> dict[str, Any]:
        service = self._require_enabled(
            service_key,
            self.store.get_mcp_service,
            "MCP service is not enabled",
            actor=actor,
            profile_key=profile_key,
            friendly_not_found=friendly_not_found,
        )
        if actor is not None:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(service))
        return service

    def _require_enabled_openapi_service(
        self,
        service_key: str,
        *,
        actor: str | None = None,
        profile_key: str | None = None,
        friendly_not_found: bool = False,
    ) -> dict[str, Any]:
        service = self._require_enabled(
            service_key,
            self.store.get_openapi_service,
            "OpenAPI service is not enabled",
            actor=actor,
            profile_key=profile_key,
            friendly_not_found=friendly_not_found,
        )
        if actor is not None:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(service))
        return service

    def _assert_tool_executable(
        self,
        tool_payload: dict[str, Any] | None,
        *,
        actor: str | None = None,
        service_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        source_type: str = SourceType.mcp_service.value,
        friendly_not_found: bool = False,
    ) -> None:
        if tool_payload is None or tool_payload.get("status") != "active":
            message = "tool not found"
            if friendly_not_found and actor is not None and service_key is not None and tool_name is not None:
                message = _friendly_not_found_message(
                    "tool",
                    "工具",
                    self._similar_tool_names(actor, service_key, tool_name, profile_key, source_type),
                    search_command=f"search(path='{service_key}')",
                )
            raise mark_builtin_failure(
                NotFound(message),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        if tool_payload["tool_type"] in READONLY_TOOL_TYPES:
            return
        if tool_payload["tool_type"] == ToolType.unconfigured.value:
            raise mark_builtin_failure(
                _mark_call_log_status(
                    ValidationError("工具类型未配置，请联系管理员在 Agent Bridge 中配置工具类型"),
                    CallLogStatus.blocked.value,
                ),
                stage=FailureStage.capability_registry.value,
                owner=FailureOwner.platform.value,
                error_type="capability_registry_error",
            )
        raise mark_builtin_failure(
            _mark_call_log_status(
                ValidationError("tool type is not executable"),
                CallLogStatus.blocked.value,
            ),
            stage=FailureStage.capability_registry.value,
            owner=FailureOwner.platform.value,
            error_type="capability_registry_error",
        )

    def _root_search_items(self, *, actor: str, profile_key: str | None = None) -> list[dict[str, Any]]:
        return self.source_registry.root_items(actor, profile_key)

    def _similar_service_names(self, actor: str, service_key: str, profile_key: str | None) -> list[str]:
        visible_names = [
            str(item["service"])
            for item in self.source_registry.root_items(actor, profile_key)
            if item.get("service")
        ]
        return _top_similar_names(service_key, visible_names)

    def _similar_tool_names(
        self,
        actor: str,
        service_key: str,
        tool_name: str,
        profile_key: str | None,
        source_type: str,
    ) -> list[str]:
        source = self.source_registry.resolve(service_key)
        candidates = source.tool_names(actor, service_key, profile_key)
        return _top_similar_names(tool_name, candidates)

    def _active_tools(self, service_key: str) -> list[dict[str, Any]]:
        return [tool for tool in self.store.list_mcp_tools(service_key) if tool.get("status") == "active"]

    def _active_openapi_tools(self, service_key: str) -> list[dict[str, Any]]:
        return [tool for tool in self.store.list_openapi_tools(service_key) if tool.get("status") == "active"]

    def _service_payload(self, service: dict[str, Any], *, redact_headers: bool = False) -> dict[str, Any]:
        payload = attach_edit_token(dict(service), self._service_edit_snapshot(service))
        headers = _json_loads(payload.pop("headers_json", None), {})
        if redact_headers:
            headers = {key: "***" if value else value for key, value in headers.items()}
        payload["headers"] = headers
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        return payload

    def _service_summary_payload(self, service: dict[str, Any]) -> dict[str, Any]:
        payload = dict(service)
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        payload["source_type"] = SourceType.mcp_service.value
        payload["tool_count"] = int(payload.get("tool_count") or 0)
        return payload

    def _tool_payload(self, tool: dict[str, Any]) -> dict[str, Any]:
        input_schema = _json_loads(tool.get("input_schema_json"), {})
        examples = _json_loads(tool.get("examples_json"), [])
        payload = dict(tool)
        payload.pop("input_schema_json", None)
        payload.pop("tags_json", None)
        payload.pop("examples_json", None)
        return {
            **payload,
            "service": tool["service_key"],
            "tool": tool["tool_name"],
            "name": tool["display_name"],
            "input_schema": input_schema,
            "tool_type": tool["tool_type"],
            "tags": _json_loads(tool.get("tags_json"), []),
            "examples": examples,
            "execute_example": examples[0] if examples else _schema_example(input_schema),
            "executable": tool["tool_type"] in READONLY_TOOL_TYPES,
        }

    def _openapi_service_payload(self, service: dict[str, Any], *, redact_secrets: bool = False) -> dict[str, Any]:
        payload = attach_edit_token(
            dict(service),
            self._openapi_service_edit_snapshot(service),
        )
        headers = _json_loads(payload.pop("headers_json", None), {})
        auth_config = _json_loads(payload.pop("auth_config_json", None), {})
        if redact_secrets:
            headers = {key: "***" if value else value for key, value in headers.items()}
            auth_config = {
                key: "***" if key in {"token", "value", "api_key"} and value else value
                for key, value in auth_config.items()
            }
        payload["headers"] = headers
        payload["auth_config"] = auth_config
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        payload["source_type"] = SourceType.openapi_service.value
        return payload

    @staticmethod
    def _service_edit_snapshot(service: dict[str, Any]) -> dict[str, Any]:
        return {
            key: service.get(key)
            for key in (
                "service_key",
                "name",
                "endpoint_url",
                "headers_json",
                "description",
                "tags_json",
                "visibility",
            )
        }

    @staticmethod
    def _openapi_service_edit_snapshot(service: dict[str, Any]) -> dict[str, Any]:
        return {
            key: service.get(key)
            for key in (
                "service_key",
                "name",
                "base_url",
                "spec_url",
                "spec_content",
                "auth_config_json",
                "headers_json",
                "description",
                "tags_json",
                "visibility",
            )
        }

    def _openapi_service_summary_payload(self, service: dict[str, Any]) -> dict[str, Any]:
        payload = dict(service)
        payload["tags"] = _json_loads(payload.pop("tags_json", None), [])
        payload["source_type"] = SourceType.openapi_service.value
        payload["tool_count"] = int(payload.get("tool_count") or 0)
        return payload

    def _openapi_tool_payload(self, tool: dict[str, Any]) -> dict[str, Any]:
        input_schema = _json_loads(tool.get("input_schema_json"), {})
        request_mapping = _json_loads(tool.get("request_mapping_json"), {})
        response_schema = _json_loads(tool.get("response_schema_json"), {})
        examples = _json_loads(tool.get("examples_json"), [])
        payload = dict(tool)
        payload.pop("input_schema_json", None)
        payload.pop("request_mapping_json", None)
        payload.pop("response_schema_json", None)
        payload.pop("tags_json", None)
        payload.pop("examples_json", None)
        return {
            **payload,
            "source_type": SourceType.openapi_service.value,
            "service": tool["service_key"],
            "tool": tool["tool_name"],
            "name": tool["display_name"],
            "input_schema": input_schema,
            "request_mapping": request_mapping,
            "response_schema": response_schema,
            "tool_type": tool["tool_type"],
            "tags": _json_loads(tool.get("tags_json"), []),
            "examples": examples,
            "execute_example": examples[0] if examples else _schema_example(input_schema),
            "executable": tool["tool_type"] in READONLY_TOOL_TYPES,
        }

    def _builtin_tool_search_item(self, source_key: str, tool: BuiltinTool) -> dict[str, Any]:
        return {
            "kind": "tool",
            "service": source_key,
            "tool": tool.tool,
            "display_tool": f"{source_key}.{tool.tool}",
            "name": tool.name,
            "description": tool.description,
            "tags": ["builtin", source_key],
            "tool_type": tool.tool_type,
            "input_schema": tool.input_schema,
            "execute_example": {},
            "executable": True,
        }

    def _tool_search_item(self, tool: dict[str, Any]) -> dict[str, Any]:
        payload = self._tool_payload(tool)
        return {
            "kind": "tool",
            "service": payload["service"],
            "tool": payload["tool"],
            "name": payload["name"],
            "description": payload["description"],
            "tags": payload["tags"],
            "tool_type": payload["tool_type"],
            "input_schema": payload["input_schema"],
            "execute_example": payload["execute_example"],
            "executable": payload["executable"],
        }

    def _openapi_tool_search_item(self, tool: dict[str, Any]) -> dict[str, Any]:
        payload = self._openapi_tool_payload(tool)
        return {
            "kind": "tool",
            "source_type": SourceType.openapi_service.value,
            "service": payload["service"],
            "tool": payload["tool"],
            "name": payload["name"],
            "description": payload["description"],
            "tags": payload["tags"],
            "tool_type": payload["tool_type"],
            "input_schema": payload["input_schema"],
            "execute_example": payload["execute_example"],
            "executable": payload["executable"],
            "method": payload["method"],
            "path": payload["path"],
        }

    def _normalize_synced_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(tool.get("name") or "")
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        examples = tool.get("examples")
        if not isinstance(examples, list):
            examples = []
        return {
            "tool_name": tool_name,
            "display_name": self._display_name(tool),
            "description": str(tool.get("description") or ""),
            "input_schema": input_schema,
            "tool_type": ToolType.unconfigured,
            "tags": self._tool_tags(tool),
            "examples": examples,
        }

    def _display_name(self, tool: dict[str, Any]) -> str:
        annotations = tool.get("annotations")
        if isinstance(annotations, dict) and annotations.get("title"):
            return str(annotations["title"])
        return str(tool.get("name") or "")

    def _tool_tags(self, tool: dict[str, Any]) -> list[str]:
        tags = tool.get("tags")
        if isinstance(tags, list):
            return [str(tag) for tag in tags]
        return []

    def _validate_service_key(self, service_key: str) -> None:
        if not SERVICE_KEY_RE.fullmatch(service_key):
            raise ValidationError("service_key may contain only letters, numbers, hyphen, and underscore")

    def _validate_tool_type(self, tool_type: Any) -> ToolType:
        try:
            return ToolType(str(tool_type or ToolType.unconfigured.value))
        except ValueError as exc:
            raise ValidationError("invalid tool type") from exc
