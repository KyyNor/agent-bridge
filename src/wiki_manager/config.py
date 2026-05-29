from __future__ import annotations

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
