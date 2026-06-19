"""Regression test: adding a new backend must backfill existing synced docs.

Previously align_backends() queried sync_states filtered by the NEW backend's
slug. Since sync_states are keyed per-backend and the new backend has none
yet, the result was always empty, so existing documents were never backfilled
into a newly-added backend.
"""
from __future__ import annotations

from pathlib import Path

from agent_bridge.core.config import AgentBridgePaths, ensure_directories
from agent_bridge.app.service import AgentBridgeService


def _service(wm_paths: AgentBridgePaths) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    return service


def _write_doc(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(f"# {name}\n\ncontent for {name}", encoding="utf-8")
    return path


def test_add_backend_backfills_existing_synced_docs(wm_paths, tmp_path: Path) -> None:
    svc = _service(wm_paths)

    # 1) Only weknora configured; create KB A and add two docs that sync to weknora.
    svc.add_backend("root", slug="weknora", backend_type="mock")
    svc.create_kb("root", "kb-a", "KB A", "")
    doc1 = svc.add_document("root", source=_write_doc(tmp_path, "doc1.md"), kb_slugs=["kb-a"], later=False)
    doc2 = svc.add_document("root", source=_write_doc(tmp_path, "doc2.md"), kb_slugs=["kb-a"], later=False)
    kb_id = svc.store.get_kb_by_slug("kb-a")["id"]

    # Sanity: both docs actually reached a synced state on weknora.
    for did in (doc1["id"], doc2["id"]):
        assert svc.store.get_sync_state(did, kb_id, "weknora")["status"] == "synced"

    # 2) Add a second backend. align_backends() must create pending create jobs
    #    that backfill the existing docs into ragflow.
    svc.add_backend("root", slug="ragflow", backend_type="mock")

    ragflow_jobs = svc.store.list_all_jobs(backend_slug="ragflow")
    pending_create = {
        j["doc_id"] for j in ragflow_jobs
        if j["operation"] == "create" and j["status"] == "pending"
    }
    assert pending_create == {doc1["id"], doc2["id"]}

    # 3) Running sync actually lands the docs in ragflow.
    svc.sync("root", all_users=False)
    for did in (doc1["id"], doc2["id"]):
        state = svc.store.get_sync_state(did, kb_id, "ragflow")
        assert state is not None and state["status"] == "synced"
