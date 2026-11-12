"""Regression test: adding a new backend must backfill existing synced docs.

Previously align_backends() queried sync_states filtered by the NEW backend's
slug. Since sync_states are keyed per-backend and the new backend has none
yet, the result was always empty, so existing documents were never backfilled
into a newly-added backend.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import AgentBridgePaths, ensure_directories
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound


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


def test_align_backends_backfills_existing_target_missing_jobs(wm_paths, tmp_path: Path) -> None:
    svc = _service(wm_paths)

    svc.add_backend("root", slug="weknora", backend_type="mock")
    svc.create_kb("root", "kb-a", "KB A", "")
    doc = svc.add_document("root", source=_write_doc(tmp_path, "doc.md"), kb_slugs=["kb-a"], later=False)
    kb_id = svc.store.get_kb_by_slug("kb-a")["id"]
    assert svc.store.get_sync_state(doc["id"], kb_id, "weknora")["status"] == "synced"

    # Simulates a migrated/config-edited database: the target row exists, but
    # historical docs were never enqueued for that backend.
    svc.store.ensure_backend_target(kb_id, slug="pageindex", backend_type="pageindex")

    svc.add_backend("root", slug="pageindex", backend_type="mock")

    pageindex_jobs = svc.store.list_all_jobs(backend_slug="pageindex")
    pending_create = [
        job for job in pageindex_jobs
        if job["doc_id"] == doc["id"]
        and job["operation"] == "create"
        and job["status"] == "pending"
    ]
    assert len(pending_create) == 1


def test_new_kb_sync_policy_follows_default_and_keeps_explicit_choices(wm_paths, tmp_path: Path) -> None:
    svc = _service(wm_paths)
    for slug in ("weknora", "ragflow", "pageindex"):
        svc.add_backend("root", slug=slug, backend_type="mock")
    kb = svc.create_kb("root", "docs", "Docs", "")
    svc.update_kb_defaults("root", "docs", default_backend_slug="weknora")

    def targets():
        summary = svc.list_kb_status_summaries("root")[0]
        return {target["slug"]: target for target in summary["backend_targets"]}

    assert {slug: target["sync_policy"] for slug, target in targets().items()} == {
        "pageindex": "paused", "ragflow": "paused", "weknora": "scheduled",
    }
    svc.update_kb_sync_policy("root", "docs", backend_sync_policies={"ragflow": "immediate"})
    doc = svc.add_document("root", _write_doc(tmp_path, "doc.md"), ["docs"], later=True)

    # 指定 Weknora 的请求不会为另外两个后端建库，也不会领取其待办。
    assert svc.sync_kb("root", "docs", backend="weknora")["succeeded"] == 1
    assert svc.store.get_sync_state(doc["id"], kb["id"], "weknora")["status"] == "synced"
    assert targets()["ragflow"]["backend_kb_id"] is None
    assert targets()["pageindex"]["backend_kb_id"] is not None  # 创建时曾是默认检索后端
    assert svc.store.get_sync_state(doc["id"], kb["id"], "pageindex") is None
    assert svc.sync_kb("root", "docs", backend="pageindex")["processed"] == 0

    # 换默认后端后，继承默认的两项随之变化，显式设置保持。
    svc.update_kb_defaults("root", "docs", default_backend_slug="pageindex")
    assert {slug: target["sync_policy"] for slug, target in targets().items()} == {
        "pageindex": "scheduled", "ragflow": "immediate", "weknora": "paused",
    }
    next_doc = svc.add_document("root", _write_doc(tmp_path, "next.md"), ["docs"], later=True)
    svc.doc_sync_scheduler._run_sync()
    for doc_id in (doc["id"], next_doc["id"]):
        for backend in ("ragflow", "pageindex"):
            assert svc.store.get_sync_state(doc_id, kb["id"], backend)["status"] == "synced"
    assert svc.store.get_sync_state(next_doc["id"], kb["id"], "weknora") is None
    assert any(
        job["doc_id"] == next_doc["id"] and job["status"] == "pending"
        for job in svc.store.list_all_jobs(backend_slug="weknora", kb_id=kb["id"])
    )


def test_enabling_new_backend_backfills_documents_that_have_never_synced(wm_paths, tmp_path: Path) -> None:
    svc = _service(wm_paths)
    svc.add_backend("root", slug="ragflow", backend_type="mock")
    kb = svc.create_kb("root", "docs", "Docs", "")
    svc.update_kb_sync_policy("root", "docs", backend_sync_policies={"ragflow": "paused"})
    doc = svc.add_document("root", _write_doc(tmp_path, "doc.md"), ["docs"], later=True)
    assert svc.sync("root", all_users=True)["processed"] == 0

    svc.add_backend("root", slug="weknora", backend_type="mock")
    svc.update_kb_defaults("root", "docs", default_backend_slug="weknora")
    with pytest.raises(NotFound, match="尚未完成建库"):
        svc.search("root", "docs", "question")
    assert svc.sync("root", all_users=True)["succeeded"] == 1
    assert svc.store.get_sync_state(doc["id"], kb["id"], "weknora")["status"] == "synced"
    assert svc.store.get_sync_state(doc["id"], kb["id"], "ragflow") is None
