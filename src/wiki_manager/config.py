from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROOT = Path("/root/wiki-manager")


@dataclass(frozen=True)
class WikiManagerPaths:
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
    def from_root(cls, root: Path = DEFAULT_ROOT) -> "WikiManagerPaths":
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


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    admins: set[str]


def ensure_directories(paths: WikiManagerPaths) -> None:
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.archive_dir,
        paths.mock_backend_dir,
        paths.logs_dir,
        paths.run_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def load_server_config(paths: WikiManagerPaths) -> ServerConfig:
    ensure_directories(paths)
    if not paths.server_config_path.exists():
        paths.server_config_path.write_text(
            'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n',
            encoding="utf-8",
        )
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    admins = {str(item) for item in raw.get("admins", ["root"])}
    return ServerConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        admins=admins,
    )
