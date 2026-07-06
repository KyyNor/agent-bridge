from __future__ import annotations


def test_skill_prompt_override_and_reset(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    default_item = svc.skills.get_skill("root", "design_workflow")
    script_item = svc.skills.get_skill("root", "design_script")

    assert default_item["skill_name"] == "design_workflow"
    assert default_item["source"] == "default"
    assert "workflow.js" in default_item["prompt"]
    assert script_item["skill_name"] == "design_script"
    assert script_item["source"] == "default"
    assert "main(envelope)" in script_item["prompt"]

    saved = svc.skills.save_skill("root", "design_workflow", "custom workflow prompt")

    assert saved["source"] == "database"
    assert saved["prompt"] == "custom workflow prompt"
    assert svc.skills.get_skill("root", "design_workflow")["prompt"] == "custom workflow prompt"

    reset = svc.skills.reset_skill("root", "design_workflow")

    assert reset["source"] == "default"
    assert reset["prompt"] == default_item["prompt"]


def test_list_skills_includes_design_script(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})

    skills = svc.skills.list_skills("root")

    assert [item["skill_name"] for item in skills] == [
        "design_html_report",
        "design_script",
        "design_workflow",
    ]
    html_skill = svc.skills.get_skill("root", "design_html_report")
    assert html_skill["source"] == "default"
    assert "<!doctype html>" in html_skill["prompt"]


def test_skill_management_only_allows_known_skills(wm_paths):
    from agent_bridge.core.domain import NotFound
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})

    try:
        svc.skills.save_skill("root", "unknown", "prompt")
    except NotFound as exc:
        assert "skill not found" in str(exc)
    else:
        raise AssertionError("unknown skill should be rejected")
