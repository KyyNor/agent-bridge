"""CodeGraph repository storage and indexing service."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_bridge.codegraph.client import CodeGraphClient
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore


MAX_INDEX_CHARS = 20_000
MAX_FILE_BYTES = 2_000_000
SYMBOL_PATTERN = re.compile(r"^\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


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

    def upsert_repository(
        self,
        actor: str,
        repo_key: str,
        name: str,
        git_url: str,
        branch: str,
        auth_ref: str,
        description: str,
        tags: list[str],
        sync_interval_minutes: int,
        status: str,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if status not in {"active", "disabled"}:
            raise ValidationError("invalid repository status")
        if not repo_key or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo_key):
            raise ValidationError("invalid repository key")
        if not name:
            raise ValidationError("repository name is required")
        if not git_url:
            raise ValidationError("git url is required")
        if not branch:
            raise ValidationError("branch is required")
        if sync_interval_minutes < 1:
            raise ValidationError("sync interval must be positive")

        repository = self.store.upsert_code_repository(
            repo_key=repo_key,
            name=name,
            git_url=git_url,
            branch=branch,
            auth_ref=auth_ref,
            description=description,
            tags=tags,
            sync_interval_minutes=sync_interval_minutes,
            status=status,
        )
        return self._repository_payload(repository)

    def list_repositories(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [self._repository_payload(repo) for repo in self.store.list_code_repositories()]

    def get_status(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        available = self.client.is_available()
        return {
            "codegraph_installed": available,
            "message": None if available else "codegraph CLI 未安装，请运行 npm i -g @colbymchenry/codegraph",
        }

    def sync_repository(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        repo = self._require_repository(repo_key)
        self.paths.codegraph_dir.mkdir(parents=True, exist_ok=True)
        local_path = self.paths.codegraph_dir / repo_key
        run = self.store.create_codegraph_sync_run(repo_key, status="running", stage="git")
        started = time.perf_counter()

        try:
            self._sync_git(repo, local_path)
            self.store.update_codegraph_sync_run(int(run["id"]), stage="indexing")
            if self.client.is_available():
                self.client.init(local_path)
                self.client.index(local_path)
                indexed_count = 0
            else:
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

    def callers(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        if not self.client.is_available():
            return []
        local_path = self._local_path(repo_key)
        nodes = self.client.callers(local_path, symbol)
        return [self._codegraph_node_payload(n) for n in nodes[:limit]]

    def callees(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        if not self.client.is_available():
            return []
        local_path = self._local_path(repo_key)
        nodes = self.client.callees(local_path, symbol)
        return [self._codegraph_node_payload(n) for n in nodes[:limit]]

    def impact(self, actor: str, repo_key: str, symbol: str) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        if not self.client.is_available():
            return []
        local_path = self._local_path(repo_key)
        nodes = self.client.impact(local_path, symbol)
        return [self._codegraph_node_payload(n) for n in nodes]

    def list_files(self, actor: str, repo_key: str) -> list[dict[str, Any]]:
        self._require_repository(repo_key)
        if not self.client.is_available():
            return []
        local_path = self._local_path(repo_key)
        return self.client.files(local_path)

    def _require_repository(self, repo_key: str) -> dict[str, Any]:
        repo = self.store.get_code_repository(repo_key)
        if repo is None or repo.get("status") != "active":
            raise NotFound("repository not found")
        return repo

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

    def _sync_git(self, repo: dict[str, Any], local_path: Path) -> None:
        if local_path.exists() and not (local_path / ".git").exists():
            shutil.rmtree(local_path)
        if not local_path.exists():
            self._run_git(local_path.parent, ["clone", str(repo["git_url"]), str(local_path)])
        else:
            self._run_git(local_path, ["fetch", "--all", "--prune"])
        branch = str(repo["branch"])
        checkout = self._run_git_result(local_path, ["checkout", branch])
        if checkout.returncode == 0:
            self._advance_branch(local_path, branch)
            return
        remote_checkout = self._run_git_result(local_path, ["checkout", f"origin/{branch}"])
        if remote_checkout.returncode == 0:
            self._advance_branch(local_path, branch)
            return
        raise RuntimeError(
            f"git checkout failed for branch '{branch}': "
            f"{self._git_result_message(remote_checkout) or self._git_result_message(checkout)}"
        )

    def _advance_branch(self, local_path: Path, branch: str) -> None:
        remote_ref = f"origin/{branch}"
        remote_exists = self._run_git_result(local_path, ["rev-parse", "--verify", remote_ref])
        if remote_exists.returncode != 0:
            return
        result = self._run_git_result(local_path, ["reset", "--hard", remote_ref])
        if result.returncode != 0:
            raise RuntimeError(
                f"git branch advance failed for branch '{branch}': {self._git_result_message(result)}"
            )

    def _index_files(self, repo_key: str, local_path: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(local_path.rglob("*")):
            if path.is_symlink() or not path.is_file() or ".git" in path.relative_to(local_path).parts:
                continue
            content = self._read_text_for_index(path)
            if content is None:
                continue
            rel_path = path.relative_to(local_path).as_posix()
            language = self._language_for_path(path)
            line_count = len(content.splitlines())
            items.append(
                {
                    "repo_key": repo_key,
                    "item_type": "file",
                    "path": rel_path,
                    "symbol": None,
                    "language": language,
                    "line_start": 1 if content else None,
                    "line_end": line_count if content else None,
                    "content": content[:MAX_INDEX_CHARS],
                }
            )
            if language == "python":
                for match in SYMBOL_PATTERN.finditer(content):
                    symbol = match.group(2)
                    line_start = content.count("\n", 0, match.start()) + 1
                    line_end_index = content.find("\n", match.start())
                    if line_end_index < 0:
                        line_end_index = len(content)
                    line = content[match.start():line_end_index]
                    items.append(
                        {
                            "repo_key": repo_key,
                            "item_type": "symbol",
                            "path": rel_path,
                            "symbol": symbol,
                            "language": language,
                            "line_start": line_start,
                            "line_end": line_start,
                            "content": line[:MAX_INDEX_CHARS],
                        }
                    )
        return items

    def _read_text_for_index(self, path: Path) -> str | None:
        try:
            if path.is_symlink():
                return None
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
            data = path.read_bytes()
        except OSError:
            return None
        if b"\x00" in data[:4096]:
            return None
        try:
            return data.decode("utf-8")[:MAX_INDEX_CHARS]
        except UnicodeDecodeError:
            return None

    def _run_git(self, cwd: Path, args: list[str]) -> None:
        cwd.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    def _run_git_result(self, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        cwd.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)

    def _git_result_message(self, result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or result.stdout or "").strip()

    def _git_output(self, cwd: Path, args: list[str]) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def _repository_payload(self, repo: dict[str, Any]) -> dict[str, Any]:
        payload = dict(repo)
        payload["tags"] = json.loads(str(payload.pop("tags_json", "[]") or "[]"))
        return payload

    def _index_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        return dict(item)

    def _language_for_path(self, path: Path) -> str | None:
        return {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".java": "java",
            ".rb": "ruby",
            ".rs": "rust",
            ".php": "php",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".sql": "sql",
            ".sh": "shell",
        }.get(path.suffix.lower())
