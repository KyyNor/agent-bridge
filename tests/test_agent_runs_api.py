from __future__ import annotations

from fastapi.testclient import TestClient


def _client(wm_paths) -> TestClient:
    from agent_bridge.api.app import create_app

    return TestClient(create_app(wm_paths, {"root"}))


def test_agent_runs_api_lists_filters_and_gets_detail(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="greeter_abc",
        agent_name="greeter",
        profile_key="p1",
        ok=True,
        prompt="hi",
        result="hello",
        events=[{"kind": "result", "message": "done"}],
        duration_ms=10,
    )
    svc.store.agent_runs.create(
        run_key="failer_def",
        agent_name="failer",
        ok=False,
        prompt="x",
        error="boom",
        events=[{"kind": "error", "message": "boom"}],
    )

    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}

    listed = client.get("/agent-runs", headers=headers)
    assert listed.status_code == 200
    keys = {row["run_key"] for row in listed.json()}
    assert keys == {"greeter_abc", "failer_def"}
    # list (summary) view drops heavy columns
    assert "prompt" not in listed.json()[0]
    assert "events" not in listed.json()[0]

    # filter by ok=true
    ok_only = client.get("/agent-runs?ok=true", headers=headers).json()
    assert [row["run_key"] for row in ok_only] == ["greeter_abc"]

    # filter by agent_name
    one = client.get("/agent-runs?agent_name=failer", headers=headers).json()
    assert [row["run_key"] for row in one] == ["failer_def"]

    # detail includes prompt / result / events
    detail = client.get("/agent-runs/greeter_abc", headers=headers).json()
    assert detail["prompt"] == "hi"
    assert detail["result"] == "hello"
    assert detail["ok"] is True
    assert detail["events"][0]["kind"] == "result"

    # 404 for missing
    missing = client.get("/agent-runs/nope", headers=headers)
    assert missing.status_code == 404
