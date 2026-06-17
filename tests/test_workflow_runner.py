from __future__ import annotations


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


def test_claude_runner_uses_auto_permission_mode(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from agent_bridge.workflows.runner import ClaudeWorkflowRunner, WorkflowRunSpec

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = ClaudeWorkflowRunner()
    runner.run(
        tmp_path,
        WorkflowRunSpec(
            run_id="run_1",
            workflow_key="github-summary",
            profile_key="dev-plane",
            workflow_js="export const manifest = {};",
            mcp_url="http://127.0.0.1:8765/mcp",
        ),
    )

    cmd = captured["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"
    assert "--dangerously-skip-permissions" not in cmd
