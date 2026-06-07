"""Read-only client for Understand Anything knowledge graph artifacts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UA_DIR = ".understand-anything"
GRAPH_FILE = "knowledge-graph.json"
META_FILE = "meta.json"


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


class UnderstandAnythingClient:

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

    def _read_meta(self, project_dir: Path) -> dict[str, Any] | None:
        meta_path = project_dir / UA_DIR / META_FILE
        if not meta_path.is_file():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
