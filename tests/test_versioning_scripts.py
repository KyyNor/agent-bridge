"""Version history + diff + Python syntax-check for managed scripts."""
from __future__ import annotations

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound, ValidationError


PERMISSIVE_INPUT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def _make_service(wm_paths):
    return AgentBridgeService.create(wm_paths, {"root"})


def _upsert(service, script_key="demo", code="def main(e):\n    return {}\n", **overrides):
    payload = dict(
        actor="root",
        script_key=script_key,
        name="Demo",
        description="",
        language="python",
        code=code,
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )
    payload.update(overrides)
    return service.scripts.upsert_script(**payload)


# --- version snapshots -------------------------------------------------------


def test_first_save_creates_revision_1(wm_paths):
    service = _make_service(wm_paths)
    saved = _upsert(service)
    assert saved["revision_no"] == 1
    revs = service.scripts.list_revisions("root", "demo")
    assert [r["revision_no"] for r in revs] == [1]
    assert revs[0]["is_current"] is True


def test_unchanged_save_does_not_create_new_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    again = _upsert(service)  # identical payload
    assert again["revision_no"] == 1


def test_changed_content_creates_new_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, code="def main(e):\n    return {}\n")
    v2 = _upsert(service, code="def main(e):\n    return {'x': 1}\n")
    assert v2["revision_no"] == 2
    revs = service.scripts.list_revisions("root", "demo")
    assert [r["revision_no"] for r in revs] == [2, 1]


def test_schema_change_creates_new_revision_even_when_code_is_unchanged(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    _upsert(
        service,
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": True,
        },
    )
    assert service.scripts.get_revision("root", "demo", 2)["snapshot"]["input_schema"]["properties"]


def test_script_save_rolls_back_when_revision_archive_fails(wm_paths, monkeypatch):
    service = _make_service(wm_paths)

    def fail_revision(**kwargs):
        raise RuntimeError("revision archive failed")

    monkeypatch.setattr(service.store.scripts, "create_revision", fail_revision)
    with pytest.raises(RuntimeError, match="revision archive failed"):
        _upsert(service)

    assert service.store.scripts.get_script("demo") is None
    assert service.store.scripts.list_revisions("demo") == []


def test_get_revision_returns_snapshot(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, code="def main(e):\n    return {'a': 1}\n")
    _upsert(service, code="def main(e):\n    return {'a': 2}\n")
    rev = service.scripts.get_revision("root", "demo", 1)
    assert rev["snapshot"]["code"] == "def main(e):\n    return {'a': 1}\n"
    with pytest.raises(NotFound):
        service.scripts.get_revision("root", "demo", 999)


def test_corrupt_script_revision_snapshot_is_not_silently_empty(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE script_revisions SET snapshot_json = ? WHERE script_key = ? AND revision_no = ?",
            ("{not-json", "demo", 1),
        )

    with pytest.raises(ValueError, match="corrupt script revision snapshot"):
        service.scripts.get_revision("root", "demo", 1)


def test_diff_revisions_returns_unified_text(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, code="def main(e):\n    return {}\n")
    _upsert(service, code="def main(e):\n    return {'changed': True}\n")
    diff = service.scripts.diff_revisions("root", "demo", from_no=1, to_no=2)
    assert diff["entity_type"] == "script"
    assert diff["from_revision"] == 1
    assert diff["to_revision"] == 2
    assert diff["text"]["identical"] is False
    assert "@@" in diff["text"]["content"]


def test_diff_defaults_to_current_vs_previous(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, code="def main(e):\n    return {}\n")
    _upsert(service, code="def main(e):\n    return {'v': 2}\n")
    _upsert(service, code="def main(e):\n    return {'v': 3}\n")
    diff = service.scripts.diff_revisions("root", "demo", from_no=None, to_no=None)
    assert diff["from_revision"] == 2
    assert diff["to_revision"] == 3


# --- syntax check ------------------------------------------------------------


def test_syntax_check_ok_for_valid_code(wm_paths):
    service = _make_service(wm_paths)
    saved = _upsert(service, code="def main(e):\n    return {}\n")
    assert saved["syntax_check"]["ok"] is True
    assert saved["syntax_check"]["errors"] == []


def test_syntax_check_reports_error_but_still_saves(wm_paths):
    service = _make_service(wm_paths)
    # Missing close paren — a real SyntaxError.
    saved = _upsert(service, code="def main(e\n    return {}\n")
    assert saved["revision_no"] == 1  # saved despite the error (warn-only)
    check = saved["syntax_check"]
    assert check["ok"] is False
    assert check["errors"][0]["line"] is not None
    assert check["errors"][0]["msg"]


def test_run_refuses_scripts_with_syntax_errors(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, code="def main(e\n")  # syntax error, still saved
    with pytest.raises(ValidationError, match="syntax"):
        service.scripts.run_script(
            actor="root",
            script_key="demo",
            script_params={},
            timeout_seconds=5,
            profile_key=None,
            workflow_context=None,
        )


def test_validate_code_endpoint_does_not_save(wm_paths):
    service = _make_service(wm_paths)
    result = service.scripts.validate_code("root", "def f(:")
    assert result["ok"] is False
    # No script should exist.
    with pytest.raises(NotFound):
        service.scripts.list_revisions("root", "demo")
