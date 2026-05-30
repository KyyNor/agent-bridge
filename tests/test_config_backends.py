from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import BackendConfig, WikiManagerPaths, load_backend_configs


def _write_config(config_dir: Path, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "server.toml").write_text(content, encoding="utf-8")


def test_no_backends_returns_empty(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, 'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n')
    backends = load_backend_configs(paths)
    assert backends == []


def test_single_backend_config(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.mock]\nbackend_type = "mock"\n'
    ))
    backends = load_backend_configs(paths)
    assert len(backends) == 1
    assert backends[0].slug == "mock"
    assert backends[0].backend_type == "mock"


def test_multiple_backend_configs(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
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


def test_backend_config_missing_required_field(tmp_path: Path):
    paths = WikiManagerPaths.from_root(tmp_path)
    _write_config(paths.config_dir, (
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n'
        '[backends.ragflow]\nbase_url = "http://localhost:9380"\n'
    ))
    with pytest.raises(ValueError, match="backend_type"):
        load_backend_configs(paths)
