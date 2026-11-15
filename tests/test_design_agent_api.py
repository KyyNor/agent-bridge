from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from agent_bridge.api.routes.agent_runs import BUSINESS_LEDGER_DESIGN_SCHEMA, SCRIPT_DESIGN_SCHEMA




def _client(wm_paths) -> TestClient:
    from agent_bridge.api.app import create_app

    return TestClient(create_app(wm_paths, {"root"}))








def test_business_ledger_design_agent_uses_design_business_ledger_skill(wm_paths) -> None:
    client = _client(wm_paths)
    svc = client.app.state.agent_bridge_service
    captured: dict[str, object] = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            error=None,
            run_key=None,
            result={
                "summary": "created",
                "notes": ["文本名称支持模糊匹配"],
                "ledger": {
                    "ledger_key": "asset_inventory",
                    "name": "资产台账",
                    "description": "维护系统资产",
                    "fields": [
                        {
                            "field_key": "name",
                            "name": "名称",
                            "field_type": "text",
                            "required": True,
                            "fuzzy_match": True,
                            "agent_readable": True,
                            "enum_values": [],
                        }
                    ],
                },
            },
        )

    svc.agents.run = fake_run
    response = client.post(
        "/api/v1/agent-runs/design/business-ledger",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "mode": "create",
            "prompt": "创建一份系统资产台账",
            "run_key": "design_business_ledger_fixed",
            "current": {"fields": []},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["ledger"]["ledger_key"] == "asset_inventory"
    assert captured["agent_name"] == "design_business_ledger"
    assert captured["run_key"] == "design_business_ledger_fixed"
    assert "design_business_ledger 内容" in str(captured["prompt"])
    assert "business ledger definition" in str(captured["prompt"])


def test_business_ledger_design_schema_accepts_complete_definition() -> None:
    validator = Draft7Validator(BUSINESS_LEDGER_DESIGN_SCHEMA)
    result = {
        "summary": "created",
        "notes": ["按 Excel 维护"],
        "ledger": {
            "ledger_key": "asset_inventory",
            "name": "资产台账",
            "description": "维护系统资产",
            "fields": [
                {
                    "field_key": "name",
                    "name": "名称",
                    "field_type": "text",
                    "required": True,
                    "fuzzy_match": True,
                    "agent_readable": True,
                    "enum_values": [],
                }
            ],
        },
    }

    assert list(validator.iter_errors(result)) == []
    invalid = {**result, "ledger": {**result["ledger"], "fields": []}}
    assert list(validator.iter_errors(invalid))






def test_script_design_agent_uses_design_script_skill(wm_paths) -> None:
    client = _client(wm_paths)
    svc = client.app.state.agent_bridge_service
    captured: dict[str, object] = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            error=None,
            run_key=None,
            result={
                "summary": "created",
                "script": {
                    "script_key": "system.echo",
                    "name": "Echo",
                    "description": "",
                    "language": "python",
                    "code": "def main(envelope):\n    return {}\n",
                    "input_schema": {"type": "object", "properties": {}, "required": []},
                    "output_schema": None,
                    "status": "active",
                    "owner_type": "system",
                    "owner_key": "",
                },
            },
        )

    svc.agents.run = fake_run

    response = client.post(
        "/api/v1/agent-runs/design/script",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "mode": "create",
            "prompt": "创建 echo 脚本",
            "run_key": "design_script_fixed",
            "current": {"language": "python"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["script"]["script_key"] == "system.echo"
    assert captured["agent_name"] == "design_script"
    assert captured["run_key"] == "design_script_fixed"
    assert "design_script 内容" in str(captured["prompt"])
    assert '"language": "python"' in str(captured["prompt"])


def test_script_design_schema_requires_object_input_schema_shape() -> None:
    validator = Draft7Validator(SCRIPT_DESIGN_SCHEMA)
    base_script = {
        "script_key": "system.echo",
        "name": "Echo",
        "description": "",
        "language": "python",
        "code": "def main(envelope):\n    return {}\n",
        "status": "active",
        "owner_type": "system",
        "owner_key": "",
    }
    missing_type = {"summary": "x", "script": {**base_script, "input_schema": {"properties": {}}}}
    wrong_type = {"summary": "x", "script": {**base_script, "input_schema": {"type": "string", "properties": {}}}}
    invalid_required = {"summary": "x", "script": {**base_script, "input_schema": {"type": "object", "properties": {}, "required": "value"}}}
    assert list(validator.iter_errors(missing_type))
    assert list(validator.iter_errors(wrong_type))
    assert list(validator.iter_errors(invalid_required))


def test_script_design_schema_accepts_nullable_output_schema_and_requires_field() -> None:
    validator = Draft7Validator(SCRIPT_DESIGN_SCHEMA)
    base_script = {
        "script_key": "system.echo",
        "name": "Echo",
        "description": "",
        "language": "python",
        "code": "def main(envelope):\n    return {}\n",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "status": "active",
        "owner_type": "system",
        "owner_key": "",
    }

    assert list(validator.iter_errors({"summary": "x", "script": {**base_script, "output_schema": None}})) == []
    assert list(
        validator.iter_errors(
            {
                "summary": "x",
                "script": {
                    **base_script,
                    "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                },
            }
        )
    ) == []
    assert list(validator.iter_errors({"summary": "x", "script": base_script}))
