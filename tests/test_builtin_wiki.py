from __future__ import annotations

import asyncio

import pytest

from wiki_manager.capabilities import ProfileResourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.domain import NotFound, ValidationError
from wiki_manager.services import WikiManagerService


def _service(wm_paths: WikiManagerPaths) -> WikiManagerService:
    service = WikiManagerService.create(wm_paths, {"root"})
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


def test_metamcp_root_search_lists_wiki_builtin_with_allowed_kbs(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", None, None, profile_key="safe-readonly")

    wiki = next(item for item in result["items"] if item["service"] == "wiki")
    assert wiki["kind"] == "builtin"
    assert wiki["tool_count"] == 4
    assert wiki["resources"] == [{"resource_type": "wiki_kb", "resource_key": "frontend-docs", "name": "Frontend Docs"}]


def test_register_external_service_rejects_builtin_wiki_key(wm_paths: WikiManagerPaths) -> None:
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


def test_metamcp_wiki_path_lists_fixed_tools(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = service.capabilities.search("root", "wiki", None, profile_key="safe-readonly")

    assert [item["tool"] for item in result["items"]] == ["ask", "get_document", "list_kbs", "search"]
    assert result["items"][0]["service"] == "wiki"
    assert result["items"][0]["display_tool"] == "wiki.ask"
    schemas = {item["tool"]: item["input_schema"] for item in result["items"]}
    assert schemas["ask"]["required"] == ["kb", "question"]
    assert schemas["get_document"]["required"] == ["kb", "doc_slug"]
    assert schemas["list_kbs"] == {"type": "object", "properties": {}}
    assert schemas["search"]["required"] == ["kb", "question"]
    assert "top_k" in schemas["search"]["properties"]


def test_wiki_list_kbs_respects_profile_resources(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    result = asyncio.run(service.capabilities.execute("root", "wiki", "list_kbs", {}, profile_key="safe-readonly"))

    assert result["service"] == "wiki"
    assert result["tool"] == "list_kbs"
    assert [kb["slug"] for kb in result["result"]["kbs"]] == ["frontend-docs"]


def test_wiki_execute_blocks_unallowed_kb(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "search",
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


def test_wiki_backend_failure_is_classified(wm_paths: WikiManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(wm_paths)

    def fail_search(actor: str, kb_slug: str, question: str, top_k: int = 6) -> list[object]:
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(service, "search", fail_search)

    with pytest.raises(ValidationError, match=r"Wiki builtin backend failed: backend unavailable .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "search",
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
    wm_paths: WikiManagerPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(wm_paths)

    def fail_search(actor: str, kb_slug: str, question: str, top_k: int = 6) -> list[object]:
        raise NotFound("knowledge base not found")

    monkeypatch.setattr(service, "search", fail_search)

    with pytest.raises(NotFound, match=r"knowledge base not found .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "wiki",
                "search",
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


def test_wiki_execute_unknown_tool_with_empty_args_raises_not_found(wm_paths: WikiManagerPaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(NotFound) as exc_info:
        asyncio.run(service.capabilities.execute("root", "wiki", "missing_tool", {}, profile_key="safe-readonly"))

    assert "tool not found" in str(exc_info.value)
