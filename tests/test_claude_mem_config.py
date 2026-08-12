from __future__ import annotations

import json
import subprocess

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.knowledge_management.memory.claude_mem.config import ClaudeMemConfigManager
from agent_bridge.knowledge_management.memory.claude_mem.worker import ClaudeMemWorkerService


def test_claude_mem_config_bootstraps_shared_env_from_claude_settings(wm_paths, tmp_path):
    claude_settings = tmp_path / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "secret-token",
                    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                    "ANTHROPIC_MODEL": "glm-5.2",
                }
            }
        ),
        encoding="utf-8",
    )

    manager = ClaudeMemConfigManager(paths=wm_paths, claude_settings_path=claude_settings)
    config = manager.get_config(bootstrap=True)

    env_file = wm_paths.data_dir / "claude-mem" / "shared" / ".env"
    assert config["env_file_exists"] is True
    assert config["env_file_path"] == str(env_file)
    assert config["base_url"] == "https://open.bigmodel.cn/api/anthropic"
    assert config["model"] == "glm-5.2"
    assert config["has_auth_token"] is True
    assert "secret-token" not in json.dumps(config)
    assert env_file.read_text(encoding="utf-8") == (
        "ANTHROPIC_AUTH_TOKEN=secret-token\n"
        "ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic\n"
    )
    assert json.loads((wm_paths.data_dir / "claude-mem" / "shared" / "config.json").read_text(encoding="utf-8")) == {
        "model": "glm-5.2"
    }


def test_claude_mem_config_strips_one_million_suffix_from_claude_settings_model(wm_paths, tmp_path):
    claude_settings = tmp_path / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "glm-5.2[1M]"}}),
        encoding="utf-8",
    )

    config = ClaudeMemConfigManager(paths=wm_paths, claude_settings_path=claude_settings).get_config(bootstrap=True)

    assert config["model"] == "glm-5.2"
    assert json.loads((wm_paths.data_dir / "claude-mem" / "shared" / "config.json").read_text(encoding="utf-8")) == {
        "model": "glm-5.2"
    }


def test_worker_env_uses_shared_env_file_and_default_chinese_mode(wm_paths, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "claude-mem"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bun-runner.js").write_text("", encoding="utf-8")
    (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
    (scripts / "version-check.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_MEM_PLUGIN_ROOT", str(plugin_dir))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "secret-token", "ANTHROPIC_MODEL": "glm-5.2"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout='{"continue":true}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ClaudeMemWorkerService, "_ensure_worker", lambda self, block, **kwargs: "http://127.0.0.1:37777")
    block = {
        "block_key": "dev-memory",
        "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
    }

    ClaudeMemWorkerService(paths=wm_paths).handle_hook(
        block,
        action="observation",
        payload={"tool_name": "Read"},
        event_name="PostToolUse",
        matcher="*",
        timeout_seconds=120,
    )

    env = calls[0]["env"]
    assert env["CLAUDE_MEM_ENV_FILE"] == str(wm_paths.data_dir / "claude-mem" / "shared" / ".env")
    assert env["CLAUDE_MEM_MODE"] == "code--zh"
    assert env["CLAUDE_MEM_PROVIDER"] == "claude"
    assert env["CLAUDE_MEM_CLAUDE_AUTH_METHOD"] == "gateway"
    assert env["CLAUDE_MEM_MODEL"] == "glm-5.2"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_claude_mem_config_api_saves_without_returning_secret_and_stops_workers(wm_paths, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(paths=wm_paths, admins={"root"})
    stopped = []
    app.state.agent_bridge_service.memory.worker_service.stop_all_workers = lambda: stopped.append(True)  # type: ignore[attr-defined]
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}

    saved = client.post(
        "/api/v1/claude-mem/config",
        json={
            "base_url": "https://open.bigmodel.cn/api/anthropic",
            "auth_token": "new-secret",
            "model": "glm-5.2",
        },
        headers=headers,
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["base_url"] == "https://open.bigmodel.cn/api/anthropic"
    assert body["has_auth_token"] is True
    assert body["model"] == "glm-5.2"
    assert "new-secret" not in json.dumps(body)
    assert "new-secret" in (wm_paths.data_dir / "claude-mem" / "shared" / ".env").read_text(encoding="utf-8")
    assert stopped == [True]
