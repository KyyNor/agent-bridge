from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _write_mock_backend_config(wm_paths) -> None:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        '[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )


def _jobs_by_operation(client: TestClient) -> dict[str, list[dict]]:
    response = client.get("/status", headers={"X-Agent-Bridge-User": "alice"})
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(job["operation"], []).append(job)
    return grouped


def test_phase_one_smoke_flow(wm_paths, tmp_path: Path) -> None:
    _write_mock_backend_config(wm_paths)
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    assert client.post("/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    assert client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    ).status_code == 200
    assert client.post(
        "/kbs/frontend-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Agent-Bridge-User": "root"},
    ).status_code == 200

    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")

    with v1.open("rb") as handle:
        added = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "alice"},
        )
    assert added.status_code == 200
    assert added.json()["current_version_no"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["create"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["create"][-1]["status"] == "succeeded"

    with v2.open("rb") as handle:
        updated = client.post(
            "/docs/guide/versions",
            data={"later": "true"},
            files={"file": ("Guide-v2.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "alice"},
        )
    assert updated.status_code == 200
    assert updated.json()["current_version_no"] == 2
    jobs = _jobs_by_operation(client)
    assert jobs["update"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["update"][-1]["status"] == "succeeded"

    deleted = client.post("/docs/guide/delete", headers={"X-Agent-Bridge-User": "alice"})
    assert deleted.status_code == 200
    jobs = _jobs_by_operation(client)
    assert jobs["delete"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    operations = [job["operation"] for job_list in jobs.values() for job in job_list]
    assert operations == ["create", "update", "delete"]
    assert jobs["delete"][-1]["status"] == "succeeded"
    assert [jobs[operation][-1]["status"] for operation in ("create", "update", "delete")] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]


def test_phase_two_multi_backend_smoke(wm_paths, tmp_path: Path) -> None:
    """Phase 2 E2E: full multi-backend flow with mock backend in registry."""
    _write_mock_backend_config(wm_paths)
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))

    # 1. Init system
    assert client.post("/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200

    # 2. Create KB — backend target auto-created from registry config
    kb_resp = client.post(
        "/kbs",
        json={"slug": "auth-docs", "name": "Auth Docs", "description": "Phase 2 test"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert kb_resp.status_code == 200

    # Grant contributor
    assert client.post(
        "/kbs/auth-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Agent-Bridge-User": "root"},
    ).status_code == 200

    # Verify backends endpoint lists mock
    backends = client.get("/backends").json()
    assert len(backends) >= 1
    assert any(b["slug"] == "mock" for b in backends)

    # 3. Add document — verify sync job created for mock backend
    v1 = tmp_path / "AuthGuide.pdf"
    v2 = tmp_path / "AuthGuide-v2.pdf"
    v1.write_bytes(b"auth version one")
    v2.write_bytes(b"auth version two")

    with v1.open("rb") as handle:
        added = client.post(
            "/docs",
            data={"kb": ["auth-docs"], "later": "true"},
            files={"file": ("AuthGuide.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "alice"},
        )
    assert added.status_code == 200
    assert added.json()["current_version_no"] == 1

    jobs = _jobs_by_operation(client)
    create_job = jobs["create"][-1]
    assert create_job["status"] == "pending"
    assert create_job["backend_slug"] == "mock"

    # 4. Sync — verify document synced to mock backend
    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["create"][-1]["status"] == "succeeded"

    # 5. Check doc detail — verify sync_states visible with backend info
    doc_detail = client.get("/docs/authguide", headers={"X-Agent-Bridge-User": "alice"})
    assert doc_detail.status_code == 200
    detail = doc_detail.json()
    assert "sync_states" in detail
    assert len(detail["sync_states"]) >= 1
    mock_state = next(s for s in detail["sync_states"] if s["backend_slug"] == "mock")
    assert mock_state["status"] == "synced"
    assert mock_state["backend_doc_id"] is not None

    # 6. Update document — verify new sync job for mock backend
    with v2.open("rb") as handle:
        updated = client.post(
            "/docs/authguide/versions",
            data={"later": "true"},
            files={"file": ("AuthGuide-v2.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "alice"},
        )
    assert updated.status_code == 200
    assert updated.json()["current_version_no"] == 2
    jobs = _jobs_by_operation(client)
    update_job = jobs["update"][-1]
    assert update_job["status"] == "pending"
    assert update_job["backend_slug"] == "mock"

    # 7. Sync — verify update synced
    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["update"][-1]["status"] == "succeeded"

    # 8. Delete document — verify delete sync job for mock backend
    deleted = client.post("/docs/authguide/delete", headers={"X-Agent-Bridge-User": "alice"})
    assert deleted.status_code == 200
    jobs = _jobs_by_operation(client)
    delete_job = jobs["delete"][-1]
    assert delete_job["status"] == "pending"
    assert delete_job["backend_slug"] == "mock"

    # 9. Sync — verify delete synced
    synced = client.post("/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["delete"][-1]["status"] == "succeeded"

    # Soft-deleted doc is no longer visible via get_doc, but all three
    # operations completed successfully through the mock backend.
    all_jobs = [j for ops in jobs.values() for j in ops]
    assert all(j["backend_slug"] == "mock" for j in all_jobs)
    assert [jobs[op][-1]["status"] for op in ("create", "update", "delete")] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]

    # 10. Test align_backends — backends endpoint still lists mock
    backends_after = client.get("/backends").json()
    assert any(b["slug"] == "mock" for b in backends_after)


def test_retrieval_strategy_e2e(wm_paths, tmp_path):
    """E2E: KB defaults -> profile override -> strategy resolution."""
    from agent_bridge.core.config import BackendConfig, ensure_directories
    from agent_bridge.knowledge.backends.registry import BackendRegistry
    from agent_bridge.knowledge.service import AgentBridgeService

    ensure_directories(wm_paths)
    svc = AgentBridgeService.create(wm_paths, admins={"root"})
    svc.registry = BackendRegistry(
        {
            "weknora": BackendConfig(slug="weknora", backend_type="weknora", base_url="http://localhost", api_key="test"),
            "ragflow": BackendConfig(slug="ragflow", backend_type="ragflow", base_url="http://localhost", api_key="test"),
        },
        paths=tmp_path,
    )
    svc.init_system()

    # 1. Create KB
    svc.create_kb("root", "docs", "Documentation", "")
    kb = svc.store.get_kb_by_slug("docs")
    assert kb is not None

    # 2. No defaults -> first active backend
    _, strategy = svc.resolve_retrieval_strategy("docs", profile_key=None)
    assert strategy.backend_slug in ("weknora", "ragflow")

    # 3. Set KB defaults
    svc.update_kb_defaults("root", "docs", default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    kb = svc.store.get_kb_by_slug("docs")
    assert kb["default_backend_slug"] == "weknora"
    assert kb["default_agent_id"] == "hybrid-rag-wiki"
    _, strategy = svc.resolve_retrieval_strategy("docs", profile_key=None)
    assert strategy.backend_slug == "weknora"
    assert strategy.agent_id == "hybrid-rag-wiki"

    # 4. Create profile and assign KB with override
    svc.governance.upsert_profile("root", "team-a", "Team A", "desc", "active")
    svc.governance.set_resource_profiles(
        "root", "wiki_kb", "docs", ["team-a"],
        overrides={"team-a": {"retrieval_backend_slug": "ragflow", "retrieval_agent_id": None}},
    )
    _, strategy = svc.resolve_retrieval_strategy("docs", profile_key="team-a")
    assert strategy.backend_slug == "ragflow"
    assert strategy.agent_id is None

    # 5. Profile without override falls back to KB defaults
    svc.governance.upsert_profile("root", "team-b", "Team B", "desc", "active")
    svc.governance.set_resource_profiles("root", "wiki_kb", "docs", ["team-b"])
    _, strategy = svc.resolve_retrieval_strategy("docs", profile_key="team-b")
    assert strategy.backend_slug == "weknora"
    assert strategy.agent_id == "hybrid-rag-wiki"
