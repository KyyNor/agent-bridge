from __future__ import annotations

import json
import os
import re
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
    def repos_dir(self) -> Path:
        return self.root / "repos"

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    admins: set[str]
    default_backend: str | None = None


@dataclass(frozen=True)
class LoggingConfig:
    """日志配置（server.toml 的 ``[logging]`` 段），所有字段都有默认值。

    由 :func:`load_logging_config` 在启动期读取一次；修改后需重启服务生效
    （日志 sink 不做每请求热重载，避免 sink 频繁重建）。
    """

    level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR
    console: bool = True  # 同时输出到 stderr（服务子进程下进 server.log）
    rotation_size_mb: int = 100  # 单个分卷大小上限，超过即轮转
    retention_days: int = 90  # 分卷保留天数
    retention_max_bytes: int = 5 * 1024 ** 3  # 5 GiB；分卷累计容量上限
    compression: str = "zip"  # 旧分卷压缩格式
    # uvicorn 访问日志：all（每条请求）/ errors_only（仅 4xx5xx，默认）/ off（全关）
    access_log: str = "errors_only"
    # httpx 每请求转发日志级别；默认 WARNING 以屏蔽 dashboard 代理等「纯转发 200」噪音，
    # 仍保留超时 / 连接失败等告警
    httpx_log_level: str = "WARNING"

    @property
    def rotation(self) -> str:
        """loguru ``rotation`` 字符串。"""
        return f"{self.rotation_size_mb} MB"


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
        paths.repos_dir,
        paths.plugins_dir,
        paths.logs_dir,
        paths.run_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def load_server_config(paths: AgentBridgePaths) -> ServerConfig:
    ensure_directories(paths)
    if not paths.server_config_path.exists():
        admin = default_user()
        paths.server_config_path.write_text(
            f"host = \"127.0.0.1\"\n"
            f"port = 8765\n"
            f"admins = [{json.dumps(admin)}]\n"
            f"\n"
            f"# [logging]  # 日志配置（取消注释并按需修改；改后需重启服务生效）\n"
            f"# level = \"INFO\"                   # DEBUG/INFO/WARNING/ERROR\n"
            f"# console = true                   # 同时输出到 stderr（进 server.log）\n"
            f"# rotation_size_mb = 100           # 单分卷大小上限，超过即轮转\n"
            f"# retention_days = 90              # 分卷保留天数\n"
            f"# retention_max_bytes = \"5 GiB\"    # 分卷累计容量上限（KB/MB/GB/KiB/MiB/GiB）\n"
            f"# compression = \"zip\"              # 旧分卷压缩格式\n"
            f"# access_log = \"errors_only\"       # uvicorn 访问日志：all/errors_only(仅4xx5xx)/off\n"
            f"# httpx_log_level = \"WARNING\"      # httpx 转发日志级别（WARNING 屏蔽纯转发 200 噪音）\n",
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


# 容量单位 → 字节数（十进制 KB/MB/GB 与二进制 KiB/MiB/GiB 均支持）
_SIZE_UNITS = {
    "B": 1,
    "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4,
    "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
}

_SIZE_RE = re.compile(r"\s*([\d.]+)\s*([A-Za-z]*)\s*")


def parse_size(value: int | float | str) -> int:
    """把 ``"5 GiB"`` / ``"500 MB"`` / ``1024`` 解析成字节数；纯数字视为字节。"""
    if isinstance(value, bool):  # bool 是 int 子类，先排除
        raise ValueError(f"无法解析容量: {value!r}")
    if isinstance(value, (int, float)):
        return int(value)
    m = _SIZE_RE.fullmatch(str(value))
    if not m:
        raise ValueError(f"无法解析容量: {value!r}")
    num = float(m.group(1))
    unit = m.group(2).upper() or "B"
    if unit not in _SIZE_UNITS:
        raise ValueError(f"未知容量单位: {m.group(2)!r}")
    return int(num * _SIZE_UNITS[unit])


def load_logging_config(paths: AgentBridgePaths) -> LoggingConfig:
    """读取 server.toml 的 ``[logging]`` 段；文件或段缺失时返回全默认值。

    仅在启动期读取一次（:func:`agent_bridge.core.logging.setup_logging` 调用）。
    """
    if not paths.server_config_path.exists():
        return LoggingConfig()
    raw = tomllib.loads(paths.server_config_path.read_text(encoding="utf-8"))
    section = raw.get("logging")
    if not section:
        return LoggingConfig()
    return LoggingConfig(
        level=str(section.get("level", "INFO")),
        console=bool(section.get("console", True)),
        rotation_size_mb=int(section.get("rotation_size_mb", 100)),
        retention_days=int(section.get("retention_days", 90)),
        retention_max_bytes=parse_size(section.get("retention_max_bytes", "5 GiB")),
        compression=str(section.get("compression", "zip")),
        access_log=str(section.get("access_log", "errors_only")),
        httpx_log_level=str(section.get("httpx_log_level", "WARNING")),
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


def migrate_toml_backends_to_db(paths: AgentBridgePaths, store: Any) -> None:
    """One-time migration: TOML backends → DB (skips slugs already in DB)."""
    toml_configs = load_backend_configs(paths)
    if not toml_configs:
        return
    existing = {b["slug"] for b in store.list_backends()}
    for cfg in toml_configs:
        if cfg.slug not in existing:
            store.upsert_backend(
                slug=cfg.slug,
                backend_type=cfg.backend_type,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=cfg.timeout,
                embedding_model_id=cfg.embedding_model_id,
                summary_model_id=cfg.summary_model_id,
            )
