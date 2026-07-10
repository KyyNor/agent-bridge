from __future__ import annotations


def _patched_runner(wm_paths, monkeypatch, fake_query, *, env=None):
    """Build a ClaudeWorkflowRunner backed by an AgentService whose SDK calls are faked.

    The workflow runner delegates SDK execution through AgentService into the
    Claude adapter, so tests patch the adapter module (not the runner).
    """
    from agent_bridge.agent_runtime.adapters import claude as claude_agent
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import ClaudeWorkflowRunner

    class _FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(claude_agent, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(claude_agent, "claude_query", fake_query)
    monkeypatch.setattr(
        claude_agent, "claude_settings_env", lambda: env or {}
    )
    agents = AgentBridgeService.create(wm_paths, {"root"}).agents
    return ClaudeWorkflowRunner(agent_service=agents), _FakeOptions


def test_message_events_skips_thinking_tokens_partial():
    from types import SimpleNamespace

    from agent_bridge.agent_runtime.events import message_events

    # Streaming partials carrying the thinking_tokens subtype are noisy and must
    # not surface in the run event log.
    thinking = SimpleNamespace(subtype="thinking_tokens", session_id="session_1")
    assert message_events(thinking, tool_names={}) == []

    # Init remains useful as a lifecycle marker, but high-frequency task progress
    # partials are internal SDK noise and must not surface in the UI event log.
    init = SimpleNamespace(subtype="init", session_id="session_1")
    init_events = message_events(init, tool_names={})
    assert len(init_events) == 1
    assert init_events[0]["kind"] == "status"
    assert init_events[0]["status"] == "init"
    task_progress = SimpleNamespace(subtype="task_progress", session_id="session_1")
    assert message_events(task_progress, tool_names={}) == []


def test_runner_drops_noisy_partial_messages_from_all_logs(wm_paths, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from claude_agent_sdk import ResultMessage

    from agent_bridge.automation.workflows.runner import WorkflowRunSpec

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

    runner, _ = _patched_runner(wm_paths, monkeypatch, fake_query)

    result = runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export default async function workflow() {}",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    stdout_text = (result.run_dir / "messages.jsonl").read_text(encoding="utf-8")
    # Noisy partials must be absent from the raw message log (AgentService now
    # writes messages.jsonl; the event stream lives in the agent_runs table).
    assert "thinking_tokens" not in stdout_text
    assert "task_progress" not in stdout_text
    # Other subtypes still flow through to the raw log.
    assert "init" in stdout_text


def test_runner_prepares_run_directory_with_workflow_files(tmp_path):
    from agent_bridge.automation.workflows.runner import WorkflowRunSpec, prepare_run_directory

    spec = WorkflowRunSpec(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        workflow_js="export default async function workflow() {}",
        mcp_url="http://127.0.0.1:8765/mcp",
    )

    run_dir = prepare_run_directory(tmp_path, spec)

    assert (run_dir / "workflow.js").read_text(encoding="utf-8") == "export default async function workflow() {}"
    assert (run_dir / "out").is_dir()
    mcp_config = (run_dir / ".mcp.json").read_text(encoding="utf-8")
    assert "X-Agent-Bridge-Workflow" in mcp_config
    assert "page-report" in mcp_config
    assert "run_1" in mcp_config


def test_fake_runner_writes_no_task_result(tmp_path):
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner, WorkflowRunSpec

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


def test_claude_runner_uses_agent_sdk_options_and_logs_messages(wm_paths, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

    from agent_bridge.automation.workflows.runner import WorkflowRunSpec

    captured: dict[str, object] = {}

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

    runner, FakeOptions = _patched_runner(
        wm_paths, monkeypatch, fake_query, env={"ANTHROPIC_BASE_URL": "https://example.test"}
    )
    svc_store = runner._agent_service.store  # noqa: SLF001 — inspect persisted run

    result = runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export default async function workflow() {}",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    options = captured["options_object"].kwargs
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
    # Raw SDK messages are now persisted by AgentService to messages.jsonl.
    messages_text = (result.run_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "session_1" in messages_text
    assert "workflow complete" in messages_text
    # The unified event stream lives in the agent_runs table (looked up via the
    # forwarded workflow_run_id), not in a per-run events.jsonl file.
    runs = svc_store.agent_runs.list(workflow_run_id="run_1")
    assert len(runs) == 1
    detail = svc_store.agent_runs.get(runs[0]["run_key"])
    events = detail["events"]
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


def test_claude_runner_returns_failure_when_sdk_raises(wm_paths, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agent_bridge.automation.workflows.runner import WorkflowRunSpec

    async def fake_query(*, prompt, options):
        yield SimpleNamespace(subtype="init", session_id="session_1")
        raise RuntimeError("sdk failed")

    runner, _ = _patched_runner(wm_paths, monkeypatch, fake_query)
    svc_store = runner._agent_service.store  # noqa: SLF001 — inspect persisted run

    result = runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export default async function workflow() {}",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    assert result.exit_code == 1
    # The raw message log captures the messages streamed before the failure.
    assert "session_1" in (result.run_dir / "messages.jsonl").read_text(encoding="utf-8")
    # The error surfaces in the persisted agent_runs event stream (unified DB copy).
    runs = svc_store.agent_runs.list(workflow_run_id="run_1")
    assert len(runs) == 1
    detail = svc_store.agent_runs.get(runs[0]["run_key"])
    events = detail["events"]
    assert events[-1]["kind"] == "error"
    assert events[-1]["message"] == "RuntimeError: sdk failed"


def test_runner_forwards_timeout_seconds_to_agent_service(tmp_path):
    from agent_bridge.agent_runtime.service import AgentRunResult
    from agent_bridge.automation.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec

    captured: dict = {}

    class _FakeAgentService:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentRunResult(ok=True, result="done")

    runner = ClaudeWorkflowRunner(agent_service=_FakeAgentService())
    runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_t",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export default async function workflow() {}",
            mcp_url="http://127.0.0.1:8765/mcp",
            timeout_seconds=1800,
        ),
    )

    # The configured per-run wall-clock cap must reach AgentService.run as `timeout`.
    assert captured["timeout"] == 1800
    assert captured["backend_key"] == "claude"
    # Workflow identity is forwarded so the produced agent_runs row can be
    # looked up via ``?workflow_run_id=`` / ``?workflow_key=``.
    assert captured["workflow_key"] == "github-summary"
    assert captured["run_id"] == "run_t"
