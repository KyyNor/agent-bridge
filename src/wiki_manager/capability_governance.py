"""Governance service for capability profiles, policy checks, and tool call logs."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from wiki_manager.capabilities import CallLogStatus, FailureOwner, FailureStage, ProfileRuleEffect, SourceType
from wiki_manager.domain import NotFound, ValidationError, require_admin_user
from wiki_manager.storage import SQLiteStore


VALID_PROFILE_STATUSES = {"active", "disabled"}


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
        return {**profile, "rules": self.store.list_profile_source_rules(profile_key)}

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
        if profile is None or profile.get("status") != "active":
            raise NotFound("profile not found")

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

        allowed = [source_key for source_key in source_keys if not allow or source_key in allow]
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

    def _validate_source_type(self, source_type: str | None) -> str:
        try:
            return SourceType(source_type).value
        except ValueError as exc:
            raise ValidationError("invalid source type") from exc

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
