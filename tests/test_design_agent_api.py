from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


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
                    "status": "active",
                    "workflow_js": "export const meta = { name: 'x', description: '', phases: [] }",
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
            "current": {"workflow_key": "page-report", "workflow_js": "old"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["workflow"]["workflow_key"] == "page-report"
    assert captured["agent_name"] == "design_workflow"
    assert captured["profile"] == "report-plane"
    assert "design_workflow 内容" in str(captured["prompt"])
    assert '"workflow_key": "page-report"' in str(captured["prompt"])


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
            "current": {"language": "python"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["script"]["script_key"] == "system.echo"
    assert captured["agent_name"] == "design_script"
    assert "design_script 内容" in str(captured["prompt"])
    assert '"language": "python"' in str(captured["prompt"])
