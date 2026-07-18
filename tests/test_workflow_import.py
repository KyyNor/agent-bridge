from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import ConflictError, ValidationError


GET_TASK_NODE = {
    "id": "n1",
    "name": "N1",
    "type": "get_task",
    "position": {"x": 0, "y": 0},
    "config": {},
}


def _make_service(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="p1", name="P1", created_by="root")
    return service


def _save(service, *, workflow_key="wf", name="W"):
    return service.workflows.upsert_definition(
        actor="root",
        workflow_key=workflow_key,
        name=name,
        description="",
        profile_key="p1",
        status="active",
        workflow_type="operation",
        definition={"nodes": [dict(GET_TASK_NODE)], "edges": []},
    )


def _export_payload(workflow_key="new-wf", *, name="Imported"):
    return {
        "format": "agent-bridge.workflow",
        "format_version": 1,
        "exported_at": "2026-07-18T00:00:00+00:00",
        "exported_by": "root",
        "workflow": {
            "workflow_key": workflow_key,
            "name": name,
            "description": "Imported description",
            "profile_key": "p1",
            "status": "active",
            "workflow_type": "operation",
            "definition": {"nodes": [dict(GET_TASK_NODE)], "edges": []},
        },
        "revision": {
            "revision_no": 1,
            "content_hash": "source-hash",
            "source": "edit",
            "created_by": "root",
            "created_at": "2026-07-18T00:00:00+00:00",
        },
    }


def _preview(service, payload, *, target_workflow_key=None, target_mode="auto"):
    return service.workflows.preview_definition_import(
        actor="root",
        filename="workflow.workflow.json",
        content=json.dumps(payload).encode("utf-8"),
        target_workflow_key=target_workflow_key,
        target_mode=target_mode,
    )


def test_import_preview_creates_new_workflow_session(wm_paths):
    service = _make_service(wm_paths)

    preview = _preview(service, _export_payload("new-wf"))

    assert preview["operation"] == "create"
    assert preview["target_workflow_key"] == "new-wf"
    assert preview["can_confirm"] is True
    assert preview["diff"] is None

    saved = service.workflows.confirm_definition_import("root", preview["import_id"])
    assert saved["workflow_key"] == "new-wf"
    assert saved["revision_no"] == 1
    assert service.workflows.get_revision("root", "new-wf", 1)["source"] == "import"
    assert service.store.workflows.get_workflow_definition_import(preview["import_id"]) is None


def test_import_preview_existing_returns_diff_and_confirm_appends_revision(wm_paths):
    service = _make_service(wm_paths)
    _save(service, name="old")

    preview = _preview(
        service,
        _export_payload("wf", name="new"),
        target_workflow_key="wf",
        target_mode="overwrite",
    )

    assert preview["operation"] == "overwrite"
    assert preview["target_revision_no"] == 1
    assert preview["diff"]["structured"]["identical"] is False
    saved = service.workflows.confirm_definition_import("root", preview["import_id"])
    assert saved["revision_no"] == 2
    assert saved["name"] == "new"
    assert service.workflows.get_revision("root", "wf", 2)["source"] == "import"


def test_import_preview_auto_uses_overwrite_for_existing_key(wm_paths):
    service = _make_service(wm_paths)
    _save(service, name="old")

    preview = _preview(service, _export_payload("wf", name="incoming"))

    assert preview["operation"] == "overwrite"
    assert preview["target_workflow_key"] == "wf"
    assert preview["diff"]["structured"]["identical"] is False


def test_import_preview_rejects_invalid_envelope(wm_paths):
    service = _make_service(wm_paths)

    with pytest.raises(ValidationError, match="请使用系统导出的工作流 JSON 文件"):
        _preview(service, {"format": "wrong", "format_version": 1})


def test_import_confirm_rejects_stale_target_without_mutation(wm_paths):
    service = _make_service(wm_paths)
    _save(service, name="old")
    preview = _preview(
        service,
        _export_payload("wf", name="incoming"),
        target_workflow_key="wf",
        target_mode="overwrite",
    )
    _save(service, name="changed-after-preview")

    with pytest.raises(ConflictError, match="预览后发生了变化"):
        service.workflows.confirm_definition_import("root", preview["import_id"])
    assert service.workflows.get_definition("root", "wf")["name"] == "changed-after-preview"
    assert len(service.workflows.list_revisions("root", "wf")) == 2


def test_import_confirm_rejects_expired_session(wm_paths):
    service = _make_service(wm_paths)
    preview = _preview(service, _export_payload("new-wf"))
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE workflow_definition_imports SET expires_at = ? WHERE import_id = ?",
            (expired, preview["import_id"]),
        )

    with pytest.raises(ValidationError, match="expired"):
        service.workflows.confirm_definition_import("root", preview["import_id"])


def test_import_overwrite_can_upgrade_legacy_workflow_without_revision(wm_paths):
    service = _make_service(wm_paths)
    _save(service, name="legacy")
    with service.store.connect() as conn:
        conn.execute("DELETE FROM workflow_definition_revisions WHERE workflow_key = ?", ("wf",))
        conn.execute(
            "UPDATE workflow_definitions SET current_revision_no = 0 WHERE workflow_key = ?",
            ("wf",),
        )

    preview = _preview(
        service,
        _export_payload("wf", name="incoming"),
        target_workflow_key="wf",
        target_mode="overwrite",
    )
    saved = service.workflows.confirm_definition_import("root", preview["import_id"])

    assert saved["revision_no"] == 1
    assert service.workflows.get_revision("root", "wf", 1)["source"] == "import"


def test_reusing_deleted_workflow_key_starts_fresh_revision_history(wm_paths):
    service = _make_service(wm_paths)
    _save(service, name="W")
    service.workflows.delete_definition("root", "wf")

    preview = _preview(service, _export_payload("wf", name="W"))
    saved = service.workflows.confirm_definition_import("root", preview["import_id"])

    assert saved["revision_no"] == 1
    assert saved["revision_source"] == "import"
    assert [item["revision_no"] for item in service.workflows.list_revisions("root", "wf")] == [1]
    assert service.workflows.export_definition("root", "wf")["revision"]["revision_no"] == 1
