"""Governance service for capability profiles, policy checks, and tool call logs."""

from __future__ import annotations

import logging
import time
import uuid
import json
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

from agent_bridge.capability_hub.models import (
    CallLogStatus,
    FailureOwner,
    FailureStage,
    ProfileResourceType,
    ProfileRuleEffect,
    SourceType,
)
from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.capability_hub.profiles.pins import (
    PINNABLE_TOOL_TYPES,
    PinnedGroup,
    ratio_target,
    tool_payload_to_pin_tool,
)
from agent_bridge.capability_hub.profiles.docs import (
    render_profile_markdown as render_profile_doc_markdown,
    stable_hash,
)
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.core.timeutil import parse_utc, utc_now
from agent_bridge.storage.sqlite import SQLiteStore


VALID_PROFILE_STATUSES = {"active", "disabled"}
VALID_PROFILE_PIN_AUTO_MODES = {"disabled", "ratio", "count"}


def make_log_id() -> str:
    stamp = utc_now().strftime("%Y%m%d_%H%M%S")
    return f"call_{stamp}_{uuid.uuid4().hex[:8]}"


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


class CapabilityGovernanceService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        access: AccessControlService | None = None,
    ) -> None:
        self.store = store
        self.admins = admins
        self.access = access

    def upsert_profile(
        self,
        actor: str,
        profile_key: str,
        name: str,
        description: str,
        status: str,
        *,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_PROFILE_STATUSES:
            raise ValidationError("invalid profile status")
        current = self.store.get_project_profile(profile_key)
        if current is None:
            scope = (
                self.access.new_resource_scope(
                    actor=actor,
                    visibility="group",
                    resource_type=ScopedResourceType.capability_profile,
                )
                if self.access is not None
                else ResourceScope.from_record({})
            )
            if self.access is None:
                require_admin_user(actor, self.admins)
        else:
            self._require_profile_write(actor, profile_key)
            scope = ResourceScope.from_record(current)
        require_edit_token(
            expected_edit_token,
            self._profile_edit_snapshot(current),
            resource_type="能力平面",
            resource_key=profile_key,
            actor=actor,
        )
        saved = self.store.upsert_project_profile(
            profile_key=profile_key,
            name=name,
            description=description,
            status=status,
            created_by=actor,
            owner_group_key=scope.owner_group_key,
            visibility=scope.visibility.value,
        )
        return attach_edit_token(saved, self._profile_edit_snapshot(saved))

    def list_profiles(self, actor: str) -> list[dict[str, Any]]:
        if self.access is None:
            require_admin_user(actor, self.admins)
            profiles = self.store.list_project_profiles()
        else:
            profiles = self.access.visible_resources(
                actor=actor, resource_type=ScopedResourceType.capability_profile
            )
        return [
            attach_edit_token(profile, self._profile_edit_snapshot(profile))
            for profile in profiles
        ]

    def get_profile(self, actor: str, profile_key: str) -> dict[str, Any]:
        if self.access is None:
            require_admin_user(actor, self.admins)
        profile = self._require_profile_read(actor, profile_key)
        rules = self.store.list_profile_source_rules(profile_key)
        resource_rules = self.store.list_profile_resource_rules(profile_key)
        return {
            **attach_edit_token(profile, self._profile_edit_snapshot(profile)),
            "rules": rules,
            "resource_rules": resource_rules,
            "rules_edit_token": attach_edit_token({}, self._rules_edit_snapshot(rules))["edit_token"],
            "resources_edit_token": attach_edit_token({}, self._resources_edit_snapshot(resource_rules))["edit_token"],
        }

    def replace_profile_rules(
        self,
        actor: str,
        profile_key: str,
        rules: list[dict[str, str]],
        *,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)
        current_rules = self.store.list_profile_source_rules(profile_key)
        require_edit_token(
            expected_edit_token,
            self._rules_edit_snapshot(current_rules),
            resource_type="能力平面规则",
            resource_key=profile_key,
            actor=actor,
        )
        normalized = [self._validate_rule(rule, actor=actor) for rule in rules]
        self.store.replace_profile_source_rules(profile_key, normalized)
        self.store.clear_profile_pin_auto_cache(profile_key)
        return self.get_profile(actor, profile_key)

    def replace_profile_resource_rules(
        self,
        actor: str,
        profile_key: str,
        rules: list[dict[str, Any]],
        *,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)
        current_rules = self.store.list_profile_resource_rules(profile_key)
        require_edit_token(
            expected_edit_token,
            self._resources_edit_snapshot(current_rules),
            resource_type="能力平面资源规则",
            resource_key=profile_key,
            actor=actor,
        )
        normalized = [self._validate_resource_rule(rule, actor=actor) for rule in rules]
        self.store.replace_profile_resource_rules(profile_key, normalized)
        return self.get_profile(actor, profile_key)

    def replace_profile_pins(
        self,
        actor: str,
        profile_key: str,
        pins: list[dict[str, Any]],
        *,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)

        require_edit_token(
            expected_edit_token,
            self._pins_edit_snapshot(profile_key),
            resource_type="能力平面 Pin 配置",
            resource_key=profile_key,
            actor=actor,
        )
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
            if self.access is not None:
                self.access.require_resource_read(
                    actor=actor,
                    resource_type=ScopedResourceType.mcp_service,
                    resource_key=service_key,
                )
            elif self.store.get_mcp_service(service_key) is None:
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
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)

        require_edit_token(
            expected_edit_token,
            self._pins_edit_snapshot(profile_key),
            resource_type="能力平面 Pin 配置",
            resource_key=profile_key,
            actor=actor,
        )
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

    def refresh_profile_pin_cache(self, actor: str, profile_key: str) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)
        self.store.clear_profile_pin_auto_cache(profile_key)
        return self.profile_pin_preview(actor, profile_key)

    def update_profile_manual_notes(
        self,
        actor: str,
        profile_key: str,
        manual_notes: str,
        *,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_profile_write(actor, profile_key)
        cache = self.store.get_profile_doc_cache(profile_key) or {}
        require_edit_token(
            expected_edit_token,
            {"manual_notes": str(cache.get("manual_notes") or "")},
            resource_type="能力平面手动说明",
            resource_key=profile_key,
            actor=actor,
        )
        self.store.upsert_profile_manual_notes(profile_key, manual_notes)
        return self.render_profile_markdown(actor, profile_key)

    def render_profile_markdown(self, actor: str, profile_key: str) -> dict[str, Any]:
        # 只读渲染：用于 SessionStart hook / agent 运行时给任意用户注入 profile 指导，
        # 无副作用，不要求全局 admin。写操作（manual notes / pin refresh）各自有校验。
        profile = self._require_profile_read(actor, profile_key)

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
        allowed_services = [
            {
                "service_key": service["service_key"],
                "name": service.get("name") or service["service_key"],
                "description": service.get("description") or "",
            }
            for service in services
            if service["service_key"] in allowed_service_keys
        ]

        resource_rules = self.store.list_profile_resource_rules(profile_key)
        allowed_repo_keys = {
            rule["resource_key"]
            for rule in resource_rules
            if rule["resource_type"] == ProfileResourceType.code_repo.value
        }
        allowed_kb_slugs = {
            rule["resource_key"]
            for rule in resource_rules
            if rule["resource_type"] == ProfileResourceType.wiki_kb.value
        }
        allowed_ledger_keys = {
            rule["resource_key"]
            for rule in resource_rules
            if rule["resource_type"] == ProfileResourceType.business_ledger.value
        }
        repositories = [
            {
                "repo_key": repository["repo_key"],
                "name": repository.get("name") or repository["repo_key"],
                "description": repository.get("description") or "",
            }
            for repository in self.store.list_code_repositories()
            if repository["repo_key"] in allowed_repo_keys
            and (
                self.access is None
                or self.access.can_read(
                    actor=actor, scope=ResourceScope.from_record(repository)
                )
            )
        ]
        knowledge_bases = [
            {
                "slug": kb["slug"],
                "name": kb.get("name") or kb["slug"],
                "description": kb.get("description") or "",
            }
            for kb in self.store.list_kbs()
            if kb["slug"] in allowed_kb_slugs
            and (
                self.access is None
                or self.access.can_read(actor=actor, scope=ResourceScope.from_record(kb))
            )
        ]
        ledger_service = getattr(self, "business_ledgers", None)
        business_ledgers = (
            ledger_service.ledger_contexts(sorted(allowed_ledger_keys), actor=actor)
            if ledger_service is not None
            else []
        )

        cache = self.store.get_profile_doc_cache(profile_key) or {}
        manual_notes = str(cache.get("manual_notes") or "")
        summary = {
            "profile_key": profile_key,
            "profile_name": profile.get("name") or profile_key,
            "services": allowed_services,
            "code_repositories": repositories,
            "knowledge_bases": knowledge_bases,
            "business_ledgers": business_ledgers,
        }
        markdown = render_profile_doc_markdown(summary, manual_notes)
        auto_summary_hash = stable_hash(summary)
        rendered_hash = stable_hash({"summary": summary, "manual_notes": manual_notes, "markdown": markdown})
        self.store.upsert_profile_rendered_doc(
            profile_key=profile_key,
            manual_notes=manual_notes,
            auto_summary=summary,
            auto_summary_hash=auto_summary_hash,
            rendered_hash=rendered_hash,
            markdown=markdown,
            mark_written=False,
        )
        return attach_edit_token(
            {"profile_key": profile_key, "markdown": markdown, "rendered_hash": rendered_hash, "manual_notes": manual_notes},
            {"manual_notes": manual_notes},
        )

    def profile_pin_preview(self, actor: str, profile_key: str) -> dict[str, Any]:
        self._require_profile_read(actor, profile_key)

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
        settings = self.store.get_profile_pin_settings(profile_key) or {
            "mode": "disabled",
            "ratio_percent": None,
            "count": None,
            "auto_cache_json": None,
            "auto_cache_computed_at": None,
        }
        groups = [
            {
                "service_key": group.service_key,
                "tool_type": group.tool_type,
                "source": group.source,
            }
            for group in sorted(manual_groups, key=lambda group: (group.service_key, group.tool_type))
        ]
        mode = settings.get("mode") or "disabled"
        if mode != "disabled":
            if mode == "ratio":
                target = ratio_target(len(candidate_group_keys), int(settings.get("ratio_percent") or 0))
            else:
                target = int(settings.get("count") or 0)

            if target > len(groups):
                cached_groups = self._get_valid_pin_auto_cache(settings)
                if cached_groups is None:
                    now = utc_now()
                    created_from = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                    cached_groups = [
                        {
                            "service_key": row["service_key"],
                            "tool_type": row["tool_type"],
                            "source": "auto",
                            "calls": int(row["calls"]),
                        }
                        for row in self.store.aggregate_pin_group_usage(
                            profile_key=profile_key,
                            created_from=created_from,
                        )
                    ]
                    settings = self.store.upsert_profile_pin_settings(
                        profile_key=profile_key,
                        mode=mode,
                        ratio_percent=settings.get("ratio_percent"),
                        count=settings.get("count"),
                        auto_cache={"groups": cached_groups},
                    )

                selected_keys = {(group["service_key"], group["tool_type"]) for group in groups}
                for auto_group in cached_groups:
                    key = (auto_group.get("service_key"), auto_group.get("tool_type"))
                    if key in selected_keys or key not in candidate_group_keys:
                        continue
                    groups.append(
                        {
                            "service_key": auto_group["service_key"],
                            "tool_type": auto_group["tool_type"],
                            "source": "auto",
                            "calls": int(auto_group.get("calls") or 0),
                        }
                    )
                    selected_keys.add(key)
                    if len(groups) >= target:
                        break

        selected_group_sources = {
            (group["service_key"], group["tool_type"]): group["source"]
            for group in groups
        }
        tools = []
        generated_tool_names = set()
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
            generated_tool_name = pin_tool["generated_tool_name"]
            if generated_tool_name in generated_tool_names:
                raise ValidationError(f"pinned tool name collision: {generated_tool_name}")
            generated_tool_names.add(generated_tool_name)
            tools.append(pin_tool)

        return attach_edit_token({
            "profile_key": profile_key,
            "settings": dict(settings),
            "groups": groups,
            "tools": tools,
        }, self._pins_edit_snapshot(profile_key))

    @staticmethod
    def _profile_edit_snapshot(profile: dict[str, Any] | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "profile_key": profile.get("profile_key"),
            "name": profile.get("name") or "",
            "description": profile.get("description") or "",
            "status": profile.get("status") or "active",
        }

    @staticmethod
    def _rules_edit_snapshot(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "source_type": rule.get("source_type"),
                    "source_key": rule.get("source_key"),
                    "effect": rule.get("effect"),
                }
                for rule in rules
            ),
            key=lambda rule: (str(rule["source_type"]), str(rule["source_key"]), str(rule["effect"])),
        )

    @staticmethod
    def _resources_edit_snapshot(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "resource_type": rule.get("resource_type"),
                    "resource_key": rule.get("resource_key"),
                }
                for rule in rules
            ),
            key=lambda rule: (str(rule["resource_type"]), str(rule["resource_key"])),
        )

    def _pins_edit_snapshot(self, profile_key: str) -> dict[str, Any]:
        settings = self.store.get_profile_pin_settings(profile_key) or {}
        pins = sorted(
            (
                {
                    "service_key": pin.get("service_key"),
                    "tool_type": pin.get("tool_type"),
                }
                for pin in self.store.list_profile_pin_rules(profile_key)
            ),
            key=lambda pin: (str(pin["service_key"]), str(pin["tool_type"])),
        )
        return {
            "pins": pins,
            "settings": {
                "mode": settings.get("mode") or "disabled",
                "ratio_percent": settings.get("ratio_percent"),
                "count": settings.get("count"),
            },
        }

    def _get_valid_pin_auto_cache(self, settings: dict[str, Any]) -> list[dict[str, Any]] | None:
        cache_json = settings.get("auto_cache_json")
        computed_at = settings.get("auto_cache_computed_at")
        if not cache_json or not computed_at:
            return None
        try:
            computed = parse_utc(computed_at)
            if computed is None or utc_now() - computed >= timedelta(hours=24):
                return None
            cache = json.loads(cache_json) if isinstance(cache_json, str) else cache_json
            groups = cache.get("groups") if isinstance(cache, dict) else None
            if not isinstance(groups, list):
                return None
            return [group for group in groups if isinstance(group, dict)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def filter_source_keys(
        self,
        *,
        actor: str,
        profile_key: str | None,
        source_type: str,
        source_keys: list[str],
    ) -> list[str]:
        """按来源级策略过滤可见的 service key。

        来源级默认拒绝：profile 下若没有任何 allow 规则则全部不可见；
        再用 deny 从 allow 里扣除。``profile_key is None`` 时（管理员/无治理
        上下文）放行全部来源。
        """
        normalized_source_type = self._validate_source_type(source_type)
        if profile_key is None:
            return source_keys

        profile = self._require_profile_read(actor, profile_key)
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
            # 来源级默认拒绝：profile 没有任何 allow 规则 → 全部 source 不可见
            logger.warning(
                "能力来源被拒绝 actor=%s profile=%s source_type=%s 原因=%s",
                actor,
                profile_key,
                normalized_source_type,
                "无 allow 规则",
            )
            return []
        allowed = [source_key for source_key in source_keys if source_key in allow]
        result = [source_key for source_key in allowed if source_key not in deny]
        # 仅在被 deny 实际拦截到本应放行的来源时记一条，避免每次查询打噪音
        denied = [source_key for source_key in allowed if source_key in deny]
        if denied:
            logger.warning(
                "能力来源被拒绝 actor=%s profile=%s source_type=%s blocked=%s 原因=%s",
                actor,
                profile_key,
                normalized_source_type,
                denied,
                "命中 deny 规则",
            )
        return result

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
        """按资源级策略过滤可见的 resource key（纯 allow-list，无 deny）。

        wiki_kb / code_repo 资源只在 profile 显式 allow 时可见；不在 allow
        集合中的资源一律隐藏，但不抛异常——由上层（如 wiki/codegraph builtin
        provider）在调用具体资源时决定是否转为“被拒绝”。
        """
        normalized_resource_type = self._validate_resource_type(resource_type)
        if profile_key is None:
            return resource_keys

        profile = self._require_profile_read(actor, profile_key)
        if profile.get("status") != "active":
            raise ValidationError("profile is disabled")

        rules = self.store.list_profile_resource_rules(profile_key)
        allow = {
            rule["resource_key"]
            for rule in rules
            if rule["resource_type"] == normalized_resource_type
        }
        blocked = [key for key in resource_keys if key not in allow]
        if blocked:
            logger.warning(
                "能力资源被拒绝 actor=%s profile=%s resource_type=%s blocked=%s 原因=%s",
                actor,
                profile_key,
                normalized_resource_type,
                blocked,
                "不在 allow 列表",
            )
        visible = [resource_key for resource_key in resource_keys if resource_key in allow]
        if self.access is None:
            return visible
        scoped_type = {
            ProfileResourceType.wiki_kb.value: ScopedResourceType.knowledge_base,
            ProfileResourceType.code_repo.value: ScopedResourceType.code_repository,
            ProfileResourceType.business_ledger.value: ScopedResourceType.business_ledger,
        }[normalized_resource_type]
        result = []
        for resource_key in visible:
            record = self.access.get_resource(scoped_type, resource_key)
            if self.access.can_read(actor=actor, scope=ResourceScope.from_record(record)):
                result.append(resource_key)
        return result

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
        normalized_type = self._validate_resource_type(resource_type)
        self._require_profile_resource_read(actor, normalized_type, resource_key)
        rows = self.store.list_resource_rule_profiles(
            resource_type=normalized_type, resource_key=resource_key
        )
        return [row for row in rows if self._can_read_profile(actor, str(row["profile_key"]))]

    def set_resource_profiles(
        self,
        actor: str,
        resource_type: str,
        resource_key: str,
        profile_keys: list[str],
        overrides: dict[str, dict[str, str | None]] | None = None,
    ) -> dict[str, Any]:
        normalized_type = self._validate_resource_type(resource_type)
        self._require_profile_resource_read(actor, normalized_type, resource_key)
        existing = self.store.list_resource_rule_profiles(
            resource_type=normalized_type, resource_key=resource_key
        )
        preserved = [
            row for row in existing
            if not self._can_write_profile(actor, str(row["profile_key"]))
        ]
        for pk in profile_keys:
            self._require_profile_write(actor, pk)
        merged_profile_keys = [str(row["profile_key"]) for row in preserved] + profile_keys
        merged_overrides = {
            str(row["profile_key"]): {
                "retrieval_backend_slug": row.get("retrieval_backend_slug"),
                "retrieval_agent_id": row.get("retrieval_agent_id"),
            }
            for row in preserved
        }
        merged_overrides.update(overrides or {})
        self.store.replace_resource_rule_profiles(
            resource_type=normalized_type,
            resource_key=resource_key,
            profile_keys=merged_profile_keys,
            overrides=merged_overrides,
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
        """工具调用的唯一审计出口。

        每次 search/execute（含被拒绝的）都经此写一行 tool_call_log，带
        status / failure_stage / failure_owner / error_type / duration_ms。
        故意不打 INFO——这是高频审计写入，逐条打日志会成为噪音；可观测性靠
        事后查 tool_call_logs 表而非日志流。失败时调用方会把返回的 log_id
        缝进异常信息便于关联。
        """
        normalized_source_type = self._optional_enum(source_type, SourceType, "source type")
        normalized_status = self._validate_call_log_status(status)
        normalized_failure_stage = self._optional_enum(failure_stage, FailureStage, "failure stage")
        normalized_failure_owner = self._optional_enum(failure_owner, FailureOwner, "failure owner")
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
            owner_group_key=(
                str(self.access.actor_group_key(actor) or "") if self.access is not None else ""
            ),
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
        search: str | None = None,
        paginated: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if self.access is None:
            require_admin_user(actor, self.admins)
        enforce_scope = self.access is not None and actor not in self.admins
        viewer_group_key = (
            self.access.actor_group_key(actor, required=True)
            if enforce_scope and self.access is not None
            else None
        )
        normalized_source_type = self._optional_enum(source_type, SourceType, "source type")
        normalized_status = self._optional_enum(status, CallLogStatus, "call log status")
        normalized_failure_stage = self._optional_enum(failure_stage, FailureStage, "failure stage")
        normalized_failure_owner = self._optional_enum(failure_owner, FailureOwner, "failure owner")
        kwargs = dict(
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
            search=search,
            viewer_group_key=viewer_group_key,
            enforce_scope=enforce_scope,
        )
        if paginated:
            return self.store.governance.list_tool_call_logs_page(**kwargs)
        if search is not None:
            return self.store.governance.list_tool_call_logs(**kwargs)
        kwargs.pop("search")
        return self.store.list_tool_call_logs(**kwargs)

    def get_log(self, *, actor: str, log_id: str) -> dict[str, Any]:
        log = self.store.get_tool_call_log(log_id)
        if log is None:
            raise NotFound("tool call log not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(log))
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
        if self.access is None:
            require_admin_user(actor, self.admins)
        enforce_scope = self.access is not None and actor not in self.admins
        viewer_group_key = (
            self.access.actor_group_key(actor, required=True)
            if enforce_scope and self.access is not None
            else None
        )
        try:
            items = self.store.aggregate_tool_call_stats(
                dimensions=dimensions,
                created_from=created_from,
                created_to=created_to,
                bucket=bucket,
                viewer_group_key=viewer_group_key,
                enforce_scope=enforce_scope,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {"dimensions": dimensions, "bucket": bucket, "items": items}

    def _validate_rule(self, rule: dict[str, str], *, actor: str) -> dict[str, str]:
        source_type = self._validate_source_type(rule.get("source_type"))

        try:
            effect = ProfileRuleEffect(rule["effect"]).value
        except (KeyError, ValueError) as exc:
            raise ValidationError("invalid rule effect") from exc

        source_key = str(rule.get("source_key") or "").strip()
        if not source_key:
            raise ValidationError("source_key is required")

        if self.access is not None and source_type in {
            SourceType.mcp_service.value,
            SourceType.openapi_service.value,
        }:
            self.access.require_resource_read(
                actor=actor,
                resource_type=(
                    ScopedResourceType.mcp_service
                    if source_type == SourceType.mcp_service.value
                    else ScopedResourceType.openapi_service
                ),
                resource_key=source_key,
            )

        return {"source_type": source_type, "source_key": source_key, "effect": effect}

    def _validate_resource_rule(self, rule: dict[str, Any], *, actor: str) -> dict[str, str]:
        resource_type = self._validate_resource_type(rule.get("resource_type"))

        resource_key = str(rule.get("resource_key") or "").strip()
        if not resource_key:
            raise ValidationError("resource_key is required")

        self._require_profile_resource_read(actor, resource_type, resource_key)

        return {"resource_type": resource_type, "resource_key": resource_key}

    def _require_profile_read(self, actor: str, profile_key: str) -> dict[str, Any]:
        if self.access is not None:
            return self.access.require_resource_read(
                actor=actor,
                resource_type=ScopedResourceType.capability_profile,
                resource_key=profile_key,
            )
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return profile

    def _require_profile_write(self, actor: str, profile_key: str) -> dict[str, Any]:
        if self.access is not None:
            return self.access.require_resource_write(
                actor=actor,
                resource_type=ScopedResourceType.capability_profile,
                resource_key=profile_key,
            )
        require_admin_user(actor, self.admins)
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        return profile

    def _can_read_profile(self, actor: str, profile_key: str) -> bool:
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            return False
        return self.access is None or self.access.can_read(
            actor=actor, scope=ResourceScope.from_record(profile)
        )

    def _can_write_profile(self, actor: str, profile_key: str) -> bool:
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            return False
        if self.access is None:
            return actor in self.admins
        return self.access.can_write(actor=actor, scope=ResourceScope.from_record(profile))

    def _require_profile_resource_read(
        self, actor: str, resource_type: str, resource_key: str
    ) -> dict[str, Any] | None:
        if self.access is None:
            return None
        scoped_type = {
            ProfileResourceType.wiki_kb.value: ScopedResourceType.knowledge_base,
            ProfileResourceType.code_repo.value: ScopedResourceType.code_repository,
            ProfileResourceType.business_ledger.value: ScopedResourceType.business_ledger,
        }[resource_type]
        return self.access.require_resource_read(
            actor=actor, resource_type=scoped_type, resource_key=resource_key
        )

    def _validate_source_type(self, source_type: str | None) -> str:
        try:
            return SourceType(source_type).value
        except ValueError as exc:
            logger.warning("策略校验失败：非法 source_type=%s", source_type)
            raise ValidationError("invalid source type") from exc

    def _validate_resource_type(self, resource_type: str | None) -> str:
        try:
            return ProfileResourceType(resource_type).value
        except ValueError as exc:
            logger.warning("策略校验失败：非法 resource_type=%s", resource_type)
            raise ValidationError("invalid resource type") from exc

    def _validate_call_log_status(self, status: str) -> str:
        try:
            return CallLogStatus(status).value
        except ValueError as exc:
            logger.warning("审计日志校验失败：非法 status=%s", status)
            raise ValidationError("invalid call log status") from exc

    def _optional_enum(self, value: str | None, enum_cls: type, label: str) -> str | None:
        if value is None:
            return None
        try:
            return enum_cls(value).value
        except ValueError as exc:
            logger.warning("策略校验失败：非法 %s=%s", label, value)
            raise ValidationError(f"invalid {label}") from exc
