from pathlib import Path

from typer.testing import CliRunner

from agent_bridge.cli.app import app


ROOT = Path(__file__).resolve().parents[1]


def test_readme_cli_examples_match_registered_root_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("server", "profile", "memory"):
        assert command in result.stdout
    assert "agb wiki" not in readme


def test_agent_documents_describe_current_workflow_and_time_contracts() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "datetime.utcnow()" in agents
    assert "utc_now()" in agents and "utc_iso()" in agents and "parse_utc()" in agents
    assert "结构化 DAG" in claude
    assert "不再执行 `workflow.js`" in claude
    assert "result_parser.py" not in claude
    assert "ClaudeWorkflowRunner" not in claude
    assert "CapabilitySourceRegistry" in claude
    assert "codegraph_index_items" in claude
    assert "不得返回空数组冒充" in claude
    assert "SQLite 隐式文本索引降级" in agents
