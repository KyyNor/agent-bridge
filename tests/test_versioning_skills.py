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
