"""平台概览的聚合与磁盘缓存。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agent_bridge.core.cache import DiskCacheStore
from agent_bridge.core.domain import ValidationError
from agent_bridge.core.timeutil import utc_iso

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_TTL_SECONDS = 4 * 60 * 60
_CATEGORIES = ("documents", "code", "memory", "ledger", "capability")


class DashboardOverviewService:
    """聚合平台概览所需数据，并隔离缓存到当前可见资源范围。"""

    def __init__(
        self,
        *,
        cache_dir: Path,
        admins: set[str],
        actor_group_key: Callable[[str], str | None],
        list_kbs: Callable[[str], list[dict[str, Any]]],
        list_repositories: Callable[[str], list[dict[str, Any]]],
        list_memory_blocks: Callable[[str], list[dict[str, Any]]],
        list_ledgers: Callable[[str], list[dict[str, Any]]],
        list_mcp_services: Callable[[str], list[dict[str, Any]]],
        list_openapi_services: Callable[[str], list[dict[str, Any]]],
        list_workflows: Callable[[str], list[dict[str, Any]]],
        list_workflow_runs: Callable[[str, str], list[dict[str, Any]]],
        list_logs: Callable[..., dict[str, Any]],
        tool_stats: Callable[..., dict[str, Any]],
    ) -> None:
        self._admins = admins
        self._actor_group_key = actor_group_key
        self._list_kbs = list_kbs
        self._list_repositories = list_repositories
        self._list_memory_blocks = list_memory_blocks
        self._list_ledgers = list_ledgers
        self._list_mcp_services = list_mcp_services
        self._list_openapi_services = list_openapi_services
        self._list_workflows = list_workflows
        self._list_workflow_runs = list_workflow_runs
        self._list_logs = list_logs
        self._tool_stats = tool_stats
        self._cache = DiskCacheStore(
            cache_dir,
            namespace="dashboard-overview",
            default_expire=DASHBOARD_CACHE_TTL_SECONDS,
        )

    def overview(
        self,
        *,
        actor: str,
        created_from: str,
        created_to: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        days = _dashboard_days(created_from, created_to)
        resources = self._visible_resources(actor)
        cache_key = {
            "version": 1,
            "actor": actor,
            "actor_group_key": None if actor in self._admins else self._actor_group_key(actor),
            "is_admin": actor in self._admins,
            "created_from": created_from,
            "created_to": created_to,
            "resources": _resource_fingerprint(resources),
        }
        if not refresh:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                logger.debug("平台概览命中磁盘缓存 actor=%s", actor)
                return cached

        result = self._build_overview(
            actor=actor,
            created_from=created_from,
            created_to=created_to,
            days=days,
            resources=resources,
        )
        self._cache.set(cache_key, result, expire=DASHBOARD_CACHE_TTL_SECONDS)
        logger.debug("平台概览写入磁盘缓存 actor=%s", actor)
        return result

    def _visible_resources(self, actor: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "kbs": self._list_kbs(actor),
            "repositories": self._list_repositories(actor),
            "memory_blocks": self._list_memory_blocks(actor),
            "ledgers": self._list_ledgers(actor),
            "mcp_services": self._list_mcp_services(actor),
            "openapi_services": self._list_openapi_services(actor),
            "workflows": self._list_workflows(actor),
        }

    def _build_overview(
        self,
        *,
        actor: str,
        created_from: str,
        created_to: str,
        days: list[str],
        resources: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        workflow_days = {day: {"key": day, "success": 0, "failed": 0} for day in days}
        for workflow in resources["workflows"]:
            workflow_key = str(workflow.get("workflow_key") or "")
            if not workflow_key:
                continue
            for run in self._list_workflow_runs(actor, workflow_key):
                day = _date_key(run.get("finished_at") or run.get("started_at"))
                if day not in workflow_days:
                    continue
                if run.get("status") == "completed":
                    workflow_days[day]["success"] += 1
                elif run.get("status") == "failed":
                    workflow_days[day]["failed"] += 1

        stats = self._tool_stats(
            actor=actor,
            dimensions=["resource_type", "source_type", "status"],
            created_from=created_from,
            created_to=created_to,
            bucket="day",
        )
        tool_calls_by_day = {category: [0] * len(days) for category in _CATEGORIES}
        day_positions = {day: index for index, day in enumerate(days)}
        for item in stats.get("items", []):
            category = _category_for_values(item.get("resource_type"), item.get("source_type"))
            position = day_positions.get(str(item.get("bucket") or ""))
            if category and position is not None:
                tool_calls_by_day[category][position] += int(item.get("calls") or 0)

        recent = {category: {"log": None, "calls": sum(tool_calls_by_day[category])} for category in _CATEGORIES}
        log_page = self._list_logs(
            actor=actor,
            created_from=created_from,
            created_to=created_to,
            limit=200,
            paginated=True,
        )
        for log in log_page.get("items", []):
            category = _category_for_values(log.get("resource_type"), log.get("source_type"), log.get("source_key"))
            if category and recent[category]["log"] is None:
                recent[category]["log"] = {
                    "tool_name": str(log.get("tool_name") or ""),
                    "created_at": str(log.get("created_at") or ""),
                    "status": str(log.get("status") or ""),
                }

        return {
            "asset_totals": {
                "documents": sum(int(kb.get("document_count") or 0) for kb in resources["kbs"]),
                "code": len(resources["repositories"]),
                "memory": len(resources["memory_blocks"]),
                "ledger": len(resources["ledgers"]),
                "capability": len(resources["mcp_services"]) + len(resources["openapi_services"]),
            },
            "workflow_days": [workflow_days[day] for day in days],
            "tool_calls_by_day": tool_calls_by_day,
            "recent_tool_activity": recent,
            "generated_at": utc_iso(),
        }


def _dashboard_days(created_from: str, created_to: str) -> list[str]:
    try:
        first_day = date.fromisoformat(created_from[:10])
        end_day = date.fromisoformat(created_to[:10])
    except ValueError as exc:
        raise ValidationError("概览日期范围格式无效") from exc
    if end_day <= first_day or end_day - first_day > timedelta(days=31):
        raise ValidationError("概览日期范围必须为 1 至 31 天")
    return [(first_day + timedelta(days=offset)).isoformat() for offset in range((end_day - first_day).days)]


def _resource_fingerprint(resources: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {
        "kbs": sorted(str(item.get("slug") or "") for item in resources["kbs"]),
        "repositories": sorted(str(item.get("repo_key") or "") for item in resources["repositories"]),
        "memory_blocks": sorted(str(item.get("block_key") or "") for item in resources["memory_blocks"]),
        "ledgers": sorted(str(item.get("ledger_key") or "") for item in resources["ledgers"]),
        "mcp_services": sorted(str(item.get("service_key") or "") for item in resources["mcp_services"]),
        "openapi_services": sorted(str(item.get("service_key") or "") for item in resources["openapi_services"]),
        "workflows": sorted(str(item.get("workflow_key") or "") for item in resources["workflows"]),
    }


def _date_key(value: object) -> str | None:
    raw = str(value or "")
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _category_for_values(resource_type: object, source_type: object, source_key: object = None) -> str | None:
    resource = str(resource_type or "").lower()
    source = str(source_type or "").lower()
    key = str(source_key or "").lower()
    if resource in {"knowledge_base", "wiki_kb"} or "wiki" in key or "knowledge" in key:
        return "documents"
    if resource in {"code_repository", "code_repo"} or "codegraph" in key or "code" in key:
        return "code"
    if resource == "memory_block" or "memory" in key or "claude-mem" in key:
        return "memory"
    if resource == "business_ledger" or "ledger" in key:
        return "ledger"
    if resource == "mcp_service" or resource == "openapi_service" or source in {"mcp_service", "openapi_service"}:
        return "capability"
    return None
