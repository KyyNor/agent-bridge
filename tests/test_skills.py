from __future__ import annotations


def test_skill_prompt_override_and_reset(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    default_item = svc.skills.get_skill("root", "design_agent_node")
    script_item = svc.skills.get_skill("root", "design_script")

    assert default_item["skill_name"] == "design_agent_node"
    assert default_item["source"] == "default"
    assert default_item["description"] == "生成或修改单个 Agent 节点的提示词。"
    assert "output_schema_json" in default_item["prompt"]
    assert script_item["skill_name"] == "design_script"
    assert script_item["source"] == "default"
    assert "main(envelope)" in script_item["prompt"]

    saved = svc.skills.save_skill("root", "design_agent_node", "custom workflow prompt")

    assert saved["source"] == "database"
    assert saved["prompt"] == "custom workflow prompt"
    assert svc.skills.get_skill("root", "design_agent_node")["prompt"] == "custom workflow prompt"

    reset = svc.skills.reset_skill("root", "design_agent_node")

    assert reset["source"] == "default"
    assert reset["prompt"] == default_item["prompt"]


def test_design_agent_node_skill_mentions_structured_validation_and_omits_history_list(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})

    prompt = service.skills.get_skill("root", "design_agent_node")["prompt"]

    assert "只生成或修改当前一个 agent 节点" in prompt
    assert "output_schema_json" in prompt
    assert "不能阻止模板预先求值" in prompt


def test_design_script_skill_preserves_and_documents_output_schema(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})

    prompt = service.skills.get_skill("root", "design_script")["prompt"]

    assert "output_schema" in prompt
    assert "不得清空已有 output_schema" in prompt


def test_design_business_ledger_skill_describes_supported_field_rules(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})

    prompt = service.skills.get_skill("root", "design_business_ledger")["prompt"]

    assert "fuzzy_match" in prompt
    assert "200,000" in prompt
    assert "布尔" in prompt


def test_list_skills_includes_design_script(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})

    skills = svc.skills.list_skills("root")

    assert [item["skill_name"] for item in skills] == [
        "design_agent_node",
        "design_business_ledger",
        "design_html_report",
        "design_script",
    ]
    html_skill = svc.skills.get_skill("root", "design_html_report")
    assert html_skill["source"] == "default"
    assert "<!doctype html>" in html_skill["prompt"]
    assert "max-width: 1280px" in html_skill["prompt"]


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
