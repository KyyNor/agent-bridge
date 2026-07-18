from __future__ import annotations

import asyncio
import json
from pathlib import Path

from claude_agent_sdk import ResultMessage

from agent_bridge.agent_runtime.service import _extract_json, _extract_result
from agent_bridge.agent_runtime.support import build_agent_bridge_server_config, write_run_mcp_json
from agent_bridge.capability_hub.profiles.docs import POINTER_START, install_profile_to_cwd, pointer_block
from agent_bridge.app.service import AgentBridgeService


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _result(**overrides) -> ResultMessage:
    base = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session_1",
        result="done",
        total_cost_usd=0.001,
    )
    base.update(overrides)
    return ResultMessage(**base)


def _patch_sdk(monkeypatch, fake_query, env=None) -> None:
    from agent_bridge.agent_runtime.adapters import claude as claude_agent

    monkeypatch.setattr(claude_agent, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(claude_agent, "claude_query", fake_query)
    monkeypatch.setattr(claude_agent, "claude_settings_env", lambda: env or {})


# --- agent_support: MCP config ---


def test_build_server_config_no_profile_returns_empty_mcp_servers() -> None:
    assert build_agent_bridge_server_config("http://x/mcp", None) == {"mcpServers": {}}


def test_build_server_config_profile_only_has_profile_header() -> None:
    config = build_agent_bridge_server_config("http://x/mcp", "abc")
    headers = config["mcpServers"]["agent-bridge"]["headers"]
    assert headers == {"X-Agent-Bridge-MetaMCP-Profile": "abc"}
    assert config["mcpServers"]["agent-bridge"]["url"] == "http://x/mcp"


def test_build_server_config_workflow_requires_both_key_and_run_id() -> None:
    full = build_agent_bridge_server_config(
        "http://x/mcp", "abc", workflow_key="wf", run_id="run_1"
    )
    headers = full["mcpServers"]["agent-bridge"]["headers"]
    assert headers["X-Agent-Bridge-Workflow"] == "true"
    assert headers["X-Agent-Bridge-Workflow-Key"] == "wf"
    assert headers["X-Agent-Bridge-Workflow-Run-Id"] == "run_1"

    missing_run = build_agent_bridge_server_config(
        "http://x/mcp", "abc", workflow_key="wf", run_id=None
    )
    assert "X-Agent-Bridge-Workflow" not in missing_run["mcpServers"]["agent-bridge"]["headers"]


def test_write_run_mcp_json_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / ".mcp.json"
    write_run_mcp_json(path, {"mcpServers": {}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}


# --- profile_docs.install_profile_to_cwd ---


def test_install_profile_to_cwd_writes_doc_and_pointer(tmp_path: Path) -> None:
    doc = install_profile_to_cwd(tmp_path, "safe-profile", "# guidance")
    assert doc.is_file()
    assert doc.read_text(encoding="utf-8") == "# guidance"
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"@{doc.resolve()}" in claude_md
    assert POINTER_START in claude_md


def test_install_profile_to_cwd_is_idempotent(tmp_path: Path) -> None:
    install_profile_to_cwd(tmp_path, "p", "v1")
    install_profile_to_cwd(tmp_path, "p", "v2")
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.count(POINTER_START) == 1
    assert (tmp_path / ".agent-bridge" / "profiles" / "p.md").read_text(encoding="utf-8") == "v2"


# --- _extract_result ---


def test_extract_result_prefers_structured_output() -> None:
    msg = _result(structured_output={"answer": 42})
    assert _extract_result(msg, {"type": "object"}) == {"answer": 42}


def test_extract_result_falls_back_to_json_in_text() -> None:
    msg = _result(result='Here you go:\n```json\n{"x": 1}\n```')
    assert _extract_result(msg, {"type": "object"}) == {"x": 1}


def test_extract_result_without_schema_returns_text() -> None:
    msg = _result(result="plain answer")
    assert _extract_result(msg, None) == "plain answer"


def test_extract_json_returns_none_for_non_json() -> None:
    assert _extract_json("no braces here") is None
    assert _extract_json("") is None


# --- AgentService.run (monkeypatched claude_query; async via asyncio.run) ---


def test_run_success_text_result(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        assert prompt == "say hi"
        yield _result(result="hello world")

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="say hi", agent_name="greeter"))

    assert res.ok is True
    assert res.result == "hello world"
    assert res.error is None
    assert res.session_id == "session_1"
    assert Path(res.run_dir).is_dir()


def test_run_rejects_active_client_run_key_before_sdk_query(wm_paths, monkeypatch) -> None:
    calls = 0

    async def fake_query(*, prompt, options):
        nonlocal calls
        calls += 1
        yield _result(result="first")

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents
    service.control_registry.register("occupied_key")

    from agent_bridge.core.domain import ConflictError

    try:
        asyncio.run(service.run(prompt="second", agent_name="agent", run_key="occupied_key"))
    except ConflictError as exc:
        assert str(exc) == "agent run key already exists"
    else:
        raise AssertionError("expected duplicate active client run key to conflict")

    assert calls == 0
    assert service.control_registry.is_active("occupied_key") is True


def test_run_rejects_existing_client_run_key_without_second_sdk_query(wm_paths, monkeypatch) -> None:
    calls = 0

    async def fake_query(*, prompt, options):
        nonlocal calls
        calls += 1
        yield _result(result="first")

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents
    asyncio.run(service.run(prompt="first", agent_name="agent", run_key="existing_key"))

    from agent_bridge.core.domain import ConflictError

    try:
        asyncio.run(service.run(prompt="second", agent_name="agent", run_key="existing_key"))
    except ConflictError as exc:
        assert str(exc) == "agent run key already exists"
    else:
        raise AssertionError("expected duplicate persisted client run key to conflict")

    assert calls == 1
    assert service.store.agent_runs.get("existing_key")["result"] == "first"
def test_run_delegates_to_injected_coding_agent(wm_paths) -> None:
    from agent_bridge.agent_runtime.registry import CodingAgentRegistry
    from agent_bridge.agent_runtime.types import CodingAgentFinal, CodingAgentUpdate
    from agent_bridge.app.service import AgentBridgeService

    captured = {}

    class _FakeRun:
        async def updates(self):
            yield CodingAgentUpdate(
                raw={"type": "FakeMessage", "result": "adapter done"},
                events=[{"kind": "agent_message", "message": "adapter says hi"}],
            )
            yield CodingAgentUpdate(
                final=CodingAgentFinal(
                    result="adapter done",
                    session_id="fake_session",
                    cost_usd=0.0,
                    num_turns=1,
                )
            )

        async def abort(self):
            captured["aborted"] = True

    class _FakeCodingAgent:
        backend_key = "fake"
        display_name = "Fake"
        source = "fake_agent"
        capabilities = None

        def start(self, request):
            captured["request"] = request
            return _FakeRun()

    service = AgentBridgeService.create(wm_paths, {"root"}).agents
    service.coding_agents = CodingAgentRegistry(
        default_backend="fake",
        agents=[_FakeCodingAgent()],
    )

    res = asyncio.run(service.run(prompt="x", agent_name="fake-agent"))

    assert res.ok is True
    assert res.result == "adapter done"
    assert res.session_id == "fake_session"
    assert captured["request"].prompt == "x"
    assert "FakeMessage" in (Path(res.run_dir) / "messages.jsonl").read_text(encoding="utf-8")
    detail = service.store.agent_runs.get(res.run_key)
    assert detail["events"][0]["message"] == "adapter says hi"


def test_run_can_select_registered_backend_by_key(wm_paths) -> None:
    from agent_bridge.agent_runtime.registry import CodingAgentRegistry
    from agent_bridge.agent_runtime.types import CodingAgentFinal, CodingAgentUpdate
    from agent_bridge.app.service import AgentBridgeService

    captured = {}

    class _FakeRun:
        def __init__(self, name):
            self.name = name

        async def updates(self):
            yield CodingAgentUpdate(final=CodingAgentFinal(result=self.name))

        async def abort(self):
            pass

    class _FakeCodingAgent:
        source = "fake"
        capabilities = None

        def __init__(self, backend_key):
            self.backend_key = backend_key
            self.display_name = backend_key

        def start(self, request):
            captured["backend"] = self.backend_key
            return _FakeRun(self.backend_key)

    service = AgentBridgeService.create(wm_paths, {"root"}).agents
    service.coding_agents = CodingAgentRegistry(
        default_backend="default",
        agents=[_FakeCodingAgent("default"), _FakeCodingAgent("other")],
    )

    res = asyncio.run(service.run(prompt="x", agent_name="select", backend_key="other"))

    assert res.ok is True
    assert res.result == "other"
    assert captured["backend"] == "other"


def test_run_unknown_backend_returns_failed_envelope(wm_paths) -> None:
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="x", agent_name="missing", backend_key="nope"))

    assert res.ok is False
    assert "not registered" in (res.error or "")


def test_run_success_structured_result(wm_paths, monkeypatch) -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "number"}}}

    async def fake_query(*, prompt, options):
        assert options.kwargs["output_format"] == {"type": "json_schema", "schema": schema}
        yield _result(structured_output={"answer": 42})

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="compute", agent_name="calc", output_schema=schema))

    assert res.ok is True
    assert res.result == {"answer": 42}


def test_run_rejects_adapter_independent_output_schema_violation(wm_paths) -> None:
    from agent_bridge.agent_runtime.registry import CodingAgentRegistry
    from agent_bridge.agent_runtime.types import (
        CodingAgentCapabilities,
        CodingAgentFinal,
        CodingAgentUpdate,
    )

    class _InvalidSchemaRun:
        async def updates(self):
            yield CodingAgentUpdate(
                final=CodingAgentFinal(structured_output={"answer": "forty-two"})
            )

        async def abort(self):
            return None

    class _InvalidSchemaAgent:
        backend_key = "schema-test"
        display_name = "Schema test"
        source = "test"
        capabilities = CodingAgentCapabilities(supports_native_json_schema=True)

        def start(self, request):
            return _InvalidSchemaRun()

    bundle = AgentBridgeService.create(wm_paths, {"root"})
    bundle.agents.coding_agents = CodingAgentRegistry(
        default_backend="schema-test",
        agents=[_InvalidSchemaAgent()],
    )
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
    }

    result = asyncio.run(
        bundle.agents.run(
            prompt="compute",
            agent_name="schema-check",
            output_schema=schema,
        )
    )

    assert result.ok is False
    assert result.result is None
    assert result.error is not None
    assert result.error.startswith("agent output_schema invalid field=answer:")
    stored = bundle.store.agent_runs.get(result.run_key)
    assert stored["status"] == "failed"
    assert stored["result"] is None


def test_run_error_returns_envelope_without_raising(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        raise RuntimeError("boom")
        yield  # pragma: no cover - makes this an async generator

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="x", agent_name="a"))

    assert res.ok is False
    assert "RuntimeError" in res.error
    assert res.result is None
    assert Path(res.run_dir).is_dir()


def test_run_result_is_error_reported(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        yield _result(is_error=True, subtype="error_max_budget_usd", result="budget exceeded")

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="x", agent_name="a"))

    assert res.ok is False
    assert res.error == "budget exceeded"


def test_run_timeout(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        await asyncio.sleep(10)
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="x", agent_name="a", timeout=0.05))

    assert res.ok is False
    assert "timed out" in res.error


def test_run_stop_cancels_query_and_persists_stopped(wm_paths, monkeypatch) -> None:
    entered = asyncio.Event()

    async def fake_query(*, prompt, options):
        entered.set()
        await asyncio.sleep(10)
        if False:
            yield _result()

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})
    run_key = "design_script_client_stop"

    async def run_and_stop():
        task = asyncio.create_task(
            bundle.agents.run(prompt="long", agent_name="design_script", run_key=run_key)
        )
        await entered.wait()
        assert bundle.agents.request_stop(run_key) is True
        return await task

    result = asyncio.run(run_and_stop())

    assert result.ok is False
    assert result.stopped is True
    assert result.error == "运行已由用户停止"
    assert bundle.store.agent_runs.get(run_key)["status"] == "stopped"
    assert bundle.agents.control_registry.is_active(run_key) is False
    events = bundle.store.agent_runs.get(run_key)["events"]
    assert any(event["kind"] == "status" and event["status"] == "stopped" for event in events)
    assert any(event["kind"] == "error" and event["message"] == "运行已由用户停止" for event in events)


def test_run_stop_requested_before_query_does_not_call_sdk(wm_paths, monkeypatch) -> None:
    called = False

    async def fake_query(*, prompt, options):
        nonlocal called
        called = True
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})
    run_key = "workflow_design_stop_before_start"
    assert bundle.agents.request_stop(run_key) is True

    result = asyncio.run(
        bundle.agents.run(prompt="cancel", agent_name="workflow", run_key=run_key)
    )

    assert result.stopped is True
    assert called is False
    assert bundle.store.agent_runs.get(run_key)["status"] == "stopped"


def test_agent_run_finish_only_updates_running_row(wm_paths) -> None:
    bundle = AgentBridgeService.create(wm_paths, {"root"})
    bundle.store.agent_runs.create(
        run_key="already_done",
        agent_name="agent",
        status="completed",
        ok=True,
        prompt="done",
    )

    updated = bundle.store.agent_runs.finish_run(
        "already_done",
        ok=False,
        status="stopped",
        error="运行已由用户停止",
    )

    assert updated is False
    row = bundle.store.agent_runs.get("already_done")
    assert row["status"] == "completed"
    assert row["ok"] is True


def test_run_copies_files_into_work_dir(wm_paths, monkeypatch, tmp_path) -> None:
    src = tmp_path / "data.txt"
    src.write_text("payload", encoding="utf-8")

    captured = {}

    async def fake_query(*, prompt, options):
        captured["cwd"] = options.kwargs["cwd"]
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="read data.txt", agent_name="f", files=[src]))

    assert res.ok is True
    assert (Path(res.run_dir) / "data.txt").read_text(encoding="utf-8") == "payload"


def test_run_installs_profile_claude_md_and_mcp_config(wm_paths, monkeypatch) -> None:
    service_bundle = AgentBridgeService.create(wm_paths, {"root"})
    service_bundle.governance.upsert_profile(
        actor="root", profile_key="safe", name="Safe", description="", status="active"
    )

    async def fake_query(*, prompt, options):
        yield _result()

    _patch_sdk(monkeypatch, fake_query)

    res = asyncio.run(
        service_bundle.agents.run(prompt="use tools", agent_name="worker", profile="safe")
    )

    assert res.ok is True
    run_dir = Path(res.run_dir)
    claude_md = (run_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@" in claude_md and "profile-pointer" in claude_md
    assert (run_dir / ".agent-bridge" / "profiles" / "safe.md").is_file()
    mcp = json.loads((run_dir / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["agent-bridge"]["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "safe"


def test_run_opencode_writes_native_mcp_config(wm_paths) -> None:
    from agent_bridge.agent_runtime.registry import CodingAgentRegistry
    from agent_bridge.agent_runtime.types import CodingAgentCapabilities, CodingAgentFinal, CodingAgentUpdate

    captured = {}

    class _FakeRun:
        async def updates(self):
            yield CodingAgentUpdate(final=CodingAgentFinal(result="ok"))

        async def abort(self):
            return None

    class _FakeOpenCode:
        backend_key = "opencode"
        display_name = "OpenCode"
        source = "opencode_cli"
        capabilities = CodingAgentCapabilities(supports_mcp=True)

        def start(self, request):
            captured["config"] = json.loads((request.cwd / "opencode.json").read_text(encoding="utf-8"))
            return _FakeRun()

    bundle = AgentBridgeService.create(wm_paths, {"root"})
    bundle.governance.upsert_profile(
        actor="root", profile_key="safe", name="Safe", description="", status="active"
    )
    bundle.agents.coding_agents = CodingAgentRegistry(
        default_backend="opencode", agents=[_FakeOpenCode()]
    )

    result = asyncio.run(
        bundle.agents.run(
            prompt="use tools", agent_name="opencode-worker", profile="safe", backend_key="opencode"
        )
    )

    assert result.ok is True
    assert captured["config"]["mcp"]["agent-bridge"]["type"] == "remote"
    assert captured["config"]["mcp"]["agent-bridge"]["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "safe"


def test_run_no_profile_skips_mcp_and_claude_md(wm_paths, monkeypatch) -> None:
    captured = {}

    async def fake_query(*, prompt, options):
        captured["setting_sources"] = options.kwargs["setting_sources"]
        captured["mcp_config"] = json.loads(
            (Path(options.kwargs["cwd"]) / ".mcp.json").read_text(encoding="utf-8")
        )
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents

    res = asyncio.run(service.run(prompt="x", agent_name="isolated"))

    assert res.ok is True
    assert captured["setting_sources"] == []
    assert captured["mcp_config"] == {"mcpServers": {}}
    assert not (Path(res.run_dir) / "CLAUDE.md").exists()


def test_run_workflow_headers_passed_to_mcp_config(wm_paths, monkeypatch) -> None:
    captured = {}

    async def fake_query(*, prompt, options):
        captured["mcp_config"] = json.loads(
            (Path(options.kwargs["cwd"]) / ".mcp.json").read_text(encoding="utf-8")
        )
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    service = AgentBridgeService.create(wm_paths, {"root"}).agents
    monkeypatch.setattr(
        service.governance, "render_profile_markdown", lambda actor, profile: {"markdown": "# x"}
    )

    asyncio.run(
        service.run(
            prompt="x",
            agent_name="wf-agent",
            profile="abc",
            workflow_key="report-wf",
            run_id="report-wf_019edf",
        )
    )

    headers = captured["mcp_config"]["mcpServers"]["agent-bridge"]["headers"]
    assert headers["X-Agent-Bridge-Workflow"] == "true"
    assert headers["X-Agent-Bridge-Workflow-Key"] == "report-wf"
    assert headers["X-Agent-Bridge-Workflow-Run-Id"] == "report-wf_019edf"


# --- agent_runs logging ---


def test_run_logs_success_to_agent_runs(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        yield _result(result="hello")

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})

    res = asyncio.run(bundle.agents.run(prompt="hi there", agent_name="greeter"))

    assert res.ok is True
    rows = bundle.store.agent_runs.list(agent_name="greeter")
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["agent_name"] == "greeter"
    assert rows[0]["backend_key"] == "claude"
    # list view drops heavy columns
    assert "prompt" not in rows[0]
    assert "events" not in rows[0]

    full = bundle.store.agent_runs.get(rows[0]["run_key"])
    assert full["backend_key"] == "claude"
    assert full["prompt"] == "hi there"
    assert full["result"] == "hello"
    assert full["ok"] is True
    assert any(event["kind"] == "result" for event in full["events"])


def test_run_logs_structured_result_and_schema(wm_paths, monkeypatch) -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "number"}}}

    async def fake_query(*, prompt, options):
        yield _result(structured_output={"answer": 7})

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})

    asyncio.run(
        bundle.agents.run(prompt="compute", agent_name="calc", output_schema=schema)
    )

    full = bundle.store.agent_runs.get(bundle.store.agent_runs.list(agent_name="calc")[0]["run_key"])
    assert full["result"] == {"answer": 7}
    assert full["output_schema"] == schema


def test_run_logs_failure_with_error_event(wm_paths, monkeypatch) -> None:
    async def fake_query(*, prompt, options):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})

    res = asyncio.run(bundle.agents.run(prompt="x", agent_name="failer"))

    assert res.ok is False
    rows = bundle.store.agent_runs.list(agent_name="failer")
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    full = bundle.store.agent_runs.get(rows[0]["run_key"])
    assert full["error"] is not None and "boom" in full["error"]
    assert any(
        event["kind"] == "error" and "boom" in event["message"]
        for event in full["events"]
    )


def test_run_logs_workflow_context(wm_paths, monkeypatch, tmp_path) -> None:
    async def fake_query(*, prompt, options):
        yield _result()

    _patch_sdk(monkeypatch, fake_query)
    bundle = AgentBridgeService.create(wm_paths, {"root"})

    asyncio.run(
        bundle.agents.run(
            prompt="x",
            agent_name="wf",
            cwd=tmp_path,
            workflow_key="report-wf",
            run_id="report-wf_abc123",
        )
    )

    full = bundle.store.agent_runs.get(
        bundle.store.agent_runs.list(agent_name="wf")[0]["run_key"]
    )
    assert full["workflow_key"] == "report-wf"
    assert full["workflow_run_id"] == "report-wf_abc123"


def test_agent_runs_list_filters_by_ok(wm_paths, monkeypatch) -> None:
    async def good_query(*, prompt, options):
        yield _result(result="ok")

    async def bad_query(*, prompt, options):
        raise RuntimeError("nope")
        yield  # pragma: no cover

    bundle = AgentBridgeService.create(wm_paths, {"root"})
    _patch_sdk(monkeypatch, good_query)
    asyncio.run(bundle.agents.run(prompt="a", agent_name="filt"))
    _patch_sdk(monkeypatch, bad_query)
    asyncio.run(bundle.agents.run(prompt="b", agent_name="filt"))

    all_rows = bundle.store.agent_runs.list(agent_name="filt")
    assert len(all_rows) == 2
    ok_rows = bundle.store.agent_runs.list(agent_name="filt", ok=True)
    assert len(ok_rows) == 1
    assert ok_rows[0]["ok"] is True
