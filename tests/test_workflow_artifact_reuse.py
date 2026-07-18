"""Artifact lineage, reuse validity, and concurrent workflow-run isolation."""

from __future__ import annotations

import hashlib


def _service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="p", name="P", created_by="root")
    svc.workflows.upsert_definition(
        actor="root", workflow_key="w", name="W", description="", profile_key="p",
        definition={"nodes": [], "edges": []}, status="active",
    )
    return svc


def _run(svc, run_id: str, *, status: str = "running", task_version: str = "v1"):
    return svc.store.create_workflow_run(
        run_id=run_id, workflow_key="w", profile_key="p", task_key="page:a", status=status,
        temp_dir=f"/tmp/{run_id}", task_version=task_version,
    )


def _artifact(svc, run_id: str, *, content: str = "# artifact", node_id: str = "out"):
    return svc.workflows.save_artifact(
        workflow_key="w", profile_key="p", run_id=run_id, task_key="page:a", task_version="v1",
        title="Artifact", path="pages/a.md", tags=[], format="markdown", summary="", content=content,
        metadata={}, producer_node_id=node_id, producer_node_fingerprint="fingerprint-out",
    )


def test_run_scoped_artifact_query_includes_reused_source_with_lineage(wm_paths):
    svc = _service(wm_paths)
    _run(svc, "source", status="completed")
    source = _artifact(svc, "source")
    _run(svc, "current")
    svc.store.associate_workflow_run_artifacts(
        "current", "out", [source["artifact_id"]], source_run_id="source", source_node_id="out",
    )

    rows = svc.store.list_artifacts_for_run("current")

    assert len(rows) == 1
    assert rows[0]["artifact_id"] == source["artifact_id"]
    assert rows[0]["source_run_id"] == "source"
    assert rows[0]["source_node_id"] == "out"
    assert rows[0]["reused"] is True
    assert rows[0]["reusable"] is True


def test_artifact_reuse_rejects_invalid_content_and_scope(wm_paths):
    svc = _service(wm_paths)
    _run(svc, "source", status="completed")
    source = _artifact(svc, "source")
    _run(svc, "current")
    svc.store.associate_workflow_run_artifacts(
        "current", "out", [source["artifact_id"]], source_run_id="source", source_node_id="out",
    )

    checks = [
        ("DELETE FROM workflow_artifacts WHERE artifact_id = ?", (source["artifact_id"],), "artifact_missing"),
        (
            "UPDATE workflow_artifacts SET content_hash = ? WHERE artifact_id = ?",
            ("bad-hash", source["artifact_id"]), "content_hash_mismatch",
        ),
        (
            "UPDATE workflow_artifacts SET reuse_allowed = 0 WHERE artifact_id = ?",
            (source["artifact_id"],), "reuse_disabled",
        ),
        (
            "UPDATE workflow_artifacts SET task_version = 'other' WHERE artifact_id = ?",
            (source["artifact_id"],), "artifact_scope_mismatch",
        ),
    ]
    for statement, params, reason in checks:
        with svc.store.connect() as conn:
            conn.execute(statement, params)
        rows = svc.store.list_artifacts_for_run("current")
        if reason == "artifact_missing":
            assert rows == []
            break
        assert rows[0]["reusable"] is False
        assert rows[0]["reuse_validation_reason"] == reason
        with svc.store.connect() as conn:
            conn.execute(
                "UPDATE workflow_artifacts SET content_hash = ?, reuse_allowed = 1, task_version = 'v1' WHERE artifact_id = ?",
                (hashlib.sha256(b"# artifact").hexdigest(), source["artifact_id"]),
            )


def test_concurrent_runs_keep_their_artifact_associations_and_content_separate(wm_paths):
    svc = _service(wm_paths)
    _run(svc, "run_a")
    _run(svc, "run_b")
    artifact_a = _artifact(svc, "run_a", content="# A", node_id="out_a")
    artifact_b = _artifact(svc, "run_b", content="# B", node_id="out_b")

    rows_a = svc.store.list_artifacts_for_run("run_a")
    rows_b = svc.store.list_artifacts_for_run("run_b")

    assert [(row["artifact_id"], row["content"]) for row in rows_a] == [(artifact_a["artifact_id"], "# A")]
    assert [(row["artifact_id"], row["content"]) for row in rows_b] == [(artifact_b["artifact_id"], "# B")]
