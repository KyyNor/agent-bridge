from __future__ import annotations

from agent_bridge.app.service import AgentBridgeService


def test_validator_returns_stable_code_for_invalid_ancestor_reference(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    result = service.workflows.validator.validate(
        actor="root",
        workflow={
            "workflow_key": "bad-ref",
            "name": "Bad Ref",
            "description": "",
            "profile_key": "default",
            "status": "active",
            "workflow_type": "operation",
            "definition": {
                "nodes": [
                    {
                        "id": "a",
                        "type": "agent",
                        "name": "A",
                        "position": {"x": 0, "y": 0},
                        "config": {"prompt": "", "backend_key": "codex"},
                    },
                    {
                        "id": "b",
                        "type": "agent",
                        "name": "B",
                        "position": {"x": 1, "y": 0},
                        "config": {"prompt": "{{ nodes.c.output.text }}", "backend_key": "codex"},
                    },
                ],
                "edges": [{"id": "a-b", "source": "a", "target": "b", "condition": None}],
            },
        },
    )

    assert result.valid is False
    assert result.errors[0].code == "invalid_reference"
    assert result.errors[0].scope == "node"
    assert result.errors[0].id == "b"
