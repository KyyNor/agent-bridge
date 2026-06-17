from __future__ import annotations

import json


def test_claude_settings_env_reads_user_settings_file(tmp_path):
    from agent_bridge.claude_agent import claude_settings_env

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "token",
                    "ANTHROPIC_BASE_URL": "https://example.test",
                    "ANTHROPIC_MODEL": "glm-5.2[1M]",
                    "API_TIMEOUT_MS": "3000000",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    env = claude_settings_env(settings_path)

    assert env == {
        "ANTHROPIC_AUTH_TOKEN": "token",
        "ANTHROPIC_BASE_URL": "https://example.test",
        "ANTHROPIC_MODEL": "glm-5.2[1M]",
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def test_claude_settings_env_ignores_missing_or_invalid_file(tmp_path):
    from agent_bridge.claude_agent import claude_settings_env

    assert claude_settings_env(tmp_path / "missing.json") == {}

    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{invalid", encoding="utf-8")
    assert claude_settings_env(settings_path) == {}
