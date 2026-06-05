from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROOT = Path("/root/agent-bridge")
ROOT_ENV_VAR = "AGENT_BRIDGE_ROOT"
USER_ENV_VAR = "AGENT_BRIDGE_USER"


def default_root() -> Path:
    raw = os.environ.get(ROOT_ENV_VAR)
    return Path(raw).expanduser() if raw else DEFAULT_ROOT


def default_user(fallback: str = "root") -> str:
    return os.environ.get(USER_ENV_VAR, fallback)


@dataclass(frozen=True)
class AgentBridgePaths:
    root: Path
    config_dir: Path
    data_dir: Path
    logs_dir: Path
    run_dir: Path
    db_path: Path
    archive_dir: Path
    mock_backend_dir: Path
    server_config_path: Path
    server_log_path: Path
    server_pid_path: Path

    @classmethod
    def from_root(cls, root: Path | None = None) -> "AgentBridgePaths":
        root = root or default_root()
        return cls(
            root=root,
            config_dir=root / "config",
            data_dir=root / "data",
            logs_dir=root / "logs",
            run_dir=root / "run",
            db_path=root / "data" / "wiki.db",
            archive_dir=root / "data" / "archive",
            mock_backend_dir=root / "data" / "backend" / "mock",
            server_config_path=root / "config" / "server.toml",
            server_log_path=root / "logs" / "server.log",
            server_pid_path=root / "run" / "server.pid",
        )

    @property
    def codegraph_dir(self) -> Path:
        return self.root / "codegraph"


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    admins: set[str]
    default_backend: str | None = None


@dataclass(frozen=True)
class BackendConfig:
    slug: str
    backend_type: str
    base_url: str | None = None
    api_key: str | None = None
    timeout: int = 120
    embedding_model_id: str | None = None
    summary_model_id: str | None = None


def ensure_directories(paths: AgentBridgePaths) -> None:
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.archive_dir,
        paths.mock_backend_dir,
        paths.codegraph_dir,
        paths.logs_dir,
        paths.run_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def load_server_config(paths: AgentBridgePaths) -> ServerConfig:
    ensure_directories(paths)
    if not paths.server_config_path.exists():
        admin = default_user()
        paths.server_config_path.write_text(
            f"host = \"127.0.0.1\"\nport = 8765\nadmins = [{json.dumps(admin)}]\n",
            encoding="utf-8",
        )
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    admins = {str(item) for item in raw.get("admins", ["root"])}
    return ServerConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        admins=admins,
        default_backend=raw.get("default_backend"),
    )


@dataclass(frozen=True)
class McpConfig:
    enabled: bool = False
    transport: str = "stdio"


def load_mcp_config(paths: AgentBridgePaths) -> McpConfig:
    if not paths.server_config_path.exists():
        return McpConfig()
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    mcp_section = raw.get("mcp", {})
    return McpConfig(
        enabled=bool(mcp_section.get("enabled", False)),
        transport=str(mcp_section.get("transport", "stdio")),
    )


def load_backend_configs(paths: AgentBridgePaths) -> list[BackendConfig]:
    if not paths.server_config_path.exists():
        return []
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    backends_raw = raw.get("backends", {})
    if not backends_raw:
        return []
    result = []
    for slug, section in backends_raw.items():
        if "backend_type" not in section:
            raise ValueError(f"backend '{slug}' missing required field: backend_type")
        result.append(BackendConfig(
            slug=slug,
            backend_type=section["backend_type"],
            base_url=section.get("base_url"),
            api_key=section.get("api_key"),
            timeout=int(section.get("timeout", 120)),
            embedding_model_id=section.get("embedding_model_id"),
            summary_model_id=section.get("summary_model_id"),
        ))
    return result
