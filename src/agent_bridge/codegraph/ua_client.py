"""Client for Understand Anything knowledge graph artifacts — read status/summary and trigger analysis."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UA_DIR = ".understand-anything"
GRAPH_FILE = "knowledge-graph.json"
META_FILE = "meta.json"
DASHBOARD_META_FILE = ".dashboard-meta.json"
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


class UnderstandAnythingClient:

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

        # Check if skills are linked in the target project
        if project_dir:
            skill_dir = project_dir / SKILL_DIR_NAME
            understand_link = skill_dir / "understand"
            if understand_link.exists() or understand_link.is_symlink():
                return UAAvailability(claude_installed=True, ua_skill_available=True)

        # Fallback: check global claude skill list
        try:
            result = subprocess.run(
                [claude_path, "skill", "list"],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout + result.stderr
            if "understand" in output.lower():
                return UAAvailability(claude_installed=True, ua_skill_available=True)
        except (subprocess.TimeoutExpired, OSError):
            pass

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
        meta = self._read_dashboard_meta(project_dir)
        if meta is None:
            return {"running": False}
        pid = meta.get("pid")
        if pid is not None and self._is_pid_alive(pid):
            return {"running": True, "url": meta.get("url"), "pid": pid, "started_at": meta.get("started_at")}
        return {"running": False}

    def start_dashboard(self, project_dir: Path, *, timeout: int = 60) -> dict[str, Any]:
        # Check if already running
        current = self.dashboard_status(project_dir)
        if current["running"]:
            return {"success": True, "url": current["url"], "pid": current["pid"]}

        # Need a graph to serve
        graph_path = project_dir / UA_DIR / GRAPH_FILE
        if not graph_path.is_file():
            return {"success": False, "error": "请先运行分析生成知识图谱"}

        # Launch via claude -p with dashboard skill
        prompt = (
            "Use the Understand Anything dashboard skill for this repository: "
            f"{project_dir}\n\n"
            "Return ONLY the final local Dashboard URL on the last line. "
            "Do not open browser windows. Do not output anything after the URL."
        )
        try:
            proc = subprocess.Popen(
                ["claude", "-p", "--dangerously-skip-permissions", prompt],
                cwd=str(project_dir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
        except (FileNotFoundError, OSError) as exc:
            return {"success": False, "error": str(exc)}

        # Read stdout with timeout to get the URL
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"success": False, "error": f"启动 Dashboard 超时（{timeout}s）"}

        if proc.returncode != 0:
            err = (stderr or "")[:2000] or f"claude -p exited with code {proc.returncode}"
            return {"success": False, "error": err}

        # Parse URL from last line of output
        lines = stdout.strip().splitlines()
        url = lines[-1].strip() if lines else ""
        if not url.startswith("http"):
            # Try to find a URL anywhere in the output
            import re
            url_match = re.search(r"https?://\S+", stdout)
            url = url_match.group(0) if url_match else ""

        if not url:
            return {"success": False, "error": "未能获取 Dashboard URL", "output": stdout[-2000:]}

        # Write meta so we can track it
        meta = {
            "pid": proc.pid,
            "url": url,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._write_dashboard_meta(project_dir, meta)
        return {"success": True, "url": url, "pid": proc.pid}

    def stop_dashboard(self, project_dir: Path) -> dict[str, Any]:
        meta = self._read_dashboard_meta(project_dir)
        if meta is None:
            return {"stopped": False, "error": "Dashboard 未运行"}

        pid = meta.get("pid")
        stopped = False
        if pid is not None:
            try:
                os.kill(pid, 15)  # SIGTERM
                stopped = True
            except ProcessLookupError:
                stopped = True  # already dead
            except OSError:
                pass

        self._delete_dashboard_meta(project_dir)
        return {"stopped": stopped}

    def _read_dashboard_meta(self, project_dir: Path) -> dict[str, Any] | None:
        path = project_dir / UA_DIR / DASHBOARD_META_FILE
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_dashboard_meta(self, project_dir: Path, meta: dict[str, Any]) -> None:
        path = project_dir / UA_DIR / DASHBOARD_META_FILE
        ua_dir = project_dir / UA_DIR
        ua_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _delete_dashboard_meta(self, project_dir: Path) -> None:
        path = project_dir / UA_DIR / DASHBOARD_META_FILE
        if path.is_file():
            path.unlink()

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _read_meta(self, project_dir: Path) -> dict[str, Any] | None:
        meta_path = project_dir / UA_DIR / META_FILE
        if not meta_path.is_file():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
