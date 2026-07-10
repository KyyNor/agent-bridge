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
        status="failed",
        prompt="x",
        error="boom",
        events=[{"kind": "error", "message": "boom"}],
    )
    svc.store.agent_runs.create(
        run_key="runner_xyz",
        agent_name="runner",
        ok=False,
        status="running",
        prompt="still working",
        events=[],
    )

    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}

    listed = client.get("/agent-runs", headers=headers)
    assert listed.status_code == 200
    keys = {row["run_key"] for row in listed.json()}
    assert keys == {"greeter_abc", "failer_def", "runner_xyz"}
    # list (summary) view drops heavy columns
    assert "prompt" not in listed.json()[0]
    assert "events" not in listed.json()[0]

    # filter by ok=true
    ok_only = client.get("/agent-runs?ok=true", headers=headers).json()
    assert [row["run_key"] for row in ok_only] == ["greeter_abc"]

    # filter by agent_name
    one = client.get("/agent-runs?agent_name=failer", headers=headers).json()
    assert [row["run_key"] for row in one] == ["failer_def"]

    # filter by terminal status, so running placeholders are not treated as failed.
    failed_only = client.get("/agent-runs?status=failed", headers=headers).json()
    assert [row["run_key"] for row in failed_only] == ["failer_def"]

    # detail includes prompt / result / events
    detail = client.get("/agent-runs/greeter_abc", headers=headers).json()
    assert detail["prompt"] == "hi"
    assert detail["result"] == "hello"
    assert detail["ok"] is True
    assert detail["events"][0]["kind"] == "result"

    # 404 for missing
    missing = client.get("/agent-runs/nope", headers=headers)
    assert missing.status_code == 404


def test_agent_runs_api_filters_by_workflow_run_id(wm_paths) -> None:
    """The workflow runner forwards workflow_key/run_id so each produced agent_runs
    row is lookable by workflow_run_id — the unified query path for run results."""
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="workflow_runA",
        agent_name="workflow",
        workflow_key="github-summary",
        workflow_run_id="run_1",
        ok=True,
        prompt="p",
        result="done",
        events=[],
        duration_ms=10,
    )
    svc.store.agent_runs.create(
        run_key="workflow_runB",
        agent_name="workflow",
        workflow_key="github-summary",
        workflow_run_id="run_2",
        ok=True,
        prompt="p",
        result="done",
        events=[],
        duration_ms=10,
    )

    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}

    # Reverse-lookup by workflow_run_id returns exactly the one matching row.
    rows = client.get("/agent-runs?workflow_run_id=run_1", headers=headers).json()
    assert [row["run_key"] for row in rows] == ["workflow_runA"]

    # And filterable by workflow_key returns both.
    rows = client.get("/agent-runs?workflow_key=github-summary", headers=headers).json()
    assert {row["run_key"] for row in rows} == {"workflow_runA", "workflow_runB"}


def test_agent_run_events_reads_live_jsonl_falling_back_to_db(wm_paths, tmp_path) -> None:
    """The /agent-runs/{run_key}/events endpoint serves the live events.jsonl
    (written in real time) when present, falling back to persisted DB events."""
    import json

    from agent_bridge.app.service import AgentBridgeService

    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    # Simulate a run in flight: AgentService writes events.jsonl as it streams.
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"kind": "status", "status": "init"}),
                json.dumps({"kind": "agent_message", "message": "working"}),
            ]
        ),
        encoding="utf-8",
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    # The placeholder row holds no events yet (they are flushed at completion).
    svc.store.agent_runs.create(
        run_key="live_run",
        agent_name="workflow",
        cwd=str(run_dir),
        status="running",
        ok=False,
        prompt="",
        events=[],
    )

    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}

    # Live file takes precedence — progress is visible before the run finishes.
    events = client.get("/agent-runs/live_run/events", headers=headers).json()
    assert [e["kind"] for e in events] == ["status", "agent_message"]
    assert events[1]["message"] == "working"

    # When the live file is absent, the persisted DB events are used instead.
    (run_dir / "events.jsonl").unlink()
    svc.store.agent_runs.finish_run(
        "live_run",
        ok=True,
        status="completed",
        events=[{"kind": "result", "message": "done"}],
    )
    events = client.get("/agent-runs/live_run/events", headers=headers).json()
    assert [e["kind"] for e in events] == ["result"]


def test_agent_run_detail_recovers_schema_result_from_agent_message_events(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="design_script_old_opencode",
        agent_name="design_script",
        ok=True,
        status="completed",
        prompt="make a script",
        output_schema={
            "type": "object",
            "required": ["summary", "script"],
            "properties": {
                "summary": {"type": "string"},
                "script": {"type": "object"},
            },
        },
        result="done",
        events=[
            {"kind": "result", "message": "done"},
            {
                "kind": "agent_message",
                "message": (
                    "```json\n"
                    '{"summary":"created","script":{"script_key":"fib","code":"def main(envelope):\\n    return {}\\n"}}'
                    "\n```"
                ),
            },
        ],
    )

    client = _client(wm_paths)
    detail = client.get(
        "/agent-runs/design_script_old_opencode",
        headers={"X-Agent-Bridge-User": "root"},
    ).json()

    assert detail["result"]["summary"] == "created"
    assert detail["result"]["script"]["script_key"] == "fib"
    assert "def main" in detail["result"]["script"]["code"]


def test_agent_run_events_includes_terminal_db_events_when_live_jsonl_is_stale(wm_paths, tmp_path) -> None:
    """If a run fails after the streaming file was closed, the terminal DB error
    must still appear even though events.jsonl exists."""
    import json

    from agent_bridge.app.service import AgentBridgeService

    run_dir = tmp_path / "run_failed"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        json.dumps({"kind": "status", "status": "init"}) + "\n",
        encoding="utf-8",
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="failed_live_run",
        agent_name="workflow",
        cwd=str(run_dir),
        status="running",
        ok=False,
        prompt="",
        events=[],
    )
    svc.store.agent_runs.finish_run(
        "failed_live_run",
        ok=False,
        status="failed",
        error="RuntimeError: sdk failed",
        events=[
            {"kind": "status", "status": "init"},
            {"kind": "error", "status": "failed", "message": "RuntimeError: sdk failed"},
        ],
    )

    client = _client(wm_paths)
    events = client.get(
        "/agent-runs/failed_live_run/events",
        headers={"X-Agent-Bridge-User": "root"},
    ).json()

    assert [event["kind"] for event in events] == ["status", "error"]
    assert events[-1]["message"] == "RuntimeError: sdk failed"


def test_agent_run_finish_preserves_existing_model_when_not_replaced(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="model_run",
        agent_name="design_script",
        model="claude-sonnet-4-5",
        status="running",
        ok=False,
        prompt="",
        events=[],
    )

    svc.store.agent_runs.finish_run(
        "model_run",
        ok=True,
        status="completed",
        model=None,
        events=[{"kind": "result", "status": "success"}],
    )

    assert svc.store.agent_runs.get("model_run")["model"] == "claude-sonnet-4-5"
