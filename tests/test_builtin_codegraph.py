from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.capabilities.models import ProfileResourceType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.knowledge.service import AgentBridgeService


def _git_repo(path: Path, content: str = "class App:\n    pass\n") -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=master"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def _service_with_repo(
    wm_paths: AgentBridgePaths,
    repo: Path,
    *,
    repo_key: str = "web-app",
    tags: list[str] | None = None,
) -> AgentBridgeService:
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.init_system()
    service.codegraph.upsert_repository(
        "root",
        repo_key,
        "Web App",
        str(repo),
        "master",
        "",
        "",
        tags or [],
        60,
        "active",
    )
    service.codegraph.sync_repository("root", repo_key)
    return service


def _allow_repo(service: AgentBridgeService, repo_key: str) -> None:
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [{"resource_type": ProfileResourceType.code_repo.value, "resource_key": repo_key}],
    )


def test_codegraph_builtin_search_and_execute_respect_profile(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"), tags=["python"])
    _allow_repo(service, "web-app")

    root = service.capabilities.search("root", None, None, profile_key="safe-readonly")
    tools = service.capabilities.search("root", "codegraph", None, profile_key="safe-readonly")
    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "codegraph",
            "search_code",
            {"repo": "web-app", "query": "App"},
            profile_key="safe-readonly",
        )
    )

    codegraph = next(item for item in root["items"] if item["service"] == "codegraph")
    assert codegraph["kind"] == "builtin"
    assert codegraph["resources"] == [
        {"resource_type": "code_repo", "resource_key": "web-app", "name": "Web App"}
    ]
    assert [item["tool"] for item in tools["items"]] == [
        "find_symbol",
        "get_file",
        "list_repositories",
        "repository_overview",
        "search_code",
        "callers",
        "callees",
        "impact",
        "list_files",
    ]
    schemas = {item["tool"]: item["input_schema"] for item in tools["items"]}
    assert schemas["search_code"]["required"] == ["repo", "query"]
    assert schemas["get_file"]["required"] == ["repo", "path"]
    assert result["result"]["matches"][0]["path"] == "app.py"


def test_codegraph_builtin_list_repositories_filters_allowed_repos(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo-a"), repo_key="web-app")
    service.codegraph.upsert_repository(
        "root",
        "api",
        "API",
        str(_git_repo(tmp_path / "repo-b", "def endpoint():\n    return True\n")),
        "master",
        "",
        "",
        [],
        60,
        "active",
    )
    _allow_repo(service, "web-app")

    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "codegraph",
            "list_repositories",
            {},
            profile_key="safe-readonly",
        )
    )

    assert result["result"]["repositories"] == [
        {"resource_type": "code_repo", "resource_key": "web-app", "name": "Web App"}
    ]


def test_codegraph_builtin_blocks_unallowed_repo(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "get_file",
                {"repo": "web-app", "path": "app.py"},
                profile_key="safe-readonly",
            )
        )

    log = service.governance.list_logs(actor="root", status="blocked")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "codegraph"
    assert log["resource_type"] == "code_repo"
    assert log["resource_key"] == "web-app"
    assert log["failure_stage"] == "profile_policy"
    assert log["failure_owner"] == "policy"
    assert log["error_type"] == "profile_policy_blocked"


def test_codegraph_builtin_unknown_tool_with_empty_args_raises_not_found(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    _allow_repo(service, "web-app")

    with pytest.raises(NotFound, match="tool not found"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "missing_tool",
                {},
                profile_key="safe-readonly",
            )
        )


def test_codegraph_builtin_backend_failure_is_classified(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    _allow_repo(service, "web-app")

    def fail_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(service.codegraph, "search_code", fail_search)

    with pytest.raises(ValidationError, match=r"CodeGraph builtin backend failed: index unavailable .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "search_code",
                {"repo": "web-app", "query": "App"},
                profile_key="safe-readonly",
            )
        )

    log = service.governance.list_logs(actor="root", status="error")[0]
    assert log["source_type"] == "builtin"
    assert log["source_key"] == "codegraph"
    assert log["failure_stage"] == "builtin_backend"
    assert log["failure_owner"] == "builtin_backend"
    assert log["error_type"] == "builtin_backend_error"
    assert log["resource_type"] == "code_repo"
    assert log["resource_key"] == "web-app"


def test_codegraph_builtin_semantic_tools_are_registered(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    _allow_repo(service, "web-app")

    tools = service.capabilities.search("root", "codegraph", None, profile_key="safe-readonly")
    tool_names = [item["tool"] for item in tools["items"]]
    assert "callers" in tool_names
    assert "callees" in tool_names
    assert "impact" in tool_names
    assert "list_files" in tool_names
    # total 9 tools
    assert len(tool_names) == 9
