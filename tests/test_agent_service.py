from __future__ import annotations

import asyncio
import json
from pathlib import Path

from claude_agent_sdk import ResultMessage

from agent_bridge.agent_runtime import service as agent_service
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
    monkeypatch.setattr(agent_service, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(agent_service, "claude_query", fake_query)
    monkeypatch.setattr(agent_service, "claude_settings_env", lambda: env or {})


# --- agent_support: MCP config ---


def test_build_server_config_no_profile_returns_empty() -> None:
    assert build_agent_bridge_server_config("http://x/mcp", None) == {}


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
    assert captured["mcp_config"] == {}
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
    # list view drops heavy columns
    assert "prompt" not in rows[0]
    assert "events" not in rows[0]

    full = bundle.store.agent_runs.get(rows[0]["run_key"])
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
