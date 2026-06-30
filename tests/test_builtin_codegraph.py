from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.capability_hub.models import ProfileResourceType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.app.service import AgentBridgeService


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
        "",
        60,
        False,
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


class FakeCodeGraphMcpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(
        self,
        project_dir: Path,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        self.calls.append(
            {"project_dir": project_dir, "tool_name": tool_name, "arguments": arguments, "timeout": timeout}
        )
        return {
            "is_error": False,
            "structured": {"answer": "explored"},
            "content": [{"type": "text", "text": "explored"}],
        }


def test_codegraph_builtin_search_exposes_only_explore_tool(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"), tags=["python"])
    _allow_repo(service, "web-app")

    root = service.capabilities.search("root", None, None, profile_key="safe-readonly")
    tools = service.capabilities.search("root", "codegraph", None, profile_key="safe-readonly")

    codegraph = next(item for item in root["items"] if item["service"] == "codegraph")
    assert codegraph["kind"] == "builtin"
    assert codegraph["tool_count"] == 1
    assert codegraph["resources"] == [
        {"resource_type": "code_repo", "resource_key": "web-app", "name": "Web App"}
    ]
    assert [item["tool"] for item in tools["items"]] == ["codegraph_explore"]
    schemas = {item["tool"]: item["input_schema"] for item in tools["items"]}
    assert schemas["codegraph_explore"]["required"] == ["repo", "query"]
    assert schemas["codegraph_explore"]["properties"]["repo"]["description"] == "要访问的代码仓库标识。"
    assert schemas["codegraph_explore"]["properties"]["query"]["description"] == "要在仓库内执行的查询内容。"
    assert tools["items"][0]["description"] == "在已授权代码仓库中进行结构化探索。"


def test_codegraph_builtin_explore_uses_repo_scoped_stdio_mcp_after_profile_check(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"), tags=["python"])
    service.store.save_sync_config(code_sync_cron="0 * * * *", mcp_timeout_seconds=150)
    fake_mcp = FakeCodeGraphMcpClient()
    service.codegraph.mcp_client = fake_mcp
    _allow_repo(service, "web-app")

    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "codegraph",
            "codegraph_explore",
            {"repo": "web-app", "query": "App flow"},
            profile_key="safe-readonly",
        )
    )

    assert result["result"]["repo"] == "web-app"
    assert result["result"]["query"] == "App flow"
    assert result["result"]["mcp_result"]["structured"] == {"answer": "explored"}
    assert fake_mcp.calls == [
        {
            "project_dir": wm_paths.repos_dir / "web-app",
            "tool_name": "codegraph_explore",
            "arguments": {"query": "App flow", "projectPath": str(wm_paths.repos_dir / "web-app")},
            "timeout": 150.0,
        }
    ]


def test_codegraph_builtin_blocks_unallowed_repo(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    fake_mcp = FakeCodeGraphMcpClient()
    service.codegraph.mcp_client = fake_mcp
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")

    with pytest.raises(ValidationError, match=r"resource is blocked by profile policy .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "codegraph_explore",
                {"repo": "web-app", "query": "App"},
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
    assert fake_mcp.calls == []


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

    async def fail_explore(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mcp unavailable")

    monkeypatch.setattr(service.codegraph, "explore", fail_explore)

    with pytest.raises(ValidationError, match=r"CodeGraph builtin backend failed: mcp unavailable .*log_id: call_"):
        asyncio.run(
            service.capabilities.execute(
                "root",
                "codegraph",
                "codegraph_explore",
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


def test_codegraph_builtin_legacy_semantic_tools_are_not_registered(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service_with_repo(wm_paths, _git_repo(tmp_path / "repo"))
    _allow_repo(service, "web-app")

    tools = service.capabilities.search("root", "codegraph", None, profile_key="safe-readonly")
    tool_names = [item["tool"] for item in tools["items"]]
    assert tool_names == ["codegraph_explore"]
