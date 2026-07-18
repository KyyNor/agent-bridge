"""Version history + diff for skill prompt overrides."""
from __future__ import annotations

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound


def _make_service(wm_paths):
    return AgentBridgeService.create(wm_paths, {"root"})


SKILL = "design_script"


def test_save_creates_revision(wm_paths):
    service = _make_service(wm_paths)
    saved = service.skills.save_skill("root", SKILL, "do thing one")
    assert saved["revision_no"] == 1
    revs = service.skills.list_revisions("root", SKILL)
    assert [r["revision_no"] for r in revs] == [1]


def test_unchanged_save_no_new_revision(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "same prompt")
    again = service.skills.save_skill("root", SKILL, "same prompt")
    assert again["revision_no"] == 1


def test_changed_save_creates_new_revision(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "version A")
    v2 = service.skills.save_skill("root", SKILL, "version B")
    assert v2["revision_no"] == 2


def test_reset_creates_revision_back_to_default(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "custom override")
    reset = service.skills.reset_skill("root", SKILL)
    assert reset["revision_no"] == 2
    assert reset["source"] == "default"
    revs = service.skills.list_revisions("root", SKILL)
    assert [r["revision_no"] for r in revs] == [2, 1]


def test_reset_to_the_same_effective_prompt_is_idempotent(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "custom override")
    first_reset = service.skills.reset_skill("root", SKILL)
    second_reset = service.skills.reset_skill("root", SKILL)

    assert first_reset["revision_no"] == 2
    assert second_reset["revision_no"] == 2
    revisions = service.skills.list_revisions("root", SKILL)
    assert [item["revision_no"] for item in revisions] == [2, 1]
    assert revisions[0]["is_current"] is True


def test_skill_save_rolls_back_when_revision_archive_fails(wm_paths, monkeypatch):
    service = _make_service(wm_paths)

    def fail_revision(**kwargs):
        raise RuntimeError("revision archive failed")

    monkeypatch.setattr(service.store, "create_skill_prompt_revision", fail_revision)
    with pytest.raises(RuntimeError, match="revision archive failed"):
        service.skills.save_skill("root", SKILL, "custom override")

    assert service.store.get_skill_prompt_override(SKILL) is None
    assert service.skills.list_revisions("root", SKILL) == []


def test_diff_revisions_returns_text(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "alpha prompt")
    service.skills.save_skill("root", SKILL, "beta prompt")
    diff = service.skills.diff_revisions("root", SKILL, from_no=1, to_no=2)
    assert diff["entity_type"] == "skill"
    assert diff["text"]["identical"] is False
    assert "alpha" in diff["text"]["content"]
    assert "beta" in diff["text"]["content"]


def test_get_revision_snapshot(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "the prompt body")
    rev = service.skills.get_revision("root", SKILL, 1)
    assert rev["snapshot"]["prompt"] == "the prompt body"
    with pytest.raises(NotFound):
        service.skills.get_revision("root", SKILL, 999)


def test_corrupt_skill_revision_snapshot_is_not_silently_empty(wm_paths):
    service = _make_service(wm_paths)
    service.skills.save_skill("root", SKILL, "the prompt body")
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE skill_prompt_revisions SET snapshot_json = ? WHERE skill_name = ? AND revision_no = ?",
            ("{not-json", SKILL, 1),
        )

    with pytest.raises(ValueError, match="corrupt skill revision snapshot"):
        service.skills.get_revision("root", SKILL, 1)
