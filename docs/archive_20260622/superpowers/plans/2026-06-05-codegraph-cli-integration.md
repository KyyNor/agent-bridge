# CodeGraph CLI Integration — Phase 1: Minimal Replacement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom regex-based code indexer with colbymchenry/codegraph CLI for indexing and querying, while keeping backward compatibility when codegraph is not installed.

**Architecture:** Create a `CodeGraphClient` that wraps codegraph CLI subprocess calls. `CodeGraphService` delegates to this client when available, falling back to the old `_index_files` regex indexer. The `codegraph_index_items` SQLite table stops being written to when codegraph is available but remains for backward compat. Query methods (`search_code`, `find_symbol`) route through `CodeGraphClient.query()` instead of SQLite FTS.

**Tech Stack:** Python 3.11, subprocess, codegraph CLI (`@colbymchenry/codegraph`), pytest with monkeypatch

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/agent_bridge/codegraph/client.py` | Create | Thin CLI wrapper — init, index, status, query, files |
| `src/agent_bridge/codegraph/service.py` | Modify | Use CodeGraphClient for sync and queries |
| `tests/test_codegraph_client.py` | Create | Unit tests for CodeGraphClient |
| `tests/test_codegraph_service.py` | Modify | Mock CodeGraphClient, test both codegraph and fallback paths |
| `tests/test_builtin_codegraph.py` | Modify | Ensure builtin provider works with new service |

---

## Task 1: Create CodeGraphClient

**Files:**
- Create: `src/agent_bridge/codegraph/client.py`
- Create: `tests/test_codegraph_client.py`

- [ ] **Step 1: Write the CodeGraphClient**

Create `src/agent_bridge/codegraph/client.py`:

```python
"""Thin wrapper around the codegraph CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


class CodeGraphClient:
    def __init__(self, cli_path: str = "codegraph") -> None:
        self.cli_path = cli_path
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            result = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            self._available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            self._available = False
        return self._available

    def init(self, project_dir: Path) -> None:
        self._run(project_dir, ["init", "-i"])

    def index(self, project_dir: Path) -> None:
        self._run(project_dir, ["index"])

    def sync(self, project_dir: Path) -> None:
        self._run(project_dir, ["sync"])

    def status(self, project_dir: Path) -> dict[str, Any]:
        result = self._run(project_dir, ["status"])
        return self._parse_status(result.stdout)

    def query(self, project_dir: Path, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["query", "--json", term])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            items = data.get("results", data.get("nodes", []))
        else:
            items = data
        return items[:limit]

    def files(self, project_dir: Path) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["files", "--json"])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("files", [])
        return data if isinstance(data, list) else []

    def callers(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["callers", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("callers", data.get("results", []))
        return data if isinstance(data, list) else []

    def callees(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["callees", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("callees", data.get("results", []))
        return data if isinstance(data, list) else []

    def impact(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        result = self._run(project_dir, ["impact", "--json", symbol])
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data.get("impacted", data.get("results", []))
        return data if isinstance(data, list) else []

    def _run(self, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.cli_path, *args],
            cwd=cwd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            msg = result.stderr or result.stdout or f"codegraph {' '.join(args)} failed"
            raise RuntimeError(msg.strip())
        return result

    def _parse_status(self, output: str) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for line in output.splitlines():
            m = re.match(r"\s*(\w[\w\s]*?)\s*:\s*([\d,]+)", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                stats[key] = int(m.group(2).replace(",", ""))
        return stats
```

- [ ] **Step 2: Write tests for CodeGraphClient**

Create `tests/test_codegraph_client.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent_bridge.codegraph.client import CodeGraphClient


def _mock_completed_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def test_is_available_returns_true_when_cli_exists() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout="codegraph 1.0.0")
        assert client.is_available() is True
        mock_run.assert_called_once()
        # cached
        assert client.is_available() is True
        assert mock_run.call_count == 1


def test_is_available_returns_false_when_missing() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run", side_effect=FileNotFoundError):
        assert client.is_available() is False


def test_init_calls_correct_args(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process()
        client.init(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["codegraph", "init", "-i"]


def test_index_calls_correct_args(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process()
        client.index(tmp_path)
        args = mock_run.call_args[0][0]
        assert args == ["codegraph", "index"]


def test_query_returns_parsed_json(tmp_path: Path) -> None:
    client = CodeGraphClient()
    nodes = [{"name": "hello", "kind": "function", "filePath": "app.py", "score": 1.0}]
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=json.dumps({"results": nodes}))
        result = client.query(tmp_path, "hello")
        assert result == nodes


def test_query_limits_results(tmp_path: Path) -> None:
    client = CodeGraphClient()
    nodes = [{"name": f"fn_{i}"} for i in range(50)]
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=json.dumps({"results": nodes}))
        result = client.query(tmp_path, "fn", limit=10)
        assert len(result) == 10


def test_status_parses_text_output(tmp_path: Path) -> None:
    client = CodeGraphClient()
    output = "Files: 42\nNodes: 1,234\nEdges: 5,678\n"
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=output)
        result = client.status(tmp_path)
        assert result["files"] == 42
        assert result["nodes"] == 1234
        assert result["edges"] == 5678


def test_run_raises_on_nonzero_exit(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stderr="fatal error", returncode=1)
        with pytest.raises(RuntimeError, match="fatal error"):
            client.index(tmp_path)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_codegraph_client.py -v`
Expected: All 8 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent_bridge/codegraph/client.py tests/test_codegraph_client.py
git commit -m "feat: add CodeGraphClient wrapping codegraph CLI subprocess calls"
```

---

## Task 2: Modify CodeGraphService to Use CodeGraphClient

**Files:**
- Modify: `src/agent_bridge/codegraph/service.py`
- Modify: `tests/test_codegraph_service.py`

- [ ] **Step 1: Modify CodeGraphService.__init__ to accept CodeGraphClient**

In `src/agent_bridge/codegraph/service.py`, change `__init__`:

```python
from agent_bridge.codegraph.client import CodeGraphClient

class CodeGraphService:
    def __init__(
        self,
        paths: AgentBridgePaths,
        store: SQLiteStore,
        admins: set[str],
        codegraph_client: CodeGraphClient | None = None,
    ) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.client = codegraph_client or CodeGraphClient()
```

- [ ] **Step 2: Modify sync_repository to use CodeGraphClient when available**

Replace the sync logic in `sync_repository`. After the git clone/fetch (`self._sync_git`), check if codegraph is available. If yes, run `init` + `index`. If not, fall back to `_index_files`.

```python
def sync_repository(self, actor: str, repo_key: str) -> dict[str, Any]:
    require_admin_user(actor, self.admins)
    repo = self._require_repository(repo_key)
    self.paths.codegraph_dir.mkdir(parents=True, exist_ok=True)
    local_path = self.paths.codegraph_dir / repo_key
    run = self.store.create_codegraph_sync_run(repo_key, status="running", stage="git")
    started = time.perf_counter()

    try:
        self._sync_git(repo, local_path)
        use_codegraph = self.client.is_available()

        if use_codegraph:
            stage = "codegraph_init"
            self.store.update_codegraph_sync_run(int(run["id"]), stage=stage)
            self.client.init(local_path)
            self.store.update_codegraph_sync_run(int(run["id"]), stage="codegraph_index")
            self.client.index(local_path)
            indexed_count = 0
        else:
            stage = "index_files"
            self.store.update_codegraph_sync_run(int(run["id"]), stage=stage)
            items = self._index_files(repo_key, local_path)
            self.store.replace_codegraph_index(repo_key, items)
            indexed_count = len(items)

        last_commit = self._git_output(local_path, ["rev-parse", "HEAD"])
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.store.mark_code_repository_sync(
            repo_key,
            local_path=str(local_path),
            last_commit=last_commit,
            success=True,
            error=None,
        )
        self.store.finish_codegraph_sync_run(
            int(run["id"]),
            status="succeeded",
            stage="indexed",
            error=None,
            duration_ms=duration_ms,
        )
        return {"repo_key": repo_key, "status": "succeeded", "indexed": indexed_count}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        message = str(exc)
        self.store.mark_code_repository_sync(
            repo_key,
            local_path=str(local_path),
            last_commit=None,
            success=False,
            error=message,
        )
        self.store.finish_codegraph_sync_run(
            int(run["id"]),
            status="failed",
            stage="failed",
            error=message,
            duration_ms=duration_ms,
        )
        raise ValidationError(f"codegraph sync failed: {message}") from exc
```

Note: This requires adding `update_codegraph_sync_run` to `SQLiteStore`. If it doesn't exist, add a simple method:

```python
def update_codegraph_sync_run(self, run_id: int, *, stage: str) -> None:
    self._execute("UPDATE codegraph_sync_runs SET stage = ? WHERE id = ?", (stage, run_id))
```

- [ ] **Step 3: Modify search_code to use CodeGraphClient when available**

```python
def search_code(self, actor: str, repo_key: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    self._require_repository(repo_key)
    if self.client.is_available():
        local_path = self._local_path(repo_key)
        nodes = self.client.query(local_path, query, limit=limit)
        return [self._codegraph_node_payload(n) for n in nodes]
    return [
        self._index_payload(item)
        for item in self.store.search_codegraph_index(repo_key, query=query, item_type="file", limit=limit)
    ]
```

- [ ] **Step 4: Modify find_symbol to use CodeGraphClient when available**

```python
def find_symbol(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
    self._require_repository(repo_key)
    if self.client.is_available():
        local_path = self._local_path(repo_key)
        nodes = self.client.query(local_path, symbol, limit=limit)
        return [self._codegraph_node_payload(n) for n in nodes]
    return [
        self._index_payload(item)
        for item in self.store.search_codegraph_index(repo_key, query=symbol, item_type="symbol", limit=limit)
    ]
```

- [ ] **Step 5: Modify get_file to read from filesystem when codegraph is available**

```python
def get_file(self, actor: str, repo_key: str, path: str) -> dict[str, Any]:
    self._require_repository(repo_key)
    if self.client.is_available():
        local_path = self._local_path(repo_key)
        file_path = local_path / path
        if not file_path.is_file():
            raise NotFound("file not found")
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise NotFound("file not found") from None
        return {
            "repo_key": repo_key,
            "path": path,
            "language": self._language_for_path(file_path),
            "content": content,
        }
    item = self.store.get_codegraph_file(repo_key, path)
    if item is None:
        raise NotFound("file not found")
    return {
        "repo_key": repo_key,
        "path": item["path"],
        "language": item["language"],
        "content": item["content"],
    }
```

- [ ] **Step 6: Modify repository_overview to use CodeGraphClient.status when available**

```python
def repository_overview(self, actor: str, repo_key: str) -> dict[str, Any]:
    repo = self._require_repository(repo_key)
    if self.client.is_available():
        local_path = self._local_path(repo_key)
        try:
            stats = self.client.status(local_path)
        except RuntimeError:
            stats = {}
        return {
            **self._repository_payload(repo),
            "file_count": stats.get("files", 0),
            "symbol_count": stats.get("nodes", 0),
            "last_synced_at": repo.get("last_synced_at"),
        }
    return {
        **self._repository_payload(repo),
        "file_count": self.store.count_codegraph_index_items(repo_key, "file"),
        "symbol_count": self.store.count_codegraph_index_items(repo_key, "symbol"),
        "last_synced_at": repo.get("last_synced_at"),
    }
```

- [ ] **Step 7: Add helper methods _local_path and _codegraph_node_payload**

```python
def _local_path(self, repo_key: str) -> Path:
    return self.paths.codegraph_dir / repo_key

def _codegraph_node_payload(self, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": node.get("filePath", node.get("path", "")),
        "symbol": node.get("name", ""),
        "kind": node.get("kind", ""),
        "line_start": node.get("startLine"),
        "line_end": node.get("endLine"),
        "snippet": node.get("signature", node.get("snippet", "")),
        "score": node.get("score"),
    }
```

- [ ] **Step 8: Update tests**

In `tests/test_codegraph_service.py`, the existing tests use the fallback path (no codegraph installed). Add a new test that mocks CodeGraphClient to test the codegraph path:

```python
def test_codegraph_sync_uses_codegraph_cli_when_available(
    tmp_path: Path, wm_paths: AgentBridgePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # codegraph path returns 0 for indexed count (codegraph manages its own DB)
    assert run["indexed"] == 0
```

- [ ] **Step 9: Run tests to verify**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_codegraph_service.py tests/test_codegraph_client.py -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/agent_bridge/codegraph/service.py tests/test_codegraph_service.py
git commit -m "refactor: CodeGraphService uses CodeGraphClient when available, falls back to regex indexer"
```

---

## Task 3: Update SQLiteStore with Missing Methods

**Files:**
- Modify: `src/agent_bridge/storage/sqlite.py`

- [ ] **Step 1: Add update_codegraph_sync_run method**

Check if `update_codegraph_sync_run` exists in SQLiteStore. If not, add:

```python
def update_codegraph_sync_run(self, run_id: int, *, stage: str) -> None:
    self._execute("UPDATE codegraph_sync_runs SET stage = ? WHERE id = ?", (stage, run_id))
```

- [ ] **Step 2: Run tests to verify**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_codegraph_service.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/agent_bridge/storage/sqlite.py
git commit -m "feat: add update_codegraph_sync_run to SQLiteStore"
```

---

## Task 4: Verify Builtin Provider Tests Still Pass

**Files:**
- Modify: `tests/test_builtin_codegraph.py` (if needed)

- [ ] **Step 1: Run builtin codegraph tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_builtin_codegraph.py -v`
Expected: All 5 tests PASS (they use the fallback path since codegraph CLI is not installed)

- [ ] **Step 2: If any test breaks, fix by ensuring CodeGraphService works without codegraph CLI**

The key is that `CodeGraphService.__init__` creates `CodeGraphClient()` by default, and `is_available()` returns `False` when codegraph is not installed, so all methods fall back to the old SQLite-based path.

- [ ] **Step 3: Commit any fixes**

```bash
git add tests/test_builtin_codegraph.py
git commit -m "fix: ensure builtin codegraph tests work with CodeGraphClient fallback"
```

---

## Task 5: Add CodeGraph Status to API

**Files:**
- Modify: `src/agent_bridge/api/routes/builtins.py`
- Modify: `src/agent_bridge/codegraph/service.py`

- [ ] **Step 1: Add get_status method to CodeGraphService**

```python
def get_status(self, actor: str) -> dict[str, Any]:
    require_admin_user(actor, self.admins)
    available = self.client.is_available()
    return {
        "codegraph_installed": available,
        "message": None if available else "codegraph CLI 未安装，请运行 npm i -g @colbymchenry/codegraph",
    }
```

- [ ] **Step 2: Add status endpoint to builtins routes**

In `src/agent_bridge/api/routes/builtins.py`:

```python
@router.get("/builtin/codegraph/status")
def get_codegraph_status(current_actor: str = Depends(actor)) -> dict[str, Any]:
    ensure_capability_schema()
    return call_safely(lambda: service.codegraph.get_status(current_actor))
```

- [ ] **Step 3: Run all codegraph-related tests**

Run: `cd /Users/kyynor/Code/agent-bridge && python -m pytest tests/test_codegraph_service.py tests/test_codegraph_client.py tests/test_builtin_codegraph.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent_bridge/api/routes/builtins.py src/agent_bridge/codegraph/service.py
git commit -m "feat: add codegraph status endpoint reporting CLI availability"
```
