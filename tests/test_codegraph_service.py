from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_bridge.codegraph.service import CodeGraphService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=master"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def _commit_file(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_codegraph_register_sync_and_search(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})

    saved = service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="master",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        sync_interval_minutes=60,
        status="active",
    )
    run = service.sync_repository("root", "web-app")
    files = service.search_code("root", "web-app", query="hello")

    assert saved["repo_key"] == "web-app"
    assert run["status"] == "succeeded"
    assert files[0]["path"] == "app.py"
    assert files[0]["language"] == "python"
    assert "hello" in files[0]["snippet"]
    assert service.get_file("root", "web-app", "app.py")["content"].startswith("def hello")


def test_codegraph_sync_fails_for_missing_branch(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})
    service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="does-not-exist",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        sync_interval_minutes=60,
        status="active",
    )

    with pytest.raises(ValidationError, match="codegraph sync failed"):
        service.sync_repository("root", "web-app")

    saved = store.get_code_repository("web-app")
    assert saved is not None
    assert saved["last_error"]
    assert "does-not-exist" in saved["last_error"]


def test_codegraph_sync_advances_existing_clone(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})
    service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="master",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        sync_interval_minutes=60,
        status="active",
    )
    service.sync_repository("root", "web-app")
    first_commit = store.get_code_repository("web-app")["last_commit"]
    upstream_commit = _commit_file(
        repo,
        "app.py",
        "def hello():\n    return 'world'\n\nNEW_UPSTREAM_CONTENT = True\n",
        "second",
    )

    service.sync_repository("root", "web-app")
    saved = store.get_code_repository("web-app")
    files = service.search_code("root", "web-app", query="NEW_UPSTREAM_CONTENT")

    assert saved["last_commit"] == upstream_commit
    assert saved["last_commit"] != first_commit
    assert files[0]["path"] == "app.py"
    assert "NEW_UPSTREAM_CONTENT" in files[0]["snippet"]


def test_codegraph_index_skips_symlinks(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    repo = _git_repo(tmp_path / "repo")
    external = tmp_path / "external.txt"
    external.write_text("EXTERNAL_ONLY_CONTENT\n", encoding="utf-8")
    (repo / "external-link.txt").symlink_to(external)
    subprocess.run(["git", "add", "external-link.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "symlink"], cwd=repo, check=True, capture_output=True)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})
    service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="master",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        sync_interval_minutes=60,
        status="active",
    )

    service.sync_repository("root", "web-app")

    assert service.search_code("root", "web-app", query="EXTERNAL_ONLY_CONTENT") == []


def test_codegraph_sync_uses_codegraph_cli_when_available(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bridge.codegraph.client import CodeGraphClient

    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client = CodeGraphClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(client, "init", lambda p: None)
    monkeypatch.setattr(client, "index", lambda p: None)
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"}, codegraph_client=client)

    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="Demo app", tags=["python"],
        sync_interval_minutes=60, status="active",
    )
    run = service.sync_repository("root", "web-app")
    assert run["status"] == "succeeded"
    assert run["indexed"] == 0
