"""项目画像治理与运行日志的兼容方法。"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from agent_bridge.core.timeutil import utc_now


class GovernanceFacadeMixin:
    def upsert_project_profile(
        self,
        *,
        profile_key: str,
        name: str,
        description: str = "",
        status: str = "active",
        created_by: str,
        owner_group_key: str = "",
        visibility: str = "group",
    ) -> dict[str, Any]:
        return self.governance.upsert_project_profile(
            profile_key=profile_key,
            name=name,
            description=description,
            status=status,
            created_by=created_by,
            owner_group_key=owner_group_key,
            visibility=visibility,
        )

    def get_project_profile(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_project_profile(profile_key=profile_key)

    def list_project_profiles(self) -> list[dict[str, Any]]:
        return self.governance.list_project_profiles()

    def replace_profile_source_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_source_rules(profile_key=profile_key, rules=rules)

    def list_profile_source_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_source_rules(profile_key=profile_key)

    def replace_profile_resource_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_resource_rules(profile_key=profile_key, rules=rules)

    def list_profile_resource_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_resource_rules(profile_key=profile_key)

    def replace_profile_pin_rules(self, profile_key: str, rules: list[dict[str, Any]]) -> None:
        return self.governance.replace_profile_pin_rules(profile_key=profile_key, rules=rules)

    def list_profile_pin_rules(self, profile_key: str) -> list[dict[str, Any]]:
        return self.governance.list_profile_pin_rules(profile_key=profile_key)

    def delete_source_rules_by_key(self, source_type: str, source_key: str) -> None:
        return self.governance.delete_source_rules_by_key(source_type=source_type, source_key=source_key)

    def delete_pin_rules_by_service(self, service_key: str) -> None:
        return self.governance.delete_pin_rules_by_service(service_key=service_key)

    def delete_resource_rules_by_key(self, resource_type: str, resource_key: str) -> None:
        return self.governance.delete_resource_rules_by_key(resource_type=resource_type, resource_key=resource_key)

    def upsert_profile_pin_settings(
        self,
        *,
        profile_key: str,
        mode: str,
        ratio_percent: int | None,
        count: int | None,
        auto_cache: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self.governance.upsert_profile_pin_settings(
            profile_key=profile_key,
            mode=mode,
            ratio_percent=ratio_percent,
            count=count,
            auto_cache=auto_cache,
        )

    def get_profile_pin_settings(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_pin_settings(profile_key=profile_key)

    def clear_profile_pin_auto_cache(self, profile_key: str) -> None:
        return self.governance.clear_profile_pin_auto_cache(profile_key=profile_key)

    def get_profile_doc_cache(self, profile_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_doc_cache(profile_key=profile_key)

    def upsert_profile_manual_notes(self, profile_key: str, manual_notes: str) -> dict[str, Any]:
        return self.governance.upsert_profile_manual_notes(profile_key=profile_key, manual_notes=manual_notes)

    def upsert_profile_rendered_doc(
        self,
        *,
        profile_key: str,
        manual_notes: str,
        auto_summary: dict[str, Any],
        auto_summary_hash: str,
        rendered_hash: str,
        markdown: str,
        mark_written: bool,
    ) -> dict[str, Any]:
        return self.governance.upsert_profile_rendered_doc(
            profile_key=profile_key,
            manual_notes=manual_notes,
            auto_summary=auto_summary,
            auto_summary_hash=auto_summary_hash,
            rendered_hash=rendered_hash,
            markdown=markdown,
            mark_written=mark_written,
        )

    def list_resource_rule_profiles(self, resource_type: str, resource_key: str) -> list[dict[str, Any]]:
        return self.governance.list_resource_rule_profiles(resource_type=resource_type, resource_key=resource_key)

    def replace_resource_rule_profiles(self, resource_type: str, resource_key: str, profile_keys: list[str], overrides: dict[str, dict[str, str | None]] | None = None) -> None:
        return self.governance.replace_resource_rule_profiles(resource_type=resource_type, resource_key=resource_key, profile_keys=profile_keys, overrides=overrides)

    def get_profile_resource_rule(self, profile_key: str, resource_type: str, resource_key: str) -> dict[str, Any] | None:
        return self.governance.get_profile_resource_rule(profile_key=profile_key, resource_type=resource_type, resource_key=resource_key)

    def create_tool_call_log(
        self,
        *,
        log_id: str,
        actor: str,
        profile_key: str | None,
        entrypoint: str,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        request: Any | None = None,
        response: Any | None = None,
        status: CallLogStatus | str,
        error_message: str | None = None,
        failure_stage: str | None = None,
        failure_owner: str | None = None,
        error_type: str | None = None,
        resource_type: str | None = None,
        resource_key: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        log = self.governance.create_tool_call_log(log_id=log_id, actor=actor, profile_key=profile_key, entrypoint=entrypoint, source_type=source_type, source_key=source_key, tool_name=tool_name, request=request, response=response, status=status, error_message=error_message, failure_stage=failure_stage, failure_owner=failure_owner, error_type=error_type, resource_type=resource_type, resource_key=resource_key, duration_ms=duration_ms)
        self.maybe_prune_runtime_logs()
        return log

    def list_tool_call_logs(
        self,
        *,
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: CallLogStatus | str | None = None,
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
        return self.governance.list_tool_call_logs(entrypoint=entrypoint, source_type=source_type, source_key=source_key, tool_name=tool_name, profile_key=profile_key, status=status, failure_stage=failure_stage, failure_owner=failure_owner, error_type=error_type, resource_type=resource_type, resource_key=resource_key, created_from=created_from, created_to=created_to, limit=limit, offset=offset)

    def aggregate_tool_call_stats(
        self,
        *,
        dimensions: list[str],
        created_from: str | None,
        created_to: str | None,
        bucket: str | None,
    ) -> list[dict[str, Any]]:
        return self.governance.aggregate_tool_call_stats(dimensions=dimensions, created_from=created_from, created_to=created_to, bucket=bucket)

    def aggregate_pin_group_usage(self, *, profile_key: str, created_from: str) -> list[dict[str, Any]]:
        return self.governance.aggregate_pin_group_usage(profile_key=profile_key, created_from=created_from)

    def get_tool_call_log(self, log_id: str) -> dict[str, Any] | None:
        return self.governance.get_tool_call_log(log_id=log_id)

    def set_runtime_log_retention_days(self, days: int) -> None:
        self._runtime_log_retention_days = max(int(days), 0)

    def maybe_prune_runtime_logs(self, force: bool = False) -> dict[str, int]:
        if self._runtime_log_retention_days <= 0:
            return {"tool_call_logs": 0, "agent_runs": 0}
        now = time.monotonic()
        if not force and self._last_runtime_log_prune_monotonic is not None:
            if now - self._last_runtime_log_prune_monotonic < self._runtime_log_prune_interval_seconds:
                return {"tool_call_logs": 0, "agent_runs": 0}
        deleted = self.prune_runtime_logs(force=True)
        self._last_runtime_log_prune_monotonic = now
        return deleted

    def prune_runtime_logs(self, force: bool = False) -> dict[str, int]:
        if self._runtime_log_retention_days <= 0:
            return {"tool_call_logs": 0, "agent_runs": 0}
        cutoff = (utc_now() - timedelta(days=self._runtime_log_retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = {
            "tool_call_logs": self.governance.purge_tool_call_logs_before(cutoff),
            "agent_runs": self.agent_runs.purge_created_before(cutoff),
        }
        if force:
            self._last_runtime_log_prune_monotonic = time.monotonic()
        return deleted
