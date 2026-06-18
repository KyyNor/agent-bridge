from __future__ import annotations


def test_message_events_skips_thinking_tokens_partial():
    from types import SimpleNamespace

    from agent_bridge.workflows.runner import _message_events

    # Streaming partials carrying the thinking_tokens subtype are noisy and must
    # not surface in the run event log.
    thinking = SimpleNamespace(subtype="thinking_tokens", session_id="session_1")
    assert _message_events(thinking, tool_names={}) == []

    # Init remains useful as a lifecycle marker, but high-frequency task progress
    # partials are internal SDK noise and must not surface in the UI event log.
    init = SimpleNamespace(subtype="init", session_id="session_1")
    init_events = _message_events(init, tool_names={})
    assert len(init_events) == 1
    assert init_events[0]["kind"] == "status"
    assert init_events[0]["status"] == "init"
    task_progress = SimpleNamespace(subtype="task_progress", session_id="session_1")
    assert _message_events(task_progress, tool_names={}) == []


def test_runner_drops_noisy_partial_messages_from_all_logs(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from claude_agent_sdk import ResultMessage

    from agent_bridge.workflows import runner as runner_module
    from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec

    class FakeOptions:
        def __init__(self, **kwargs):
            pass

    async def fake_query(*, prompt, options):
        yield SimpleNamespace(subtype="init", session_id="session_1")
        yield SimpleNamespace(subtype="thinking_tokens", session_id="session_1")
        yield SimpleNamespace(subtype="task_progress", session_id="session_1")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session_1",
            result="done",
            total_cost_usd=0.0,
        )

    monkeypatch.setattr(runner_module, "ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr(runner_module, "claude_query", fake_query)
    monkeypatch.setattr(runner_module, "claude_settings_env", lambda: {})

    result = ClaudeWorkflowRunner().run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export const manifest = {};",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    stdout_text = result.stdout_path.read_text(encoding="utf-8")
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # Noisy partials must be absent from both the raw message log and the event stream.
    assert "thinking_tokens" not in stdout_text
    assert "task_progress" not in stdout_text
    assert all(event.get("status") != "thinking_tokens" for event in events)
    assert all(event.get("status") != "task_progress" for event in events)
    # Other subtypes still flow through.
    assert any(event.get("status") == "init" for event in events)


def test_runner_prepares_run_directory_with_workflow_files(tmp_path):
    from agent_bridge.workflows.runner import WorkflowRunSpec, prepare_run_directory

    spec = WorkflowRunSpec(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        workflow_js="export const manifest = {};",
        mcp_url="http://127.0.0.1:8765/mcp",
    )

    run_dir = prepare_run_directory(tmp_path, spec)

    assert (run_dir / "workflow.js").read_text(encoding="utf-8") == "export const manifest = {};"
    assert (run_dir / "out").is_dir()
    mcp_config = (run_dir / ".mcp.json").read_text(encoding="utf-8")
    assert "X-Agent-Bridge-Workflow" in mcp_config
    assert "page-report" in mcp_config
    assert "run_1" in mcp_config


def test_fake_runner_writes_no_task_result(tmp_path):
    from agent_bridge.workflows.runner import FakeWorkflowRunner, WorkflowRunSpec

    runner = FakeWorkflowRunner(status="no_executable_task")
    result = runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="page-report",
            profile_key="report-plane",
            workflow_js="",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    assert result.exit_code == 0
    assert (result.run_dir / "out" / "result.json").exists()


def test_claude_runner_uses_agent_sdk_options_and_logs_messages(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

    from agent_bridge.workflows import runner as runner_module
    from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec

    captured: dict[str, object] = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["options"] = kwargs

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options_object"] = options
        yield SimpleNamespace(subtype="init", session_id="session_1")
        yield AssistantMessage(
            content=[
                TextBlock("Reading workflow.js"),
                ToolUseBlock(id="toolu_1", name="workflow_claim_task", input={"hidden": True}),
            ],
            model="test-model",
            session_id="session_1",
        )
        yield UserMessage(content=[ToolResultBlock(tool_use_id="toolu_1", content="hidden result", is_error=False)])
        yield ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="session_1",
            result="workflow complete",
            total_cost_usd=0.01,
        )

    monkeypatch.setattr(runner_module, "ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr(runner_module, "claude_query", fake_query)
    monkeypatch.setattr(runner_module, "claude_settings_env", lambda: {"ANTHROPIC_BASE_URL": "https://example.test"})

    result = ClaudeWorkflowRunner().run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export const manifest = {};",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    options = captured["options"]
    assert captured["prompt"] == "Run the workflow defined in ./workflow.js and write the final result to ./out/result.json."
    assert options["cwd"] == result.run_dir
    assert options["tools"] == {"type": "preset", "preset": "claude_code"}
    assert options["mcp_servers"] == result.run_dir / ".mcp.json"
    assert options["strict_mcp_config"] is True
    assert options["permission_mode"] == "auto"
    assert "ANTHROPIC_BASE_URL" in options["env"]
    assert options["setting_sources"] == []
    assert options["include_partial_messages"] is True
    assert options["system_prompt"]["type"] == "preset"
    assert options["system_prompt"]["preset"] == "claude_code"
    assert "Agent Bridge workflow" in options["system_prompt"]["append"]
    stdout = result.stdout_path.read_text(encoding="utf-8")
    assert "session_1" in stdout
    assert "workflow complete" in stdout
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["kind"] for event in events] == [
        "status",
        "agent_message",
        "tool_call",
        "tool_result",
        "result",
    ]
    assert events[1]["message"] == "Reading workflow.js"
    assert events[1]["agent_name"] == "claude"
    assert events[1]["source"] == "claude_agent_sdk"
    assert events[2]["tool_name"] == "workflow_claim_task"
    assert events[2]["status"] == "started"
    assert events[3]["tool_name"] == "workflow_claim_task"
    assert events[3]["status"] == "success"
    assert events[3]["message"] == "工具 workflow_claim_task 调用成功"
    assert events[4]["message"] == "workflow complete"
    assert events[4]["total_cost_usd"] == 0.01


def test_claude_runner_returns_failure_when_sdk_raises(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from agent_bridge.workflows import runner as runner_module
    from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec

    class FakeOptions:
        def __init__(self, **kwargs):
            pass

    async def fake_query(*, prompt, options):
        yield SimpleNamespace(subtype="init", session_id="session_1")
        raise RuntimeError("sdk failed")

    monkeypatch.setattr(runner_module, "ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr(runner_module, "claude_query", fake_query)

    result = ClaudeWorkflowRunner().run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export const manifest = {};",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    assert result.exit_code == 1
    assert "session_1" in result.stdout_path.read_text(encoding="utf-8")
    stderr = result.stderr_path.read_text(encoding="utf-8")
    assert "RuntimeError" in stderr
    assert "sdk failed" in stderr
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["kind"] == "error"
    assert events[-1]["message"] == "RuntimeError: sdk failed"
