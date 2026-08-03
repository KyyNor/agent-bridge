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
        backend_key="opencode",
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
    assert detail["backend_key"] == "opencode"
    assert detail["prompt"] == "hi"
    assert detail["result"] == "hello"
    assert detail["ok"] is True
    assert detail["events"][0]["kind"] == "result"

    # 404 for missing
    missing = client.get("/agent-runs/nope", headers=headers)
    assert missing.status_code == 404


def test_agent_run_stop_api_handles_active_terminal_pending_and_missing(wm_paths) -> None:
    client = _client(wm_paths)
    svc = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="active_run",
        agent_name="workflow",
        status="running",
        ok=False,
        prompt="",
    )
    svc.agents.control_registry.register("active_run")
    svc.store.agent_runs.create(
        run_key="completed_run",
        agent_name="workflow",
        status="completed",
        ok=True,
        prompt="",
        result={"ok": True},
    )
    headers = {"X-Agent-Bridge-User": "root"}

    active = client.post("/agent-runs/active_run/stop", headers=headers)
    assert active.status_code == 202
    assert active.json() == {"status": "stopping", "run_key": "active_run"}
    assert svc.agents.control_registry.is_stop_requested("active_run") is True

    repeated = client.post("/agent-runs/active_run/stop", headers=headers)
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "stopping"

    terminal = client.post("/agent-runs/completed_run/stop", headers=headers)
    assert terminal.status_code == 200
    assert terminal.json()["run_key"] == "completed_run"
    assert terminal.json()["status"] == "completed"

    svc.agents.request_stop("pending_run")
    pending = client.post("/agent-runs/pending_run/stop", headers=headers)
    assert pending.status_code == 202
    assert pending.json() == {"status": "stopping", "run_key": "pending_run"}

    missing = client.post("/agent-runs/missing_run/stop", headers=headers)
    assert missing.status_code == 404


def test_agent_run_stop_requires_admin_and_active_controller_for_running_rows(wm_paths) -> None:
    client = _client(wm_paths)
    svc = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="stale_running",
        agent_name="workflow",
        status="running",
        ok=False,
        prompt="",
    )
    headers = {"X-Agent-Bridge-User": "root"}

    non_admin = client.post(
        "/agent-runs/stale_running/stop",
        headers={"X-Agent-Bridge-User": "viewer"},
    )
    assert non_admin.status_code == 403
    assert not svc.agents.control_registry.has_pending_control("stale_running")

    stale = client.post("/agent-runs/stale_running/stop", headers=headers)
    assert stale.status_code == 409
    assert not svc.agents.control_registry.has_pending_control("stale_running")


def test_agent_run_stop_accepts_active_registration_before_database_row(wm_paths) -> None:
    client = _client(wm_paths)
    svc = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.agents.control_registry.register("registered_before_row")

    response = client.post(
        "/agent-runs/registered_before_row/stop",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "stopping", "run_key": "registered_before_row"}


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


def test_agent_runs_api_paginated_search_and_status_counts(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="shared_success",
        agent_name="planner",
        profile_key="p1",
        workflow_key="shared-workflow",
        ok=True,
        status="completed",
    )
    svc.store.agent_runs.create(
        run_key="shared_failed",
        agent_name="planner",
        profile_key="p1",
        workflow_key="shared-workflow",
        ok=False,
        status="failed",
        error="failed",
    )
    svc.store.agent_runs.create(
        run_key="shared_running",
        agent_name="planner",
        profile_key="p1",
        workflow_key="shared-workflow",
        ok=False,
        status="running",
    )
    svc.store.agent_runs.create(
        run_key="shared_stopped",
        agent_name="planner",
        profile_key="p1",
        workflow_key="shared-workflow",
        ok=False,
        status="stopped",
    )

    client = _client(wm_paths)
    response = client.get(
        "/agent-runs",
        params={"paginated": "true", "search": "shared", "status": "failed", "limit": 1, "offset": -4},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["run_key"] for item in body["items"]] == ["shared_failed"]
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert body["counts"] == {"all": 4, "success": 1, "failed": 1, "running": 1, "stopped": 1}


def test_agent_runs_api_paginates_beyond_two_hundred_rows(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    for index in range(205):
        svc.store.agent_runs.create(
            run_key=f"bulk_{index:03d}",
            agent_name="bulk-agent",
            ok=True,
            status="completed",
        )

    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    first = client.get(
        "/agent-runs",
        params={"paginated": "true", "limit": 10, "offset": 0},
        headers=headers,
    ).json()
    last = client.get(
        "/agent-runs",
        params={"paginated": "true", "limit": 10, "offset": 200},
        headers=headers,
    ).json()

    assert first["total"] == 205
    assert len(first["items"]) == 10
    assert len(last["items"]) == 5
    assert {item["run_key"] for item in first["items"]}.isdisjoint(
        item["run_key"] for item in last["items"]
    )

def test_agent_runs_schema_migrates_backend_key_for_existing_database(wm_paths) -> None:
    import sqlite3

    from agent_bridge.storage.sqlite import SQLiteStore

    wm_paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(wm_paths.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agent_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_key TEXT NOT NULL UNIQUE,
              agent_name TEXT NOT NULL,
              profile_key TEXT,
              workflow_key TEXT,
              workflow_run_id TEXT,
              session_id TEXT,
              cwd TEXT,
              model TEXT,
              ok INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT '',
              error TEXT,
              duration_ms INTEGER,
              cost_usd REAL,
              num_turns INTEGER,
              prompt TEXT NOT NULL,
              output_schema_json TEXT,
              result_json TEXT,
              events_json TEXT NOT NULL DEFAULT '[]',
              started_at TEXT,
              finished_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    row = store.agent_runs.create(
        run_key="migrated_run",
        agent_name="agent",
        backend_key="codex",
        prompt="x",
    )

    assert row["backend_key"] == "codex"


def test_agent_runs_schema_backfills_backend_key_from_historical_events(wm_paths) -> None:
    import json
    import sqlite3

    from agent_bridge.storage.sqlite import SQLiteStore

    wm_paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(wm_paths.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agent_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_key TEXT NOT NULL UNIQUE,
              agent_name TEXT NOT NULL,
              backend_key TEXT,
              profile_key TEXT,
              workflow_key TEXT,
              workflow_run_id TEXT,
              session_id TEXT,
              cwd TEXT,
              model TEXT,
              ok INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT '',
              error TEXT,
              duration_ms INTEGER,
              cost_usd REAL,
              num_turns INTEGER,
              prompt TEXT NOT NULL,
              output_schema_json TEXT,
              result_json TEXT,
              events_json TEXT NOT NULL DEFAULT '[]',
              started_at TEXT,
              finished_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO agent_runs (run_key, agent_name, backend_key, ok, status, prompt, events_json)
            VALUES (?, ?, NULL, 1, 'completed', ?, ?)
            """,
            (
                "old_opencode_run",
                "design_script",
                "x",
                json.dumps([{"kind": "agent_message", "source": "opencode_cli"}]),
            ),
        )

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    assert store.agent_runs.get("old_opencode_run")["backend_key"] == "opencode"


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


def test_agent_run_payload_api_reads_large_payload_and_rejects_traversal(wm_paths, tmp_path) -> None:
    from agent_bridge.agent_runtime.trace import externalize_event_payloads
    from agent_bridge.app.service import AgentBridgeService

    run_dir = tmp_path / "payload_run"
    run_dir.mkdir()
    event = externalize_event_payloads(
        {
            "kind": "tool_result",
            "call_id": "call_1",
            "output": {"stdout": "x" * 10000},
        },
        run_dir,
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="payload_run",
        agent_name="codex",
        cwd=str(run_dir),
        status="completed",
        ok=True,
        prompt="",
        events=[event],
    )
    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}

    payload = client.get(
        "/agent-runs/payload_run/payload",
        params={"ref": event["output_payload_ref"]},
        headers=headers,
    )
    assert payload.status_code == 200
    assert payload.json()["stdout"] == "x" * 10000

    traversal = client.get(
        "/agent-runs/payload_run/payload",
        params={"ref": "../outside.json"},
        headers=headers,
    )
    assert traversal.status_code == 404


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


def test_agent_run_event_stream_replays_from_last_event_id_then_closes_terminal(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.agent_runs.create(
        run_key="stream_done",
        agent_name="workflow",
        status="completed",
        ok=True,
        prompt="",
        events=[
            {"event_id": 1, "kind": "status", "status": "running"},
            {"event_id": 2, "kind": "agent_message", "message": "one"},
            {"event_id": 3, "kind": "stage", "stage_name": "run.total"},
        ],
    )

    client = _client(wm_paths)
    response = client.get(
        "/agent-runs/stream_done/events/stream",
        headers={"X-Agent-Bridge-User": "root", "Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert "id: 2\nevent: agent_event\ndata: {\"event_id\":2" in response.text
    assert "id: 3\nevent: agent_event\ndata: {\"event_id\":3" in response.text
    assert "event: run_terminal" in response.text
    assert "id: 1\nevent: agent_event" not in response.text
