"""Governance service for capability profiles, policy checks, and tool call logs."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from agent_bridge.capabilities.models import (
    CallLogStatus,
    FailureOwner,
    FailureStage,
    ProfileResourceType,
    ProfileRuleEffect,
    SourceType,
)
from agent_bridge.capabilities.profile_pins import (
    PINNABLE_TOOL_TYPES,
    PinnedGroup,
    safe_pin_tool_name,
    tool_payload_to_pin_tool,
)
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore


VALID_PROFILE_STATUSES = {"active", "disabled"}
VALID_PROFILE_PIN_AUTO_MODES = {"disabled", "ratio", "count"}


def make_log_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"call_{stamp}_{uuid.uuid4().hex[:8]}"


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


class CapabilityGovernanceService:
    def __init__(self, *, store: SQLiteStore, admins: set[str]) -> None:
        self.store = store
        self.admins = admins

    def upsert_profile(
        self,
        actor: str,
        profile_key: str,
        name: str,
        description: str,
        status: str,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if status not in VALID_PROFILE_STATUSES:
            raise ValidationError("invalid profile status")
        return self.store.upsert_project_profile(
            profile_key=profile_key,
            name=name,
            description=description,
            status=status,
            created_by=actor,
        )

    def list_profiles(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_project_profiles()

    def get_profile(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return {
            **profile,
            "rules": self.store.list_profile_source_rules(profile_key),
            "resource_rules": self.store.list_profile_resource_rules(profile_key),
        }

    def replace_profile_rules(
        self,
        actor: str,
        profile_key: str,
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        normalized = [self._validate_rule(rule) for rule in rules]
        self.store.replace_profile_source_rules(profile_key, normalized)
        return self.get_profile(actor, profile_key)

    def replace_profile_resource_rules(
        self,
        actor: str,
        profile_key: str,
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")
        normalized = [self._validate_resource_rule(rule) for rule in rules]
        self.store.replace_profile_resource_rules(profile_key, normalized)
        return self.get_profile(actor, profile_key)

    def replace_profile_pins(
        self,
        actor: str,
        profile_key: str,
        pins: list[dict[str, Any]],
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")

        normalized = []
        for pin in pins:
            service_key = str(pin.get("service_key") or "").strip()
            if not service_key:
                raise ValidationError("service_key is required")
            tool_type = str(pin.get("tool_type") or "").strip()
            if not tool_type:
                raise ValidationError("tool_type is required")
            if tool_type not in PINNABLE_TOOL_TYPES:
                raise ValidationError("tool_type is not pinnable")
            if self.store.get_mcp_service(service_key) is None:
                raise NotFound("service not found")
            normalized.append(
                {
                    "service_key": service_key,
                    "tool_type": tool_type,
                    "created_by": actor,
                }
            )

        self.store.replace_profile_pin_rules(profile_key, normalized)
        self.store.clear_profile_pin_auto_cache(profile_key)
        return self.profile_pin_preview(actor, profile_key)

    def update_profile_pin_settings(
        self,
        actor: str,
        profile_key: str,
        *,
        auto_mode: str | None = None,
        mode: str | None = None,
        ratio_percent: int | None = None,
        count_limit: int | None = None,
        count: int | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")

        normalized_auto_mode = str(auto_mode or "").strip()
        normalized_mode = str(mode or "").strip()
        if normalized_auto_mode and normalized_mode and normalized_auto_mode != normalized_mode:
            raise ValidationError("profile pin mode aliases conflict")
        effective_mode = normalized_auto_mode or normalized_mode
        if effective_mode not in VALID_PROFILE_PIN_AUTO_MODES:
            raise ValidationError("invalid profile pin auto mode")

        if count_limit is not None and count is not None and count_limit != count:
            raise ValidationError("profile pin count aliases conflict")
        effective_count = count_limit if count_limit is not None else count

        if effective_mode == "disabled":
            ratio_percent = None
            effective_count = None
        elif effective_mode == "ratio":
            if effective_count is not None:
                raise ValidationError("profile pin settings are mutually exclusive")
            if ratio_percent is None or ratio_percent < 1 or ratio_percent > 100:
                raise ValidationError("ratio_percent must be between 1 and 100")
        elif effective_mode == "count":
            if ratio_percent is not None:
                raise ValidationError("profile pin settings are mutually exclusive")
            if effective_count is None or effective_count < 1:
                raise ValidationError("count_limit must be at least 1")

        self.store.upsert_profile_pin_settings(
            profile_key=profile_key,
            mode=effective_mode,
            ratio_percent=ratio_percent,
            count=effective_count,
            auto_cache=None,
        )
        return self.profile_pin_preview(actor, profile_key)

    def profile_pin_preview(self, actor: str, profile_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise NotFound("profile not found")

        services = [
            service
            for service in self.store.list_mcp_services()
            if service.get("status") == "enabled"
        ]
        service_keys = [service["service_key"] for service in services]
        allowed_service_keys = set(
            self.filter_source_keys(
                actor=actor,
                profile_key=profile_key,
                source_type=SourceType.mcp_service.value,
                source_keys=service_keys,
            )
        )
        candidate_services = {
            service["service_key"]: service
            for service in services
            if service["service_key"] in allowed_service_keys
        }

        candidate_group_keys = {
            (tool["service_key"], tool["tool_type"])
            for tool in self.store.list_mcp_tools()
            if tool.get("status") == "active"
            and tool.get("service_key") in candidate_services
            and tool.get("tool_type") in PINNABLE_TOOL_TYPES
        }
        manual_groups = [
            PinnedGroup(
                service_key=rule["service_key"],
                tool_type=rule["tool_type"],
                source="manual",
            )
            for rule in self.store.list_profile_pin_rules(profile_key)
            if (rule["service_key"], rule["tool_type"]) in candidate_group_keys
        ]
        groups = [
            {
                "service_key": group.service_key,
                "tool_type": group.tool_type,
                "source": group.source,
            }
            for group in sorted(manual_groups, key=lambda group: (group.service_key, group.tool_type))
        ]

        selected_group_sources = {
            (group["service_key"], group["tool_type"]): group["source"]
            for group in groups
        }
        tools = []
        for tool in sorted(
            self.store.list_mcp_tools(),
            key=lambda item: (item["service_key"], item["tool_type"], item["tool_name"]),
        ):
            if tool.get("status") != "active":
                continue
            if (tool.get("service_key"), tool.get("tool_type")) not in selected_group_sources:
                continue
            pin_tool = tool_payload_to_pin_tool(
                tool=tool,
                service_name=candidate_services[tool["service_key"]].get("name") or tool["service_key"],
                source=selected_group_sources[(tool["service_key"], tool["tool_type"])],
            )
            pin_tool["generated_tool_name"] = safe_pin_tool_name(tool["service_key"], tool["tool_name"])
            tools.append(pin_tool)

        settings = self.store.get_profile_pin_settings(profile_key) or {
            "mode": "disabled",
            "ratio_percent": None,
            "count": None,
            "auto_cache_json": None,
            "auto_cache_computed_at": None,
        }
        return {
            "profile_key": profile_key,
            "settings": dict(settings),
            "groups": groups,
            "tools": tools,
        }

    def filter_source_keys(
        self,
        *,
        actor: str,
        profile_key: str | None,
        source_type: str,
        source_keys: list[str],
    ) -> list[str]:
        normalized_source_type = self._validate_source_type(source_type)
        if profile_key is None:
            return source_keys

        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        if profile.get("status") != "active":
            raise ValidationError("profile is disabled")

        rules = self.store.list_profile_source_rules(profile_key)
        relevant_rules = [rule for rule in rules if rule["source_type"] == normalized_source_type]
        allow = {
            rule["source_key"]
            for rule in relevant_rules
            if rule["effect"] == ProfileRuleEffect.allow.value
        }
        deny = {
            rule["source_key"]
            for rule in relevant_rules
            if rule["effect"] == ProfileRuleEffect.deny.value
        }

        if not allow:
            return []
        allowed = [source_key for source_key in source_keys if source_key in allow]
        return [source_key for source_key in allowed if source_key not in deny]

    def is_source_allowed(
        self,
        actor: str,
        profile_key: str | None,
        source_type: str,
        source_key: str,
    ) -> bool:
        return source_key in self.filter_source_keys(
            actor=actor,
            profile_key=profile_key,
            source_type=source_type,
            source_keys=[source_key],
        )

    def filter_resource_keys(
        self,
        *,
        actor: str,
        profile_key: str | None,
        resource_type: str,
        resource_keys: list[str],
    ) -> list[str]:
        normalized_resource_type = self._validate_resource_type(resource_type)
        if profile_key is None:
            return resource_keys

        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        if profile.get("status") != "active":
            raise ValidationError("profile is disabled")

        rules = self.store.list_profile_resource_rules(profile_key)
        allow = {
            rule["resource_key"]
            for rule in rules
            if rule["resource_type"] == normalized_resource_type
        }
        return [resource_key for resource_key in resource_keys if resource_key in allow]

    def is_resource_allowed(
        self,
        actor: str,
        profile_key: str | None,
        resource_type: str,
        resource_key: str,
    ) -> bool:
        return resource_key in self.filter_resource_keys(
            actor=actor,
            profile_key=profile_key,
            resource_type=resource_type,
            resource_keys=[resource_key],
        )

    def get_resource_profiles(
        self, actor: str, resource_type: str, resource_key: str
    ) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        normalized_type = self._validate_resource_type(resource_type)
        return self.store.list_resource_rule_profiles(
            resource_type=normalized_type, resource_key=resource_key
        )

    def set_resource_profiles(
        self,
        actor: str,
        resource_type: str,
        resource_key: str,
        profile_keys: list[str],
        overrides: dict[str, dict[str, str | None]] | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_type = self._validate_resource_type(resource_type)
        for pk in profile_keys:
            if self.store.get_project_profile(pk) is None:
                raise NotFound(f"profile not found: {pk}")
        self.store.replace_resource_rule_profiles(
            resource_type=normalized_type,
            resource_key=resource_key,
            profile_keys=profile_keys,
            overrides=overrides,
        )
        return {
            "resource_type": normalized_type,
            "resource_key": resource_key,
            "profile_keys": profile_keys,
        }

    def log_tool_call(
        self,
        *,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None,
        source_key: str | None,
        tool_name: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        error_message: str | None,
        duration_ms: int | None,
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_source_type = self._validate_optional_source_type(source_type)
        normalized_status = self._validate_call_log_status(status)
        normalized_failure_stage = self._validate_optional_failure_stage(failure_stage)
        normalized_failure_owner = self._validate_optional_failure_owner(failure_owner)
        return self.store.create_tool_call_log(
            log_id=make_log_id(),
            actor=actor,
            profile_key=profile_key,
            entrypoint=entrypoint,
            source_type=normalized_source_type,
            source_key=source_key,
            tool_name=tool_name,
            request=request,
            response=response,
            status=normalized_status,
            error_message=error_message,
            failure_stage=normalized_failure_stage,
            failure_owner=normalized_failure_owner,
            error_type=error_type,
            resource_type=resource_type,
            resource_key=resource_key,
            duration_ms=duration_ms,
        )

    def list_logs(
        self,
        *,
        actor: str,
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: str | None = None,
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        normalized_source_type = self._validate_optional_source_type(source_type)
        normalized_status = self._validate_optional_call_log_status(status)
        normalized_failure_stage = self._validate_optional_failure_stage(failure_stage)
        normalized_failure_owner = self._validate_optional_failure_owner(failure_owner)
        return self.store.list_tool_call_logs(
            entrypoint=entrypoint,
            source_type=normalized_source_type,
            source_key=source_key,
            tool_name=tool_name,
            profile_key=profile_key,
            status=normalized_status,
            failure_stage=normalized_failure_stage,
            failure_owner=normalized_failure_owner,
            error_type=error_type,
            resource_type=resource_type,
            resource_key=resource_key,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )

    def get_log(self, *, actor: str, log_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        log = self.store.get_tool_call_log(log_id)
        if log is None:
            raise NotFound("tool call log not found")
        return log

    def stats(
        self,
        *,
        actor: str,
        dimensions: list[str],
        created_from: str | None = None,
        created_to: str | None = None,
        bucket: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        try:
            items = self.store.aggregate_tool_call_stats(
                dimensions=dimensions,
                created_from=created_from,
                created_to=created_to,
                bucket=bucket,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {"dimensions": dimensions, "bucket": bucket, "items": items}

    def _validate_rule(self, rule: dict[str, str]) -> dict[str, str]:
        source_type = self._validate_source_type(rule.get("source_type"))

        try:
            effect = ProfileRuleEffect(rule["effect"]).value
        except (KeyError, ValueError) as exc:
            raise ValidationError("invalid rule effect") from exc

        source_key = str(rule.get("source_key") or "").strip()
        if not source_key:
            raise ValidationError("source_key is required")

        return {"source_type": source_type, "source_key": source_key, "effect": effect}

    def _validate_resource_rule(self, rule: dict[str, Any]) -> dict[str, str]:
        resource_type = self._validate_resource_type(rule.get("resource_type"))

        resource_key = str(rule.get("resource_key") or "").strip()
        if not resource_key:
            raise ValidationError("resource_key is required")

        return {"resource_type": resource_type, "resource_key": resource_key}

    def _validate_source_type(self, source_type: str | None) -> str:
        try:
            return SourceType(source_type).value
        except ValueError as exc:
            raise ValidationError("invalid source type") from exc

    def _validate_resource_type(self, resource_type: str | None) -> str:
        try:
            return ProfileResourceType(resource_type).value
        except ValueError as exc:
            raise ValidationError("invalid resource type") from exc

    def _validate_optional_source_type(self, source_type: str | None) -> str | None:
        if source_type is None:
            return None
        return self._validate_source_type(source_type)

    def _validate_call_log_status(self, status: str) -> str:
        try:
            return CallLogStatus(status).value
        except ValueError as exc:
            raise ValidationError("invalid call log status") from exc

    def _validate_optional_call_log_status(self, status: str | None) -> str | None:
        if status is None:
            return None
        return self._validate_call_log_status(status)

    def _validate_optional_failure_stage(self, failure_stage: str | None) -> str | None:
        if failure_stage is None:
            return None
        try:
            return FailureStage(failure_stage).value
        except ValueError as exc:
            raise ValidationError("invalid failure stage") from exc

    def _validate_optional_failure_owner(self, failure_owner: str | None) -> str | None:
        if failure_owner is None:
            return None
        try:
            return FailureOwner(failure_owner).value
        except ValueError as exc:
            raise ValidationError("invalid failure owner") from exc
