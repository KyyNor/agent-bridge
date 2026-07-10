from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import (
    AgentBridgePaths,
    AgentRuntimeConfig,
    load_agent_runtime_config,
    load_backend_configs,
    load_mcp_config,
    load_server_config,
    save_agent_runtime_config,
    AgentBackendConfig,
)


def _write_config(config_dir: Path, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "server.toml").write_text(content, encoding="utf-8")


def test_no_backends_returns_empty(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(paths.config_dir, 'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n')
    backends = load_backend_configs(paths)
    assert backends == []


def test_single_backend_config(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.mock]\nbackend_type = "mock"\n'
    ))
    backends = load_backend_configs(paths)
    assert len(backends) == 1
    assert backends[0].slug == "mock"
    assert backends[0].backend_type == "mock"


def test_multiple_backend_configs(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.mock]\nbackend_type = "mock"\n\n'
        '[backends.ragflow]\nbackend_type = "ragflow"\nbase_url = "http://localhost:9380"\napi_key = "ragflow-test"\ntimeout = 120\n'
    ))
    backends = load_backend_configs(paths)
    assert len(backends) == 2
    slugs = {b.slug for b in backends}
    assert slugs == {"mock", "ragflow"}
    ragflow = next(b for b in backends if b.slug == "ragflow")
    assert ragflow.base_url == "http://localhost:9380"
    assert ragflow.api_key == "ragflow-test"
    assert ragflow.timeout == 120


def test_weknora_backend_config_reads_model_ids(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.weknora]\n'
        'backend_type = "weknora"\n'
        'base_url = "http://localhost"\n'
        'api_key = "wek-test"\n'
        'timeout = 120\n'
        'embedding_model_id = "emb-1"\n'
        'summary_model_id = "chat-1"\n'
    ))
    backends = load_backend_configs(paths)
    weknora = backends[0]
    assert weknora.slug == "weknora"
    assert weknora.embedding_model_id == "emb-1"
    assert weknora.summary_model_id == "chat-1"


def test_backend_config_missing_required_field(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.ragflow]\nbase_url = "http://localhost:9380"\n'
    ))
    with pytest.raises(ValueError, match="backend_type"):
        load_backend_configs(paths)


def test_load_server_config_reads_default_backend(tmp_path):
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\ndefault_backend = "ragflow"\n',
        encoding="utf-8",
    )
    paths = AgentBridgePaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(config_path, paths.server_config_path)

    config = load_server_config(paths)
    assert config.default_backend == "ragflow"


def test_load_server_config_default_backend_none_when_missing(tmp_path):
    paths = AgentBridgePaths.from_root(tmp_path)
    config = load_server_config(paths)
    assert config.default_backend is None


def test_load_mcp_config_returns_defaults(tmp_path):
    paths = AgentBridgePaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    # No [mcp] section in config
    config = load_mcp_config(paths)
    assert config.enabled is False
    assert config.transport == "stdio"


def test_load_mcp_config_reads_values(tmp_path):
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n[mcp]\nenabled = true\ntransport = "sse"\n',
        encoding="utf-8",
    )
    paths = AgentBridgePaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(config_path, paths.server_config_path)

    config = load_mcp_config(paths)
    assert config.enabled is True
    assert config.transport == "sse"


def test_load_agent_runtime_config_defaults_to_claude(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    assert load_agent_runtime_config(paths) == AgentRuntimeConfig()


def test_load_agent_runtime_config_reads_agents_section(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(
        paths.config_dir,
        (
            'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
            '[agents]\n'
            'default = "claude-sonnet"\n\n'
            '[agents.claude-sonnet]\n'
            'type = "claude"\n'
            'model = "claude-sonnet-test"\n'
            'command = "ignored-for-claude"\n'
        ),
    )

    config = load_agent_runtime_config(paths)

    assert config.default_backend == "claude-sonnet"
    assert len(config.backends) == 1
    assert config.backends[0].slug == "claude-sonnet"
    assert config.backends[0].agent_type == "claude"
    assert config.backends[0].model == "claude-sonnet-test"
    assert config.backends[0].command == "ignored-for-claude"


def test_load_agent_runtime_config_requires_backend_type(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(
        paths.config_dir,
        (
            'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
            '[agents]\n'
            'default = "custom"\n\n'
            '[agents.custom]\n'
            'model = "x"\n'
        ),
    )

    with pytest.raises(ValueError, match="type"):
        load_agent_runtime_config(paths)


def test_save_agent_runtime_config_replaces_only_agents_section(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    _write_config(
        paths.config_dir,
        (
            'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
            '[agents]\ndefault = "old"\n\n'
            '[agents.old]\ntype = "claude"\n\n'
            '[logging]\nlevel = "DEBUG"\n'
        ),
    )

    saved = save_agent_runtime_config(
        paths,
        AgentRuntimeConfig(
            default_backend="opencode",
            backends=(
                AgentBackendConfig(
                    slug="opencode",
                    agent_type="opencode",
                    command="opencode",
                    model="anthropic/claude-sonnet-4",
                ),
            ),
        ),
    )

    text = paths.server_config_path.read_text(encoding="utf-8")
    assert saved.default_backend == "opencode"
    assert '[logging]\nlevel = "DEBUG"' in text
    assert '[agents.old]' not in text
    assert '[agents.opencode]' in text
    assert 'command = "opencode"' in text
    assert load_agent_runtime_config(paths).default_backend == "opencode"


def test_save_agent_runtime_config_requires_default_backend_to_exist(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)

    with pytest.raises(ValueError, match="not configured"):
        save_agent_runtime_config(
            paths,
            AgentRuntimeConfig(default_backend="opencode", backends=()),
        )


def test_agent_runtime_config_accepts_codex_backend(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)

    saved = save_agent_runtime_config(
        paths,
        AgentRuntimeConfig(
            default_backend="codex",
            backends=(
                AgentBackendConfig(
                    slug="codex",
                    agent_type="codex",
                    command="codex",
                    model="gpt-5",
                ),
            ),
        ),
    )

    loaded = load_agent_runtime_config(paths)

    assert saved.default_backend == "codex"
    assert loaded.default_backend == "codex"
    assert loaded.backends[0].slug == "codex"
    assert loaded.backends[0].agent_type == "codex"
    assert loaded.backends[0].command == "codex"
    assert loaded.backends[0].model == "gpt-5"
