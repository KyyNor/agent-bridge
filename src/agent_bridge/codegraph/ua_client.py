"""Client for Understand Anything knowledge graph artifacts — read status/summary and trigger analysis."""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UA_DIR = ".understand-anything"
GRAPH_FILE = "knowledge-graph.json"
META_FILE = "meta.json"
SKILL_DIR_NAME = ".claude/skills"
UA_SKILLS = [
    "understand",
    "understand-chat",
    "understand-dashboard",
    "understand-diff",
    "understand-domain",
    "understand-explain",
    "understand-knowledge",
    "understand-onboard",
]

ANALYZE_PROMPT = """\
Use the Understand Anything skill to analyze this repository: {project_dir}

Requirements:
- Generate .understand-anything/knowledge-graph.json
- Use --language zh
- Prefer incremental update if the graph already exists
- Do not modify source code
- Report only final status, graph path, node count, edge count, and errors
"""


@dataclass
class UAAvailability:
    claude_installed: bool
    ua_skill_available: bool
    message: str | None = None


@dataclass
class UAGraphStatus:
    graph_exists: bool
    graph_path: str | None = None
    stale: bool = False
    node_count: int = 0
    edge_count: int = 0
    layer_count: int = 0
    tour_count: int = 0
    analyzed_at: str | None = None
    git_commit: str | None = None
    analyzed_files: int | None = None
    error: str | None = None


@dataclass
class UAGraphSummary:
    project_name: str | None = None
    description: str | None = None
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)
    key_nodes: list[dict[str, Any]] = field(default_factory=list)
    tours: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UAAnalyzeResult:
    success: bool
    node_count: int = 0
    edge_count: int = 0
    error: str | None = None
    output: str | None = None
    duration_ms: int = 0


class DashboardPool:
    """Manages multiple Dashboard vite processes with LRU eviction and idle timeout."""

    def __init__(self, max_sessions: int = 20, idle_timeout: int = 3600) -> None:
        self._max = max_sessions
        self._idle_timeout = idle_timeout
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300
        self._start_cleanup_thread()

    # -- Public API --

    def acquire(self, project_key: str, launcher: Any) -> dict[str, Any]:
        """Get or create a dashboard session. Returns {running, url, ...}."""
        with self._lock:
            session = self._sessions.get(project_key)
            if session and self._is_pid_alive(session["pid"]):
                session["last_accessed_at"] = time.time()
                logger.debug("pool: reused session for %s pid=%s", project_key, session["pid"])
                return {"running": True, "url": session["url"], "pid": session["pid"],
                        "started_at": session.get("started_at")}

            if session:
                self._remove_locked(project_key)

            if len(self._sessions) >= self._max:
                self._evict_lru_locked()

            result = launcher()
            if not result.get("success"):
                return result

            now = time.time()
            self._sessions[project_key] = {
                "pid": result["pid"],
                "url": result["url"],
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "last_accessed_at": now,
            }
            logger.info("pool: started session for %s pid=%s url=%s total=%d",
                        project_key, result["pid"], result["url"], len(self._sessions))
            return {"running": True, "url": result["url"], "pid": result["pid"]}

    def touch(self, project_key: str) -> None:
        with self._lock:
            session = self._sessions.get(project_key)
            if session:
                session["last_accessed_at"] = time.time()

    def status(self, project_key: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(project_key)
            if session and self._is_pid_alive(session["pid"]):
                return {"running": True, "url": session["url"], "pid": session["pid"],
                        "started_at": session.get("started_at")}
            if session:
                self._remove_locked(project_key)
            return {"running": False}

    def stop(self, project_key: str) -> dict[str, Any]:
        with self._lock:
            return self._remove_locked(project_key)

    def stop_all(self) -> None:
        with self._lock:
            for key in list(self._sessions):
                self._remove_locked(key)

    # -- Internal --

    def _remove_locked(self, project_key: str) -> dict[str, Any]:
        session = self._sessions.pop(project_key, None)
        if session is None:
            return {"stopped": False}
        try:
            os.killpg(session["pid"], 15)
        except (ProcessLookupError, OSError):
            pass
        logger.info("pool: stopped session for %s pid=%s", project_key, session["pid"])
        return {"stopped": True}

    def _evict_lru_locked(self) -> None:
        oldest_key = min(self._sessions, key=lambda k: self._sessions[k]["last_accessed_at"])
        logger.info("pool: evicting LRU session %s", oldest_key)
        self._remove_locked(oldest_key)

    def _evict_idle(self) -> None:
        cutoff = time.time() - self._idle_timeout
        with self._lock:
            for key in list(self._sessions):
                session = self._sessions[key]
                if session["last_accessed_at"] < cutoff and self._is_pid_alive(session["pid"]):
                    logger.info("pool: evicting idle session %s (last access %ds ago)",
                                key, int(time.time() - session["last_accessed_at"]))
                    self._remove_locked(key)

    def _start_cleanup_thread(self) -> None:
        def _run():
            self._evict_idle()
            t = threading.Timer(self._cleanup_interval, _run)
            t.daemon = True
            t.start()
        t = threading.Timer(self._cleanup_interval, _run)
        t.daemon = True
        t.start()

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


class UnderstandAnythingClient:

    def __init__(self) -> None:
        self.pool = DashboardPool(max_sessions=20, idle_timeout=3600)

    def _ua_repo_dir(self) -> Path:
        return Path.home() / ".understand-anything" / "repo"

    def _ua_skills_src_dir(self) -> Path:
        return self._ua_repo_dir() / "understand-anything-plugin" / "skills"

    def check_availability(self, *, project_dir: Path | None = None) -> UAAvailability:
        claude_path = shutil.which("claude")
        if not claude_path:
            return UAAvailability(
                claude_installed=False,
                ua_skill_available=False,
                message="claude CLI 未安装。请先安装 Claude Code: npm install -g @anthropic-ai/claude-code",
            )

        if project_dir:
            skill_dir = project_dir / SKILL_DIR_NAME
            understand_link = skill_dir / "understand"
            if understand_link.exists() or understand_link.is_symlink():
                return UAAvailability(claude_installed=True, ua_skill_available=True)

        return UAAvailability(
            claude_installed=True,
            ua_skill_available=False,
            message="Understand Anything 技能未安装，且未配置自动安装地址。请在「知识处理配置」中填写 UA Git URL。",
        )

    def check_availability_with_config(
        self, *, project_dir: Path, ua_git_url: str,
    ) -> UAAvailability:
        avail = self.check_availability(project_dir=project_dir)
        if avail.ua_skill_available:
            return avail
        if not ua_git_url:
            return avail
        return UAAvailability(
            claude_installed=True,
            ua_skill_available=False,
            message=None,
        )

    def ensure_skills(self, project_dir: Path, ua_git_url: str) -> str | None:
        """Clone UA repo and symlink skills. Returns error string or None on success."""
        repo_dir = self._ua_repo_dir()
        skills_src = self._ua_skills_src_dir()

        # Clone or update the shared repo
        if not (repo_dir / ".git").is_dir():
            logger.info("Cloning UA repo from %s", ua_git_url)
            try:
                repo_dir.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "clone", ua_git_url, str(repo_dir)],
                    capture_output=True, text=True, timeout=120, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                msg = exc.stderr if hasattr(exc, "stderr") and exc.stderr else str(exc)
                return f"clone UA 仓库失败: {msg}"
        else:
            # Pull latest
            try:
                subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=str(repo_dir), capture_output=True, text=True, timeout=60, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                pass  # stale repo is fine

        if not skills_src.is_dir():
            return f"UA 仓库已 clone 但未找到 skills 目录: {skills_src}"

        # Create .claude/skill/ and symlink each skill
        skill_dir = project_dir / SKILL_DIR_NAME
        skill_dir.mkdir(parents=True, exist_ok=True)

        for skill_name in UA_SKILLS:
            link_path = skill_dir / skill_name
            target = skills_src / skill_name
            if not target.is_dir():
                continue
            if link_path.is_symlink() or link_path.exists():
                if link_path.resolve() == target.resolve():
                    continue
                link_path.unlink()
            link_path.symlink_to(target)

        return None

    def analyze(
        self,
        project_dir: Path,
        *,
        ua_git_url: str = "",
        language: str = "zh",
        timeout: int = 600,
    ) -> UAAnalyzeResult:
        # Step 1: Check if skills are already available
        avail = self.check_availability(project_dir=project_dir)
        if not avail.claude_installed:
            return UAAnalyzeResult(success=False, error=avail.message)

        # Step 2: If not available, try auto-install
        if not avail.ua_skill_available:
            if not ua_git_url:
                return UAAnalyzeResult(success=False, error=avail.message)
            install_error = self.ensure_skills(project_dir, ua_git_url)
            if install_error:
                return UAAnalyzeResult(success=False, error=install_error)
            # Re-check after install
            avail = self.check_availability(project_dir=project_dir)
            if not avail.ua_skill_available:
                return UAAnalyzeResult(success=False, error="自动安装 UA 技能后仍未检测到")

        # Step 3: Run analysis
        prompt = ANALYZE_PROMPT.format(project_dir=str(project_dir))
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                ["claude", "-p", "--dangerously-skip-permissions", prompt],
                cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return UAAnalyzeResult(success=False, error=f"分析超时（{timeout}s）", duration_ms=duration_ms)
        except (FileNotFoundError, OSError) as exc:
            return UAAnalyzeResult(success=False, error=str(exc))

        duration_ms = int((time.perf_counter() - started) * 1000)
        output = (proc.stdout or "")[-4000:]

        if proc.returncode != 0:
            return UAAnalyzeResult(
                success=False,
                error=(proc.stderr or "")[:2000] or f"claude -p exited with code {proc.returncode}",
                output=output,
                duration_ms=duration_ms,
            )

        graph_path = project_dir / UA_DIR / GRAPH_FILE
        if not graph_path.is_file():
            return UAAnalyzeResult(
                success=False,
                error="分析完成但未生成 knowledge-graph.json",
                output=output,
                duration_ms=duration_ms,
            )

        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return UAAnalyzeResult(success=False, error=f"图谱解析失败: {exc}", output=output, duration_ms=duration_ms)

        return UAAnalyzeResult(
            success=True,
            node_count=len(data.get("nodes") or []),
            edge_count=len(data.get("edges") or []),
            output=output,
            duration_ms=duration_ms,
        )

    def status(self, project_dir: Path, current_commit: str | None = None) -> UAGraphStatus:
        graph_path = project_dir / UA_DIR / GRAPH_FILE
        if not graph_path.is_file():
            return UAGraphStatus(graph_exists=False)

        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return UAGraphStatus(graph_exists=True, graph_path=str(graph_path), error=str(exc))

        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        layers = data.get("layers") or []
        tour = data.get("tour") or []

        meta = self._read_meta(project_dir)
        graph_commit = (
            data.get("project", {}).get("gitCommitHash")
            or (meta.get("gitCommitHash") if meta else None)
        )
        stale = False
        if current_commit and graph_commit and current_commit != graph_commit:
            stale = True

        return UAGraphStatus(
            graph_exists=True,
            graph_path=str(graph_path),
            stale=stale,
            node_count=len(nodes),
            edge_count=len(edges),
            layer_count=len(layers),
            tour_count=len(tour),
            analyzed_at=meta.get("lastAnalyzedAt") if meta else None,
            git_commit=graph_commit,
            analyzed_files=meta.get("analyzedFiles") if meta else None,
        )

    def summary(self, project_dir: Path) -> UAGraphSummary | None:
        graph_path = project_dir / UA_DIR / GRAPH_FILE
        if not graph_path.is_file():
            return None

        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

        project = data.get("project") or {}
        nodes = data.get("nodes") or []
        tour = data.get("tour") or []

        modules = []
        for node in nodes:
            if node.get("type") == "module":
                modules.append({
                    "name": node.get("name", ""),
                    "summary": node.get("summary", ""),
                })

        non_file_nodes = [n for n in nodes if n.get("type") != "file"]
        non_file_nodes.sort(key=lambda n: len(n.get("summary") or ""), reverse=True)
        key_nodes = [
            {
                "id": n.get("id", ""),
                "name": n.get("name", ""),
                "type": n.get("type", ""),
                "summary": n.get("summary", ""),
            }
            for n in non_file_nodes[:10]
        ]

        tours = []
        if tour:
            tours.append({
                "title": "Project Onboarding Tour",
                "description": tour[0].get("description", "") if tour else "",
                "step_count": len(tour),
            })

        return UAGraphSummary(
            project_name=project.get("name"),
            description=project.get("description"),
            languages=project.get("languages") or [],
            frameworks=project.get("frameworks") or [],
            modules=modules,
            key_nodes=key_nodes,
            tours=tours,
        )

    def read_graph_raw(self, project_dir: Path) -> dict[str, Any] | None:
        graph_path = project_dir / UA_DIR / GRAPH_FILE
        if not graph_path.is_file():
            return None
        try:
            return json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    # -- Dashboard lifecycle --

    def dashboard_status(self, project_dir: Path) -> dict[str, Any]:
        return self.pool.status(str(project_dir))

    def start_dashboard(self, project_dir: Path, *, timeout: int = 90) -> dict[str, Any]:
        # Check graph exists first
        if not (project_dir / UA_DIR / GRAPH_FILE).is_file():
            return {"success": False, "error": "请先运行分析生成知识图谱"}
        dashboard_dir = self._find_dashboard_dir(project_dir)
        if dashboard_dir is None:
            return {"success": False, "error": "找不到 Dashboard 目录，请确认 UA 技能已安装"}
        error = self._ensure_dashboard_built(dashboard_dir)
        if error:
            return {"success": False, "error": error}

        def _launch() -> dict[str, Any]:
            return self._launch_vite(project_dir, dashboard_dir, timeout)

        result = self.pool.acquire(str(project_dir), _launch)
        if result.get("running"):
            return {"success": True, "running": True, "url": result["url"], "pid": result["pid"]}
        return result

    def touch_dashboard(self, project_dir: Path) -> None:
        self.pool.touch(str(project_dir))

    def stop_dashboard(self, project_dir: Path) -> dict[str, Any]:
        return self.pool.stop(str(project_dir))

    def stop_all_dashboards(self) -> None:
        self.pool.stop_all()

    def _launch_vite(self, project_dir: Path, dashboard_dir: Path, timeout: int) -> dict[str, Any]:
        log_msgs: list[str] = []
        env = {**os.environ, "GRAPH_DIR": str(project_dir)}

        vite_bin = dashboard_dir / "node_modules" / ".bin" / "vite"
        if vite_bin.is_file():
            cmd = [str(vite_bin), "--host", "127.0.0.1", "--port", "5173", "--no-open"]
        else:
            cmd = ["npx", "vite", "--host", "127.0.0.1", "--port", "5173", "--no-open"]

        log_msgs.append(f"cmd={' '.join(cmd)} cwd={dashboard_dir}")
        logger.info("_launch_vite: %s in %s", cmd, dashboard_dir)
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(dashboard_dir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env, start_new_session=True,
            )
        except (FileNotFoundError, OSError) as exc:
            return {"success": False, "error": str(exc), "debug": log_msgs}

        url = None
        deadline = time.time() + timeout
        buffer = ""
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    log_msgs.append(f"process_exited rc={proc.returncode}")
                    logger.warning("_launch_vite: exited rc=%s", proc.returncode)
                    break
                time.sleep(0.1)
                continue
            stripped = line.rstrip()
            if len(buffer) < 3000:
                logger.debug("_launch_vite stdout: %s", stripped[:200])
            buffer += line
            m = re.search(r"https?://(?:127\.0\.0\.1|localhost):\d+\S*token=\S+", buffer)
            if m:
                url = re.sub(r"[,;.!]+$", "", m.group(0))
                logger.info("_launch_vite: found URL %s", url)
                break

        if url is None:
            proc.kill()
            return {
                "success": False,
                "error": f"Dashboard 启动超时（{timeout}s）",
                "output": buffer[-2000:],
                "debug": log_msgs,
            }

        return {"success": True, "pid": proc.pid, "url": url}

    def _find_dashboard_dir(self, project_dir: Path) -> Path | None:
        """Resolve dashboard dir from .claude/skills/understand-dashboard symlink."""
        skill_link = project_dir / SKILL_DIR_NAME / "understand-dashboard"
        logger.debug("_find_dashboard_dir: checking %s", skill_link)
        if not skill_link.exists():
            logger.debug("_find_dashboard_dir: %s does not exist (is_symlink=%s)", skill_link, skill_link.is_symlink())
            return None
        if not skill_link.is_symlink():
            logger.debug("_find_dashboard_dir: %s exists but is not a symlink", skill_link)
            return None
        skill_real = skill_link.resolve()
        logger.debug("_find_dashboard_dir: symlink resolves to %s", skill_real)
        plugin_root = skill_real.parent.parent
        logger.debug("_find_dashboard_dir: plugin_root=%s", plugin_root)
        dashboard_dir = plugin_root / "packages" / "dashboard"
        if not dashboard_dir.is_dir():
            logger.debug("_find_dashboard_dir: %s is not a directory", dashboard_dir)
            return None
        pkg = dashboard_dir / "package.json"
        if not pkg.is_file():
            logger.debug("_find_dashboard_dir: %s not found", pkg)
            return None
        logger.info("_find_dashboard_dir: found dashboard at %s", dashboard_dir)
        return dashboard_dir

    def _ensure_dashboard_built(self, dashboard_dir: Path) -> str | None:
        """Install deps and build core if not already done. Returns error or None."""
        plugin_root = dashboard_dir.parent.parent
        build_flag = plugin_root / "packages" / "core" / ".built-flag"
        logger.debug("_ensure_dashboard_built: build_flag=%s exists=%s", build_flag, build_flag.is_file())
        if build_flag.is_file():
            return None

        logger.info("_ensure_dashboard_built: running pnpm install in %s", dashboard_dir)
        try:
            result = subprocess.run(
                ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"],
                cwd=str(dashboard_dir), capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.error("_ensure_dashboard_built: pnpm not found or timed out: %s", exc)
            return f"pnpm 未安装或超时: {exc}"
        if result.returncode != 0:
            # Try without frozen lockfile
            logger.warning(
                "_ensure_dashboard_built: pnpm install --frozen-lockfile failed (rc=%s), retrying without frozen",
                result.returncode,
            )
            try:
                result = subprocess.run(
                    ["pnpm", "install", "--prefer-offline"],
                    cwd=str(dashboard_dir), capture_output=True, text=True, timeout=120,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                return f"pnpm 安装失败: {exc}"
        if result.returncode != 0:
            err = (result.stderr or result.stdout)[:500]
            logger.error("_ensure_dashboard_built: pnpm install failed: %s", err)
            return f"安装 Dashboard 依赖失败: {err}"

        logger.info("_ensure_dashboard_built: running pnpm --filter @understand-anything/core build in %s", plugin_root)
        try:
            result = subprocess.run(
                ["pnpm", "--filter", "@understand-anything/core", "build"],
                cwd=str(plugin_root), capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return f"构建 UA core 失败: {exc}"
        if result.returncode != 0:
            err = (result.stderr or result.stdout)[:500]
            logger.error("_ensure_dashboard_built: core build failed: %s", err)
            return f"构建 UA core 失败: {err}"

        build_flag.touch()
        logger.info("_ensure_dashboard_built: build complete, flag set")
        return None

    def _read_meta(self, project_dir: Path) -> dict[str, Any] | None:
        meta_path = project_dir / UA_DIR / META_FILE
        if not meta_path.is_file():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
