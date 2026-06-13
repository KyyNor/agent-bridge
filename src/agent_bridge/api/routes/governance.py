"""Governance: profiles, rules, logs, stats endpoints."""
from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import (
    ProfileManualNotesRequest,
    ProfilePinSettingsRequest,
    ProfilePinsRequest,
    ProfileResourceRuleRequest,
    ProfileResourcesRequest,
    ProfileRulesRequest,
    ProfileSourceRuleRequest,
    ProjectProfileRequest,
    ResourceProfilesRequest,
)




def create_governance_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    @router.post("/capability-profiles")
    def upsert_capability_profile(payload: ProjectProfileRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.upsert_profile(current_actor, payload.profile_key, payload.name, payload.description, payload.status))

    @router.get("/capability-profiles")
    def list_capability_profiles(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.list_profiles(current_actor))

    @router.get("/capability-profiles/{profile_key}")
    def get_capability_profile(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_profile(current_actor, profile_key))

    @router.put("/capability-profiles/{profile_key}/rules")
    def replace_capability_profile_rules(profile_key: str, payload: ProfileRulesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        rules = [rule.model_dump() for rule in payload.rules]
        return call_safely(lambda: service.governance.replace_profile_rules(current_actor, profile_key, rules))

    @router.get("/capability-profiles/{profile_key}/pins")
    def get_profile_pins(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.profile_pin_preview(current_actor, profile_key))

    @router.put("/capability-profiles/{profile_key}/pins")
    def replace_profile_pins(profile_key: str, payload: ProfilePinsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        pins = [pin.model_dump() for pin in payload.pins]
        return call_safely(lambda: service.governance.replace_profile_pins(current_actor, profile_key, pins))

    @router.put("/capability-profiles/{profile_key}/pins/settings")
    def update_profile_pin_settings(profile_key: str, payload: ProfilePinSettingsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.update_profile_pin_settings(
            current_actor,
            profile_key,
            mode=payload.mode,
            ratio_percent=payload.ratio_percent,
            count=payload.count,
        ))

    @router.post("/capability-profiles/{profile_key}/pins/refresh")
    def refresh_profile_pins(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.refresh_profile_pin_cache(current_actor, profile_key))

    @router.post("/capability-profiles/{profile_key}/doc/render")
    def render_profile_doc(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.render_profile_markdown(current_actor, profile_key))

    @router.put("/capability-profiles/{profile_key}/doc/manual-notes")
    def update_profile_manual_notes(profile_key: str, payload: ProfileManualNotesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.update_profile_manual_notes(current_actor, profile_key, payload.manual_notes))

    @router.put("/capability-profiles/{profile_key}/resources")
    def replace_capability_profile_resources(profile_key: str, payload: ProfileResourcesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        resources = [resource.model_dump() for resource in payload.resources]
        return call_safely(lambda: service.governance.replace_profile_resource_rules(current_actor, profile_key, resources))

    @router.get("/tool-call-logs")
    def list_tool_call_logs(
        entrypoint: str | None = None, source_type: str | None = None, source_key: str | None = None,
        tool_name: str | None = None, profile_key: str | None = None, status: str | None = None,
        failure_stage: str | None = None, failure_owner: str | None = None, error_type: str | None = None,
        resource_type: str | None = None, resource_key: str | None = None, limit: int = 50, offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.list_logs(actor=current_actor, entrypoint=entrypoint, source_type=source_type, source_key=source_key, tool_name=tool_name, profile_key=profile_key, status=status, failure_stage=failure_stage, failure_owner=failure_owner, error_type=error_type, resource_type=resource_type, resource_key=resource_key, limit=limit, offset=offset))

    @router.get("/tool-call-logs/{log_id}")
    def get_tool_call_log(log_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_log(actor=current_actor, log_id=log_id))

    @router.get("/tool-call-stats")
    def tool_call_stats(
        dimensions: str = "profile_key,source_key,tool_name", created_from: str | None = None,
        created_to: str | None = None, bucket: str | None = None, current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        parsed_dimensions = [part.strip() for part in dimensions.split(",") if part.strip()]
        return call_safely(lambda: service.governance.stats(actor=current_actor, dimensions=parsed_dimensions, created_from=created_from, created_to=created_to, bucket=bucket))

    @router.get("/resource-profiles/{resource_type}/{resource_key}")
    def get_resource_profiles(resource_type: str, resource_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_resource_profiles(current_actor, resource_type, resource_key))

    @router.put("/resource-profiles/{resource_type}/{resource_key}")
    def set_resource_profiles(resource_type: str, resource_key: str, payload: ResourceProfilesRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.set_resource_profiles(
            current_actor, resource_type, resource_key,
            payload.profile_keys, overrides=payload.overrides,
        ))

    return router
