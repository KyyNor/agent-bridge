from __future__ import annotations

import asyncio
import time

import pytest

from agent_bridge.capability_hub.models import ProfileResourceType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.app.service import AgentBridgeService


def _service(wm_paths: AgentBridgePaths) -> AgentBridgeService:
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "payroll", "Payroll", "")
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [{"resource_type": ProfileResourceType.wiki_kb.value, "resource_key": "frontend-docs"}],
    )
    return service


def test_metamcp_root_search_lists_wiki_builtin_with_allowed_kbs(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", None, None, profile_key="safe-readonly")

    wiki = next(item for item in result["items"] if item["service"] == "wiki")
    assert wiki["kind"] == "builtin"
    assert wiki["tool_count"] == 4
    assert wiki["resources"] == [{"resource_type": "wiki_kb", "resource_key": "frontend-docs", "name": "Frontend Docs"}]


def test_register_external_service_rejects_builtin_wiki_key(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match="service_key is reserved for built-in capability"):
        service.capabilities.register_service(
            "root",
            "wiki",
            "External Wiki",
            "https://wiki.test/mcp",
            {},
            "",
            [],
        )
    service.store.create_mcp_service(
        service_key="wiki",
        name="External Wiki",
        endpoint_url="https://wiki.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    service.store.update_mcp_service_status("wiki", "enabled")

    result = service.capabilities.search("root", None, None)

    wiki_items = [item for item in result["items"] if item["service"] == "wiki"]
    assert [item["kind"] for item in wiki_items] == ["builtin"]


def test_metamcp_wiki_path_lists_fixed_tools(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", "wiki", None, profile_key="safe-readonly")

    assert [item["tool"] for item in result["items"]] == ["ask", "get_document", "list_kbs", "search_all"]
    assert result["items"][0]["service"] == "wiki"
    assert result["items"][0]["display_tool"] == "wiki.ask"
    schemas = {item["tool"]: item["input_schema"] for item in result["items"]}
    assert schemas["ask"]["required"] == ["kb", "question"]
    assert schemas["get_document"]["required"] == ["kb", "doc_slug"]
    assert schemas["list_kbs"] == {"type": "object", "properties": {}}
    assert schemas["ask"]["properties"]["kb"]["description"] == "要访问的知识库 slug。"
    assert schemas["ask"]["properties"]["question"]["description"] == "要向知识库提出的问题。"
    assert result["items"][0]["description"] == "向已授权知识库提问。"


def test_wiki_list_kbs_respects_profile_resources(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    result = asyncio.run(service.capabilities.execute("root", "wiki", "list_kbs", {}, profile_key="safe-readonly"))

    assert result["service"] == "wiki"
    assert result["tool"] == "list_kbs"
    assert [kb["slug"] for kb in result["result"]["kbs"]] == ["frontend-docs"]


def test_wiki_ask_does_not_block_event_loop(wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(wm_paths)

    def slow_ask(
        actor: str,
        kb_slug: str,
        question: str,
        session_id: str | None = None,
        profile_key: str | None = None,
    ) -> dict[str, object]:
        time.sleep(0.2)
        return {"answer": ""}

    monkeypatch.setattr(service, "ask", slow_ask)

    async def run_concurrent_tasks() -> float:
        started = time.monotonic()
        execute_task = asyncio.create_task(
            service.capabilities.execute(
                "root",
                "wiki",
                "ask",
                {"kb": "frontend-docs", "question": "css"},
                profile_key="safe-readonly",
            )
        )
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - started
        await execute_task
        return elapsed

    assert asyncio.run(run_concurrent_tasks()) < 0.1


def test_wiki_search_is_not_exposed_or_executable(wm_paths: AgentBridgePaths) -> None:
    from agent_bridge.capability_hub.sources.builtin.wiki import WIKI_SEARCH_ENABLED

    assert WIKI_SEARCH_ENABLED is False
    service = _service(wm_paths)

    with pytest.raises(NotFound, match="tool not found"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "search",
                {"kb": "frontend-docs", "question": "css"},
                profile_key="safe-readonly",
            )
        )


def test_wiki_execute_blocks_unallowed_kb(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "ask",
                {"kb": "payroll", "question": "salary"},
                profile_key="safe-readonly",
            )
        )

    log = service.governance.list_logs(actor="root", status="blocked")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "wiki"
    assert log["resource_type"] == "wiki_kb"
    assert log["resource_key"] == "payroll"
    assert log["error_type"] == "profile_policy_blocked"


def test_wiki_backend_failure_is_classified(wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(wm_paths)

    def fail_ask(
        actor: str,
        kb_slug: str,
        question: str,
        session_id: str | None = None,
        profile_key: str | None = None,
    ) -> dict[str, object]:
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(service, "ask", fail_ask)

    with pytest.raises(ValidationError, match=r"Wiki builtin backend failed: backend unavailable .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "ask",
                {"kb": "frontend-docs", "question": "css"},
                profile_key="safe-readonly",
            )
        )

    log = service.governance.list_logs(actor="root", status="error")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "wiki"
    assert log["failure_stage"] == "builtin_backend"
    assert log["failure_owner"] == "builtin_backend"
    assert log["error_type"] == "builtin_backend_error"
    assert log["resource_type"] == "wiki_kb"
    assert log["resource_key"] == "frontend-docs"


def test_wiki_domain_errors_are_not_classified_as_backend_failures(
    wm_paths: AgentBridgePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(wm_paths)

    def fail_ask(
        actor: str,
        kb_slug: str,
        question: str,
        session_id: str | None = None,
        profile_key: str | None = None,
    ) -> dict[str, object]:
        raise NotFound("knowledge base not found")

    monkeypatch.setattr(service, "ask", fail_ask)

    with pytest.raises(NotFound, match=r"knowledge base not found .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "ask",
                {"kb": "frontend-docs", "question": "css"},
                profile_key="safe-readonly",
            )
        )

    log = service.governance.list_logs(actor="root", status="error")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "wiki"
    assert log["failure_stage"] != "builtin_backend"
    assert log["error_type"] != "builtin_backend_error"
    assert log["resource_type"] == "wiki_kb"
    assert log["resource_key"] == "frontend-docs"


def test_wiki_execute_unknown_tool_with_empty_args_raises_not_found(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(NotFound) as exc_info:
        asyncio.run(service.capabilities.execute("root", "wiki", "missing_tool", {}, profile_key="safe-readonly"))

    assert "tool not found" in str(exc_info.value)
