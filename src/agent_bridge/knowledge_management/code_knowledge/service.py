"""CodeGraph repository storage and indexing service."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from agent_bridge.knowledge_management.code_knowledge.backend import CliCodeGraphBackend, CodeGraphBackend
from agent_bridge.knowledge_management.code_knowledge.dashboard_urls import external_dashboard_url
from agent_bridge.knowledge_management.code_knowledge.ua_client import UnderstandAnythingClient
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.core.domain import (
    AgentBridgeError,
    BackendUnavailable,
    NotFound,
    ValidationError,
    require_admin_user,
)
from agent_bridge.plugin_runtime import GitPluginRuntime
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


T = TypeVar("T")


class CodeGraphService:
    def __init__(
        self,
        paths: AgentBridgePaths,
        store: SQLiteStore,
        admins: set[str],
        backend: CodeGraphBackend | None = None,
        ua_client: UnderstandAnythingClient | None = None,
        agent_service: Any = None,
    ) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.backend = backend or CliCodeGraphBackend()
        self.ua_client = ua_client or UnderstandAnythingClient(root=paths.root, agent_service=agent_service)
        self.plugin_runtime = GitPluginRuntime(paths)

    def _mcp_timeout_seconds(self) -> float:
        config = self.store.get_sync_config()
        return float(config.get("mcp_timeout_seconds") or DEFAULT_MCP_TIMEOUT_SECONDS)

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
        category_key: str,
        sync_interval_minutes: int,
        auto_understand: bool,
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

        if not auth_ref:
            existing = self.store.get_code_repository(repo_key)
            if existing:
                auth_ref = existing.get("auth_ref", "")

        repository = self.store.upsert_code_repository(
            repo_key=repo_key,
            name=name,
            git_url=git_url,
            branch=branch,
            auth_ref=auth_ref,
            description=description,
            tags=tags,
            category_key=category_key,
            sync_interval_minutes=sync_interval_minutes,
            auto_understand=auto_understand,
            status=status,
        )
        return self._repository_payload(repository)

    def list_repositories(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [self._repository_payload(repo) for repo in self.store.list_code_repositories()]

    def delete_repository(self, actor: str, repo_key: str) -> dict[str, Any]:
        """硬删除一个代码仓库，并清理其副作用数据。

        清理顺序：停止 Understand Anything 进程（容错）→ 删除本地镜像目录 →
        清理能力平面里引用该仓库的 resource 规则（无外键，需手动删）→ 删除仓库
        行，依赖外键 ON DELETE CASCADE 清除 sync_runs / index_items。
        """
        from agent_bridge.capability_hub.models import ProfileResourceType

        require_admin_user(actor, self.admins)
        repo = self.store.get_code_repository(repo_key)
        if repo is None:
            raise NotFound("repository not found")
        local_path = self._local_path(repo_key)

        # 停止可能正在运行的 Understand Anything 进程（容错，失败不阻断删除）
        try:
            self.ua_client.stop_dashboard(local_path)
        except Exception:
            logger.warning("删除代码仓库 %s 时停止 UA 进程失败，已忽略", repo_key, exc_info=True)

        # 删除本地镜像目录（容错，目录可能不存在）
        shutil.rmtree(local_path, ignore_errors=True)

        # 治理软关联清理（无外键）：移除能力平面里引用该仓库的 resource 规则
        self.store.delete_resource_rules_by_key(
            resource_type=ProfileResourceType.code_repo.value, resource_key=repo_key
        )

        self.store.delete_code_repository(repo_key)
        logger.info("已删除代码知识库 %s", repo_key)
        return {"repo_key": repo_key, "deleted": True}

    def get_status(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        available = self.backend.is_available()
        return {
            "codegraph_installed": available,
            "message": None if available else "codegraph CLI 未安装，请运行 npm i -g @colbymchenry/codegraph",
        }

    def recover_interrupted_sync_runs(self) -> int:
        return self.store.interrupt_running_codegraph_sync_runs(
            error="server startup recovered stale run interrupted by prior process exit",
        )

    def stop_active_processes(self) -> None:
        self.backend.terminate_active_processes()

    def sync_repository(self, actor: str, repo_key: str) -> dict[str, Any]:
        """同步代码仓库：镜像 git → 建索引。

        CodeGraph CLI 是唯一索引后端；后端缺失或索引失败时明确标记失败。
        全程记录 sync run 状态；任一阶段失败都标记 run 并抛明确领域错误。
        """
        require_admin_user(actor, self.admins)
        repo = self._require_repository(repo_key)
        self.paths.repos_dir.mkdir(parents=True, exist_ok=True)
        local_path = self.paths.repos_dir / repo_key
        run = self.store.create_codegraph_sync_run(repo_key, status="running", stage="git")
        started = time.perf_counter()
        logger.info("仓库镜像开始 repo=%s 本地=%s", repo_key, local_path)

        try:
            self._require_backend_available("同步代码索引")
            self._sync_git(repo, local_path)
            logger.info("仓库镜像完成 repo=%s", repo_key)
            self.store.update_codegraph_sync_run(int(run["id"]), stage="indexing")
            logger.info("代码索引开始 repo=%s 后端=CodeGraph", repo_key)
            indexed_count = self._backend_call(
                "构建索引",
                lambda: self.backend.build_index(local_path),
            )
            logger.info("代码索引完成 repo=%s 索引项=%d", repo_key, indexed_count)
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
            logger.info("仓库同步完成 repo=%s 耗时=%dms", repo_key, duration_ms)
            return {"repo_key": repo_key, "status": "succeeded", "indexed": indexed_count}
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc)
            logger.error(
                "仓库同步失败 repo=%s 原因=%s 耗时=%dms",
                repo_key, message, duration_ms, exc_info=True,
            )
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
            if isinstance(exc, AgentBridgeError):
                raise
            raise ValidationError(f"CodeGraph 同步失败：{message}") from exc

    def search_code(self, actor: str, repo_key: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """使用正式 CodeGraph 后端搜索代码图。"""
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "查询")
        logger.debug("代码查询后端=CodeGraph repo=%s query=%s", repo_key, query)
        nodes = self._backend_call(
            "查询",
            lambda: self.backend.query(local_path, query, limit=limit),
        )
        return [self._codegraph_node_payload(n) for n in nodes]

    def get_file(self, actor: str, repo_key: str, path: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._require_local_repository(repo_key).resolve()
        file_path = (local_path / path).resolve()
        try:
            file_path.relative_to(local_path)
        except ValueError:
            raise NotFound("file not found")
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

    def find_symbol(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "查找符号")
        nodes = self._backend_call(
            "查找符号",
            lambda: self.backend.query(local_path, symbol, limit=limit),
        )
        return [self._codegraph_node_payload(n) for n in nodes]

    def repository_overview(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        repo = self._require_repository(repo_key)
        local_path = self._require_indexed_repository(repo_key, "读取索引状态")
        stats = self._backend_call("读取索引状态", lambda: self.backend.status(local_path))
        file_count = stats.get("files")
        if file_count is None:
            file_count = len(self._backend_call("列出索引文件", lambda: self.backend.files(local_path)))
        return {
            **self._repository_payload(repo),
            "file_count": file_count,
            "symbol_count": stats.get("nodes", stats.get("symbols", 0)),
            "last_synced_at": repo.get("last_synced_at"),
        }

    def callers(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "查询调用者")
        nodes = self._backend_call("查询调用者", lambda: self.backend.callers(local_path, symbol))
        return [self._codegraph_node_payload(n) for n in nodes[:limit]]

    def callees(self, actor: str, repo_key: str, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "查询被调用者")
        nodes = self._backend_call("查询被调用者", lambda: self.backend.callees(local_path, symbol))
        return [self._codegraph_node_payload(n) for n in nodes[:limit]]

    def impact(self, actor: str, repo_key: str, symbol: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "分析影响范围")
        nodes = self._backend_call("分析影响范围", lambda: self.backend.impact(local_path, symbol))
        return [self._codegraph_node_payload(n) for n in nodes]

    def list_files(self, actor: str, repo_key: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._require_local_repository(repo_key)
        return self._tracked_files(local_path)

    async def explore(self, actor: str, repo_key: str, query: str) -> dict[str, Any]:
        """通过 codegraph MCP 直连执行代码图查询（``codegraph_explore`` 工具）。"""
        require_admin_user(actor, self.admins)
        local_path = self._require_indexed_repository(repo_key, "探索代码图")
        logger.info("代码探索开始 repo=%s 后端=CodeGraph query=%s", repo_key, query)
        try:
            mcp_result = await self.backend.explore(
                local_path,
                query,
                timeout=self._mcp_timeout_seconds(),
            )
        except Exception as exc:
            logger.error("代码探索失败 repo=%s 后端=CodeGraph query=%s", repo_key, query, exc_info=True)
            raise BackendUnavailable(
                f"CodeGraph 探索失败，仓库索引可能未就绪，请重新同步：{exc}"
            ) from exc
        logger.info("代码探索完成 repo=%s 后端=CodeGraph query=%s", repo_key, query)
        return {
            "repo": repo_key,
            "query": query,
            "mcp_result": mcp_result,
        }

    def _require_repository(self, repo_key: str) -> dict[str, Any]:
        repo = self.store.get_code_repository(repo_key)
        if repo is None or repo.get("status") != "active":
            raise NotFound("repository not found")
        return repo

    def _local_path(self, repo_key: str) -> Path:
        return self.paths.repos_dir / repo_key

    def _require_local_repository(self, repo_key: str) -> Path:
        local_path = self._local_path(repo_key)
        if not local_path.is_dir() or not (local_path / ".git").is_dir():
            raise NotFound("代码仓库尚未同步，请先同步仓库")
        return local_path

    def _require_indexed_repository(self, repo_key: str, operation: str) -> Path:
        repo = self._require_repository(repo_key)
        local_path = self._require_local_repository(repo_key)
        self._require_backend_available(operation)
        indexed_commit = str(repo.get("last_commit") or "").strip()
        if not indexed_commit:
            raise BackendUnavailable(
                f"CodeGraph 索引未就绪，无法{operation}；请先同步仓库"
            )
        try:
            current_commit = self._git_output(local_path, ["rev-parse", "HEAD"])
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendUnavailable(f"无法确认仓库索引状态：{exc}") from exc
        if current_commit != indexed_commit:
            raise BackendUnavailable(
                f"CodeGraph 索引已过期，无法{operation}；请重新同步仓库"
            )
        return local_path

    def _require_backend_available(self, operation: str) -> None:
        if self.backend.is_available():
            return
        raise BackendUnavailable(
            f"CodeGraph CLI 不可用，无法{operation}；请先安装并确认 codegraph --version 可执行"
        )

    def _backend_call(self, operation: str, call: Callable[[], T]) -> T:
        self._require_backend_available(operation)
        try:
            return call()
        except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError, OSError) as exc:
            logger.error("CodeGraph 后端调用失败 操作=%s 原因=%s", operation, exc, exc_info=True)
            raise BackendUnavailable(
                f"CodeGraph {operation}失败，仓库索引可能未就绪，请重新同步：{exc}"
            ) from exc

    # -- Understand Anything --

    def get_understand_status(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        repo = self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        current_commit = repo.get("last_commit")
        result = self.ua_client.status(local_path, current_commit)
        dash = self.ua_client.dashboard_status(local_path)
        return {
            "graph_exists": result.graph_exists,
            "graph_path": result.graph_path,
            "stale": result.stale,
            "node_count": result.node_count,
            "edge_count": result.edge_count,
            "layer_count": result.layer_count,
            "tour_count": result.tour_count,
            "analyzed_at": result.analyzed_at,
            "git_commit": result.git_commit,
            "analyzed_files": result.analyzed_files,
            "error": result.error,
            "dashboard_running": dash.get("running", False),
            "dashboard_url": external_dashboard_url(repo_key, dash.get("url")),
        }

    def get_understand_summary(self, actor: str, repo_key: str) -> dict[str, Any] | None:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        result = self.ua_client.summary(local_path)
        if result is None:
            return {
                "project_name": None,
                "description": None,
                "languages": [],
                "frameworks": [],
                "modules": [],
                "key_nodes": [],
                "tours": [],
            }
        return {
            "project_name": result.project_name,
            "description": result.description,
            "languages": result.languages,
            "frameworks": result.frameworks,
            "modules": result.modules,
            "key_nodes": result.key_nodes,
            "tours": result.tours,
        }

    def check_understand_availability(self, actor: str, repo_key: str | None = None) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        project_dir = self._local_path(repo_key) if repo_key else None
        sync_config = self.store.get_sync_config()
        ua_git_url = sync_config.get("ua_git_url", "")
        avail = self.ua_client.check_availability_with_config(
            project_dir=project_dir, ua_git_url=ua_git_url,
        ) if project_dir else self.ua_client.check_availability()
        return {
            "claude_installed": avail.claude_installed,
            "ua_skill_available": avail.ua_skill_available,
            "message": avail.message,
            "ua_git_url_configured": bool(ua_git_url),
        }

    def ensure_understand_plugin(self, git_url: str) -> dict[str, Any]:
        return self.plugin_runtime.ensure_repo(plugin_key="understand-anything", git_url=git_url)

    def analyze_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
        """触发 Understand Anything 知识图谱分析（委托 ``ua_client.analyze``）。

        从 sync_config 读 ``ua_git_url`` 与 ``understand_timeout_minutes``。
        """
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        if not local_path.is_dir():
            raise NotFound("repository local path not found — please sync first")
        sync_config = self.store.get_sync_config()
        ua_git_url = sync_config.get("ua_git_url", "")
        timeout_minutes = int(sync_config.get("understand_timeout_minutes") or 120)
        logger.info("Understand 分析开始 repo=%s 超时=%dmin", repo_key, timeout_minutes)
        result = self.ua_client.analyze(
            local_path, ua_git_url=ua_git_url, timeout=timeout_minutes * 60
        )
        if result.success:
            logger.info(
                "Understand 分析完成 repo=%s 节点=%d 边=%d 耗时=%dms",
                repo_key, result.node_count, result.edge_count, result.duration_ms,
            )
        else:
            logger.warning(
                "Understand 分析失败 repo=%s 原因=%s 耗时=%dms",
                repo_key, result.error or "unknown error", result.duration_ms,
            )
        return {
            "success": result.success,
            "node_count": result.node_count,
            "edge_count": result.edge_count,
            "error": result.error,
            "output": result.output,
            "duration_ms": result.duration_ms,
        }

    def dashboard_status_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        return self._external_dashboard_payload(repo_key, self.ua_client.dashboard_status(local_path))

    def start_dashboard_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        if not local_path.is_dir():
            raise NotFound("repository local path not found — please sync first")
        return self._external_dashboard_payload(repo_key, self.ua_client.start_dashboard(local_path))

    def stop_dashboard_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        return self.ua_client.stop_dashboard(local_path)

    def touch_understand_dashboard(self, actor: str, repo_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_repository(repo_key)
        local_path = self._local_path(repo_key)
        self.ua_client.touch_dashboard(local_path)
        return {"ok": True}

    def dashboard_proxy_target(self, repo_key: str) -> str | None:
        repo = self.store.get_code_repository(repo_key)
        if repo is None or repo.get("status") != "active":
            return None
        dash = self.ua_client.dashboard_status(self._local_path(repo_key))
        if not dash.get("running"):
            return None
        url = dash.get("url")
        return str(url) if url else None

    def dashboard_repo_by_token(self, token: str) -> str | None:
        if not token:
            return None
        for repo in self.store.list_code_repositories():
            repo_key = str(repo.get("repo_key") or "")
            if not repo_key or repo.get("status") != "active":
                continue
            dash = self.ua_client.dashboard_status(self._local_path(repo_key))
            if not dash.get("running"):
                continue
            url = str(dash.get("url") or "")
            if parse_qs(urlsplit(url).query).get("token", [None])[0] == token:
                return repo_key
        return None

    def _external_dashboard_payload(self, repo_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "url" not in payload:
            return payload
        return {**payload, "url": external_dashboard_url(repo_key, payload.get("url"))}

    def _codegraph_node_payload(self, node: dict[str, Any]) -> dict[str, Any]:
        score = node.get("score")
        node = node.get("node", node)
        name = str(node.get("name", "") or "")
        signature = node.get("signature")
        snippet = node.get("snippet")
        if not snippet and signature:
            signature_text = str(signature)
            separator = "" if signature_text.startswith(("(", "[", "{")) else " "
            snippet = f"{name}{separator}{signature_text}".strip()
        return {
            "path": node.get("filePath", node.get("path", "")),
            "symbol": name,
            "kind": node.get("kind", ""),
            "language": node.get("language"),
            "line_start": node.get("startLine"),
            "line_end": node.get("endLine"),
            "snippet": snippet or name,
            "score": node.get("score", score),
        }

    def _sync_git(self, repo: dict[str, Any], local_path: Path) -> None:
        if local_path.exists() and not (local_path / ".git").exists():
            shutil.rmtree(local_path)
        auth_url = self._auth_url(repo)
        if not local_path.exists():
            self._run_git(local_path.parent, ["clone", auth_url, str(local_path)])
        else:
            self._run_git(local_path, ["fetch", "--all", "--prune"])
            self._run_git_result(local_path, ["remote", "set-url", "origin", auth_url])
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

    def _tracked_files(self, local_path: Path) -> list[dict[str, Any]]:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendUnavailable(f"读取 Git 仓库文件列表失败：{exc}") from exc
        files: list[dict[str, Any]] = []
        for raw_path in result.stdout.split(b"\0"):
            if not raw_path:
                continue
            path = raw_path.decode("utf-8", errors="surrogateescape")
            files.append({"path": path, "language": self._language_for_path(Path(path))})
        return files

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

    def _auth_url(self, repo: dict[str, Any]) -> str:
        """Return git URL with embedded credentials if auth_ref is configured."""
        git_url = str(repo.get("git_url", ""))
        auth_ref = repo.get("auth_ref", "") or ""
        if not auth_ref:
            return git_url
        try:
            auth = json.loads(auth_ref)
        except (json.JSONDecodeError, TypeError):
            return git_url
        auth_type = auth.get("type", "")
        if auth_type == "username_password":
            user = auth.get("username", "")
            pwd = auth.get("password", "")
            if user and pwd:
                import urllib.parse
                userinfo = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(pwd, safe='')}"
                return self._embed_userinfo(git_url, userinfo)
        elif auth_type == "token":
            token = auth.get("token", "")
            if token:
                import urllib.parse
                return self._embed_userinfo(git_url, f"oauth2:{urllib.parse.quote(token, safe='')}")
        return git_url

    @staticmethod
    def _embed_userinfo(url: str, userinfo: str) -> str:
        import urllib.parse
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                rest = url[len(prefix):]
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                return f"{prefix}{userinfo}@{rest}"
        return url

    def test_clone(self, actor: str, git_url: str, auth_ref: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        repo = {"git_url": git_url, "auth_ref": auth_ref}
        auth_url = self._auth_url(repo)
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", auth_url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return {"success": True, "message": "认证成功，可以访问仓库"}
            error_msg = (result.stderr or result.stdout or "").strip()
            if auth_url != git_url:
                error_msg = error_msg.replace(auth_url, git_url)
            return {"success": False, "message": f"无法访问: {error_msg}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "连接超时，请检查仓库地址"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _repository_payload(self, repo: dict[str, Any]) -> dict[str, Any]:
        payload = dict(repo)
        payload["tags"] = json.loads(str(payload.pop("tags_json", "[]") or "[]"))
        payload["has_auth_ref"] = bool(payload.get("auth_ref", ""))
        payload.pop("auth_ref", None)
        return payload

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
