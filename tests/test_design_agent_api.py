from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from agent_bridge.api.routes.agent_runs import SCRIPT_DESIGN_SCHEMA, WORKFLOW_DESIGN_SCHEMA


def _workflow_definition() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "collect",
                "type": "agent",
                "name": "Collect",
                "position": {"x": 0, "y": 0},
                "config": {
                    "prompt": "collect",
                    "backend_key": "codex",
                    "result_mode": "json",
                    "output_schema": {"type": "object", "properties": {}},
                },
            }
        ],
        "edges": [],
    }


def _client(wm_paths) -> TestClient:
    from agent_bridge.api.app import create_app

    return TestClient(create_app(wm_paths, {"root"}))


def test_workflow_design_agent_uses_design_workflow_skill(wm_paths) -> None:
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
                "summary": "updated",
                "notes": ["kept task protocol"],
                "workflow": {
                    "workflow_key": "page-report",
                    "name": "Page Report",
                    "description": "desc",
                    "profile_key": "report-plane",
                    "workflow_type": "operation",
                    "status": "active",
                    "definition": _workflow_definition(),
                },
            },
        )

    svc.agents.run = fake_run

    response = client.post(
        "/agent-runs/design/workflow",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "mode": "modify",
            "prompt": "加一个清理输出的步骤",
            "profile_key": "report-plane",
            "run_key": "design_workflow_fixed",
            "current": {
                "workflow_key": "page-report",
                "workflow_type": "operation",
                "definition": {"nodes": [], "edges": []},
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["workflow"]["workflow_key"] == "page-report"
    assert response.json()["result"]["workflow"]["definition"] == _workflow_definition()
    assert captured["agent_name"] == "design_workflow"
    assert captured["profile"] == "report-plane"
    assert captured["run_key"] == "design_workflow_fixed"
    assert "design_workflow 内容" in str(captured["prompt"])
    assert "structured workflow definition" in str(captured["prompt"])
    assert '"workflow_key": "page-report"' in str(captured["prompt"])


def test_workflow_design_schema_accepts_structured_definition() -> None:
    assert WORKFLOW_DESIGN_SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "definitions" in WORKFLOW_DESIGN_SCHEMA
    assert "$defs" not in WORKFLOW_DESIGN_SCHEMA
    validator = Draft7Validator(WORKFLOW_DESIGN_SCHEMA)
    result = {
        "summary": "updated",
        "notes": ["kept DAG shape"],
        "workflow": {
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "desc",
            "profile_key": "report-plane",
            "workflow_type": "operation",
            "status": "active",
            "definition": _workflow_definition(),
        },
    }

    assert list(validator.iter_errors(result)) == []


def test_workflow_design_schema_requires_complete_envelope() -> None:
    validator = Draft7Validator(WORKFLOW_DESIGN_SCHEMA)
    result = {
        "summary": "updated",
        "workflow": {
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "desc",
            "profile_key": "report-plane",
            "workflow_type": "operation",
            "status": "active",
            "definition": _workflow_definition(),
        },
    }

    assert any(error.validator == "required" and "notes" in error.message for error in validator.iter_errors(result))


def test_workflow_design_schema_rejects_legacy_workflow_js() -> None:
    validator = Draft7Validator(WORKFLOW_DESIGN_SCHEMA)
    result = {
        "summary": "updated",
        "workflow": {
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "desc",
            "profile_key": "report-plane",
            "status": "active",
            "workflow_js": "export const meta = {};",
        },
    }

    errors = list(validator.iter_errors(result))

    assert errors
    assert any("workflow_js" in error.message for error in errors)


def test_workflow_design_schema_rejects_incomplete_definition_node_config() -> None:
    validator = Draft7Validator(WORKFLOW_DESIGN_SCHEMA)
    result = {
        "summary": "invalid",
        "workflow": {
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "desc",
            "profile_key": "report-plane",
            "workflow_type": "operation",
            "status": "active",
            "definition": {
                "nodes": [
                    {
                        "id": "collect",
                        "type": "agent",
                        "name": "Collect",
                        "position": {"x": 0, "y": 0},
                        "config": {"prompt": "missing backend"},
                    }
                ],
                "edges": [],
            },
        },
    }

    errors = list(validator.iter_errors(result))

    assert errors
    pending = list(errors)
    messages: list[str] = []
    while pending:
        error = pending.pop()
        messages.append(error.message)
        pending.extend(error.context)
    assert any("backend_key" in message for message in messages)


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
        "/agent-runs/design/script",
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
