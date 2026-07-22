from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.knowledge_management.code_knowledge.backend import CliCodeGraphBackend
from agent_bridge.knowledge_management.code_knowledge.client import CodeGraphClient
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import AccessDenied, BackendUnavailable, NotFound, ValidationError
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


def _available_backend(
    monkeypatch: pytest.MonkeyPatch,
    **overrides,
) -> CliCodeGraphBackend:
    client = CodeGraphClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(client, "init", lambda path: None)
    monkeypatch.setattr(client, "index", lambda path: None)
    monkeypatch.setattr(client, "files", lambda path: [{"path": "app.py", "language": "python"}])
    for name, implementation in overrides.items():
        monkeypatch.setattr(client, name, implementation)
    return CliCodeGraphBackend(client=client)


@pytest.mark.codegraph_cli
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
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
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


def test_codegraph_read_methods_require_admin(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
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
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )

    with pytest.raises(AccessDenied):
        service.repository_overview("alice", "web-app")
    with pytest.raises(AccessDenied):
        service.search_code("alice", "web-app", query="hello")
    with pytest.raises(AccessDenied):
        service.list_files("alice", "web-app")


def test_codegraph_sync_fails_for_missing_branch(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(
        paths=wm_paths,
        store=store,
        admins={"root"},
        backend=_available_backend(monkeypatch),
    )
    service.upsert_repository(
        actor="root",
        repo_key="web-app",
        name="Web App",
        git_url=str(repo),
        branch="does-not-exist",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )

    with pytest.raises(ValidationError, match="CodeGraph 同步失败"):
        service.sync_repository("root", "web-app")

    saved = store.get_code_repository("web-app")
    assert saved is not None
    assert saved["last_error"]
    assert "does-not-exist" in saved["last_error"]


@pytest.mark.codegraph_cli
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
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
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


def test_startup_marks_stale_codegraph_runs_interrupted(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_code_repository(
        repo_key="web-app",
        name="Web App",
        git_url="https://example.test/web-app.git",
        branch="main",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )
    stale = store.create_codegraph_sync_run("web-app", status="running", stage="indexing")

    AgentBridgeService.create(wm_paths, {"root"})

    with store.connect() as conn:
        recovered = conn.execute(
            "SELECT status, stage, error, finished_at FROM codegraph_sync_runs WHERE id = ?",
            (stale["id"],),
        ).fetchone()

    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["stage"] == "interrupted"
    assert "server startup recovered stale run" in recovered["error"]
    assert recovered["finished_at"] is not None


def test_dashboard_repo_by_token_matches_running_dashboard_url(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})
    for repo_key in ("first", "second"):
        service.upsert_repository(
            actor="root",
            repo_key=repo_key,
            name=repo_key.title(),
            git_url=str(tmp_path / repo_key),
            branch="master",
            auth_ref="",
            description="",
            tags=[],
            category_key="",
            sync_interval_minutes=60,
            auto_understand=False,
            status="active",
        )

    def fake_dashboard_status(project_dir: Path) -> dict[str, object]:
        token = "first-token" if project_dir.name == "first" else "second-token"
        return {"running": True, "url": f"http://127.0.0.1:48000/?token={token}"}

    monkeypatch.setattr(service.ua_client, "dashboard_status", fake_dashboard_status)

    assert service.dashboard_repo_by_token("first-token") == "first"
    assert service.dashboard_repo_by_token("second-token") == "second"
    assert service.dashboard_repo_by_token("missing-token") is None


@pytest.mark.codegraph_cli
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
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )

    service.sync_repository("root", "web-app")

    assert service.search_code("root", "web-app", query="EXTERNAL_ONLY_CONTENT") == []


def test_codegraph_sync_uses_codegraph_cli_when_available(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(
        paths=wm_paths,
        store=store,
        admins={"root"},
        backend=_available_backend(monkeypatch),
    )

    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="Demo app", tags=["python"],
        category_key="",
        sync_interval_minutes=60, auto_understand=False, status="active",
    )
    run = service.sync_repository("root", "web-app")
    assert run["status"] == "succeeded"
    assert run["indexed"] == 1


def test_codegraph_queries_fail_clearly_when_cli_is_unavailable(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    """CodeGraph 不可用不能伪装成“没有匹配结果”。"""
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client = CodeGraphClient()
    client._available = False
    service = CodeGraphService(
        paths=wm_paths,
        store=store,
        admins={"root"},
        backend=CliCodeGraphBackend(client=client),
    )
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="", tags=[], category_key="", sync_interval_minutes=60, auto_understand=False, status="active",
    )
    local_repo = wm_paths.repos_dir / "web-app"
    local_repo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(repo), str(local_repo)], check=True, capture_output=True)

    for operation in (
        lambda: service.search_code("root", "web-app", "hello"),
        lambda: service.callers("root", "web-app", "hello"),
        lambda: service.callees("root", "web-app", "hello"),
        lambda: service.impact("root", "web-app", "hello"),
    ):
        with pytest.raises(BackendUnavailable, match="CodeGraph CLI 不可用"):
            operation()

    assert service.list_files("root", "web-app") == [{"path": "app.py", "language": "python"}]


def test_codegraph_sync_fails_clearly_when_cli_is_unavailable(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client = CodeGraphClient()
    client._available = False
    service = CodeGraphService(
        paths=wm_paths,
        store=store,
        admins={"root"},
        backend=CliCodeGraphBackend(client=client),
    )
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="", tags=[], category_key="",
        sync_interval_minutes=60, auto_understand=False, status="active",
    )

    with pytest.raises(BackendUnavailable, match="CodeGraph CLI 不可用"):
        service.sync_repository("root", "web-app")

    saved = store.get_code_repository("web-app")
    assert saved is not None
    assert "CodeGraph CLI 不可用" in str(saved["last_error"])


def test_codegraph_query_fails_clearly_when_index_is_not_ready(
    tmp_path: Path,
    wm_paths: AgentBridgePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(
        paths=wm_paths,
        store=store,
        admins={"root"},
        backend=_available_backend(monkeypatch),
    )
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="", tags=[], category_key="",
        sync_interval_minutes=60, auto_understand=False, status="active",
    )
    local_repo = wm_paths.repos_dir / "web-app"
    local_repo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(repo), str(local_repo)], check=True, capture_output=True)

    with pytest.raises(BackendUnavailable, match="索引未就绪"):
        service.search_code("root", "web-app", "hello")


def test_codegraph_get_file_rejects_paths_outside_repository(tmp_path: Path, wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(tmp_path / "repo"),
        branch="master", auth_ref="", description="", tags=[], category_key="", sync_interval_minutes=60, auto_understand=False, status="active",
    )
    local_repo = wm_paths.repos_dir / "web-app"
    local_repo.mkdir(parents=True)
    (local_repo / ".git").mkdir()
    (wm_paths.repos_dir / "secret.txt").write_text("SECRET\n", encoding="utf-8")

    with pytest.raises(NotFound, match="file not found"):
        service.get_file("root", "web-app", "../secret.txt")


def test_codegraph_semantic_methods_delegate_to_client(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    backend = _available_backend(
        monkeypatch,
        callers=lambda path, symbol: [
            {"name": "caller_fn", "kind": "function", "filePath": "main.py", "startLine": 10}
        ],
        callees=lambda path, symbol: [
            {"name": "callee_fn", "kind": "function", "filePath": "utils.py", "startLine": 5}
        ],
        impact=lambda path, symbol: [
            {"name": "impacted_fn", "kind": "function", "filePath": "handler.py", "startLine": 20}
        ],
    )
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"}, backend=backend)
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="", tags=[], category_key="", sync_interval_minutes=60, auto_understand=False, status="active",
    )
    service.sync_repository("root", "web-app")

    callers = service.callers("root", "web-app", "hello")
    assert len(callers) == 1
    assert callers[0]["symbol"] == "caller_fn"
    assert callers[0]["path"] == "main.py"

    callees = service.callees("root", "web-app", "hello")
    assert len(callees) == 1
    assert callees[0]["symbol"] == "callee_fn"

    impacted = service.impact("root", "web-app", "hello")
    assert len(impacted) == 1
    assert impacted[0]["symbol"] == "impacted_fn"

    files = service.list_files("root", "web-app")
    assert len(files) == 1
    assert files[0]["path"] == "app.py"


def test_codegraph_node_payload_unwraps_cli_query_result(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"})

    payload = service._codegraph_node_payload({
        "node": {
            "name": "hello",
            "kind": "function",
            "filePath": "app.py",
            "startLine": 1,
            "endLine": 2,
            "signature": "()",
        },
        "score": 90.0,
    })

    assert payload["path"] == "app.py"
    assert payload["symbol"] == "hello"
    assert payload["score"] == 90.0


def test_codegraph_overview_falls_back_to_files_when_cli_status_is_empty(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    backend = _available_backend(
        monkeypatch,
        status=lambda path: {},
        files=lambda path: [
            {"path": "app.py", "language": "python"},
            {"path": "README.md", "language": "markdown"},
        ],
    )
    service = CodeGraphService(paths=wm_paths, store=store, admins={"root"}, backend=backend)
    service.upsert_repository(
        actor="root", repo_key="web-app", name="Web App", git_url=str(repo),
        branch="master", auth_ref="", description="", tags=[], category_key="", sync_interval_minutes=60, auto_understand=False, status="active",
    )
    service.sync_repository("root", "web-app")

    overview = service.repository_overview("root", "web-app")

    assert overview["file_count"] == 2
